# update-decision-settlement-contract Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把判定契约补齐到 outcome 评估口径：默认窗口 252→20、结算入场价由行情派生（`settle_entry_price`）、neutral 走回避判定（`avoidance_status`，不进胜率）、long/short 补齐 Score 上报、回测 replay 与生产判定同源。

**Architecture:** 生产侧 `outcome/track_record/`（model 迁移两列 → ingest 显式 horizon → judgment 抽共享纯函数含派生入场价与回避分支 → job 两路径改造 + Score 上报 → 统计与 overview 加回避字段与口径过滤）；评估侧 `evals/backtest/replay.py` 切共享函数（适配层保留下游 `settle_date`/`settle_price` 契约），旧 `outcome/settle.py` 标 deprecated（golden gates 语义独立保留）。

**Tech Stack:** Python（sqlite3 / pandas / pydantic 既有模式），pytest。

## Global Constraints

- **工作区**：`.worktrees/outcome-eval`（分支 `feat/outcome-profitability-eval`）。
- **口径（唯一定义 docs/evals/metrics.md §1.8）**：默认窗口 `OUTCOME_DEFAULT_HORIZON_DAYS=20`；派生结算入场价 = 决策归属日收盘（收盘后决策 → 次一交易日；归属日非交易日 → 其后首个交易日；收盘切分 `15:00`）；±2% 中性带沿用 `judgment.DEFAULT_NEUTRAL_BAND`；neutral 回避判定不进胜率。
- **不追溯**：存量已结算行不重算；存量 open 观点按原 horizon 判定；避免动 `resolved_at` 之外的存量字段。
- **测试隔离**：测试一律 `tmp_path` 注入 DB（incident 031 教训，绝不落真实 `data/sessions.db`）。
- **冻结纪律**：`_FROZEN_FIELDS`（direction/entry_price/target_price/rationale_snapshot/created_at/source_type/symbol）不得加入；新列 `avoidance_status` / `settle_entry_price` 属判定产出，加入 `_MUTABLE_FIELDS`。
- **命名**：新列名 `avoidance_status`（TEXT，取值 avoidance_win/loss/neutral）、`settle_entry_price`（REAL）。
- **命令**：`uv run pytest tests/outcome -v`、`uv run pytest tests/evals/backtest -v`；`uv run ruff check`；`uv run mypy`（触碰文件零新增）。
- **commit 风格**：中文 + conventional 前缀（`feat(outcome):` / `fix(outcome):` / `refactor(evals):`）。
- **Score 上报 API 形态**（既有先例 `outcome/job.py:51`）：`langfuse.create_score(name=..., value=..., trace_id=..., data_type=..., comment=...)`；`data_type` 取 `"BOOLEAN"` / `"NUMERIC"`。

---

### Task 1: 配置常量 + schema 迁移（两列 + DDL 默认 20）

**Files:**
- Modify: `src/finance_agent/outcome/track_record/judgment.py`（顶部常量区）
- Modify: `src/finance_agent/outcome/track_record/model.py`（DDL / 迁移 / `_MUTABLE_FIELDS`）
- Test: `tests/outcome/test_track_record_judgment.py`、`tests/outcome/test_track_record_model.py`

**Interfaces:**
- Produces: `judgment.DEFAULT_HORIZON_DAYS: int`（env `OUTCOME_DEFAULT_HORIZON_DAYS`，默认 20）、`judgment.CLOSE_TIME_CUTOFF: str = "15:00"`；`model._SETTLEMENT_CONTRACT_COLUMNS` 迁移；predictions 新列 `avoidance_status` / `settle_entry_price`

- [ ] **Step 1: 失败测试**

`tests/outcome/test_track_record_judgment.py` 追加：

```python
def test_default_horizon_days_and_cutoff_pinned():
    from finance_agent.outcome.track_record import judgment

    assert judgment.DEFAULT_HORIZON_DAYS == 20
    assert judgment.CLOSE_TIME_CUTOFF == "15:00"
```

`tests/outcome/test_track_record_model.py` 追加：

```python
def test_settlement_contract_columns_migrated(tmp_path):
    """老库（无新列）经 init 后补齐 avoidance_status / settle_entry_price，幂等。"""
    import sqlite3

    from finance_agent.outcome.track_record.model import init_track_record_tables

    db = tmp_path / "old.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE predictions (prediction_id TEXT PRIMARY KEY, status TEXT)")
    conn.commit()
    conn.close()

    init_track_record_tables(db)
    init_track_record_tables(db)  # 幂等
    cols = {r[1] for r in sqlite3.connect(db).execute("PRAGMA table_info(predictions)")}
    assert {"avoidance_status", "settle_entry_price"} <= cols
    assert {"version_seq", "snapshot_hash"} <= cols  # 既有迁移不回归


def test_mutable_fields_include_settlement_outputs():
    from finance_agent.outcome.track_record.model import _FROZEN_FIELDS, _MUTABLE_FIELDS

    assert "avoidance_status" in _MUTABLE_FIELDS
    assert "settle_entry_price" in _MUTABLE_FIELDS
    assert "avoidance_status" not in _FROZEN_FIELDS
    assert "settle_entry_price" not in _FROZEN_FIELDS
```

- [ ] **Step 2: 跑测确认红**

Run: `uv run pytest tests/outcome/test_track_record_judgment.py tests/outcome/test_track_record_model.py -v`
Expected: FAIL（`AttributeError: DEFAULT_HORIZON_DAYS` / 列缺失）

- [ ] **Step 3: 实现**

`judgment.py` 顶部（`MAX_HORIZON_DAYS` 旁）：

```python
# outcome 评估默认判定窗口（delta update-decision-settlement-contract；口径 metrics.md §1.8）
DEFAULT_HORIZON_DAYS = int(os.getenv("OUTCOME_DEFAULT_HORIZON_DAYS", "20"))
CLOSE_TIME_CUTOFF = "15:00"  # 归属日收盘切分：created_at 时间部分 < 此值 → 归属日收盘入场
```

（补 `import os`。）

`model.py`：
1. `PREDICTIONS_DDL` 的 `horizon_days INTEGER NOT NULL DEFAULT 252` → `DEFAULT 20`，并新增两列：
```sql
  avoidance_status   TEXT,
  settle_entry_price REAL,
```
2. `_STAGE_C_COLUMNS` 之后新增：
```python
# ── update-decision-settlement-contract：回避判定 + 派生结算入场价（幂等迁移）──
_SETTLEMENT_CONTRACT_COLUMNS = (
    ("avoidance_status", "ALTER TABLE predictions ADD COLUMN avoidance_status TEXT"),
    ("settle_entry_price", "ALTER TABLE predictions ADD COLUMN settle_entry_price REAL"),
)


def _migrate_settlement_contract_columns(conn: sqlite3.Connection) -> None:
    cols = {r[1] for r in conn.execute("PRAGMA table_info(predictions)").fetchall()}
    for col, ddl in _SETTLEMENT_CONTRACT_COLUMNS:
        if col not in cols:
            conn.execute(ddl)
```
3. `init_track_record_tables` 中 `_migrate_stage_c_columns(conn)` 之后加 `_migrate_settlement_contract_columns(conn)`。
4. `_MUTABLE_FIELDS` 追加 `"avoidance_status"` 与 `"settle_entry_price"`。

- [ ] **Step 4: 跑测确认绿 + Commit**

Run: `uv run pytest tests/outcome/test_track_record_judgment.py tests/outcome/test_track_record_model.py tests/outcome/test_track_record_stage_c.py -v`
Expected: 全 passed（stage_c 覆盖迁移不回归）

```bash
git add src/finance_agent/outcome/track_record/judgment.py src/finance_agent/outcome/track_record/model.py tests/outcome/test_track_record_judgment.py tests/outcome/test_track_record_model.py
git commit -m "feat(outcome): 结算契约 schema——avoidance_status/settle_entry_price 迁移 + 默认窗口配置常量（delta update-decision-settlement-contract）"
```

---

### Task 2: ingest 改造（horizon 显式写入 + 申报价入快照 + 参考价不可得不阻断）

**Files:**
- Modify: `src/finance_agent/outcome/track_record/ingest.py`
- Test: `tests/outcome/test_track_record_ingest.py`、`tests/outcome/test_track_record_ingest_shared.py`、`tests/outcome/test_decision_logging.py`（按实际断言点更新）

**Interfaces:**
- Consumes: Task 1 的 `judgment.DEFAULT_HORIZON_DAYS`
- Produces: predictions 行含 `horizon_days=20`（显式）与 `rationale_snapshot["declared_prices"]`；参考价不可得时 status 保持 `open`（不再 unresolvable）

- [ ] **Step 1: 失败测试**（在 `tests/outcome/test_track_record_ingest.py` 追加/改写；先读既有测试确认夹具形态）

```python
def test_ingest_writes_horizon_and_declared_prices(tmp_path, monkeypatch):
    """horizon_days 显式 20；申报价位冻结入快照。"""
    import sqlite3

    from finance_agent.outcome.track_record.ingest import persist_prediction_from_accumulated
    from finance_agent.outcome.track_record.model import init_track_record_tables

    db = tmp_path / "s.db"
    monkeypatch.setenv("SESSIONS_DB_PATH", str(db))
    init_track_record_tables(db)
    accumulated = {
        "final_trade_decision": {
            "action": "buy",
            "confidence": 0.7,
            "entry_price": 101.5,
            "stop_loss": 95.0,
            "target_price": 120.0,
        },
        "stock_quote": {"price": 100.0},
        "langfuse_trace_id": "t1",
    }
    persist_prediction_from_accumulated(accumulated, "sess-1", "600519", "贵州茅台")
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM predictions").fetchone()
    assert row["horizon_days"] == 20
    assert row["status"] == "open"
    import json

    snap = json.loads(row["rationale_snapshot"])
    assert snap["declared_prices"] == {"entry_price": 101.5, "stop_loss": 95.0, "target_price": 120.0}


def test_ingest_reference_price_missing_stays_open(tmp_path, monkeypatch):
    """参考价不可得 → 存档 + 状态 open（判定不依赖参考价）。"""
    import sqlite3

    from finance_agent.outcome.track_record.ingest import persist_prediction_from_accumulated
    from finance_agent.outcome.track_record.model import init_track_record_tables

    db = tmp_path / "s.db"
    monkeypatch.setenv("SESSIONS_DB_PATH", str(db))
    init_track_record_tables(db)
    accumulated = {"final_trade_decision": {"action": "watch", "confidence": 0.5}}
    persist_prediction_from_accumulated(accumulated, "sess-2", "000001", "平安银行")
    conn = sqlite3.connect(db)
    row = conn.execute("SELECT status, entry_price FROM predictions").fetchone()
    assert row[0] == "open" and row[1] is None
```

- [ ] **Step 2: 跑测确认红** → FAIL（status 为 unresolvable / horizon 252）

- [ ] **Step 3: 实现**（`ingest.py`）

```python
from finance_agent.outcome.track_record.judgment import DEFAULT_HORIZON_DAYS
```
record 增加：
```python
                "horizon_days": DEFAULT_HORIZON_DAYS,
                "rationale_snapshot": {
                    "action": action,
                    "fund_manager_decision": accumulated.get("fund_manager_decision"),
                    "fund_manager_decision_reasoning": accumulated.get(
                        "fund_manager_decision_reasoning"
                    ),
                    "declared_prices": {
                        "entry_price": decision.get("entry_price"),
                        "stop_loss": decision.get("stop_loss"),
                        "target_price": decision.get("target_price"),
                    },
                },
```
参考价分支改为：
```python
        status = "open"
        resolution_rule = None
        if entry_price is None:
            logger.warning(
                "参考价不可得（quote 与 kline 均无）: %s；判定时由行情派生结算入场价", stock_code
            )
```
（`insert_prediction` 的 `entry_price` 传值逻辑不变；模块 docstring 同步更新语义。）

- [ ] **Step 4: 绿 + 更新既有测试**（`test_decision_logging.py` / `test_track_record_ingest_shared.py` 中依赖「缺参考价 → unresolvable」的断言改为 open + WARN 语义）+ Commit

```bash
git add src/finance_agent/outcome/track_record/ingest.py tests/outcome/
git commit -m "feat(outcome): ingest 显式 horizon=20 + 申报价入快照 + 参考价不可得不阻断（delta update-decision-settlement-contract）"
```

---

### Task 3: judgment 共享函数——派生入场价 + 回避判定 + 富化 Resolution

**Files:**
- Modify: `src/finance_agent/outcome/track_record/judgment.py`
- Test: `tests/outcome/test_track_record_judgment.py`

**Interfaces:**
- Produces: `derive_entry(prediction: dict, kline: pd.DataFrame) -> tuple[str, float] | None`（返回 `(entry_date, close)`）；`Resolution` 新字段 `entry_date / entry_price / exit_date / hold_days`；`resolve_prediction` 以派生入场价计算、neutral → `avoidance_win/loss/neutral`
- Consumes: Task 1 的 `CLOSE_TIME_CUTOFF`

- [ ] **Step 1: 失败测试**

```python
def _kline(rows: list[tuple[str, float]]) -> "pd.DataFrame":
    import pandas as pd

    return pd.DataFrame({"日期": [r[0] for r in rows], "收盘": [r[1] for r in rows]})


def test_derive_entry_before_close_takes_same_day_close():
    from finance_agent.outcome.track_record.judgment import derive_entry

    kline = _kline([("2026-09-01", 10.0), ("2026-09-02", 11.0), ("2026-09-03", 12.0)])
    p = {"created_at": "2026-09-02T10:00:00"}
    assert derive_entry(p, kline) == ("2026-09-02", 11.0)


def test_derive_entry_after_close_takes_next_trading_day():
    from finance_agent.outcome.track_record.judgment import derive_entry

    kline = _kline([("2026-09-01", 10.0), ("2026-09-02", 11.0), ("2026-09-03", 12.0)])
    p = {"created_at": "2026-09-02T18:00:00"}
    assert derive_entry(p, kline) == ("2026-09-03", 12.0)


def test_derive_entry_non_trading_day_takes_next():
    from finance_agent.outcome.track_record.judgment import derive_entry

    kline = _kline([("2026-09-04", 10.0), ("2026-09-07", 11.0)])  # 9/5-9/6 周末
    p = {"created_at": "2026-09-05T10:00:00"}
    assert derive_entry(p, kline) == ("2026-09-07", 11.0)


def test_derive_entry_no_rows_returns_none():
    from finance_agent.outcome.track_record.judgment import derive_entry

    assert derive_entry({"created_at": "2026-09-10T10:00:00"}, _kline([("2026-09-01", 10.0)])) is None


def test_resolve_neutral_avoidance_semantics():
    from finance_agent.outcome.track_record.judgment import resolve_prediction

    # 入场 9/2 收盘 11；20 个交易日后收盘 9.9（跌 10%）→ 回避正确
    rows = [("2026-09-02", 11.0)] + [(f"2026-10-{d:02d}", 9.9) for d in range(1, 21)]
    p = {
        "created_at": "2026-09-02T10:00:00",
        "direction": "neutral",
        "horizon_days": 20,
    }
    r = resolve_prediction(p, _kline(rows))
    assert r is not None
    assert r.status == "avoidance_win"
    assert r.entry_date == "2026-09-02" and r.entry_price == 11.0
    assert r.exit_date == "2026-10-20" and r.hold_days == 20


def test_resolve_neutral_missed_upside():
    from finance_agent.outcome.track_record.judgment import resolve_prediction

    rows = [("2026-09-02", 11.0)] + [(f"2026-10-{d:02d}", 13.2) for d in range(1, 21)]
    p = {"created_at": "2026-09-02T10:00:00", "direction": "neutral", "horizon_days": 20}
    r = resolve_prediction(p, _kline(rows))
    assert r is not None and r.status == "avoidance_loss"


def test_resolve_long_uses_derived_entry_not_reference():
    from finance_agent.outcome.track_record.judgment import resolve_prediction

    rows = [("2026-09-02", 11.0)] + [(f"2026-10-{d:02d}", 12.1) for d in range(1, 21)]
    p = {
        "created_at": "2026-09-02T10:00:00",
        "direction": "long",
        "horizon_days": 20,
        "entry_price": 999.0,  # 参考价离谱也必须被忽略
    }
    r = resolve_prediction(p, _kline(rows))
    assert r is not None and r.entry_price == 11.0
    assert abs(r.raw_return - (12.1 / 11.0 - 1.0)) < 1e-6
```

（既有 `test_track_record_judgment.py` 用例若以 `entry_price` 参考价造数，改为依赖派生入场价——逐条读后更新，保持断言语义。）

- [ ] **Step 2: 跑测确认红** → FAIL（`derive_entry` 不存在 / 状态不是 avoidance_*）

- [ ] **Step 3: 实现**（`judgment.py`）

```python
@dataclass
class Resolution:
    status: str  # resolved_win/loss/neutral 或 avoidance_win/loss/neutral
    entry_date: str
    entry_price: float
    exit_date: str
    exit_price: float
    raw_return: float
    excess_return: float | None
    hold_days: int
    resolution_rule: str  # expiry / superseded


def _norm(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out["日期"] = out["日期"].astype(str).str[:10]
    return out.sort_values("日期").reset_index(drop=True)


def derive_entry(prediction: dict, kline: pd.DataFrame) -> tuple[str, float] | None:
    """派生结算入场价（date, close）：归属日收盘；收盘后决策 → 次一交易日；非交易日 → 其后首个交易日。"""
    created = str(prediction["created_at"])
    day, _, time_part = created.partition("T")
    before_close = (time_part or "00:00:00")[:5] < CLOSE_TIME_CUTOFF
    df = _norm(kline)
    rows = df[df["日期"] >= day] if before_close else df[df["日期"] > day]
    if rows.empty:
        return None
    row = rows.iloc[0]
    return str(row["日期"]), float(row["收盘"])
```

`resolve_prediction` 重写（保留签名与「未到点返回 None」语义）：

```python
def resolve_prediction(
    prediction: dict,
    kline: pd.DataFrame,
    benchmark: pd.DataFrame | None = None,
    neutral_band: float = DEFAULT_NEUTRAL_BAND,
) -> Resolution | None:
    """horizon 到点判定。入场基准为派生 settle_entry_price（非参考价）。"""
    derived = derive_entry(prediction, kline)
    if derived is None:
        return None
    entry_date, entry_price = derived
    if entry_price <= 0:
        return None
    horizon = _effective_horizon(prediction)
    direction = prediction.get("direction", "long")
    df = _norm(kline)
    rows = df[df["日期"] > entry_date].reset_index(drop=True)
    if len(rows) < horizon:
        return None
    exit_row = rows.iloc[horizon - 1]
    exit_price = float(exit_row["收盘"])
    exit_date = str(exit_row["日期"])
    sign = -1.0 if direction == "short" else 1.0  # neutral 按 long 口径
    raw_return = sign * (exit_price / entry_price - 1.0)
    excess = raw_return
    if benchmark is not None and not benchmark.empty:
        bench_df = _norm(benchmark)
        entry_bench = _bench_close_on_or_before(bench_df, entry_date)
        exit_bench = _bench_close_on_or_before(bench_df, exit_date)
        if entry_bench is not None and exit_bench is not None:
            excess = raw_return - sign * (exit_bench / entry_bench - 1.0)
    band = float(neutral_band)
    if direction == "neutral":
        status = (
            "avoidance_win" if excess < -band else ("avoidance_loss" if excess > band else "avoidance_neutral")
        )
    else:
        status = "resolved_win" if excess > band else ("resolved_loss" if excess < -band else "resolved_neutral")
    return Resolution(
        status=status,
        entry_date=entry_date,
        entry_price=round(entry_price, 6),
        exit_date=exit_date,
        exit_price=exit_price,
        raw_return=round(raw_return, 6),
        excess_return=round(excess, 6),
        hold_days=horizon,
        resolution_rule="expiry",
    )
```

- [ ] **Step 4: 绿 + Commit**

Run: `uv run pytest tests/outcome/test_track_record_judgment.py -v`
Expected: 全 passed（含既有 horizon/中性带/superseded 用例零回归）

```bash
git add src/finance_agent/outcome/track_record/judgment.py tests/outcome/test_track_record_judgment.py
git commit -m "feat(outcome): judgment 派生结算入场价 + neutral 回避判定（共享纯函数，delta update-decision-settlement-contract）"
```

---

### Task 4: job 改造——两路径用派生入场价 + 回避落库 + long/short Score 上报

**Files:**
- Modify: `src/finance_agent/outcome/track_record/job.py`
- Modify: `src/finance_agent/outcome/job.py`（旧链路 hold/watch 排除，一行守卫）
- Test: `tests/outcome/test_track_record_job.py`、`tests/outcome/test_job.py`

**Interfaces:**
- Consumes: Task 3 的 `derive_entry` / `Resolution`（新字段）
- Produces: `settle_open_predictions(*, client=None, db_path=None, kline_days=280, langfuse=None)`；判定落库含 `settle_entry_price` / `avoidance_status`；long/short 上报 decision_hit/return/excess

- [ ] **Step 1: 失败测试**（`tests/outcome/test_track_record_job.py` 追加；先读既有夹具构造 fake client 的方式并沿用）

```python
def test_neutral_prediction_writes_avoidance_status(tmp_path, monkeypatch):
    """neutral 观点 → avoidance_status 落库，status 仍 open 之外不再写 resolved_*。"""
    # 用既有 fake client 夹具：kline 造「入场 11 → 20 日后 9.9」+ benchmark 同值 → 回避正确
    ...


def test_long_prediction_writes_settle_entry_price(tmp_path, monkeypatch):
    """long 判定落库含派生 settle_entry_price 与真实 resolved_at（结算日）。"""
    ...


def test_scores_reported_for_long_only(tmp_path, monkeypatch):
    """long/short 上报 3 个 Score；neutral 上报 0。用 fake langfuse 收集 create_score 调用。"""
    ...
```

（三个用例的具体造数细节由实现者按既有 `test_track_record_job.py` 夹具模式补齐；断言点：`avoidance_status`、`settle_entry_price`、`resolved_at == exit_date`、`langfuse.create_score` 调用次数与 name 集合。）

- [ ] **Step 2: 跑测确认红**

- [ ] **Step 3: 实现**（`track_record/job.py`）

1. 签名加 `langfuse: Any = None`；函数开头：
```python
    if langfuse is None:
        try:
            from finance_agent.langfuse_tracing import get_langfuse

            langfuse = get_langfuse()
        except Exception:  # noqa: BLE001 - 观测旁路，不阻断结算
            langfuse = None
```
2. superseded 路径：`if old["direction"] == "neutral": continue`（spec：neutral 不走 superseded）；入场改用派生价：
```python
                        derived = derive_entry(old, kline)
                        if derived is None:
                            result["skipped"] += 1
                            continue
                        _, entry = derived
                        ...
                        raw = sign * (exit_price / entry - 1.0) if entry > 0 else 0.0
                        update_prediction_status(old["prediction_id"], {..., "settle_entry_price": entry, ...})
```
3. horizon 路径落库 payload 改为：
```python
            payload = {
                "exit_price": resolution.exit_price,
                "raw_return": resolution.raw_return,
                "excess_return": resolution.excess_return,
                "resolution_rule": resolution.resolution_rule,
                "resolved_at": resolution.exit_date,
                "settle_entry_price": resolution.entry_price,
            }
            if resolution.status.startswith("avoidance_"):
                payload["avoidance_status"] = resolution.status
            else:
                payload["status"] = resolution.status
            update_prediction_status(p["prediction_id"], payload, db_path)
```
   （`resolved_at` 修正为真实结算日——原实现写 `created_at[:10]`，与 superseded 路径语义不一致，属本次一并修正的 1 行缺陷，补测试。）
4. Score 上报（模块内新函数）：
```python
def _report_scores(langfuse: Any, prediction: dict, resolution: Resolution) -> int:
    """long/short 结算后上报 3 个 Score；neutral 回避判定与缺 trace/langfuse 跳过。"""
    trace_id = prediction.get("langfuse_trace_id")
    if not trace_id or langfuse is None or resolution.status.startswith("avoidance_"):
        return 0
    comment = (
        f"settle_price={resolution.exit_price} hold_days={resolution.hold_days} "
        f"benchmark_return={(resolution.raw_return - resolution.excess_return) if resolution.excess_return is not None else 'n/a'}"
    )
    scores: list[tuple[str, float, str]] = [
        ("decision_hit", 1.0 if resolution.raw_return > 0 else 0.0, "BOOLEAN"),
        ("decision_return", resolution.raw_return, "NUMERIC"),
    ]
    if resolution.excess_return is not None:
        scores.append(("decision_excess", resolution.excess_return, "NUMERIC"))
    reported = 0
    for name, value, data_type in scores:
        try:
            langfuse.create_score(
                name=name, value=value, trace_id=trace_id, data_type=data_type, comment=comment
            )
            reported += 1
        except Exception as e:  # noqa: BLE001 - trace 不可查容错
            logger.warning("score 上报失败(trace 不可查?): %s %s", name, e)
    return reported
```
   horizon 路径判定成功后调用：`result.setdefault("scores_reported", 0)`；`result["scores_reported"] += _report_scores(langfuse, p, resolution)`（返回 dict 的 key 集合扩一个）。
5. 旧链路 `outcome/job.py::report_outcome_scores` 开头加守卫：
```python
    if str(decision.get("action") or "") in ("hold", "watch"):
        return 0
```

- [ ] **Step 4: 绿 + Commit**

Run: `uv run pytest tests/outcome/test_track_record_job.py tests/outcome/test_job.py tests/outcome/test_scheduler.py -v`

```bash
git add src/finance_agent/outcome/track_record/job.py src/finance_agent/outcome/job.py tests/outcome/
git commit -m "feat(outcome): 判定 job 派生入场价/回避落库/Score 补齐 + neutral 不走 superseded（delta update-decision-settlement-contract）"
```

---

### Task 5: 统计与 API——胜率仅 long/short + 回避正确率独立字段 + 口径过滤

**Files:**
- Modify: `src/finance_agent/outcome/track_record/model.py`（`prediction_stats` 加 `horizon_days` 过滤 + 胜率限 long/short；新增 `avoidance_stats`）
- Modify: `src/finance_agent/api.py`（overview additive 字段）
- Test: `tests/outcome/test_track_record_model.py`、`tests/test_api_track_record.py`

**Interfaces:**
- Produces: `prediction_stats(source_type=None, db_path=None, version_seq=None, horizon_days: int | None = None) -> dict`；`avoidance_stats(source_type=None, db_path=None, version_seq=None) -> dict`（键：`avoidance_win/avoidance_loss/avoidance_neutral/settled/avoidance_rate`）；overview 响应新增 `avoidance` 与 `caliber_horizon`、`legacy_settled`

- [ ] **Step 1: 失败测试**（要点：① 胜率分母只含 long/short——存量 neutral 已 resolved 行不计入；② 回避统计独立；③ horizon 过滤把 252 存量段排除出头条读数并披露）

```python
def test_win_rate_excludes_neutral_rows(tmp_path): ...      # 4 long win + 1 neutral resolved_win → win_rate=1.0, settled=4
def test_avoidance_stats_rate_and_threshold(tmp_path): ...  # 6 win + 3 loss → rate 0.667；settled=9
def test_stats_horizon_filter(tmp_path): ...                # horizon=20 只数新口径行；horizon=None 全量
```

- [ ] **Step 2: 跑测确认红**

- [ ] **Step 3: 实现**

`prediction_stats`：wins/losses/avg_excess 三个 SQL 追加 `AND direction IN ('long','short')`；新增可选 `horizon_days` 参数并入 `where` 条件（`AND horizon_days=?`）。

`avoidance_stats`（新增函数，紧邻 `prediction_stats`）：

```python
def avoidance_stats(
    source_type: str | None = None,
    db_path: str | Path | None = None,
    version_seq: int | None = None,
) -> dict[str, Any]:
    """neutral 观点回避正确率：avoidance_win/(avoidance_win+avoidance_loss)；neutral 与未判定不进分母。"""
    conn = _connect(db_path)
    try:
        cond, params = "", []
        if source_type:
            cond += " AND source_type=?"
            params.append(source_type)
        if version_seq is not None:
            cond += " AND version_seq=?"
            params.append(version_seq)
        rows = conn.execute(
            f"SELECT avoidance_status, COUNT(*) FROM predictions WHERE avoidance_status IS NOT NULL{cond}"  # noqa: S608
            " GROUP BY avoidance_status",
            params,
        ).fetchall()
        counts = dict(rows)
        wins = int(counts.get("avoidance_win", 0))
        losses = int(counts.get("avoidance_loss", 0))
        settled = wins + losses
        return {
            "avoidance_win": wins,
            "avoidance_loss": losses,
            "avoidance_neutral": int(counts.get("avoidance_neutral", 0)),
            "settled": settled,
            "avoidance_rate": round(wins / settled, 4) if settled else None,
        }
    finally:
        conn.close()
```

`api.py` overview（`stats = prediction_stats(...)` 调用处）：传入 `horizon_days=DEFAULT_HORIZON_DAYS`（`from finance_agent.outcome.track_record.judgment import DEFAULT_HORIZON_DAYS`），并追加：

```python
        avoidance = await asyncio.to_thread(avoidance_stats, source, None, version)
        legacy_all = await asyncio.to_thread(prediction_stats, source, None, version)
        ...
        return {
            **stats, ...,
            "avoidance": {
                **avoidance,
                "avoidance_rate": None if avoidance["settled"] < 10 else avoidance["avoidance_rate"],
            },
            "caliber_horizon": DEFAULT_HORIZON_DAYS,
            "legacy_settled": legacy_all["settled"] - stats["settled"],
        }
```
（按 api.py 既有 `asyncio.to_thread` 调用风格对齐实参；`insufficient_sample` 逻辑保持 settled<10。）

- [ ] **Step 4: 绿 + Commit**

Run: `uv run pytest tests/outcome/test_track_record_model.py tests/test_api_track_record.py -v`

```bash
git add src/finance_agent/outcome/track_record/model.py src/finance_agent/api.py tests/
git commit -m "feat(outcome): 胜率限 long/short + 回避正确率独立统计 + overview 口径字段（delta update-decision-settlement-contract）"
```

---

### Task 6: 回测 replay 切同源 + settle.py deprecated

**Files:**
- Modify: `evals/backtest/replay.py`
- Modify: `src/finance_agent/outcome/settle.py`（docstring + 首次调用 WARN）
- Test: `tests/evals/backtest/test_replay.py`

**Interfaces:**
- Consumes: Task 3 的 `resolve_prediction`（新 Resolution）；`judgment.DEFAULT_HORIZON_DAYS`
- Produces: `replay_decision` 返回的 `settlement` dict 保留下游契约键：`status / settle_date / settle_price / hold_days / decision_return / benchmark_return / decision_excess / decision_hit`（由 Resolution 映射）；`entry_price` 返回派生值

- [ ] **Step 1: 失败测试**（`tests/evals/backtest/test_replay.py`：把「mock 图 + 假 K 线」既有用例改为断言映射结果；新增中性带/入场派生断言）

```python
def test_replay_settlement_maps_shared_judgment(...):
    # 假 K 线：决策日 2026-09-02 收盘 11，20 个交易日后 12.1 → status resolved_win
    # settlement["settle_date"] == "2026-10-20"；settle_price == 12.1；decision_hit True
    # entry_price == 11.0（派生，非参考价）
def test_replay_neutral_action_maps_avoidance(...): ...
```

- [ ] **Step 2: 跑测确认红**

- [ ] **Step 3: 实现**

`replay.py`：
- import 改为 `from finance_agent.outcome.track_record.judgment import DEFAULT_HORIZON_DAYS, resolve_prediction`；删除 `from finance_agent.outcome.settle import evaluate_decision`。
- 删除 `build_decision_record`（其契约属旧引擎；先 `grep -rn build_decision_record` 确认调用方仅测试与 `tests/scripts/backtest_pilot_2023.py`，一并更新）。
- 结算段替换为：

```python
    prediction = {
        "created_at": decision_date,  # 无时间部分 → before_close → 归属日收盘（与旧 _close_on_or_before 同值）
        "direction": {"buy": "long", "sell": "short"}.get(action, "neutral"),
        "horizon_days": DEFAULT_HORIZON_DAYS,
        "target_price": decision.get("target_price"),
    }
    benchmark = full_benchmark if full_benchmark is not None else state.get("benchmark_kline")
    resolution = resolve_prediction(prediction, kline, benchmark)
    if resolution is None:
        return {..., "settlement": None, "entry_price": None, ...}
    settlement = {
        "status": resolution.status,
        "settle_date": resolution.exit_date,
        "settle_price": resolution.exit_price,
        "hold_days": resolution.hold_days,
        "decision_return": resolution.raw_return,
        "benchmark_return": (
            None
            if resolution.excess_return is None
            else resolution.raw_return - resolution.excess_return
        ),
        "decision_excess": resolution.excess_return,
        "decision_hit": resolution.raw_return > 0,
    }
```
- 返回 dict 的 `"entry_price": resolution.entry_price`；`_close_on_or_before` 若无残余调用方则删除（`grep -n "_close_on_or_before" evals/ tests/`）。
- 模块 docstring 改为「结算语义与生产 track-record 判定同源（horizon/超额/±2% 带），不另造一套」。

`outcome/settle.py`：模块 docstring 顶部加 deprecated 说明；`evaluate_decision` 首次调用 `logger.warning`（模块级 `_DEPRECATION_WARNED` 标志，只提示一次，避免测试输出噪声；补 `import logging`）。`evals/golden/gates.py` **不动**（design 已登记其语义独立）。

- [ ] **Step 4: 绿 + 回测离线冒烟 + Commit**

Run: `uv run pytest tests/evals/backtest -v`（`run_backtest`/`performance` 用 fake replay_fn 的用例零回归）
Run（离线冒烟，零 LLM）：`uv run python - <<'PY'` 构造假 kline 直接调 `replay` 的映射逻辑或 `resolve_prediction`，断言与 Task 3 用例一致。

```bash
git add evals/backtest/replay.py src/finance_agent/outcome/settle.py tests/evals/backtest/test_replay.py
git commit -m "refactor(evals): 回测结算切 track-record 同源判定 + settle.py deprecated（delta update-decision-settlement-contract）"
```

---

### Task 7: 验证收口——全量校验 + metrics.md 切点 + tasks.md 勾选 + 验证报告

**Files:**
- Modify: `docs/evals/metrics.md`（§2 时间线追加「结算契约切点」段）
- Modify: `openspec/changes/update-decision-settlement-contract/tasks.md`（勾选）
- Create: `tests/validation/2026-09-23-update-decision-settlement-contract-validation.md`

- [ ] **Step 1: 全量相关测试 + lint + 类型**

```bash
uv run pytest tests/outcome tests/evals/backtest tests/test_api_track_record.py -v
uv run ruff check src/finance_agent/outcome evals/backtest tests/outcome
uv run mypy src/finance_agent/outcome
```
Expected: 全绿 / 零违例 / 触碰文件零新增类型错误

- [ ] **Step 2: 离线真实链路验证**（零 LLM，脚本化）

① `persist_prediction_from_accumulated`（假 accumulated）→ 核对 predictions 行：`horizon_days=20`、参考价口径、`declared_prices` 在快照内；
② `settle_open_predictions` + fake client（真实形态 K 线帧）→ 核对：long 行 `settle_entry_price` 派生正确、`resolved_at=exit_date`、Score 调用计数；neutral 行 `avoidance_status` 落库且 `status` 未变；
③ 两腿同口径抽查：同一假 K 线走 `resolve_prediction`（生产）与 `replay` 映射，断言 `raw_return`/`excess_return` 一致（这是「双腿互证」的实现级证据）。
产出落 `tests/validation/` 附日志摘要。

- [ ] **Step 3: metrics.md §2 切点段**

在「Outcome 收益口径切点（2026-09-23…）」段之后追加：

```markdown
**结算契约切点（2026-09-23，delta `update-decision-settlement-contract`，未跑批）**：默认判定窗口 252→20（配置项 `OUTCOME_DEFAULT_HORIZON_DAYS`）；判定入场基准改为派生 `settle_entry_price`（归属日收盘 / 收盘后次一交易日 / 非交易日顺延），参考价仅展示与盯市；neutral 观点改回避判定（`avoidance_status`，不进胜率，不走 superseded）；track-record 判定链路补齐 long/short 的 decision_* Score 上报。**存量已结算行不重算、存量 open 按原 horizon 判定；跨此切点的胜率读数不可直接比较**（头条口径按 `caliber_horizon=20` 过滤，`legacy_settled` 字段披露存量计数）。真实链路 LLM 端到端（deep 分析 → 落库 → 判定）留待 owner 有预算时补跑。
```

- [ ] **Step 4: tasks.md 勾选 + 验证报告 + Commit**

`openspec/changes/update-decision-settlement-contract/tasks.md`：1.1–1.3、2.1–2.2、3.1–3.5、4.1–4.3、5.1–5.4 勾选；5.5 注明「validate 已过；sync/archive 待全部 delta 完成后统一」。

验证报告模板（`tests/validation/2026-09-23-update-decision-settlement-contract-validation.md`）：
```markdown
# 人工验证报告: update-decision-settlement-contract

**日期**: 2026-09-23
**验证人**: agent（机器项）+ owner（待补：真实链路 LLM 端到端 + 战绩页展示核对）
**关联 delta**: openspec/changes/update-decision-settlement-contract/
**E2E 门禁**: 不适用（非交互类：零前端代码改动；战绩页语义变化为被动展示）

## Scenario 对照表（spec 六条 MODIFIED 需求逐条 → 落点 + 证据）
## 机器验证结果（测试/ruff/mypy/离线链路日志摘要）
## Owner 待办（LLM 端到端实跑 + 战绩页人工核对）
## 异常记录
## 结论
```

```bash
openspec validate update-decision-settlement-contract --strict
git add docs/evals/metrics.md openspec/changes/update-decision-settlement-contract/tasks.md tests/validation/2026-09-23-update-decision-settlement-contract-validation.md
git commit -m "test(outcome): update-decision-settlement-contract 验证收口——切点登记 + Scenario 对照（tasks 勾选）"
```

---

## Self-Review（计划自审）

- **Spec 覆盖**：决策落库（Task 2 + 5 的参考价语义）/ 事后行情追踪（Task 1 窗口 + 3 派生基准）/ Score 上报（Task 4）/ 观点数据模型（Task 1 两列 + 2 快照）/ 判定规则（Task 3 回避 + 4 superseded 排除）/ 基础统计（Task 5）。六条 MODIFIED 需求均有落点。
- **占位符扫描**：Task 4 的三个测试以「要点 + 断言点」给出（夹具形态依既有测试文件，实现者先读后写）；其余步骤含完整代码。Task 4 的省略属「读既有夹具后适配」，非 TBD。
- **类型一致性**：`Resolution` 字段在 Task 3 定义、Task 4/6 消费一致；`derive_entry` 返回 `(date, close)` 在 Task 3/4 一致；`prediction_stats` 新参数在 Task 5 定义、api 调用一致。
- **已知范围声明**：真实链路 LLM 端到端与回测正式批属 owner 预算门控项（Task 7 已标注）；golden gates 与 `decision_log` 只读 API 不动。
