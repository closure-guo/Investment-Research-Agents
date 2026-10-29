# B4 拒绝池复苏重评通道 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 新增 `factor_revival_daily` 日批（零 LLM）：滚动 |ICIR| 越复苏静默线且冷却期外的 rejected 行，经外部导入同一路径重过五道闸；rejected 保持终态，状态机零改动。

**Architecture:** 新模块 `factors/revival.py`（筛选 + cap + 提交，复用 `screening.ranker.recent_ic_stats` 公式与 `factors/external.run_factor_external_import` 准入路径）；常量入 `factors/constraints.py`（静默线/窗口复用 `MIN_ABS_ICIR`/`IC_WINDOW` 不另立阈值）；ops 注册四表 + scheduler 包装；17:10 mon-fri。

**Tech Stack:** Python 3.12 / pytest / ruff / mypy；零新依赖。

## Global Constraints

- rejected 是终态（`rejected → ∅`）：过闸=新 candidate 行；再拒=既有 `re_rejected` upsert（冷却重置）。**禁止**新增状态迁移边。
- 静默线只作提交预筛；五道闸是唯一准入权威，闸② 0.3 等判据零改动。
- 面板仅在存在待提交候选时构建（复用 external 路径 `_panel_from_snapshot`）；dry_run/空选零面板。
- mypy `files=["src"]` 全注解；pre-commit 锁坏 → `ruff check` + `ruff format --check` + `python tests/scripts/check_secrets.py` 等价检查后 `--no-verify`。
- 分支在 `.worktrees/add-rejected-factor-revival`；主检出有并发会话脏文件（docker-compose/前端），实施与提交全在 worktree。
- 测试面板复用 `tests.factors.test_builtin._strong_panel / _STRONG_SEED`（`delta(close,20)` 过闸、`div(ts_mean(close,5),close)` 闸② WEAK_ICIR 拒）。

---

### Task 1: constraints 复苏常量

**Files:**
- Modify: `src/finance_agent/factors/constraints.py`（`BUCKET_TOP_K` 定义区之后）
- Test: `tests/factors/test_constraints.py`（存在则追加；不存在则新建最小文件）

**Interfaces:**
- Produces: `REVIVAL_COOLDOWN_DAYS: int = 30`、`REVIVAL_DAILY_CAP: int = 5`

- [ ] **Step 1: 失败测试**（新建/追加 `tests/factors/test_constraints.py`）

```python
"""因子约束常量单调守护（既有值不得静默漂移）。"""

from finance_agent.factors.constraints import MIN_ABS_ICIR, REVIVAL_COOLDOWN_DAYS, REVIVAL_DAILY_CAP


def test_revival_constants() -> None:
    assert REVIVAL_COOLDOWN_DAYS == 30
    assert REVIVAL_DAILY_CAP == 5
    # 静默线与闸②同值：常量复用而非复制（单一真相源由实现 import 保证）
    assert MIN_ABS_ICIR == 0.3
```

- [ ] **Step 2: 跑红** `uv run pytest tests/factors/test_constraints.py -v` → ImportError（REVIVAL_* 不存在）
- [ ] **Step 3: 实现**——`constraints.py` 的 `BUCKET_TOP_K: int = 10` 之后追加：

```python
# 拒绝池复苏重评（add-rejected-factor-revival）：冷却期（自然日，自最近 created/
# re_rejected 事件起算）与单次运行提交上限。静默线复用闸② ``MIN_ABS_ICIR``、
# 窗口复用 ``tools.IC_WINDOW``——单一真相源，不另立阈值。
REVIVAL_COOLDOWN_DAYS: int = 30
REVIVAL_DAILY_CAP: int = 5
```

- [ ] **Step 4: 跑绿** `uv run pytest tests/factors/test_constraints.py -v` → PASS
- [ ] **Step 5: 提交** `git add -A && git commit --no-verify -m "feat(factors): 复苏重评常量 (add-rejected-factor-revival T1)"`

---

### Task 2: revival.py 核心模块

**Files:**
- Create: `src/finance_agent/factors/revival.py`
- Test: `tests/factors/test_revival.py`

**Interfaces:**
- Consumes: `library.list_factors(status=)/list_events()/list_ic_series()`；`screening.ranker.recent_ic_stats(rows, window=)`；`external.run_factor_external_import(payload, panel=, db_path=, snapshot_db_path=, ledger_source=)`；`tools.IC_WINDOW`；`constraints.MIN_ABS_ICIR/REVIVAL_*`
- Produces: `run_factor_revival(*, dry_run: bool = False, db_path: str|Path|None = None, snapshot_db_path: str|Path|None = None, panel: MarketPanel|None = None, cooldown_days: int = REVIVAL_COOLDOWN_DAYS, daily_cap: int = REVIVAL_DAILY_CAP, now: datetime|None = None) -> dict`（键：`checked/eligible/cooldown_skipped/submitted/admitted/re_rejected/errors/dry_run/revived/rejections`）；`_last_revival_event_at(events) -> str|None`；`_in_cooldown(last_at, *, now, cooldown_days) -> bool`

- [ ] **Step 1: 失败测试**（新建 `tests/factors/test_revival.py`，完整文件）

```python
"""拒绝池复苏重评通道（add-rejected-factor-revival）单测。

IC 序列造数：点值 0.05/0.15 交替 → mean=0.10, std≈0.05 → 滚动 ICIR≈2.0（过静默线）；
点值恒 0.01（常量序列）→ ICIR=0.0（低于静默线）。准入面板复用
``tests.factors.test_builtin`` 合成强信号面板（``delta(close,20)`` 过闸）。
"""

时钟纪律（clock-bomb 教训）：事件时间戳是**真实运行时钟**（library._now()），
``now`` 一律从事件戳**动态推导**（+40 天=冷却外；+10 天=冷却内），禁止写死日期——
写死日期的用例在任何非编写日运行必炸。
"""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import pytest

from finance_agent.factors import library
from finance_agent.factors.constraints import REVIVAL_COOLDOWN_DAYS, REVIVAL_DAILY_CAP
from finance_agent.factors.revival import (
    _in_cooldown,
    _last_revival_event_at,
    run_factor_revival,
)
from tests.factors.test_builtin import _STRONG_SEED, _strong_panel

_HYPOTHESIS = "拒绝池复苏重提：测试用经济学假设文本，长度达标并描述与下一期收益的关系。"
_PASS_EXPRESSION = "delta(close,20)"
_REJECT_EXPRESSION = "div(ts_mean(close,5),close)"


def _seed_rejected(db: str | Path, expression: str, *, ic_points: int, ic_value: float) -> int:
    """造 rejected 行 + IC 序列（点数 ic_points、点值 ic_value 或交替强信号）。

    IC 日期取 2026-05-01 起逐日递增的**互异**日期（record_ic 按 (factor, date)
    幂等 upsert，日期循环会折叠成 30 点——正好踩 60 点门槛的假阴性）。
    """
    result = library.record_rejected_factor(
        expression=expression,
        hypothesis=_HYPOTHESIS,
        category="外部动量",
        ast_json=None,
        source="external",
        gate=2,
        reason="WEAK_ICIR",
        detail=None,
        db_path=db,
    )
    factor_id = int(result["factor_id"])
    base = datetime(2026, 5, 1)
    for i in range(ic_points):
        value = ic_value
        if ic_value == "alternating":
            value = 0.05 if i % 2 == 0 else 0.15
        library.record_ic(
            factor_id, (base + timedelta(days=i)).date().isoformat(), value, db_path=db
        )
    return factor_id


def _db(tmp_path: Path) -> str:
    return str(tmp_path / "factor_library.db")


def _now_after_cooldown(db: str | Path, *, days: int = REVIVAL_COOLDOWN_DAYS + 10) -> datetime:
    """从库内最新 created/re_rejected 事件戳推导 now（冷却外/内由 days 控制）。"""
    last = _last_revival_event_at(library.list_events(db_path=db))
    assert last is not None
    return datetime.fromisoformat(last) + timedelta(days=days)


def test_in_cooldown_unit() -> None:
    now = datetime(2026, 12, 1, 12, 0, 0)
    assert _in_cooldown(None, now=now, cooldown_days=30) is False
    recent = (now - timedelta(days=10)).isoformat()
    assert _in_cooldown(recent, now=now, cooldown_days=30) is True
    stale = (now - timedelta(days=40)).isoformat()
    assert _in_cooldown(stale, now=now, cooldown_days=30) is False
    assert _in_cooldown("not-a-timestamp", now=now, cooldown_days=30) is False  # 损坏放行


def test_last_revival_event_at_takes_max() -> None:
    events = [
        {"event": "created", "created_at": "2026-10-28T09:00:00"},
        {"event": "status_change", "created_at": "2026-10-29T09:00:00"},
        {"event": "re_rejected", "created_at": "2026-11-20T09:00:00"},
    ]
    assert _last_revival_event_at(events) == "2026-11-20T09:00:00"
    assert _last_revival_event_at([]) is None


def test_eligible_row_submitted_and_admitted(tmp_path) -> None:
    db = _db(tmp_path)
    _seed_rejected(db, _PASS_EXPRESSION, ic_points=60, ic_value="alternating")
    summary = run_factor_revival(
        db_path=db, panel=_strong_panel(), seed=_STRONG_SEED, now=_now_after_cooldown(db)
    )
    assert summary["checked"] == 1
    assert summary["eligible"] == 1
    assert summary["submitted"] == 1
    assert summary["admitted"] == 1
    rows = library.list_factors(db_path=db)
    active = [r for r in rows if r["status"] == "active"]
    rejected = [r for r in rows if r["status"] == "rejected"]
    assert len(active) == 1 and len(rejected) == 1  # 新行入库，原行终态不变
    assert active[0]["expression"] == _PASS_EXPRESSION


def test_below_line_row_not_eligible(tmp_path) -> None:
    db = _db(tmp_path)
    _seed_rejected(db, _PASS_EXPRESSION, ic_points=60, ic_value=0.01)  # 常量 → ICIR 0.0
    summary = run_factor_revival(db_path=db, now=_now_after_cooldown(db))
    assert summary["eligible"] == 0 and summary["submitted"] == 0


def test_few_points_row_not_eligible(tmp_path) -> None:
    db = _db(tmp_path)
    _seed_rejected(db, _PASS_EXPRESSION, ic_points=30, ic_value="alternating")
    summary = run_factor_revival(db_path=db, now=_now_after_cooldown(db))
    assert summary["eligible"] == 0


def test_cooldown_skip(tmp_path) -> None:
    db = _db(tmp_path)
    _seed_rejected(db, _PASS_EXPRESSION, ic_points=60, ic_value="alternating")
    summary = run_factor_revival(db_path=db, now=_now_after_cooldown(db, days=10))
    assert summary["cooldown_skipped"] == 1 and summary["submitted"] == 0


def test_re_rejection_resets_cooldown(tmp_path) -> None:
    db = _db(tmp_path)
    _seed_rejected(db, _REJECT_EXPRESSION, ic_points=60, ic_value="alternating")
    outside = _now_after_cooldown(db)  # 冷却外
    # 第一次：面板不可得 → external 路径 fail loud（可复苏候选 + panel=None + 空快照库）
    with pytest.raises(Exception):
        run_factor_revival(
            db_path=db, snapshot_db_path=str(tmp_path / "empty-snap"), now=outside
        )
    # 第二次：真面板 + 会再拒的表达式 → re_rejected 落事件（冷却时钟锚点更新）
    summary = run_factor_revival(
        db_path=db, panel=_strong_panel(), seed=_STRONG_SEED, now=outside
    )
    assert summary["re_rejected"] == 1 and summary["admitted"] == 0
    events = library.list_events(db_path=db)
    assert any(e["event"] == "re_rejected" for e in events)
    # 第三次：自最新 re_rejected 事件 +5 天 → 冷却内不再提交
    again = run_factor_revival(
        db_path=db, panel=_strong_panel(), now=_now_after_cooldown(db, days=5)
    )
    assert again["cooldown_skipped"] == 1 and again["submitted"] == 0


def test_zero_candidates_no_panel_build(tmp_path, monkeypatch) -> None:
    db = _db(tmp_path)
    _seed_rejected(db, _PASS_EXPRESSION, ic_points=10, ic_value="alternating")  # 点数不足
    def _boom(**kwargs):
        raise AssertionError("面板不得构建")
    monkeypatch.setattr("finance_agent.factors.revival.run_factor_external_import", _boom)
    summary = run_factor_revival(db_path=db, now=_now_after_cooldown(db))
    assert summary["submitted"] == 0


def test_daily_cap_orders_by_abs_icir(tmp_path) -> None:
    db = _db(tmp_path)
    _seed_rejected(db, _PASS_EXPRESSION, ic_points=60, ic_value="alternating")  # |ICIR|≈2.0
    for k in range(8):  # 8 个可复苏（cap 5）——其余用不同构表达式
        _seed_rejected(db, f"add(div(close,ref(close,{k + 1})),1)", ic_points=60, ic_value="alternating")
    summary = run_factor_revival(
        db_path=db,
        panel=_strong_panel(),
        seed=_STRONG_SEED,
        daily_cap=5,
        now=_now_after_cooldown(db),
    )
    assert summary["eligible"] == 9 and summary["submitted"] == 5


def test_dry_run_previews_without_submit(tmp_path, monkeypatch) -> None:
    db = _db(tmp_path)
    _seed_rejected(db, _PASS_EXPRESSION, ic_points=60, ic_value="alternating")
    def _boom(**kwargs):
        raise AssertionError("dry_run 不得提交")
    monkeypatch.setattr("finance_agent.factors.revival.run_factor_external_import", _boom)
    summary = run_factor_revival(
        db_path=db, panel=_strong_panel(), dry_run=True, now=_now_after_cooldown(db)
    )
    assert summary["dry_run"] is True and summary["submitted"] == 0
    assert summary["eligible"] == 1 and summary["revived"]


def test_constants_mirror_constraints() -> None:
    assert REVIVAL_COOLDOWN_DAYS == 30 and REVIVAL_DAILY_CAP == 5
```

- [ ] **Step 2: 跑红** `uv run pytest tests/factors/test_revival.py -v` → ImportError（revival 不存在）
- [ ] **Step 3: 实现**（新建 `src/finance_agent/factors/revival.py`）

```python
"""拒绝池复苏重评通道（add-rejected-factor-revival；零 LLM）。

拒绝池（``rejected``）每天记录 IC（add-rejected-factor-pool），本模块把「记录」
变成「消费者」：滚动 |ICIR| 越过与闸②同值的复苏静默线（``MIN_ABS_ICIR``）且
冷却期外的 rejected 行，经外部导入同一路径（``run_factor_external_import``，
``ledger_source="revival"``）重新过五道闸。契约：

- ``rejected`` 保持终态：过闸 = 新 candidate 行入库；再拒 = 既有同构 upsert 追加
  ``re_rejected`` 事件（冷却自该事件重算，天然防乒乓）；
- 静默线只作提交预筛，五道闸是唯一准入权威；
- 冷却时钟 = 该行 ``created`` / ``re_rejected`` 事件时间戳的最大值
  （``REVIVAL_COOLDOWN_DAYS`` 自然日）；时间戳损坏 → 不冷却（如实放行）；
- 单次运行提交 ≤ ``REVIVAL_DAILY_CAP``（按 |滚动 ICIR| 降序、并列按 factor_id 升序）；
- ``dry_run=True`` 只出预览不提交；无可复苏候选零面板；面板不可得 fail loud
  （随 external 路径上抛，run_job 记 failed）。
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Any

from finance_agent.factors.constraints import (
    MIN_ABS_ICIR,
    REVIVAL_COOLDOWN_DAYS,
    REVIVAL_DAILY_CAP,
)
from finance_agent.factors.eval_engine import MarketPanel
from finance_agent.factors.external import DEFAULT_IMPORT_SEED, run_factor_external_import
from finance_agent.factors.library import list_events, list_factors, list_ic_series
from finance_agent.factors.tools import IC_WINDOW
from finance_agent.screening.ranker import recent_ic_stats

logger = logging.getLogger(__name__)


def _last_revival_event_at(events: list[dict[str, Any]]) -> str | None:
    """该行 ``created`` / ``re_rejected`` 事件时间戳的最大值（冷却锚点）；无 → ``None``。"""
    stamps = [
        str(event.get("created_at"))
        for event in events
        if str(event.get("event")) in {"created", "re_rejected"} and event.get("created_at")
    ]
    return max(stamps) if stamps else None


def _in_cooldown(last_at: str | None, *, now: datetime, cooldown_days: int) -> bool:
    """冷却判定：``last_at`` 距 ``now`` 不足 ``cooldown_days`` 自然日 → True。

    ``last_at`` 缺失（无事件，不可能出现在 rejected 行但防御保留）或解析失败
    → False（如实放行；事件历史可审计，不虚构冷却）。
    """
    if not last_at:
        return False
    try:
        last = datetime.fromisoformat(last_at)
    except ValueError:
        return False
    return (now - last).days < cooldown_days


def run_factor_revival(
    *,
    dry_run: bool = False,
    db_path: str | Path | None = None,
    snapshot_db_path: str | Path | None = None,
    panel: MarketPanel | None = None,
    seed: int = DEFAULT_IMPORT_SEED,
    cooldown_days: int = REVIVAL_COOLDOWN_DAYS,
    daily_cap: int = REVIVAL_DAILY_CAP,
    now: datetime | None = None,
) -> dict[str, Any]:
    """拒绝池复苏日批入口（ops job ``factor_revival_daily`` 与测试共用）。

    返回摘要：``checked``（rejected 行数）/ ``eligible``（过静默线且冷却外）/
    ``cooldown_skipped`` / ``submitted`` / ``admitted`` / ``re_rejected`` /
    ``errors`` / ``dry_run`` / ``revived``（提交或预览明细）/ ``rejections``。
    """
    current = now or datetime.now()
    rows = list_factors(status="rejected", db_path=db_path)
    stats = recent_ic_stats(rows, window=IC_WINDOW, db_path=db_path) if rows else {}
    events_by_factor: dict[int, list[dict[str, Any]]] = {}
    for event in list_events(db_path=db_path):
        events_by_factor.setdefault(int(event["factor_id"]), []).append(event)

    eligible: list[tuple[float, int, dict[str, Any]]] = []
    cooldown_skipped = 0
    for row in rows:
        factor_id = int(row["id"])
        series = list_ic_series(factor_id, limit=IC_WINDOW, db_path=db_path)
        if len(series) < IC_WINDOW:
            continue
        _mean, icir = stats.get(factor_id, (0.0, 0.0))
        if abs(icir) < MIN_ABS_ICIR:
            continue
        if _in_cooldown(
            _last_revival_event_at(events_by_factor.get(factor_id, [])),
            now=current,
            cooldown_days=cooldown_days,
        ):
            cooldown_skipped += 1
            continue
        eligible.append((abs(icir), factor_id, row))

    eligible.sort(key=lambda item: (-item[0], item[1]))
    selected = eligible[: max(0, daily_cap)]
    summary: dict[str, Any] = {
        "checked": len(rows),
        "eligible": len(eligible),
        "cooldown_skipped": cooldown_skipped,
        "submitted": 0,
        "admitted": 0,
        "re_rejected": 0,
        "errors": 0,
        "dry_run": bool(dry_run),
        "revived": [
            {
                "factor_id": factor_id,
                "expression": str(row.get("expression") or ""),
                "category": str(row.get("category") or ""),
                "rolling_abs_icir": round(abs_icir, 4),
            }
            for _abs_icir, factor_id, row in selected
        ],
        "rejections": [],
    }
    if not selected:
        return summary
    if dry_run:
        return summary

    payload = [
        {
            "expression": str(row.get("expression") or ""),
            "hypothesis": str(row.get("hypothesis") or "").strip() or "拒绝池复苏重提",
            "category": str(row.get("category") or "").strip() or "外部-其他",
        }
        for _abs_icir, _factor_id, row in selected
    ]
    import_summary = run_factor_external_import(
        payload,
        panel=panel,
        seed=seed,
        db_path=db_path,
        snapshot_db_path=snapshot_db_path,
        ledger_source="revival",
    )
    summary["submitted"] = len(payload)
    summary["admitted"] = int(import_summary.get("admitted") or 0)
    summary["re_rejected"] = int(import_summary.get("rejected") or 0)
    summary["errors"] = int(import_summary.get("errors") or 0)
    summary["rejections"] = list(import_summary.get("rejections") or [])
    logger.info(
        "factor revival: checked=%s eligible=%s submitted=%s admitted=%s re_rejected=%s",
        summary["checked"],
        summary["eligible"],
        summary["submitted"],
        summary["admitted"],
        summary["re_rejected"],
    )
    return summary
```

测试接口注记（实现者必读）：提交场景显式传 `panel=_strong_panel()`（面板不可得场景传 `snapshot_db_path=str(tmp_path/"empty-snap")`，external 路径对空快照库 fail loud）；`seed` kwarg 是 run_factor_revival 透传给 run_factor_external_import 的准入置换检验种子（实现时签名需含 `seed: int = DEFAULT_IMPORT_SEED`，从 external 模块 import 同名缺省）。

- [ ] **Step 4: 跑绿** `uv run pytest tests/factors/test_revival.py -v` → 全过（修测试到与实现一致，契约以 spec 为准）
- [ ] **Step 5: 提交** `git add -A && git commit --no-verify -m "feat(factors): 复苏重评核心模块 (add-rejected-factor-revival T2)"`

---

### Task 3: ops 注册与调度

**Files:**
- Modify: `src/finance_agent/outcome/ops/jobs.py`（JOB_IDS / JOBS / JOB_LABELS / SCHEDULES）
- Modify: `src/finance_agent/outcome/scheduler.py`（包装 + add_job）
- Test: `tests/outcome/test_ops_jobs.py`（钉数 29→30）

- [ ] **Step 1: 失败测试**——`test_ops_jobs.py` 钉数断言改 `len(JOB_IDS) == 30`（注释追加一行「30：+factor_revival_daily（拒绝池复苏，add-rejected-factor-revival）」）
- [ ] **Step 2: 跑红** `uv run pytest tests/outcome/test_ops_jobs.py -k aligned -v` → FAIL
- [ ] **Step 3: 实现**——四处：

① `jobs.py` JOB_IDS：`"foreign_broker_news_daily",` 之后插入：

```python
    # 拒绝池复苏（add-rejected-factor-revival）：17:10——外资快讯（17:05）之后、
    # 诊断（17:15）之前；零 LLM，仅存在可复苏候选时构建准入面板
    "factor_revival_daily",
```

② `JOBS` dict（`"foreign_broker_news_daily": run_foreign_broker_news_daily,` 之后）：

```python
    "factor_revival_daily": run_factor_revival,
```

顶部 import 区追加 `from finance_agent.factors.revival import run_factor_revival`（按既有字母序插入 factors 导入组）。

③ `JOB_LABELS`：`"factor_revival_daily": "拒绝池复苏",`

④ `SCHEDULES`（`"foreign_broker_news_daily"` 条目之后）：

```python
    # 拒绝池复苏（add-rejected-factor-revival）：17:10——零 LLM；rejected 行滚动
    # |ICIR| 越复苏静默线（=闸②同值）且冷却期外者，经外部导入路径重过五道闸。
    # 每天（含周末）跑无意义（IC 只在交易日增长）→ mon-fri。
    "factor_revival_daily": {
        "day_of_week": "mon-fri",
        "hour": 17,
        "minute": 10,
        "timezone": "Asia/Shanghai",
    },
```

⑤ `scheduler.py`：import 区追加 `from finance_agent.factors.revival import run_factor_revival`；`_foreign_broker_news_job` 包装之后新增：

```python
def _factor_revival_job() -> None:
    """add-rejected-factor-revival：拒绝池复苏日批（17:10，外资快讯之后、诊断之前）。

    零 LLM：rejected 行滚动 |ICIR| 越复苏静默线（=闸②同值）且冷却期外者，经
    外部导入同一路径重过五道闸；过闸=新行入库（rejected 保持终态），再拒=
    re_rejected 事件重置冷却。无候选零摘要零面板；面板不可得上抛（记 failed）。
    """
    _with_retry("factor_revival_daily", "factor revival daily", run_factor_revival)
```

`start_scheduler` 中 `_foreign_broker_news_job` 的 add_job 块之后插入：

```python
    # add-rejected-factor-revival：拒绝池复苏日批（17:10）——零 LLM，仅存在可复苏
    # 候选时构建准入面板；无候选零摘要（job 记 ok）。
    scheduler.add_job(
        _factor_revival_job,
        _cron_for("factor_revival_daily"),
        id="factor_revival_daily",
        replace_existing=True,
        **_JOB_MISFIRE_KW,
    )
```

- [ ] **Step 4: 跑绿** `uv run pytest tests/outcome/test_ops_jobs.py tests/outcome/test_scheduler.py tests/outcome/test_ops_catchup.py -v` → 全过（`test_add_job_ids_match_job_ids_exactly` 自动对齐；scheduler 测试如有 job 顺序/清单断言一并对齐）
- [ ] **Step 5: 提交** `git add -A && git commit --no-verify -m "feat(ops): factor_revival_daily 注册与调度 (add-rejected-factor-revival T3)"`

---

### Task 4: 全量门禁 + 真实数据 dry-run + umbrella 回写 + PR

- [ ] **Step 1: 门禁** `uv run ruff check && uv run ruff format --check`；`uv run mypy src/finance_agent/factors/revival.py`；`uv run pytest tests/factors tests/outcome -q` → 全绿
- [ ] **Step 2: 真实数据 dry-run**（只读，零提交）

```bash
uv run python -c "
from finance_agent.factors.revival import run_factor_revival
s = run_factor_revival(dry_run=True, db_path='data/factor_library.db')
print({k: s[k] for k in ('checked', 'eligible', 'cooldown_skipped', 'submitted')})
for r in s['revived'][:5]:
    print(r)"
```

Expected: `checked ≈ 191`（拒绝池行数）；eligible/cooldown_skipped 之和 ≤ checked；`submitted=0`（dry_run，now 取真实时钟）。池 2026-10-28 建、30 天冷却至 11-27——11-27 前多数行仍在冷却，eligible 预计为 0 或个位数属正常，如实记录；11-27 后逐日出现贴线复苏候选。
- [ ] **Step 3: umbrella 回写**——`openspec/changes/optimize-factor-supply-funnel/tasks.md` B4 行改 `- [x]` 并括注「已另立 add-rejected-factor-revival 实现（触发=滚动静默线而非面板加深，面板加深后通道自动受益）」
- [ ] **Step 4: 提交 + squash PR**（备份全史分支 → squash 单提交 → push → `gh pr create`，CI 绿后合并）
- [ ] **Step 5: 部署验证**——主检出 ff 同步后 watchfiles 自动 reload；`GET /api/v1/ops/jobs` 出现 `factor_revival_daily`（label 拒绝池复苏）；手动 dry-run 触发一次核对读数；勾 T1–T6
