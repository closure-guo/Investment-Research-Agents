# 零买入决策修复 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让系统能给出可执行、可验证的买入建议——先装观测、再用消融实验证明责任归属、再改决策规则、最后输出两份口径提案。

**Architecture:** 分四段：①观测层加决策分布读数与连续零买入告警；②在隔离环境（独立库、独立进程、变体 prompt）跑 A/B，对照本日已存基线，确认是「催化剂门槛」「评分带宽」还是「风控否决」在卡死买入；③按实验结论改写 `trader` / `risk_judge` 提示词，同批票回归验证；④账本持仓语义与评分口径只出提案、不改代码。

**Tech Stack:** Python 3.12 / FastAPI / LangGraph / SQLite（`data/sessions.db`）/ pytest / Langfuse prompt（production label，本地 `prompts/*.md` 兜底）。

---

## Global Constraints

- **一次只动一个变量**：消融实验一次只改一段提示词，改两处就不可归因。
- **提示词权威源**：`src/finance_agent/prompts/*.md`；改完必须 `uv run python scripts/deploy_prompts.py` 发布，否则 eval 门禁拒绝运行。**变体提示词不得放这个目录**（会被当生产提示词发布）。
- **不重启后端**：交易日 15:30–17:10（日批）与 17:55–20:45（cohort/日报）窗口内禁止重建/重启；重启前先查 `GET /api/sessions` 无 `running` 会话、`GET /api/v1/ops/jobs` 无 running 作业。
- **不碰生产库**：消融实验的 `SESSIONS_DB_PATH` 必须指向临时库，跑完 `grep` 校验生产库当日 prediction 行数不变。
- **成功标准不是「买入变多」**：是「出现可执行买入建议（含价位/仓位/认错位）且能被连续指标度量」。乱买同样判失败。
- **停止点**：生产 prompt 切换、口径落地（改 `metrics.md` §1 与结算代码）、任何需要人类终裁的判断——做完准备动作后停下报告。
- **币值上限**：整轮实验 ≤ 300 万 tokens（12 只票 × 2 臂 × ~22 万 tokens/次 ≈ 530 万，**超预算**，故本计划用 8 只票 × 2 臂 ≈ 350 万，仍超则降到 6 只）。

## 背景与证据（执行者必读，决定改哪里）

| 事实 | 数值 | 来源 |
|---|---|---|
| 全部分析会话中「买入」次数 | **0 / 581** | `sessions.final_trade_decision` |
| 研究经理给出「看多」的次数 | 12 / 201（6%） | `agent_process.research_conclusion` 正则 |
| 12 条看多之后的最终动作 | **12 条全部 watch，0 条 buy** | 同上 |
| 12 条看多中提及「催化剂/已披露」 | **12 / 12** | 同上 |
| 12 条看多中提及「±2%/带宽/分辨率」 | 6 / 12 | 同上 |
| 107 条 watch 中提及带宽/σ | 63 / 107（59%） | 同上 |
| 已盯市 60 条看空判断的方向性收益 | **-2.68%**，超额 **-3.56%**，胜率 **12%** | `predictions` ⨝ `daily_marks` |

**机制**：买入需证明「20 个交易日内有可兑现催化剂」，而 11 月处于财报真空期（三季报已披露、下一个窗口在次年 1 月）→ 结构性禁止买入；风险辩论可用「止损距离小于日波动」否决任何方案；±2% 判分带宽被当成风险预算 → 模型推出「零敞口才自洽」。

---

## 文件结构

| 文件 | 职责 |
|---|---|
| `src/finance_agent/outcome/ops/decision_mix.py`（新建） | 决策分布读数：按日统计 buy/sell/watch，识别连续零买入 |
| `tests/outcome/ops/test_decision_mix.py`（新建） | 上述模块单测 |
| `src/finance_agent/outcome/daily_brief.py`（改） | 复用读数、日报写入告警行 |
| `tests/outcome/test_daily_brief_zero_buy_warning.py`（新建） | 告警文案与阈值单测 |
| `evals/variants/zero_buy/trader_no_catalyst.md`（新建） | 变体 A：只删「20 日内必须有催化剂」 |
| `evals/variants/zero_buy/trader_no_contract.md`（新建） | 变体 B：删掉整段「周期契约」 |
| `evals/variants/zero_buy/risk_judge_no_contract.md`（新建） | 变体 B 配套的裁判提示词 |
| `scripts/run_zero_buy_ablation.py`（新建） | 隔离环境跑单次分析 + 注入变体提示词 |
| `tests/scripts/test_run_zero_buy_ablation.py`（新建） | 注入器单测（patch 生效、库路径隔离） |
| `docs/evals/zero-buy-ablation-2026-11-06.md`（新建） | 实验报告（结论 + 原始数据表） |
| `src/finance_agent/prompts/trader.md`（改，Task 5） | 按实验结论删改「周期契约」 |
| `src/finance_agent/prompts/risk_judge.md`（改，Task 6） | 风控处置权：否决 → 降仓 |
| `docs/evals/zero-buy-fix-2026-11-06.md`（新建） | 全程进度与结论台账 |

---

### Task 0: 决策分布读数模块

**Files:**
- Create: `src/finance_agent/outcome/ops/decision_mix.py`
- Test: `tests/outcome/ops/test_decision_mix.py`

**Interfaces:**
- Produces: `action_from_snapshot(raw: object) -> str | None`
- Produces: `compute_decision_mix(days: int = 10, db_path: str | Path | None = None) -> dict`，返回
  `{"days": [{"date": "YYYY-MM-DD", "counts": {"buy": int, "sell": int, "watch": int, "other": int}}], "zero_buy_streak": int, "total": {"buy": int, "sell": int, "watch": int, "other": int}}`
- 后续 Task 1 依赖上述两个符号。

- [ ] **Step 1: 写失败测试**

```python
# tests/outcome/ops/test_decision_mix.py
import json
import sqlite3

from finance_agent.outcome.ops.decision_mix import compute_decision_mix


def _seed(db_path, rows):
    conn = sqlite3.connect(db_path)
    conn.execute(
        "CREATE TABLE predictions (prediction_id TEXT PRIMARY KEY, "
        "created_at TEXT, rationale_snapshot TEXT)"
    )
    conn.executemany(
        "INSERT INTO predictions VALUES (?, ?, ?)",
        [(f"p{i}", ts, json.dumps({"action": act}, ensure_ascii=False))
         for i, (ts, act) in enumerate(rows)],
    )
    conn.commit()
    conn.close()


def test_counts_by_day_and_flags_zero_buy_streak(tmp_path):
    db = tmp_path / "s.db"
    _seed(db, [
        ("2026-11-04T18:00:00", "sell"), ("2026-11-04T18:01:00", "watch"),
        ("2026-11-05T18:00:00", "sell"), ("2026-11-05T18:01:00", "sell"),
    ])
    mix = compute_decision_mix(days=10, db_path=db)
    assert mix["total"] == {"buy": 0, "sell": 3, "watch": 1, "other": 0}
    assert mix["zero_buy_streak"] == 2
    assert [d["date"] for d in mix["days"]] == ["2026-11-04", "2026-11-05"]


def test_streak_resets_on_buy(tmp_path):
    db = tmp_path / "s.db"
    _seed(db, [
        ("2026-11-03T18:00:00", "sell"),
        ("2026-11-04T18:00:00", "buy"),
        ("2026-11-05T18:00:00", "watch"),
    ])
    assert compute_decision_mix(days=10, db_path=db)["zero_buy_streak"] == 1
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/outcome/ops/test_decision_mix.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'finance_agent.outcome.ops.decision_mix'`

- [ ] **Step 3: 最小实现**

```python
# src/finance_agent/outcome/ops/decision_mix.py
"""决策分布读数：按日统计 buy/sell/watch，识别连续零买入（看门狗）。"""

from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from typing import Any

_ACTIONS = ("buy", "sell", "watch")


def action_from_snapshot(raw: object) -> str | None:
    if not raw:
        return None
    try:
        payload = json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    action = payload.get("action")
    return str(action) if action else None


def _db_path(db_path: str | Path | None) -> Path:
    return Path(db_path) if db_path else Path(os.getenv("SESSIONS_DB_PATH", "data/sessions.db"))


def compute_decision_mix(days: int = 10, db_path: str | Path | None = None) -> dict[str, Any]:
    conn = sqlite3.connect(_db_path(db_path))
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT created_at, rationale_snapshot FROM predictions ORDER BY created_at ASC"
    ).fetchall()
    conn.close()

    per_day: dict[str, dict[str, int]] = {}
    for row in rows:
        ts = str(row["created_at"] or "")
        if len(ts) < 10:
            continue
        day = ts[:10]
        counts = per_day.setdefault(day, {"buy": 0, "sell": 0, "watch": 0, "other": 0})
        action = action_from_snapshot(row["rationale_snapshot"])
        counts[action if action in _ACTIONS else "other"] += 1

    ordered = sorted(per_day)[-days:] if days > 0 else sorted(per_day)
    streak = 0
    for day in reversed(ordered):
        if per_day[day]["buy"]:
            break
        streak += 1

    total = {k: sum(per_day[d][k] for d in ordered) for k in ("buy", "sell", "watch", "other")}
    return {
        "days": [{"date": d, "counts": per_day[d]} for d in ordered],
        "zero_buy_streak": streak,
        "total": total,
    }
```

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest tests/outcome/ops/test_decision_mix.py -v`
Expected: `2 passed`

- [ ] **Step 5: 提交**

```bash
git add src/finance_agent/outcome/ops/decision_mix.py tests/outcome/ops/test_decision_mix.py
git commit -m "feat(ops): 决策分布读数——连续零买入识别"
```

---

### Task 1: 日报接入零买入告警

**Files:**
- Modify: `src/finance_agent/outcome/daily_brief.py`（`_action_from_snapshot` 改为复用新模块；warnings 段追加）
- Test: `tests/outcome/test_daily_brief_zero_buy_warning.py`

**Interfaces:**
- Consumes: `compute_decision_mix`（Task 0）
- Produces: `zero_buy_warning(mix: dict, threshold: int = 3) -> str | None`，连续零买入 ≥ threshold 天返回中文告警句，否则 `None`。

- [ ] **Step 1: 写失败测试**

```python
# tests/outcome/test_daily_brief_zero_buy_warning.py
from finance_agent.outcome.daily_brief import zero_buy_warning


def test_warns_after_three_zero_buy_days():
    mix = {"zero_buy_streak": 3, "total": {"buy": 0, "sell": 20, "watch": 10, "other": 0}}
    msg = zero_buy_warning(mix)
    assert msg is not None
    assert "连续 3 个交易日零买入" in msg


def test_silent_below_threshold():
    assert zero_buy_warning({"zero_buy_streak": 2, "total": {}}) is None
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/outcome/test_daily_brief_zero_buy_warning.py -v`
Expected: FAIL — `ImportError: cannot import name 'zero_buy_warning'`

- [ ] **Step 3: 实现**

```python
# daily_brief.py 顶部导入区追加
from finance_agent.outcome.ops.decision_mix import action_from_snapshot, compute_decision_mix

# 删除本文件的 _action_from_snapshot 定义，改为：
_action_from_snapshot = action_from_snapshot


def zero_buy_warning(mix: dict, threshold: int = 3) -> str | None:
    """连续零买入告警：决策分布退化为「只会说不」时，日报必须显式喊出来。"""
    streak = int(mix.get("zero_buy_streak") or 0)
    if streak < threshold:
        return None
    total = mix.get("total") or {}
    return (
        f"决策分布异常：连续 {streak} 个交易日零买入"
        f"（sell={total.get('sell', 0)}、watch={total.get('watch', 0)}）"
        "——疑似决策规则锁死，排查方向见 docs/superpowers/plans/2026-11-06-zero-buy-decision-fix.md"
    )
```

在 `run_daily_brief` 组装 warnings 的位置追加：

```python
_mix = compute_decision_mix(days=10)
_warn = zero_buy_warning(_mix)
if _warn:
    warnings.append(_warn)
```

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest tests/outcome/test_daily_brief_zero_buy_warning.py tests/outcome/test_daily_brief.py -v`
Expected: 新测试 2 passed，既有 brief 测试不回归。

- [ ] **Step 5: 提交**

```bash
git add src/finance_agent/outcome/daily_brief.py tests/outcome/test_daily_brief_zero_buy_warning.py
git commit -m "feat(brief): 连续零买入告警进日报 warnings"
```

---

### Task 2: 变体提示词（不得放进生产提示词目录）

**Files:**
- Create: `evals/variants/zero_buy/trader_no_catalyst.md`
- Create: `evals/variants/zero_buy/trader_no_contract.md`
- Create: `evals/variants/zero_buy/risk_judge_no_contract.md`

**做法**（无代码，纯文本，逐字复制 `src/finance_agent/prompts/trader.md` 后做单点替换）：

| 变体 | 改动 | 保留 |
|---|---|---|
| A `trader_no_catalyst.md` | 删掉「周期契约」中 `buy 决策 MUST 说明催化剂…` 与 `兑现期超过 20 个交易日的长线论据…` 两条，替换为：「buy 决策 MUST 说明为何在当前价位买入、认错位在哪、仓位多少；不要求证明窗口内催化剂。」 | 20 日窗口、置信度分档、价位纪律、sell 分型 |
| B `trader_no_contract.md` | 整段「## 周期契约」删除（含 20 日窗口与 ±2% 表述） | 其余全部 |
| B 配套 `risk_judge_no_contract.md` | 同样删除「周期契约」段，另加一条：「风险辩论对买入方案的否决，只能落到『降低仓位档位』或『放宽/上移止损』；MUST NOT 以『止损距离小于日波动』直接否定方向。」 | 其余全部 |

**验收：**

```bash
Select-String -Path evals/variants/zero_buy/trader_no_catalyst.md -Pattern "±2%"   # 应仍有匹配（保留窗口说明）
Select-String -Path evals/variants/zero_buy/trader_no_contract.md -Pattern "周期契约"  # 应无匹配
```

- [ ] **Step 1: 生成三个变体文件**
- [ ] **Step 2: 跑上面两条校验命令，确认替换生效**
- [ ] **Step 3: 提交**

```bash
git add evals/variants/zero_buy
git commit -m "chore(eval): 零买入消融变体提示词 A/B"
```

---

### Task 3: 隔离环境运行器

**Files:**
- Create: `scripts/run_zero_buy_ablation.py`
- Test: `tests/scripts/test_run_zero_buy_ablation.py`

**Interfaces:**
- Produces: `patch_prompts(variant_dir: Path, names: tuple[str, ...]) -> contextlib.ExitStack`——上下文管理器，在块内把 `finance_agent.nodes.trader.load_prompt_with_meta` 等模块级引用替换为读变体文件的函数，退出后还原。
- Produces: CLI：`python scripts/run_zero_buy_ablation.py --variant evals/variants/zero_buy/trader_no_catalyst.md --symbols 002916,300033 --db tmp/ablation.db --out reports/ablation/zero-buy-A.json`

**关键约束：**
- 进程启动前 `os.environ["SESSIONS_DB_PATH"] = str(db)`，且断言 `db.resolve() != Path("data/sessions.db").resolve()`。
- 只 patch `finance_agent.nodes.trader`、`finance_agent.nodes.risk`、`finance_agent.nodes.fund_manager`、`finance_agent.nodes.debate`、`finance_agent.nodes.research_manager` 五个模块的 `load_prompt_with_meta`（它们都是模块级导入，patch 模块属性才生效）。
- 每条分析落库前打日志 `[ablation] symbol=... variant=... db=...`。

- [ ] **Step 1: 写失败测试**

```python
# tests/scripts/test_run_zero_buy_ablation.py
from pathlib import Path

from scripts.run_zero_buy_ablation import patch_prompts


def test_patch_overrides_trader_prompt_then_restores(tmp_path):
    variant = tmp_path / "trader.md"
    variant.write_text("VARIANT-TRADER", encoding="utf-8")
    import finance_agent.nodes.trader as trader_mod

    original = trader_mod.load_prompt_with_meta
    with patch_prompts(tmp_path, {"trader": "trader.md"}):
        assert trader_mod.load_prompt_with_meta("trader").template == "VARIANT-TRADER"
    assert trader_mod.load_prompt_with_meta is original


def test_refuses_production_db(tmp_path):
    import pytest
    from scripts.run_zero_buy_ablation import assert_isolated_db

    with pytest.raises(RuntimeError):
        assert_isolated_db(Path("data/sessions.db"))
    assert_isolated_db(tmp_path / "x.db") == (tmp_path / "x.db").resolve()
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/scripts/test_run_zero_buy_ablation.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'scripts.run_zero_buy_ablation'`

- [ ] **Step 3: 实现**

```python
# scripts/run_zero_buy_ablation.py
"""零买入消融：隔离库 + 变体提示词跑单次分析，落 final_trade_decision。

⚠️ 关键顺序：必须先设 SESSIONS_DB_PATH，再 import 任何 finance_agent 模块——
session_store 在 import 期就把路径固化到模块常量。本文件因此把业务 import
全部放进函数内部。
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import os
import sys
import time
from pathlib import Path

_NODE_MODULES = (
    "finance_agent.nodes.trader",
    "finance_agent.nodes.risk",
    "finance_agent.nodes.fund_manager",
    "finance_agent.nodes.debate",
    "finance_agent.nodes.research_manager",
)
_PROD_DB = Path("data/sessions.db")


def assert_isolated_db(path: Path) -> Path:
    resolved = Path(path).expanduser().resolve()
    if resolved == _PROD_DB.resolve():
        raise RuntimeError("消融实验禁止写生产库 data/sessions.db")
    return resolved


def _reader(variant_dir: Path, mapping: dict[str, str]):
    from finance_agent.prompts.loader import PromptInfo, load_prompt_with_meta as _real

    def _load(name: str) -> PromptInfo:
        if name in mapping:
            text = (variant_dir / mapping[name]).read_text(encoding="utf-8")
            return PromptInfo(template=text, prompt_name=f"variant:{name}", prompt_version="variant")
        return _real(name)

    return _load


@contextlib.contextmanager
def _swap(obj, attr: str, value):
    original = getattr(obj, attr)
    setattr(obj, attr, value)
    try:
        yield
    finally:
        setattr(obj, attr, original)


@contextlib.contextmanager
def patch_prompts(variant_dir: Path, mapping: dict[str, str]):
    import importlib

    stack = contextlib.ExitStack()
    reader = _reader(Path(variant_dir), mapping)
    for mod_name in _NODE_MODULES:
        mod = importlib.import_module(mod_name)
        if hasattr(mod, "load_prompt_with_meta"):
            stack.enter_context(_swap(mod, "load_prompt_with_meta", reader))
    try:
        yield
    finally:
        stack.close()


def _read_session(session_id: str) -> dict | None:
    import sqlite3

    conn = sqlite3.connect(os.environ["SESSIONS_DB_PATH"])
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT session_id, stock_code, status, final_trade_decision "
        "FROM sessions WHERE session_id = ?",
        (session_id,),
    ).fetchone()
    conn.close()
    return dict(row) if row else None


async def run_symbol(symbol: str, *, timeout_s: int = 2400) -> dict:
    from finance_agent.api import start_analysis_session_for_operator

    receipt = await start_analysis_session_for_operator(symbol)
    session_id = receipt.get("session_id") or receipt.get("sessionId")
    if not session_id:
        raise RuntimeError(f"{symbol} 派单未返回 session_id: {receipt!r}")
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        row = _read_session(session_id)
        if row and row["status"] in ("completed", "failed", "interrupted"):
            return {
                "symbol": symbol,
                "session_id": session_id,
                "status": row["status"],
                "final_trade_decision": row["final_trade_decision"],
            }
        await asyncio.sleep(15)
    raise TimeoutError(f"{symbol} 分析超时（{timeout_s}s）")


async def _main(args: argparse.Namespace) -> int:
    variant_path = Path(args.variant)
    mapping = {variant_path.stem.split("_")[0].replace("trader", "trader"): variant_path.name}
    if variant_path.stem.startswith("risk_judge"):
        mapping = {"risk_judge": variant_path.name}
    results = []
    with patch_prompts(variant_path.parent, mapping):
        for symbol in [s.strip() for s in args.symbols.split(",") if s.strip()]:
            print(f"[ablation] symbol={symbol} variant={variant_path.name} db={os.environ['SESSIONS_DB_PATH']}")
            results.append(await run_symbol(symbol))
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    buys = sum(1 for r in results if _action(r) == "buy")
    print(f"[ablation] done: {len(results)} runs, buy={buys} -> {out}")
    return 0


def _action(result: dict) -> str | None:
    raw = result.get("final_trade_decision")
    if not raw:
        return None
    try:
        return (json.loads(raw) if isinstance(raw, str) else raw).get("action")
    except (TypeError, ValueError):
        return None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--variant", required=True)
    ap.add_argument("--symbols", required=True, help="逗号分隔股票代码")
    ap.add_argument("--db", default="tmp/ablation.db")
    ap.add_argument("--out", required=True)
    args = ap.parse_args(argv)
    # 必须在 import finance_agent 之前落 env（session_store 导入期固化路径）
    os.environ["SESSIONS_DB_PATH"] = str(assert_isolated_db(Path(args.db)))
    return asyncio.run(_main(args))


if __name__ == "__main__":
    sys.exit(main())
```

> **执行者注意**：`start_analysis_session_for_operator`（`src/finance_agent/api.py:2573`）是 operator 派单入口，async、返回回执不阻塞；管线在本进程内跑，故 `patch_prompts` 的上下文必须覆盖整个等待循环（上面代码已保证）。它派单后会话 `status` 从 `clarifying` 起步，轮询到 `completed/failed/interrupted` 即收口。**先跑 1 只票跑通，再扩到 8 只**；跑通判据 = 临时库 `sessions` 表新增 1 行且 `final_trade_decision` 非空。

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest tests/scripts/test_run_zero_buy_ablation.py -v`
Expected: `2 passed`

- [ ] **Step 5: 跑冒烟（1 只票）**

Run: `uv run python scripts/run_zero_buy_ablation.py --variant evals/variants/zero_buy/trader_no_contract.md --symbols 002916 --db tmp/ablation-smoke.db --out reports/ablation/smoke.json`
Expected: 临时库新增会话 1 行；`data/sessions.db` 的 `predictions` 行数不变（跑前跑后各查一次）。

- [ ] **Step 6: 提交**

```bash
git add scripts/run_zero_buy_ablation.py tests/scripts/test_run_zero_buy_ablation.py
git commit -m "feat(eval): 零买入消融隔离运行器"
```

---

### Task 4: 跑实验并出报告（非 TDD，验收明确）

**Files:**
- Create: `docs/evals/zero-buy-ablation-2026-11-06.md`

**样本**：从本日 20 条决策里取 8 只——4 只研究结论看多（600026、300033、002916、601066）＋ 4 只看空/中性（002852、600780、600496、000019），保证两臂对比的不是同质性样本。

**臂**：
- 对照 = 本日已存的真实决策（现状提示词，无需重跑）
- 实验 A = `trader_no_catalyst.md`
- 实验 B = `trader_no_contract.md` + `risk_judge_no_contract.md`

**成本控制**：8 只 × 2 臂 ≈ 350 万 tokens；超 300 万上限则砍到 6 只（3+3）。

**判据（写进报告，不许含糊）**：

| 结果 | 结论 |
|---|---|
| A 臂出现 ≥2 条 buy | 催化剂门槛是主因 → Task 5 按 A 改 |
| A 臂 0 buy、B 臂 ≥2 条 buy | 带宽/整套契约也是锁 → Task 5、6 一起改 |
| 两臂都 0 buy | 规则不是唯一原因 → 停下来报告，检查池子与模型偏见，不改提示词 |

- [ ] **Step 1: 跑 A 臂 8 只，产出 `reports/ablation/zero-buy-A.json`**
- [ ] **Step 2: 跑 B 臂 8 只，产出 `reports/ablation/zero-buy-B.json`**
- [ ] **Step 3: 写报告**：本日基线 vs A vs B 的动作分布表 + 每条 buy 的完整决策文本 + 结论（按上表）
- [ ] **Step 4: 提交**

```bash
git add docs/evals/zero-buy-ablation-2026-11-06.md
git commit -m "docs(eval): 零买入消融实验结果"
```

---

### Task 5: 按实验结论改 `trader` 提示词

**前置**：Task 4 结论为「A 臂出现 buy」或「B 臂出现 buy」。若结论是「两臂都 0」，**跳过本任务并报告**。

**Files:**
- Modify: `src/finance_agent/prompts/trader.md`
- Test: `tests/prompts/test_trader_prompt_contract.py`

- [ ] **Step 1: 写失败测试**（提示词文本契约测试，防止下次被静默改回去）

```python
# tests/prompts/test_trader_prompt_contract.py
from finance_agent.prompts.loader import load_prompt


def test_trader_prompt_does_not_gate_buy_on_catalyst():
    text = load_prompt("trader")
    assert "MUST 说明催化剂" not in text
    assert "MUST NOT 作为买入主因" not in text
    assert "为何在当前价位买入" in text


def test_trader_prompt_keeps_horizon_not_band():
    text = load_prompt("trader")
    assert "20 个交易日" in text
    assert "±2%" not in text
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/prompts/test_trader_prompt_contract.py -v`
Expected: FAIL（现文本仍含 `MUST 说明催化剂`）

- [ ] **Step 3: 改 `trader.md`**：删除「周期契约」两条买入限制，换为「buy MUST 说明为何在当前价位买入、认错位在哪、仓位多少；不要求证明窗口内催化剂」；保留 20 日窗口表述，删掉 ±2% 中性带字样（口径交给评估侧）。

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest tests/prompts/test_trader_prompt_contract.py -v`
Expected: `2 passed`

- [ ] **Step 5: 发布提示词并验证**

Run: `uv run python scripts/deploy_prompts.py`
Expected: 输出包含 `trader` 新版本号；随后 `uv run python scripts/prompt_history.py --name trader` 能看到新版本。

- [ ] **Step 6: 提交**

```bash
git add src/finance_agent/prompts/trader.md tests/prompts/test_trader_prompt_contract.py
git commit -m "feat(prompt): 买入不再以窗口内催化剂为硬门槛"
```

---

### Task 6: 改 `risk_judge` —— 否决权改降仓权

**前置**：同 Task 5。

**Files:**
- Modify: `src/finance_agent/prompts/risk_judge.md`
- Modify: `src/finance_agent/prompts/risk_debater.md`（辩论纪律段追加一条）
- Test: `tests/prompts/test_risk_prompt_contract.py`

- [ ] **Step 1: 写失败测试**

```python
def test_risk_judge_forbids_band_as_direction_veto():
    text = load_prompt("risk_judge")
    assert "MUST NOT 以『止损距离小于日波动』直接否定方向" in text
    assert "降低仓位档位" in text
```

- [ ] **Step 2-4**：跑红 → 改文本 → 跑绿
- [ ] **Step 5: 发布**：`uv run python scripts/deploy_prompts.py`
- [ ] **Step 6: 提交**

---

### Task 7: 回归验证（同批票，改后重跑）

**Files:**
- Create: `docs/validation/2026-11-06-zero-buy-prompt-change.md`

- [ ] **Step 1: 用 Task 3 的运行器在同一批 8 只票上跑一次（生产提示词，隔离库）**
- [ ] **Step 2: 记录动作分布对比（改前 vs 改后）、每条 buy 的价位/仓位/认错位是否齐全**
- [ ] **Step 3: 判定**：≥2 条 buy 且参数齐全 → 通过；否则回到 Task 5 分析原因，不得连改两处
- [ ] **Step 4: 落报告并提交**

```bash
git add docs/validation/2026-11-06-zero-buy-prompt-change.md
git commit -m "docs(validation): 零买入提示词改造回归验证"
```

---

### Task 8: 账本持仓语义提案（只出文档，不改代码）

**Files:**
- Create: `openspec/changes/fix-track-record-holdings-semantics/proposal.md`

**核心内容**（必须写清，不许留 TBD）：
- 现状：`judgment.direction_for_action` 把 `sell`（含 `sell_type=exit`）一律映射为 `short`；而受众持仓默认「未申报」，exit 对未持仓者等于「别买」，收益应为 0。
- 影响：56 条方向性观点全部被记成空头，净值曲线实际是「假设做空」的盈亏，与散户可执行动作无关。
- 方案：`predictions` 增加 `holdings_state`（held/not_held/unreported）；held → 按 exit 记方向性；not_held → 记 `avoidance` 语义（收益 0，机会成本单列）；unreported → 保持现状但单独打标，不混入主账本。
- 切点：存量 56 条不回填，标注口径来源；新口径从切点起算。

- [ ] **Step 1: 写 proposal.md**
- [ ] **Step 2: `openspec validate fix-track-record-holdings-semantics --strict`**
- [ ] **Step 3: 提交（不 archive、不同步主规范——停下等确认）**

---

### Task 9: 评分口径提案（只出文档，不改代码）

**Files:**
- Modify: `docs/evals/metrics.md`（**只加「提案」小节，不改 §1 既有定义**）
- Create: `openspec/changes/update-outcome-band-volatility-scaled/proposal.md`

**核心内容**：
- 现状问题：±2% 固定带对 20 日 1σ=8–19% 的标的分辨率不足；二元「对/错」把连续结果扔掉，检测 3% 的真本事需要约 275 条，连续值只要约 88 条。
- 提案：主指标改连续超额（平均超额、超额胜率、超额 IR）；±2% 降为辅助分类；带宽改 `max(1.5%, 0.5 × 20日波动)`。
- 切点：存量分类不动，新指标从切点起算，双轨展示 2 周后评估。
- **明确标注**：这是 proposal，需 owner 拍板；未拍板前不得改 §1 与结算代码。

- [ ] **Step 1: 写 proposal + metrics.md 提案小节**
- [ ] **Step 2: 提交，停下报告**

---

## 验收矩阵

| 阶段 | 验收证据 | 失败处理 |
|---|---|---|
| Task 0–1 | `uv run pytest tests/outcome/ops/test_decision_mix.py tests/outcome/test_daily_brief_zero_buy_warning.py -v` 全绿；次日日报出现告警行 | 修实现，不放宽断言 |
| Task 2–3 | 变体文件校验命令输出符合预期；烟雾测试后生产库行数不变 | 停下查隔离失败原因 |
| Task 4 | 报告有「对照 / A / B」三列动作分布 + 结论落在判据表某一格 | 数据不全不得下结论 |
| Task 5–6 | 提示词契约测试绿；`deploy_prompts.py` 有新版本号 | 不发布就不算完成 |
| Task 7 | ≥2 条 buy 且参数齐全，报告落 `docs/validation/` | 不通过则回 Task 5，一次只改一处 |
| Task 8–9 | `openspec validate --strict` 通过；文档明确标注「待 owner 拍板」 | — |

## 停止点（做到这里必须停下报告）

1. 生产提示词切换之后、观察第一个交易日之前 —— 报告改了什么、预期什么、怎么回滚（`prompt_history.py` + Langfuse label 回退）。
2. `metrics.md` §1 与结算代码的开始动作 —— 只交提案，不动代码。
3. Task 4 出现「两臂都 0 buy」—— 说明规则不是唯一原因，停下来重新归因。
4. 任何需要判断「这条买入建议质量如何」的环节 —— 交人工终裁。

## 风险

| 风险 | 对策 |
|---|---|
| 变体提示词被 `deploy_prompts.py` 当生产发布 | 变体放 `evals/variants/`，脚本只扫 `src/finance_agent/prompts/` |
| 消融跑批污染生产库 | `assert_isolated_db` 硬校验 + 跑前后行数对比 |
| 实验期间撞上 15:30 日批 | 实验进程独立库、独立进程；若跨到 15:30 先停实验 |
| 改完仍 0 buy 却强行上线 | 判据表写死「两臂都 0 → 不改提示词」 |
| token 超支 | 8 只封顶；超 300 万砍到 6 只 |
