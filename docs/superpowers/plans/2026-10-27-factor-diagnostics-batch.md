# 因子诊断读数日批 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把因子详情的诊断读数（风格暴露 + 分层单调性）从 HTTP 请求内现算移到日批落库，详情接口无条件零面板构建。

**Architecture:** 新库表 `factor_diagnostics`（每因子一行）+ 新模块 `factors/diagnostics.py`（日批入口 `run_factor_diagnostics`，增量判定：窗口 IC ≥ 30 且读数缺失或 `as_of` 落后）+ 注册作业 `factor_diagnostics_daily`（17:15，含周末）+ `api.py` 诊断块改纯读库。诊断口径函数（`check_monotonicity` / `compute_style_exposure`）原样复用，不改语义。

**Tech Stack:** Python 3.12 / FastAPI / sqlite3（library.py 短连接纪律）/ pytest。

## Global Constraints

- openspec change: `add-factor-diagnostics-batch`（spec delta 已 validate --strict 通过）
- API 详情路径 **无条件** MUST NOT 构建准入面板（spec 因子库只读查询 MODIFIED）
- 诊断读数是只读观测项，SHALL NOT 参与任何准入裁决或处置
- 表名只允许出现在 library.py（api.py 不得拼 SQL）
- 逐因子失败隔离：单因子失败保留旧读数、不中断批、不抛错上抛
- 面板仅在有 ≥ 1 个待算因子时构建，单次运行至多一次
- 库路径 env：`FACTOR_LIBRARY_DB_PATH` / `SNAPSHOT_DB_PATH` 调用期读（测试注入 tmp）
- 工作目录：worktree `.worktrees/factor-diagnostics-batch`（主检出有并发会话，禁碰）
- Commit 格式：`feat: [因子库] <内容> (#change)`

---

### Task 1: 存储层——factor_diagnostics 表 + 读写函数

**Files:**
- Modify: `src/finance_agent/factors/library.py`（FACTOR_LIBRARY_DDL 常量尾部、reset 表清单、新函数）
- Test: `tests/factors/test_library_diagnostics.py`（新建）

**Interfaces:**
- Consumes: `library._connect(db_path)`、`library._now_iso()`（既有）
- Produces: `set_factor_diagnostics(factor_id: int, as_of: str, *, style_exposure: dict | None, monotonicity: dict | None, db_path=None) -> None`；`get_factor_diagnostics(factor_id: int, db_path=None) -> dict | None`（键：`as_of` / `style_exposure` / `monotonicity` / `computed_at`）

- [ ] **Step 1: 写失败测试**

```python
"""factor_diagnostics 存储层单测（add-factor-diagnostics-batch Task 1）。

契约：roundtrip（JSON 载荷原样往返）/ upsert 覆盖（幂等重写）/ 无记录 → None /
reset 清空。零网络：tmp 库（FACTOR_LIBRARY_DB_PATH 调用期读 env）。
"""

from __future__ import annotations

import pytest

from finance_agent.factors.library import (
    get_factor_diagnostics,
    init_library,
    reset_factor_library,
    set_factor_diagnostics,
)


@pytest.fixture(autouse=True)
def _tmp_db(tmp_path, monkeypatch):
    monkeypatch.setenv("FACTOR_LIBRARY_DB_PATH", str(tmp_path / "factor_library.db"))
    init_library()
    return tmp_path


def test_roundtrip_and_upsert() -> None:
    set_factor_diagnostics(
        3, "2026-10-24",
        style_exposure={"dominant": "momentum_20", "n_days": 41},
        monotonicity={"passed": True, "spearman": 0.71},
    )
    stored = get_factor_diagnostics(3)
    assert stored is not None
    assert stored["as_of"] == "2026-10-24"
    assert stored["style_exposure"] == {"dominant": "momentum_20", "n_days": 41}
    assert stored["monotonicity"] == {"passed": True, "spearman": 0.71}
    assert stored["computed_at"]

    # 幂等覆盖：同因子重写同 as_of → 新值生效、行数仍为 1
    set_factor_diagnostics(3, "2026-10-24", style_exposure=None, monotonicity=None)
    stored = get_factor_diagnostics(3)
    assert stored["style_exposure"] is None
    assert stored["monotonicity"] is None


def test_missing_factor_returns_none() -> None:
    assert get_factor_diagnostics(999) is None


def test_reset_clears_diagnostics() -> None:
    set_factor_diagnostics(1, "2026-10-24", style_exposure={}, monotonicity={})
    removed = reset_factor_library()
    assert removed["factor_diagnostics"] == 1
    assert get_factor_diagnostics(1) is None
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/factors/test_library_diagnostics.py -v`
Expected: FAIL（`ImportError: cannot import name 'set_factor_diagnostics'`）

- [ ] **Step 3: 最小实现**

`library.py` 顶部确认已有 `import json`（无则补）。`FACTOR_LIBRARY_DDL` 字符串末尾（`idx_factor_ledger_p` 索引之后）追加：

```sql
CREATE TABLE IF NOT EXISTS factor_diagnostics (
  factor_id      INTEGER PRIMARY KEY,
  as_of          TEXT NOT NULL,
  style_exposure TEXT,
  monotonicity   TEXT,
  computed_at    TEXT NOT NULL
);
```

模块尾部（`reset_factor_library` 附近）新增：

```python
# ── 诊断读数存储（add-factor-diagnostics-batch）─────────────────────────────
# 每因子一行；日批（factors.diagnostics）生产、详情接口只读消费。


def set_factor_diagnostics(
    factor_id: int,
    as_of: str,
    *,
    style_exposure: dict[str, Any] | None,
    monotonicity: dict[str, Any] | None,
    db_path: str | Path | None = None,
) -> None:
    """Upsert 单因子诊断读数（日批幂等写：同 (factor) 覆盖）。

    ``style_exposure`` / ``monotonicity`` 为 JSON 友好载荷（style_exposure_payload
    输出 / check_monotonicity detail 扩展）；``None`` 存 NULL。
    """
    conn = _connect(db_path)
    try:
        conn.execute(
            "INSERT INTO factor_diagnostics (factor_id, as_of, style_exposure, monotonicity, computed_at)"
            " VALUES (?, ?, ?, ?, ?)"
            " ON CONFLICT(factor_id) DO UPDATE SET as_of=excluded.as_of,"
            " style_exposure=excluded.style_exposure, monotonicity=excluded.monotonicity,"
            " computed_at=excluded.computed_at",
            (
                int(factor_id),
                str(as_of),
                json.dumps(style_exposure, ensure_ascii=False) if style_exposure is not None else None,
                json.dumps(monotonicity, ensure_ascii=False) if monotonicity is not None else None,
                _now_iso(),
            ),
        )
        conn.commit()
    finally:
        conn.close()


def get_factor_diagnostics(factor_id: int, db_path: str | Path | None = None) -> dict[str, Any] | None:
    """读单因子诊断读数；无记录 → ``None``（详情接口据此置 null，不虚构）。"""
    conn = _connect(db_path)
    try:
        row = conn.execute(
            "SELECT as_of, style_exposure, monotonicity, computed_at"
            " FROM factor_diagnostics WHERE factor_id = ?",
            (int(factor_id),),
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        return None
    return {
        "as_of": str(row["as_of"]),
        "style_exposure": json.loads(row["style_exposure"]) if row["style_exposure"] else None,
        "monotonicity": json.loads(row["monotonicity"]) if row["monotonicity"] else None,
        "computed_at": str(row["computed_at"]),
    }
```

`reset_factor_library` 的表清单元组追加 `"factor_diagnostics"`（子表语义，放最前）：
```python
for table in ("factor_diagnostics", "factor_admission_ledger", "factor_events", "factor_ic", "factors"):
```

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest tests/factors/test_library_diagnostics.py -v`
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add src/finance_agent/factors/library.py tests/factors/test_library_diagnostics.py
git commit -m "feat: [因子库] factor_diagnostics 读数存储表 + 读写函数 (add-factor-diagnostics-batch)"
```

---

### Task 2: 日批模块 factors/diagnostics.py

**Files:**
- Create: `src/finance_agent/factors/diagnostics.py`
- Test: `tests/factors/test_diagnostics.py`（新建）

**Interfaces:**
- Consumes: Task 1 `set_factor_diagnostics` / `get_factor_diagnostics`；`library.list_factors` / `list_ic_series`；`tools.IC_WINDOW`；`style_exposure.MIN_STYLE_SAMPLES` / `compute_style_exposure` / `style_exposure_payload`；`validate.check_monotonicity`；`eval_engine.evaluate` / `forward_returns` / `rank_ic_series` / `MarketPanel`；`dsl.parse`；`monthly._panel_from_snapshot`；`data.snapshot.latest_snapshot_date`
- Produces: `run_factor_diagnostics(*, panel: MarketPanel | None = None, db_path=None, snapshot_db_path=None) -> dict`（键：`candidates` / `computed: list[int]` / `skipped_fresh` / `skipped_samples` / `failed: dict[int, str]` / `as_of: str | None`）

- [ ] **Step 1: 写失败测试**

```python
"""诊断日批单测（add-factor-diagnostics-batch Task 2；Task 4 的注册表测试并入本文件）。

契约（spec「诊断读数日批」）：存量补算 / 已新鲜零面板构建 / 失败隔离 / 幂等 /
面板注入不依赖快照库。面板用合成 MarketPanel（120 日 × 8 票小矩阵，秒级；
fields 键集 = eval_engine.PANEL_FIELDS 逐字：open/high/low/close/volume/amount）。
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from finance_agent.factors.diagnostics import run_factor_diagnostics
from finance_agent.factors.eval_engine import MarketPanel
from finance_agent.factors.library import (
    add_factor,
    get_factor_diagnostics,
    init_library,
    record_ic,
    set_status,
)


DATES = [f"2026-0{m}-{d:02d}" for m in range(6, 10) for d in range(1, 21)]  # 120 日


@pytest.fixture(autouse=True)
def _tmp_db(tmp_path, monkeypatch):
    monkeypatch.setenv("FACTOR_LIBRARY_DB_PATH", str(tmp_path / "factor_library.db"))
    monkeypatch.setenv("SNAPSHOT_DB_PATH", str(tmp_path / "snapshot.db"))
    init_library()
    return tmp_path


def _synthetic_panel() -> MarketPanel:
    """合成面板：120 日 × 8 票，close 随机游走、volume 截面差异足够 rank 稳定。"""
    rng = np.random.default_rng(7)
    idx = pd.Index(DATES, name="trade_date")
    cols = [f"00000{i}.SZ" for i in range(8)]
    base = pd.DataFrame(rng.normal(10.0, 0.2, (len(DATES), 8)).cumsum(axis=0) + 10.0, index=idx, columns=cols)
    fields = {
        "close": base,
        "open": base.shift(1).fillna(base.iloc[0]),
        "high": base + 0.1,
        "low": base - 0.1,
        "volume": pd.DataFrame(rng.uniform(1e6, 5e6, (len(DATES), 8)), index=idx, columns=cols),
        "amount": pd.DataFrame(rng.uniform(1e7, 5e7, (len(DATES), 8)), index=idx, columns=cols),
    }
    return MarketPanel(fields=fields)


@pytest.fixture()
def _panel(monkeypatch):
    """注入合成面板：diagnostics._panel_from_snapshot 打桩（断言可捕获面板构建次数）。"""
    calls = {"n": 0}
    panel = _synthetic_panel()

    def _fake(snapshot_db_path=None):
        calls["n"] += 1
        return panel

    monkeypatch.setattr("finance_agent.factors.diagnostics._panel_from_snapshot", _fake)
    return panel, calls


def _seed_factor(expression: str, *, ic_days: int) -> int:
    fid = add_factor(expression=expression, hypothesis="测试", category="测试", status="candidate", source="llm")
    set_status(fid, "active", detail={"reason": "ADMITTED"})
    for i in range(ic_days):
        record_ic(fid, DATES[i], 0.05 * ((-1) ** (i % 3)))
    return fid


def test_backfills_eligible_and_skips_low_samples(_panel) -> None:
    panel, calls = _panel
    hi_a = _seed_factor("ts_rank(close,20)", ic_days=40)
    hi_b = _seed_factor("delta(close,20)", ic_days=35)
    low = _seed_factor("rank(volume)", ic_days=4)
    summary = run_factor_diagnostics()
    assert summary["computed"] == sorted([hi_a, hi_b])
    assert summary["skipped_samples"] == 1
    assert calls["n"] == 1  # 面板单次构建、逐因子复用
    stored = get_factor_diagnostics(hi_a)
    assert stored is not None
    assert stored["as_of"] == str(panel.fields["close"].index[-1])
    assert "monotonicity" in stored and "style_exposure" in stored
    assert get_factor_diagnostics(low) is None


def test_fresh_readings_skip_without_panel_build(_panel, monkeypatch) -> None:
    panel, calls = _panel
    fid = _seed_factor("ts_rank(close,20)", ic_days=40)
    run_factor_diagnostics()
    first = calls["n"]
    assert first >= 1
    # 二跑：读数已新鲜 → 零面板构建、零重算
    summary = run_factor_diagnostics()
    assert summary["computed"] == []
    assert summary["skipped_fresh"] == 1
    assert calls["n"] == first


def test_failure_isolation_keeps_old_and_does_not_raise(_panel, monkeypatch) -> None:
    panel, calls = _panel
    bad = _seed_factor("ts_rank(close,20)", ic_days=40)
    good = _seed_factor("delta(close,20)", ic_days=40)
    import finance_agent.factors.diagnostics as diag

    real_evaluate = diag.evaluate
    def _boom(expression, panel_):
        if "ts_rank" in str(expression):
            raise ValueError("合成求值失败")
        return real_evaluate(expression, panel_)
    monkeypatch.setattr(diag, "evaluate", _boom)
    summary = run_factor_diagnostics()
    assert summary["computed"] == [good]
    assert bad in summary["failed"]
    assert get_factor_diagnostics(good) is not None
    assert get_factor_diagnostics(bad) is None


def test_no_candidates_no_panel_build(_panel) -> None:
    _, calls = _panel
    _seed_factor("ts_rank(close,20)", ic_days=4)  # 仅 4 点 → 非候选
    summary = run_factor_diagnostics()
    assert summary["candidates"] == 0
    assert calls["n"] == 0
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/factors/test_diagnostics.py -v`
Expected: FAIL（`ModuleNotFoundError: finance_agent.factors.diagnostics`）

- [ ] **Step 3: 最小实现**

```python
"""因子诊断读数日批（add-factor-diagnostics-batch）。

背景：详情接口曾在请求内现算 500 日准入面板（风格暴露 + 分层单调性），IC 历史
回填把护栏（窗口 IC < 30 短路）绕过后，active 因子详情全部挂死（生产实测单请求
71+ 分钟）。本模块把读数生产移到日批（作业 factor_diagnostics_daily）：

- 待算集合：窗口 IC 点数 ≥ ``MIN_STYLE_SAMPLES`` 且（无落库读数 或 ``as_of`` 落后
  最新快照日）——周末快照无新交易日 → 全部已新鲜 → 零面板构建近零成本；
- 面板按需：仅在有 ≥ 1 个待算因子时经 ``monthly._panel_from_snapshot`` 构建，
  单次运行至多一次并逐因子复用（与收割「无候选不建面板」语义一致）；
- 逐因子失败隔离：单因子异常（面板不可得/表达式不可求值/数据异常）只跳过该因子
  （保留旧读数），MUST NOT 中断其余因子或上抛（作业记 ok）；
- 落库读数为只读观测项，SHALL NOT 参与任何准入裁决或处置（处置仍只经衰减监控）。

诊断口径与原详情路径逐字同源：``check_monotonicity`` / ``compute_style_exposure``
不改语义；读数反映日批运行时点的面板（``as_of`` = 面板最新交易日）。
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from finance_agent.data.snapshot import latest_snapshot_date
from finance_agent.factors.dsl import parse
from finance_agent.factors.eval_engine import (
    MarketPanel,
    evaluate,
    forward_returns,
    rank_ic_series,
)
from finance_agent.factors.library import (
    get_factor_diagnostics,
    list_factors,
    list_ic_series,
    set_factor_diagnostics,
)
from finance_agent.factors.style_exposure import MIN_STYLE_SAMPLES
from finance_agent.factors.tools import IC_WINDOW
from finance_agent.factors.validate import check_monotonicity
from finance_agent.factors.style_exposure import compute_style_exposure, style_exposure_payload
from finance_agent.factors.monthly import _panel_from_snapshot  # noqa: PLC2701 -- 与 builtin/harvest 同先例

logger = logging.getLogger(__name__)


def run_factor_diagnostics(
    *,
    panel: MarketPanel | None = None,
    db_path: str | Path | None = None,
    snapshot_db_path: str | Path | None = None,
) -> dict[str, Any]:
    """诊断读数日批入口（作业零参数调用）。

    ``panel`` 仅供测试注入；生产路径按需经 ``_panel_from_snapshot`` 构建（准入
    深度口径不变）。返回摘要 ``{"candidates", "computed", "skipped_fresh",
    "skipped_samples", "failed", "as_of"}``。
    """
    rows = list_factors(db_path=db_path)
    candidates: list[dict[str, Any]] = []
    skipped_samples = 0
    for row in rows:
        series = list_ic_series(int(row["id"]), limit=IC_WINDOW, db_path=db_path)
        if len(series) < MIN_STYLE_SAMPLES:
            skipped_samples += 1
            continue
        candidates.append(row)

    injected = panel is not None
    if injected:
        as_of = str(panel.fields["close"].index[-1])  # type: ignore[union-attr]
    else:
        as_of = latest_snapshot_date(snapshot_db_path)
        if as_of is None:
            if candidates:
                raise ValueError("快照库为空：存在待算因子但无法构建诊断面板（先跑 market_snapshot_daily）")
            return {
                "candidates": 0,
                "computed": [],
                "skipped_fresh": 0,
                "skipped_samples": skipped_samples,
                "failed": {},
                "as_of": None,
            }

    # 增量过滤：读数缺失或 as_of 落后 → 待算；其余已新鲜（周末快照无新日 → 全跳过）
    stale: list[dict[str, Any]] = []
    skipped_fresh = 0
    for row in candidates:
        stored = get_factor_diagnostics(int(row["id"]), db_path=db_path)
        if stored is not None and str(stored["as_of"]) >= str(as_of):
            skipped_fresh += 1
            continue
        stale.append(row)

    summary: dict[str, Any] = {
        "candidates": len(candidates),
        "computed": [],
        "skipped_fresh": skipped_fresh,
        "skipped_samples": skipped_samples,
        "failed": {},
        "as_of": as_of,
    }
    if not stale:
        return summary  # 零待算 → 零面板构建（MUST NOT 为空集建面板）

    if panel is None:
        panel = _panel_from_snapshot(snapshot_db_path)
    assert panel is not None  # noqa: S101 -- 上一行保证非 None（类型收窄）
    fwd = forward_returns(panel.fields["close"])

    for row in stale:
        factor_id = int(row["id"])
        try:
            values = evaluate(parse(str(row["expression"])), panel)
            factor_ic = rank_ic_series(values, fwd)
            monotonicity = check_monotonicity(values, fwd, factor_ic)
            exposure = style_exposure_payload(compute_style_exposure(factor_ic, panel, fwd=fwd))
            set_factor_diagnostics(
                factor_id,
                str(as_of),
                style_exposure=exposure,
                monotonicity={"passed": monotonicity.passed, **monotonicity.detail},
                db_path=db_path,
            )
            summary["computed"].append(factor_id)
        except (OSError, ValueError) as exc:
            # 失败隔离：保留旧读数（无则维持无读数），不中断批、不上抛
            summary["failed"][factor_id] = f"{type(exc).__name__}: {exc}"
            logger.warning("因子 %s 诊断读数计算失败（保留旧读数）: %s", factor_id, exc)
    return summary
```

实现注意：
- `list_factors` / `list_ic_series` 均为 kwarg-only 且带 `db_path`（已核对 library.py:377/409），签名即上。
- `DSLParseError` / `FactorComplexityError` 均为 `ValueError` 子类（dsl.py:128/140，已核对）——`except (OSError, ValueError)` 捕获面足够，无需扩大。
- `as_of` 比较：ISO 日期字符串字典序 = 时间序（`stored["as_of"] >= as_of` 判已新鲜，防御上溢）。
- `check_monotonicity` 的 `detail` 含 `spearman` / `q10_q1` / `decile_returns`（可能 NaN）——进 JSON 前须把非有限值置 None（模块内联 `_finite_or_none`，对 detail 递归清洗；否则 SQLite 文本里出现 `NaN` 字面量违反 spec「MUST NOT 以虚构读数冒充」的精神且下游 `json.loads` 产 NaN）。

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest tests/factors/test_diagnostics.py -v`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add src/finance_agent/factors/diagnostics.py tests/factors/test_diagnostics.py
git commit -m "feat: [因子库] 诊断读数日批——增量待算/按需面板/失败隔离 (add-factor-diagnostics-batch)"
```

---

### Task 3: API 纯读库改造 + 零面板构建回归测试

**Files:**
- Modify: `src/finance_agent/api.py:4407-4447`（`_factor_detail_diagnostics` 整体重写；`_factor_detail_payload` 不变——其 `detail.update(...)` 自动并入 `diagnostics_as_of`）
- Modify: `src/finance_agent/api.py:58-64`（library import 块加 `get_factor_diagnostics`）
- Test: `tests/test_api_factors.py`（重写 `test_detail_monotonicity_observed_with_panel`；新增零面板构建回归测试）

**Interfaces:**
- Consumes: Task 1 `get_factor_diagnostics`
- Produces: 详情响应新增键 `diagnostics_as_of: str | null`

- [ ] **Step 1: 写失败测试（先红）**

`tests/test_api_factors.py` 新增（文件尾部）：

```python
def test_detail_never_builds_panel_regardless_of_ic_points(_factors_db, monkeypatch) -> None:
    """回归测试（add-factor-diagnostics-batch）：详情无条件零面板构建。

    生产事故：IC 回填使窗口 IC ≥ 30 后，详情请求内现算 500 日面板（单请求 71+
    分钟，详情页全部挂死）。本测试把面板构建打桩为 raise——请求路径任何分支
    触及面板构建即 500，MUST 保持 200。
    """
    llm_id, _ = _seed_library()
    for day in range(40):  # 窗口 IC 40 点 ≥ 30：旧代码在此进入慢路径
        record_ic(llm_id, f"2026-09-{day + 1:02d}", 0.08)

    import finance_agent.factors.monthly as monthly
    import finance_agent.factors.diagnostics as diag

    def _boom(*_args, **_kwargs):
        raise AssertionError("API 详情路径 MUST NOT 构建准入面板")

    monkeypatch.setattr(monthly, "_panel_from_snapshot", _boom)
    monkeypatch.setattr(diag, "_panel_from_snapshot", _boom)

    resp = TestClient(app).get(f"{_FACTORS}/{llm_id}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["style_exposure"] is None
    assert body["monotonicity"] is None
    assert body["diagnostics_as_of"] is None


def test_detail_returns_stored_diagnostics_with_as_of(_factors_db) -> None:
    """日批已落库 → 详情返回读数 + diagnostics_as_of（数据截止交易日）。"""
    llm_id, _ = _seed_library()
    set_factor_diagnostics(
        llm_id, "2026-10-24",
        style_exposure={"correlations": {"momentum_20": 0.81}, "dominant": "momentum_20",
                        "dominant_correlation": 0.81, "flagged": True, "n_days": 41,
                        "threshold": 0.7, "basket": ["momentum_20"]},
        monotonicity={"passed": True, "spearman": 0.72, "q10_q1": 0.03,
                      "decile_returns": [0.0] * 10, "valid_days": 100, "threshold": 0.6},
    )
    resp = TestClient(app).get(f"{_FACTORS}/{llm_id}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["style_exposure"]["dominant"] == "momentum_20"
    assert body["monotonicity"]["passed"] is True
    assert body["diagnostics_as_of"] == "2026-10-24"
```

（`set_factor_diagnostics` 加进该文件顶部 library import 块；请求用文件既有 `TestClient(app)` 直连形态，与 `test_detail_returns_expression_ic_series_and_events` 同款。）

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/test_api_factors.py::test_detail_never_builds_panel_regardless_of_ic_points tests/test_api_factors.py::test_detail_returns_stored_diagnostics_with_as_of -v`
Expected: 两个均 FAIL（旧代码：前者 AssertionError 冒泡 → 500；后者无 `diagnostics_as_of` 键）

- [ ] **Step 3: 改造 api.py**

`_factor_detail_diagnostics`（api.py:4407-4447）整体替换为：

```python
def _factor_detail_diagnostics(row: dict[str, Any]) -> dict[str, Any]:
    """诊断读数纯读库（add-factor-diagnostics-batch）。

    读数生产在 ``factors.diagnostics.run_factor_diagnostics``（日批
    factor_diagnostics_daily，17:15）。接口**无条件**零面板构建——任何分支、任何
    IC 点数下详情响应不因快照库大小而阻塞（生产事故：IC 回填绕过旧「<30 短路」
    护栏后，请求内现算 500 日面板把详情拖成数十分钟）。无落库读数（日批未及 /
    样本不足 / 当日不可算）→ 双 None（前端如实「数据不足」，MUST NOT 用 0/虚构值
    冒充）；``diagnostics_as_of`` 为读数数据截止交易日（可观测性，前端不消费）。
    """
    stored = get_factor_diagnostics(int(row["id"]))
    if stored is None:
        return {"style_exposure": None, "monotonicity": None, "diagnostics_as_of": None}
    return {
        "style_exposure": stored["style_exposure"],
        "monotonicity": stored["monotonicity"],
        "diagnostics_as_of": stored["as_of"],
    }
```

api.py:58-64 library import 块追加 `get_factor_diagnostics,`。

- [ ] **Step 4: 重写既有面板注入测试 + 全量跑通**

`test_detail_monotonicity_observed_with_panel`（api.py:432 旧路径 monkeypatch 注入面板已不存在）重写为：经 `run_factor_diagnostics(panel=合成面板)` 落库后 GET 详情断言读数字段齐备（`passed/spearman/q10_q1/decile_returns/valid_days/threshold` + `style_exposure` 逐风格）——合成面板构造复用 `tests/factors/test_diagnostics.py` 的 `_synthetic_panel`（import 之，勿复制）。

`test_detail_style_exposure_skips_panel_when_ic_points_below_min`：断言不变（无读数 → null），实现后自然绿。

Run: `uv run pytest tests/test_api_factors.py -v`
Expected: 全部 passed

- [ ] **Step 5: Commit**

```bash
git add src/finance_agent/api.py tests/test_api_factors.py
git commit -m "feat: [因子库] 详情诊断纯读库+零面板构建回归测试 (add-factor-diagnostics-batch)"
```

---

### Task 4: 作业注册 factor_diagnostics_daily

**Files:**
- Modify: `src/finance_agent/outcome/ops/jobs.py`（JOB_IDS / JOB_LABELS / JOB_FUNCS / SCHEDULES 四处 + import）
- Test: `tests/factors/test_diagnostics.py`（注册表断言并入 Task 2 的测试文件尾部；`tests/test_ops_api.py:296/446` 的既有顺序断言是动态对照 `list(JOB_IDS)`，插位不破坏，无需改）

**Interfaces:**
- Consumes: Task 2 `run_factor_diagnostics`
- Produces: `JOB_IDS` 含 `factor_diagnostics_daily`（插在 `foreign_broker_news_daily` 之后、`cohort_batch` 之前）；`SCHEDULES["factor_diagnostics_daily"] = {"day_of_week": "*", "hour": 17, "minute": 15, "timezone": "Asia/Shanghai"}`

- [ ] **Step 1: 写失败测试**

追加到 `tests/factors/test_diagnostics.py` 尾部：

```python
def test_diagnostics_job_registered() -> None:
    from finance_agent.outcome.ops.jobs import JOB_FUNCS, JOB_IDS, JOB_LABELS, SCHEDULES

    assert "factor_diagnostics_daily" in JOB_IDS
    assert JOB_LABELS["factor_diagnostics_daily"] == "因子诊断读数"
    assert callable(JOB_FUNCS["factor_diagnostics_daily"])
    sched = SCHEDULES["factor_diagnostics_daily"]
    assert sched == {"day_of_week": "*", "hour": 17, "minute": 15, "timezone": "Asia/Shanghai"}
    # 触发序：17:15 在外资快讯（17:05）之后、cohort 之前（展示顺序断言不变）
    assert JOB_IDS.index("factor_diagnostics_daily") > JOB_IDS.index("foreign_broker_news_daily")
    assert JOB_IDS.index("factor_diagnostics_daily") < JOB_IDS.index("cohort_batch")
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/factors/test_diagnostics.py::test_diagnostics_job_registered -v`
Expected: FAIL（`KeyError: 'factor_diagnostics_daily'`）

- [ ] **Step 3: 注册四处**

```python
# JOB_IDS：foreign_broker_news_daily 之后
    # 因子诊断读数日批（add-factor-diagnostics-batch）：17:15——日批窗口（15:30-17:10）
    # 之后、cohort 之前；读数生产移出 HTTP 详情路径（生产实测请求内现算 500 日面板
    # 把详情拖成数十分钟）。每天（含周末）跑：周末收割新准入的因子当日获得读数；
    # 快照无新交易日时全部已新鲜 → 零面板构建近零成本。
    "factor_diagnostics_daily",

# JOB_LABELS
    "factor_diagnostics_daily": "因子诊断读数",

# JOB_FUNCS（import 区加 from finance_agent.factors.diagnostics import run_factor_diagnostics）
    "factor_diagnostics_daily": run_factor_diagnostics,

# SCHEDULES
    "factor_diagnostics_daily": {
        "day_of_week": "*",
        "hour": 17,
        "minute": 15,
        "timezone": "Asia/Shanghai",
    },
```

- [ ] **Step 4: 跑测试确认通过 + 核对 scheduler 接线**

Run: `uv run pytest tests/factors/test_diagnostics.py -v`
Expected: 全部 passed（Task 2 的 4 个 + 本任务的注册表断言）

`grep -n "JOB_FUNCS\|run_job(" src/finance_agent/outcome/scheduler.py`：确认 scheduler 经 `JOB_IDS`+`SCHEDULES` 泛型调度（无 per-job 硬编码清单需追加）。若有硬编码清单，同步追加。

- [ ] **Step 5: Commit**

```bash
git add src/finance_agent/outcome/ops/jobs.py tests/factors/test_diagnostics.py
git commit -m "feat: [因子库] 注册 factor_diagnostics_daily 日批作业 17:15 (add-factor-diagnostics-batch)"
```

---

### Task 5: 收口——全量门禁 + 打开 change

- [ ] **Step 1: 全量后端测试**

Run: `uv run pytest`
Expected: 0 failed（既有全量基线内偶发外网用例阻塞则按惯例排除该用例并记录）

- [ ] **Step 2: Lint + 类型**

Run: `uv run ruff check && uv run ruff format --check && uv run mypy`
Expected: 0 error（mypy 存量基线不新增）

- [ ] **Step 3: 勾选 tasks.md + 归档材料准备**

`openspec/changes/add-factor-diagnostics-batch/tasks.md` 全勾；交互类变更否（纯后端）→ 无 E2E 门禁与人工验证报告义务，但部署后须实页抽查（Step 5.3 部署任务）。

- [ ] **Step 4: PR**

```bash
git push -u origin feat/add-factor-diagnostics-batch
gh pr create --title "feat(factors): 因子诊断读数日批——详情接口零面板构建 (add-factor-diagnostics-batch)" --body "$(cat <<'EOF'
## Why

详情接口在请求内现算 500 日准入面板做风格暴露/单调性诊断；IC 历史回填使「<30 点短路」护栏失效后，active 因子详情全部挂死（生产实测单请求 71+ 分钟，探针 CPU 180%+）。

## What Changes

- 新表 factor_diagnostics（每因子一行，含 as_of）+ get/set 读写（library.py）
- 新日批 run_factor_diagnostics（增量待算 / 按需面板 / 逐因子失败隔离 / 幂等）
- 注册作业 factor_diagnostics_daily（17:15，含周末；零待算日零面板构建）
- api.py 诊断块改纯读库 + 新增 diagnostics_as_of；无条件零面板构建（回归测试钉死）
- 纯后端变更：无前端/prompt 改动；部署后 ops 手动补跑一次为存量 12 因子回填

openspec change: add-factor-diagnostics-batch（validate --strict 通过）
EOF
)"
```

- [ ] **Step 5: 部署 + 存量回填 + 实页验证（合并后）**

1. 红线检查：`GET /api/sessions` 无 running 会话、`GET /api/v1/ops/jobs` 无 running 作业、当前时间不在 15:30–17:10 / 17:55–20:45 窗口。
2. `docker compose up -d --build backend`。
3. ops API 手动触发 `factor_diagnostics_daily`（存量 12 因子回填，预计 10~20 分钟）。
4. 实页验证：`/factor-research` → 因子库展开 active 因子详情 → 毫秒级返回且带读数与「主导风格」；`GET /api/factors/3` 实测延迟 < 2s。
