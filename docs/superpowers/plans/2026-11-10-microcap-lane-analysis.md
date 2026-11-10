# 小市值池每日分析 + 独立模拟盘 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 小票池（microcap lane）每日自动深度分析前 10 只，决策带 lane 标记进独立纸面账本（第二执行 DB，单票 5%、滑点 +1 档），读侧 API/日报可查，主档路径逐字节不变。

**Architecture:** 两列幂等加列（`pool_analysis_runs.pool` / `predictions.lane`，DEFAULT 'main'）实现 lane 归属；执行链驱动器/撮合参数化（`lane` + `single_cap` + `tier_bump`）复用同一套代码驱动第二账本 `data/execution_microcap.db`；三个新 ops job（16:12/16:24/16:47）；既有消费者两处加 `pool='main'` 过滤保主档口径。

**Tech Stack:** Python 3.14 / sqlite3（WAL）/ pytest / uv；无新依赖。

**权威依据:** delta 规范 `openspec/changes/add-microcap-lane-analysis/`（specs/ 六份）+ 设计文档 `docs/superpowers/specs/2026-11-10-microcap-lane-daily-analysis-design.md`。实现与 delta 冲突时以 delta 为准。

## Global Constraints

- **主档回归红线**：主档行为逐字节不变——所有修改点对存量数据（默认 'main'）的行为必须与变更前一致；既有测试全绿是每个任务的验收项之一
- **worktree 纪律**：全程在 `.worktrees/microcap-lane-analysis` 分支 `feat-microcap-lane-analysis` 实施；每个 Bash 调用先 `cd` 或用绝对路径（CWD 会重置）；验证前先 `pwd`
- **环境**：worktree 无 `.venv`，首个任务先跑 `uv sync`（一次性，约 1-2 分钟）
- 测试运行一律 `uv run pytest <path> -v`（在 worktree 根）；lint `uv run ruff check`；类型 `uv run mypy`
- 新 env 均有默认值：`AUTO_POOL_MICROCAP_ENABLED=1`、`AUTO_POOL_MICROCAP_MAX_SYMBOLS=10`、`EXECUTION_MICROCAP_DB_PATH=data/execution_microcap.db`
- 档位常量单一来源：`screening/constraints.py`（`MICROCAP_SUGGESTED_SINGLE_CAP=0.05`、`MICROCAP_SLIPPAGE_TIER_BUMP=1`）——执行层 import 复用，禁止复制数值
- TDD 纪律：每任务先写失败测试、跑红、最小实现、跑绿、提交；禁 mock 被测系统
- 提交信息格式 `feat(scope): ...` / `test(scope): ...`；**不 push**（主检出有并行会话，合并时统一走 PR）

---

### Task 1: `pool_analysis_runs.pool` 归属列

**Files:**
- Modify: `src/finance_agent/screening/pool_analysis.py`（`_SCHEMA`、`init_pool_analysis_runs`、`record_pool_analysis_run`、`get_pool_analysis_run`、`list_pool_analysis_runs`）
- Test: `tests/screening/test_pool_analysis_runs_pool.py`（新建）

**Interfaces:**
- Produces: `record_pool_analysis_run(trade_date, symbol, session_id, status="queued", error=None, *, pool="main", db_path=None)`；`get_pool_analysis_run(trade_date, symbol, *, db_path=None)` 返回 dict 含 `pool` 键；`list_pool_analysis_runs(trade_date, *, pool=None, db_path=None)`（`pool=None` 全量、`"main"`/`"microcap"` 过滤）；模块常量 `POOL_MAIN="main"`、`POOL_MICROCAP="microcap"`
- Consumes: 无（数据层起点）

- [ ] **Step 1: 写失败测试**

```python
"""pool_analysis_runs.pool 归属列（add-microcap-lane-analysis T1）。"""
from finance_agent.screening.pool_analysis import (
    POOL_MAIN,
    POOL_MICROCAP,
    get_pool_analysis_run,
    list_pool_analysis_runs,
    record_pool_analysis_run,
)


def test_pool_column_default_main(tmp_path):
    db = tmp_path / "screening.db"
    record_pool_analysis_run("2026-11-10", "002040", "sess-1", "queued", db_path=db)
    row = get_pool_analysis_run("2026-11-10", "002040", db_path=db)
    assert row["pool"] == POOL_MAIN


def test_pool_column_explicit_microcap(tmp_path):
    db = tmp_path / "screening.db"
    record_pool_analysis_run(
        "2026-11-10", "002749", "sess-2", "queued", pool=POOL_MICROCAP, db_path=db
    )
    row = get_pool_analysis_run("2026-11-10", "002749", db_path=db)
    assert row["pool"] == POOL_MICROCAP


def test_pool_filter_list(tmp_path):
    db = tmp_path / "screening.db"
    record_pool_analysis_run("2026-11-10", "002040", "s1", "queued", db_path=db)
    record_pool_analysis_run(
        "2026-11-10", "002749", "s2", "queued", pool=POOL_MICROCAP, db_path=db
    )
    assert [r["symbol"] for r in list_pool_analysis_runs("2026-11-10", pool=POOL_MAIN, db_path=db)] == ["002040"]
    assert [r["symbol"] for r in list_pool_analysis_runs("2026-11-10", pool=POOL_MICROCAP, db_path=db)] == ["002749"]
    assert len(list_pool_analysis_runs("2026-11-10", db_path=db)) == 2


def test_old_db_migration_adds_pool(tmp_path):
    """旧库（无 pool 列）经 init 幂等补列，存量行 pool='main'。"""
    import sqlite3

    db = tmp_path / "screening.db"
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE pool_analysis_runs (trade_date TEXT NOT NULL, symbol TEXT NOT NULL,"
        " session_id TEXT NOT NULL, status TEXT NOT NULL, error TEXT,"
        " created_at TEXT NOT NULL, updated_at TEXT NOT NULL,"
        " PRIMARY KEY (trade_date, symbol))"
    )
    conn.execute(
        "INSERT INTO pool_analysis_runs VALUES ('2026-11-09','600251','old','completed',NULL,'t','t')"
    )
    conn.commit()
    conn.close()
    record_pool_analysis_run("2026-11-10", "002040", "s1", "queued", db_path=db)
    old = get_pool_analysis_run("2026-11-09", "600251", db_path=db)
    assert old["pool"] == POOL_MAIN  # 迁移默认值
```

- [ ] **Step 2: 跑红**

Run: `uv run pytest tests/screening/test_pool_analysis_runs_pool.py -v`
Expected: FAIL（`ImportError: cannot import name 'POOL_MAIN'`）

- [ ] **Step 3: 实现**

`pool_analysis.py` 顶部常量区（`AUTO_POOL_DISPLAY_SUFFIX` 旁）加：

```python
# 池归属常量（add-microcap-lane-analysis）：运行行标注来源档位；列不进主键，
# (trade_date, symbol) 全局幂等键保持不变——跨池去重靠它实现。
POOL_MAIN = "main"
POOL_MICROCAP = "microcap"
```

`_SCHEMA` 的 `PRIMARY KEY` 行之前加列（新库直建）：

```sql
  pool TEXT NOT NULL DEFAULT 'main',
```

`init_pool_analysis_runs` 改为幂等迁移（模式照抄 `screening/store.py` 的
`_ensure_batch_columns`）：

```python
_RUNS_COLUMN_MIGRATIONS: tuple[tuple[str, str], ...] = (
    ("pool", "ALTER TABLE pool_analysis_runs ADD COLUMN pool TEXT NOT NULL DEFAULT 'main'"),
)


def init_pool_analysis_runs(db_path: str | Path | None = None) -> None:
    with _connect(db_path) as conn:
        conn.execute(_SCHEMA)
        columns = {row[1] for row in conn.execute("PRAGMA table_info(pool_analysis_runs)")}
        for name, ddl in _RUNS_COLUMN_MIGRATIONS:
            if name not in columns:
                conn.execute(ddl)
        conn.commit()
```

`record_pool_analysis_run` 签名加 `*, pool: str = POOL_MAIN`，INSERT 两处改：

```python
        conn.execute(
            """
            INSERT INTO pool_analysis_runs
                (trade_date, symbol, session_id, status, error, pool, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(trade_date, symbol) DO UPDATE SET
                session_id = excluded.session_id,
                status = excluded.status,
                error = excluded.error,
                updated_at = excluded.updated_at
            """,
            (trade_date, symbol, session_id, status, error, pool, now, now),
        )
```

（注意 ON CONFLICT 更新**不含 pool**——首次写入后归属不变，防重派改池。）

`get_pool_analysis_run` SELECT 加 `pool`；`list_pool_analysis_runs` 签名加 `*, pool: str | None = None`，SELECT 加 `pool`，`pool` 非 None 时拼 ` AND pool = ?`。

- [ ] **Step 4: 跑绿 + 回归**

Run: `uv run pytest tests/screening/test_pool_analysis_runs_pool.py -v` → PASS
Run: `uv run pytest tests/screening/ -v` → 全绿（主档回归）

- [ ] **Step 5: 提交**

```bash
git add src/finance_agent/screening/pool_analysis.py tests/screening/test_pool_analysis_runs_pool.py
git commit -m "feat(screening): pool_analysis_runs 归属列 pool（默认 main，microcap 显式）"
```

---

### Task 2: `predictions.lane` 归属列 + ingest 反查

**Files:**
- Modify: `src/finance_agent/outcome/track_record/model.py`（DDL + 落库路径）、`src/finance_agent/outcome/track_record/ingest.py`
- Modify: `src/finance_agent/screening/pool_analysis.py`（新增反查助手）
- Test: `tests/track_record/test_prediction_lane.py`（新建）

**Interfaces:**
- Produces: `get_pool_by_session(session_id: str, *, db_path=None) -> str | None`（screening 侧；命中返回 `'main'|'microcap'`）；predictions 行含 `lane` 键；ingest 落库行 `lane` 按 session 反查、失败回退 `'main'`
- Consumes: Task 1 的 pool 列

- [ ] **Step 1: 写失败测试**

```python
"""predictions.lane 归属（add-microcap-lane-analysis T2）。"""
import sqlite3

from finance_agent.screening.pool_analysis import (
    POOL_MICROCAP,
    get_pool_by_session,
    record_pool_analysis_run,
)


def test_get_pool_by_session_hit(tmp_path):
    db = tmp_path / "screening.db"
    record_pool_analysis_run(
        "2026-11-10", "002749", "sess-mc", "queued", pool=POOL_MICROCAP, db_path=db
    )
    assert get_pool_by_session("sess-mc", db_path=db) == POOL_MICROCAP


def test_get_pool_by_session_miss(tmp_path):
    db = tmp_path / "screening.db"
    assert get_pool_by_session("no-such-session", db_path=db) is None


def test_predictions_have_lane_default_main(tmp_path):
    """迁移后存量行 lane='main'；新落库默认 main。"""
    from finance_agent.outcome.track_record.model import list_predictions

    # list_predictions 返回行须含 lane 键（列存在性由 DDL/迁移保证）
    db = tmp_path / "track.db"
    rows = list_predictions(status="open", limit=10, db_path=db)
    assert isinstance(rows, list)  # 空库返回空表不报错
    # 列存在性直查
    conn = sqlite3.connect(db)
    cols = {row[1] for row in conn.execute("PRAGMA table_info(predictions)")}
    conn.close()
    assert "lane" in cols
```

（ingest 归属逻辑的端到端用例放 Step 3 的实现处一并补——ingest 入口签名含
`session_id`，测试构造 `accumulated` 最小 dict 调 `_persist` 路径即可；以现有
ingest 测试文件的模式为准，先跑 `grep -rn "def test" tests/track_record/test_ingest*.py | head -3` 找样例。）

- [ ] **Step 2: 跑红**

Run: `uv run pytest tests/track_record/test_prediction_lane.py -v`
Expected: FAIL（`ImportError: cannot import name 'get_pool_by_session'` 或 `no such column: lane`）

- [ ] **Step 3: 实现**

`pool_analysis.py` 加反查助手（供 track_record 跨库调用；screening 不依赖 outcome，
方向正确）：

```python
def get_pool_by_session(session_id: str, *, db_path: str | Path | None = None) -> str | None:
    """按会话 id 反查运行行池归属（add-microcap-lane-analysis T2）。

    命中返回 POOL_MAIN / POOL_MICROCAP；无行返回 None（调用方回退 main）。
    """
    init_pool_analysis_runs(db_path)
    with _connect(db_path) as conn:
        row = conn.execute(
            "SELECT pool FROM pool_analysis_runs WHERE session_id = ? ORDER BY updated_at DESC LIMIT 1",
            (str(session_id or "").strip(),),
        ).fetchone()
    return str(row["pool"]) if row else None
```

`model.py` 的 `PREDICTIONS_DDL`：`session_id TEXT,` 行后加：

```sql
  lane TEXT NOT NULL DEFAULT 'main',
```

同文件找 `_PREDICTIONS_COLUMN_MIGRATIONS` 或等价迁移单点（若无，仿 Task 1 模式在
`init_predictions`/建库入口加 `_ensure_prediction_columns`——以文件内现有迁移模式为准，
先 `grep -n "PRAGMA table_info(predictions)" src/finance_agent/outcome/track_record/model.py` 定位）：

```python
    ("lane", "ALTER TABLE predictions ADD COLUMN lane TEXT NOT NULL DEFAULT 'main'"),
```

`ingest.py` 落库函数（`accumulated, session_id, ...` 入口）内，构造 INSERT 参数前加：

```python
        # lane 归属：按会话反查池（add-microcap-lane-analysis）——查不到回退 main，
        # 反查失败不阻断落库（旁路铁律）。
        try:
            from finance_agent.screening.pool_analysis import (
                POOL_MAIN,
                get_pool_by_session,
            )

            lane = get_pool_by_session(session_id) or POOL_MAIN
        except Exception:  # noqa: BLE001 -- 旁路：归属失败不阻断业务落库
            logger.exception("lane 反查失败，回退 main")
            lane = POOL_MAIN
```

INSERT 语句与参数加 `lane` 列。跨库反查的 screening db 路径用其默认 env（生产同机
`data/screening.db`）；测试经 monkeypatch `SCREENING_DB_PATH` 或 db_path 注入（以
ingest 现有测试对 db 的隔离方式为准）。

- [ ] **Step 4: 跑绿 + 回归**

Run: `uv run pytest tests/track_record/test_prediction_lane.py tests/track_record/ -v` → 全绿
Run: `uv run pytest tests/ -k "ingest or prediction" -v` → 全绿

- [ ] **Step 5: 提交**

```bash
git add src/finance_agent/outcome/track_record/ src/finance_agent/screening/pool_analysis.py tests/track_record/test_prediction_lane.py
git commit -m "feat(track-record): predictions.lane 归属列 + ingest 按会话反查池"
```

---

### Task 3: 执行链参数化（lane / single_cap / tier_bump）

**Files:**
- Modify: `src/finance_agent/execution/matching.py:154`（`slippage_rate_for_amount`）、`src/finance_agent/execution/matching.py:205`（`match_order`）、`src/finance_agent/execution/brokers/paper.py:113`（`PaperBroker.__init__`）、`src/finance_agent/execution/driver.py`（`run_execution_daily:472` + `_create_signals_from_decisions:271`）
- Test: `tests/execution/test_lane_execution_params.py`（新建）

**Interfaces:**
- Produces: `slippage_rate_for_amount(amount, market_amounts, config, tier_bump=0)`；`match_order(order, bar, *, config, position=None, market_amounts=None, tier_bump=0)`；`PaperBroker(db_path=None, initial_cash=1_000_000.0, slippage_tier_bump=0)`；`run_execution_daily(as_of=None, *, db_path=None, execution_db_path=None, snapshot_db_path=None, regime_db_path=None, window_days=5, lane="main", single_cap=MAX_SINGLE_POSITION, slippage_tier_bump=0)`——**新增参数全部带默认值=既有行为**
- Consumes: Task 2 的 lane 列；`constraints.MAX_SINGLE_POSITION`

- [ ] **Step 1: 写失败测试**

```python
"""执行链 lane/single_cap/tier_bump 参数化（add-microcap-lane-analysis T3）。"""
from finance_agent.execution.matching import MatchingConfig, slippage_rate_for_amount

# 两档配置：分位 ≤0.5 → 0.002；>0.5 → 0.004
def _two_tier_config() -> MatchingConfig:
    return MatchingConfig(slippage_tiers=(
        _tier(0.5, 0.002), _tier(1.0, 0.004),
    ))  # 其余字段以 MatchingConfig 现有默认构造为准——先读类定义再补全


def _tier(quantile_max: float, rate: float):
    from finance_agent.execution.matching import SlippageTier

    return SlippageTier(quantile_max=quantile_max, slippage_rate=rate)


def test_tier_bump_zero_is_unchanged():
    amounts = [100.0, 200.0, 300.0, 400.0]  # 250 → 分位 0.5 → 首档
    cfg = _two_tier_config()
    assert slippage_rate_for_amount(250.0, amounts, cfg) == 0.002
    assert slippage_rate_for_amount(250.0, amounts, cfg, tier_bump=0) == 0.002


def test_tier_bump_one_shifts_and_clamps():
    amounts = [100.0, 200.0, 300.0, 400.0]
    cfg = _two_tier_config()
    assert slippage_rate_for_amount(250.0, amounts, cfg, tier_bump=1) == 0.004
    # 末档再 bump 封顶
    assert slippage_rate_for_amount(900.0, amounts, cfg, tier_bump=1) == 0.004


def test_missing_sample_still_conservative_with_bump():
    cfg = _two_tier_config()
    # 样本缺失 → 最保守档（最大 slippage 0.004），bump 不越界
    assert slippage_rate_for_amount(250.0, None, cfg, tier_bump=1) == 0.004
```

（`MatchingConfig` 构造参数以类定义为准——先读
`src/finance_agent/execution/matching.py` 的 `MatchingConfig`/`SlippageTier` dataclass
再补全构造参数；本测试只动 slippage_tiers，其余字段用类默认。）

- [ ] **Step 2: 跑红**

Run: `uv run pytest tests/execution/test_lane_execution_params.py -v`
Expected: FAIL（`slippage_rate_for_amount() got an unexpected keyword argument 'tier_bump'`）

- [ ] **Step 3: 实现 matching**

`slippage_rate_for_amount` 签名加 `tier_bump: int = 0`，取档改索引制：

```python
    if amount is None or market_amounts is None or len(market_amounts) == 0:
        return max(tier.slippage_rate for tier in config.slippage_tiers)
    quantile = sum(1 for sample in market_amounts if sample <= amount) / len(market_amounts)
    tiers = config.slippage_tiers
    index = next(
        (i for i, tier in enumerate(tiers) if quantile <= tier.quantile_max),
        len(tiers) - 1,  # 防御：末档 quantile_max==1.0 保证可达
    )
    bumped = min(index + max(0, int(tier_bump)), len(tiers) - 1)
    return tiers[bumped].slippage_rate
```

docstring 加一行：``tier_bump``（add-microcap-lane-analysis）：选中档索引 +N 后
clamp 末档（小票账本滑点档位提升；0 = 主档行为不变）。

`match_order` 签名加 `tier_bump: int = 0`，取档行改
`slippage_rate_for_amount(bar.amount, market_amounts, config, tier_bump)`。

- [ ] **Step 4: PaperBroker 构造参数**

`PaperBroker.__init__(self, db_path=None, initial_cash=1_000_000.0)` 加
`slippage_tier_bump: int = 0`，存 `self._slippage_tier_bump`；`place_order` 内
`match_order(...)` 调用加 `tier_bump=self._slippage_tier_bump`。

- [ ] **Step 5: 写驱动器失败测试（lane 过滤 + single_cap 透传）**

```python
"""run_execution_daily lane 过滤（主档只吃 lane='main'）。"""
import sqlite3

from finance_agent.execution.driver import run_execution_daily
from finance_agent.execution.store import EXECUTION_DDL


def _seed_prediction(db, *, pid, symbol, lane, created="2026-01-05T10:00:00"):
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    cols = {r[1] for r in conn.execute("PRAGMA table_info(predictions)")}
    if "lane" not in cols:
        conn.execute("ALTER TABLE predictions ADD COLUMN lane TEXT NOT NULL DEFAULT 'main'")
    conn.execute(
        "INSERT INTO predictions (prediction_id, source_type, symbol, direction,"
        " rationale_snapshot, status, created_at, updated_at, lane)"
        " VALUES (?, 'live', ?, 'long', ?, 'open', ?, ?, ?)",
        (pid, symbol, '{"fund_manager_decision":"approve","action":"buy"}', created, created, lane),
    )
    conn.commit()
    conn.close()


def test_main_lane_excludes_microcap(tmp_path, monkeypatch):
    pred_db = tmp_path / "pred.db"
    exec_db = tmp_path / "exec.db"
    conn = sqlite3.connect(pred_db)
    # 建最小 predictions 表（以 model.PREDICTIONS_DDL 为准，先 import 执行）
    from finance_agent.outcome.track_record.model import PREDICTIONS_DDL

    conn.executescript(PREDICTIONS_DDL)
    conn.close()
    conn = sqlite3.connect(exec_db)
    conn.executescript(EXECUTION_DDL)
    conn.close()
    _seed_prediction(pred_db, pid="p-main", symbol="600519", lane="main")
    _seed_prediction(pred_db, pid="p-mc", symbol="002749", lane="microcap")

    summary = run_execution_daily(
        as_of="2026-01-06",
        db_path=pred_db,
        execution_db_path=exec_db,
        snapshot_db_path=tmp_path / "empty-snapshot.db",  # 空快照 → no_snapshot_data，
        regime_db_path=tmp_path / "empty-regime.db",      # 但信号段已可断言
    )
    from finance_agent.execution.store import list_signals

    ids = {s.decision_id for s in list_signals(db_path=exec_db)}
    assert "p-main" in ids and "p-mc" not in ids
    assert summary["skipped_reason"] == "no_snapshot_data"
```

（空快照让批次走 `skipped_reason="no_snapshot_data"`，但信号段不依赖行情——正好只测
lane 过滤。rationale_snapshot 的 JSON 结构以 `_decision_payload` 实际读取的键为准：
先读 `driver.py` 的 `_decision_payload`，确保 approve/buy 可解析。）

- [ ] **Step 6: 跑红 → 实现驱动器**

`run_execution_daily` 签名加（默认值 = 既有行为）：

```python
    lane: str = "main",
    single_cap: float = constraints.MAX_SINGLE_POSITION,
    slippage_tier_bump: int = 0,
```

`_create_signals_from_decisions(...)` 加 `lane: str` 形参，观点循环开头加：

```python
        if str(row.get("lane") or "main") != lane:
            continue  # lane 隔离：主档通道不消费 microcap 观点（反之亦然）
```

segment ② 的 `aggregate_targets(...)` 调用加 `single_cap=single_cap`；
`PaperBroker(db_path=execution_db_path)` 改
`PaperBroker(db_path=execution_db_path, slippage_tier_bump=slippage_tier_bump)`。

Run: `uv run pytest tests/execution/test_lane_execution_params.py -v` → PASS
Run: `uv run pytest tests/execution/ -v` → 全绿（主档回归：既有用例全部走默认参数）

- [ ] **Step 7: 提交**

```bash
git add src/finance_agent/execution/ tests/execution/test_lane_execution_params.py
git commit -m "feat(execution): 驱动器/撮合参数化 lane+single_cap+tier_bump（默认值=既有行为）"
```

---

### Task 4: 小票执行/结算封装 + 新 env

**Files:**
- Modify: `src/finance_agent/execution/driver.py`（尾部加 `run_execution_microcap`）、`src/finance_agent/execution/brokers/paper.py:409`（`run_paper_settle_daily` 加 `execution_db_path` 参数）
- Test: `tests/execution/test_microcap_lane_runner.py`（新建）

**Interfaces:**
- Produces: `run_execution_microcap(as_of=None, **kwargs) -> dict`（ops 入口，lane='microcap' + single_cap=0.05 + tier_bump=1 + 第二库）；`run_paper_settle_microcap(as_of=None) -> dict`；`run_paper_settle_daily(as_of=None, *, execution_db_path=None)`（默认行为不变）
- Consumes: Task 3 全部参数化；`constraints.MICROCAP_SUGGESTED_SINGLE_CAP` / `MICROCAP_SLIPPAGE_TIER_BUMP`

- [ ] **Step 1: 写失败测试**

```python
"""小票执行/结算封装（add-microcap-lane-analysis T4）。"""
import sqlite3

from finance_agent.constraints import (
    MICROCAP_SLIPPAGE_TIER_BUMP,
    MICROCAP_SUGGESTED_SINGLE_CAP,
)
from finance_agent.execution.brokers.paper import run_paper_settle_microcap
from finance_agent.execution.driver import run_execution_microcap


def test_constants_wired():
    assert MICROCAP_SUGGESTED_SINGLE_CAP == 0.05
    assert MICROCAP_SLIPPAGE_TIER_BUMP == 1


def test_microcap_runner_writes_separate_db(tmp_path, monkeypatch):
    pred_db = tmp_path / "pred.db"
    exec_db = tmp_path / "exec-mc.db"
    from finance_agent.outcome.track_record.model import PREDICTIONS_DDL
    from finance_agent.execution.store import EXECUTION_DDL

    conn = sqlite3.connect(pred_db)
    conn.executescript(PREDICTIONS_DDL)
    conn.execute(
        "INSERT INTO predictions (prediction_id, source_type, symbol, direction,"
        " rationale_snapshot, status, created_at, updated_at, lane)"
        " VALUES ('p-mc-1', 'live', '002749', 'long',"
        " '{\"fund_manager_decision\":\"approve\",\"action\":\"buy\"}', 'open',"
        " '2026-01-05T17:00:00', '2026-01-05T17:00:00', 'microcap')"
    )
    conn.commit()
    conn.close()
    conn = sqlite3.connect(exec_db)
    conn.executescript(EXECUTION_DDL)
    conn.close()

    monkeypatch.setenv("EXECUTION_MICROCAP_DB_PATH", str(exec_db))
    summary = run_execution_microcap(
        as_of="2026-01-06",
        db_path=pred_db,
        snapshot_db_path=tmp_path / "empty-snapshot.db",
        regime_db_path=tmp_path / "empty-regime.db",
    )
    assert summary["skipped_reason"] == "no_snapshot_data"
    from finance_agent.execution.store import list_signals

    assert {s.decision_id for s in list_signals(db_path=exec_db)} == {"p-mc-1"}


def test_microcap_settle_empty_account(tmp_path, monkeypatch):
    exec_db = tmp_path / "exec-mc.db"
    conn = sqlite3.connect(exec_db)
    conn.executescript(EXECUTION_DDL)
    conn.close()
    monkeypatch.setenv("EXECUTION_MICROCAP_DB_PATH", str(exec_db))
    result = run_paper_settle_microcap()
    # 空快照 → no_snapshot_data 旁路；不报错
    assert result["reason"] == "no_snapshot_data"
```

- [ ] **Step 2: 跑红**

Run: `uv run pytest tests/execution/test_microcap_lane_runner.py -v`
Expected: FAIL（`ImportError: cannot import name 'run_execution_microcap'`）

- [ ] **Step 3: 实现**

`paper.py` `run_paper_settle_daily` 签名改 `def run_paper_settle_daily(as_of: str | None = None, *, execution_db_path: str | Path | None = None) -> dict[str, Any]:`——函数内 `latest_snapshot_date()` 不动；`list_positions()` 改
`list_positions(db_path=execution_db_path)`；`as_of` 允许显式传入（缺省仍取快照最新日）。
文件尾部加：

```python
def _microcap_execution_db() -> Path:
    """小票账本路径（调用期读 env；add-microcap-lane-analysis）。"""
    return Path(os.getenv("EXECUTION_MICROCAP_DB_PATH", "data/execution_microcap.db"))


def run_paper_settle_microcap(as_of: str | None = None) -> dict[str, Any]:
    """ops 日批入口（小票账本结算）：与主档同口径，写独立库 paper_equity。"""
    db = _microcap_execution_db()
    snapshot_db = None  # 快照库与主档共用（默认 env）
    from finance_agent.data.snapshot import latest_snapshot_date, read_bars

    eff_as_of = as_of or latest_snapshot_date(snapshot_db)
    if eff_as_of is None:
        return {"trade_date": None, "cash": None, "positions_value": None, "equity": None, "reason": "no_snapshot_data"}
    positions = list_positions(db_path=db)
    close_prices: dict[str, float] = {}
    if positions:
        codes = sorted({_snapshot_code(str(p["symbol"])) for p in positions})
        frame = read_bars(codes, eff_as_of, eff_as_of, as_of=eff_as_of)
        by_code = {
            str(row["code"]): price
            for row in frame[["code", "close_raw"]].to_dict("records")
            if (price := _valid_close(row["close_raw"])) is not None
        }
        close_prices = {
            str(p["symbol"]): by_code[code]
            for p in positions
            if (code := _snapshot_code(str(p["symbol"]))) in by_code
        }
    return {**settle_daily(eff_as_of, close_prices=close_prices, db_path=db), "reason": None}
```

（实现时对照 `run_paper_settle_daily` 原函数逐行同构——它就是模板。）

`driver.py` 尾部加：

```python
def run_execution_microcap(
    as_of: str | None = None,
    *,
    db_path: str | Path | None = None,
    snapshot_db_path: str | Path | None = None,
    regime_db_path: str | Path | None = None,
    window_days: int = _DEFAULT_BACKLOG_WINDOW_DAYS,
) -> dict[str, Any]:
    """ops 日批入口（小票账本，add-microcap-lane-analysis）：独立库 + 小票经济学。

    与主档差异仅三处：lane='microcap'、单票上限 5%、滑点档 +1；其余语义
    （次一交易日开盘 / 幂等 / 快照守卫 / regime）与主档一致。
    """
    from finance_agent.constraints import (
        MICROCAP_SLIPPAGE_TIER_BUMP,
        MICROCAP_SUGGESTED_SINGLE_CAP,
    )

    return run_execution_daily(
        as_of,
        db_path=db_path,
        execution_db_path=os.getenv("EXECUTION_MICROCAP_DB_PATH", "data/execution_microcap.db"),
        snapshot_db_path=snapshot_db_path,
        regime_db_path=regime_db_path,
        window_days=window_days,
        lane="microcap",
        single_cap=MICROCAP_SUGGESTED_SINGLE_CAP,
        slippage_tier_bump=MICROCAP_SLIPPAGE_TIER_BUMP,
    )
```

- [ ] **Step 4: 跑绿 + 回归 + 提交**

Run: `uv run pytest tests/execution/ -v` → 全绿

```bash
git add src/finance_agent/execution/ tests/execution/test_microcap_lane_runner.py
git commit -m "feat(execution): 小票账本执行/结算封装（第二 DB + 5% + tier_bump=1）"
```

---

### Task 5: 小票池每日自动分析编排

**Files:**
- Modify: `src/finance_agent/screening/pool_analysis.py`（`prepare_pool_analysis_batch` 参数化 + 新增 `run_pool_analysis_microcap_daily` / `run_pool_analysis_microcap`）
- Test: `tests/screening/test_pool_analysis_microcap.py`（新建）

**Interfaces:**
- Produces: `prepare_pool_analysis_batch(*, as_of=None, max_symbols=None, enqueue=None, db_path=None, screening_db_path=None, pool="main")`；`run_pool_analysis_microcap_daily()`（开关 + env 上限解析 + `_submit(pool="microcap", max_symbols=...)`）；`api.submit_pool_analysis_batch(*, max_symbols=None, pool="main", timeout=120.0)`
- Consumes: Task 1 的 `POOL_MICROCAP` / pool 列；`latest_successful_pool(pool=...)`（pipeline.py:729 已有该参数，先核签名）
- 桥接：`api.submit_pool_analysis_batch` 加 `pool` 形参透传 `prepare_pool_analysis_batch`

- [ ] **Step 1: 写失败测试（跨池去重 + 顺延补位）**

```python
"""小票池自动分析编排（add-microcap-lane-analysis T5）。"""
from finance_agent.screening.pool_analysis import (
    POOL_MICROCAP,
    prepare_pool_analysis_batch,
    record_pool_analysis_run,
)


class _FakePool:
    """latest_successful_pool 的桩：返回固定 microcap 池。"""

    def __init__(self, rows):
        self.rows = rows

    def __call__(self, *, db_path=None, pool="main"):
        return (
            {"batch": {"trade_date": "2026-11-10"}, "pool": self.rows}
            if pool == POOL_MICROCAP
            else None
        )


def _rows(symbols):
    return [
        {"symbol": s, "rank": i + 1, "stock_name": f"票{s}"}
        for i, s in enumerate(symbols)
    ]


def test_skip_analyzed_and_fill(monkeypatch, tmp_path):
    db = tmp_path / "screening.db"
    # 当日主档已分析 002040（全局幂等行）
    record_pool_analysis_run("2026-11-10", "002040", "s-main", "queued", db_path=db)
    rows = _rows(["002040", "002749", "002852", "301058"])
    monkeypatch.setattr(
        "finance_agent.screening.pool_analysis.latest_successful_pool", _FakePool(rows)
    )
    enqueue = _EnqueueSpy()
    result = prepare_pool_analysis_batch(
        as_of="2026-11-10", max_symbols=2, enqueue=enqueue, screening_db_path=db, db_path=db, pool=POOL_MICROCAP
    )
    # 002040 跳过（主档行在），002749/002852 按序补位
    assert result["selected"] == 2
    assert result["started_symbols"] == ["002749", "002852"]
    assert result["skipped_existing"] == 1


class _EnqueueSpy:
    def __init__(self):
        self.calls = []

    def __call__(self, symbol, session_id):
        self.calls.append(symbol)
        return True


def test_fill_insufficient_disclosed(monkeypatch, tmp_path):
    db = tmp_path / "screening.db"
    record_pool_analysis_run("2026-11-10", "002040", "s-main", "queued", db_path=db)
    record_pool_analysis_run("2026-11-10", "002749", "s-mc", "queued", pool=POOL_MICROCAP, db_path=db)
    rows = _rows(["002040", "002749", "002852"])
    monkeypatch.setattr(
        "finance_agent.screening.pool_analysis.latest_successful_pool", _FakePool(rows)
    )
    result = prepare_pool_analysis_batch(
        as_of="2026-11-10", max_symbols=10, enqueue=_EnqueueSpy(), screening_db_path=db, db_path=db, pool=POOL_MICROCAP
    )
    assert result["selected"] == 1  # 只剩 002852 可选，如实披露
```

（stale_pool 分支复用既有主档测试的模式——先看
`grep -n "skipped_stale_pool" tests/screening/test_pool_analysis*.py`，把既有
`_accept_batch_date` 的 monkeypatch 方式照搬。）

- [ ] **Step 2: 跑红**

Run: `uv run pytest tests/screening/test_pool_analysis_microcap.py -v`
Expected: FAIL（`prepare_pool_analysis_batch() got an unexpected keyword argument 'pool'`）

- [ ] **Step 3: 实现**

`prepare_pool_analysis_batch` 签名加 `*, pool: str = POOL_MAIN`；函数内三处：

```python
    successful = latest_successful_pool(db_path=screening_db_path, pool=pool)
```

选样改「跳过 + 顺延补位」（原 `selected = sorted(...)[:cap]` 替换为）：

```python
    ordered = sorted(
        pool,
        key=lambda row: (int(row.get("rank") or 10**9), str(row.get("symbol") or "")),
    )
    selected: list[dict[str, Any]] = []
    for row in ordered:
        symbol = str(row.get("symbol") or "").strip()
        if not symbol:
            continue
        if get_pool_analysis_run(day, symbol, db_path=db_path) is not None:
            continue  # 当日任意批次已建行（queued/completed/failed 重派除外，下方处理）
        selected.append(row)
        if len(selected) >= cap:
            break
    result["selected"] = len(selected)
```

（注意：原实现先截断后查 existing；新实现边扫边查——failed 行的重派语义由下方
循环里 `existing.get("status") not in ("queued", "completed")` 分支保留，但选样阶段
把 failed 行也排除在补位外吗？**不**——failed 行应当可重派。修正：选样查询放行
failed 类行。为保持简单：选样阶段查 `get_pool_analysis_run`，若行 status ∈
{queued, completed} 则跳过，否则（failed/dispatch_failed/session_failed/None）入选，
循环体内既有重派逻辑照旧。上面代码片段改为相应 if 判断——实施者按此语义写测试。）

display_name 后缀按 pool 切换：

```python
    _DISPLAY_SUFFIX = POOL_MICROCAP_SUFFIX if pool == POOL_MICROCAP else AUTO_POOL_DISPLAY_SUFFIX
```

模块常量 `AUTO_POOL_MICROCAP_DISPLAY_SUFFIX = "小票池自动分析"`；`source=` 传
`"auto_pool" if pool == POOL_MAIN else "auto_pool_microcap"`；每条
`record_pool_analysis_run(...)` 调用加 `pool=pool`。

文件尾部加 ops 入口（模式照抄 `run_pool_analysis_daily`）：

```python
def _resolve_microcap_max_symbols(max_symbols: int | None) -> int:
    if max_symbols is None:
        raw = os.getenv("AUTO_POOL_MICROCAP_MAX_SYMBOLS", "10")
        try:
            value = int(raw)
        except (TypeError, ValueError):
            logger.warning("AUTO_POOL_MICROCAP_MAX_SYMBOLS 非法（%r），回退 10", raw)
            value = 10
        max_symbols = value
    return max(1, min(MAX_SYMBOLS_CAP, int(max_symbols)))


def run_pool_analysis_microcap_daily() -> dict[str, Any]:
    """日批入口（小票池，add-microcap-lane-analysis）：开关解析后提交 API 主循环。"""
    if os.getenv("AUTO_POOL_MICROCAP_ENABLED", "1").strip() == "0":
        return {"status": "skipped_disabled"}
    return _submit(pool=POOL_MICROCAP, max_symbols=_resolve_microcap_max_symbols(None))
```

`api.py` `submit_pool_analysis_batch`（3943）签名加 `pool: str = POOL_MAIN`，内部
`prepare_pool_analysis_batch` 调用透传；`_run()` 的 `asyncio.to_thread` 参数同步加。

- [ ] **Step 4: 跑绿 + 回归 + 提交**

Run: `uv run pytest tests/screening/ -v` → 全绿

```bash
git add src/finance_agent/screening/pool_analysis.py src/finance_agent/api.py tests/screening/test_pool_analysis_microcap.py
git commit -m "feat(screening): 小票池自动分析编排（跨池去重跳过+顺延补位，pool 参数化）"
```

---

### Task 6: ops 注册三个新 job + scheduler 接线

**Files:**
- Modify: `src/finance_agent/outcome/ops/jobs.py`（`JOB_IDS` / `JOB_LABELS` / `JOB_FUNCS` / `SCHEDULES` 四张表）、`src/finance_agent/outcome/scheduler.py`（三个 `_xxx_job` + `add_job` + 启动日志）
- Test: `tests/outcome/test_microcap_jobs_registry.py`（新建）

**Interfaces:**
- Produces: job_id `paper_execute_microcap`（16:12）/ `paper_settle_microcap`（16:24）/ `auto_pool_analysis_microcap`（16:47）；四个注册表各含三条新目
- Consumes: Task 4 的 `run_execution_microcap` / `run_paper_settle_microcap`；Task 5 的 `run_pool_analysis_microcap_daily`
- 时序锚点：16:12 插 `paper_execute_daily` 与 `earnings_triage_daily` 之间；16:24 插 `auto_pool_analysis_daily`（16:22）与 `microcap_screening_daily`（16:45）之间；16:47 插 `microcap_screening_daily` 之后

- [ ] **Step 1: 写失败测试**

```python
"""三个新 job 的注册表接线（add-microcap-lane-analysis T6）。"""
from finance_agent.outcome.ops.jobs import JOB_FUNCS, JOB_IDS, JOB_LABELS, SCHEDULES


def test_job_ids_registered():
    for job in ("paper_execute_microcap", "paper_settle_microcap", "auto_pool_analysis_microcap"):
        assert job in JOB_IDS
        assert job in JOB_LABELS
        assert job in JOB_FUNCS
        assert job in SCHEDULES


def test_schedule_times():
    assert SCHEDULES["paper_execute_microcap"]["hour"] == 12
    assert SCHEDULES["paper_settle_microcap"]["hour"] == 16
    assert SCHEDULES["paper_settle_microcap"]["minute"] == 24
    assert SCHEDULES["auto_pool_analysis_microcap"]["hour"] == 16
    assert SCHEDULES["auto_pool_analysis_microcap"]["minute"] == 47


def test_job_ids_order_ascending():
    """日批链顺序断言：16:12 在 16:10 与 16:15 之间，16:24 在 16:22 与 16:45 之间。"""
    def pos(job):
        return JOB_IDS.index(job)

    assert pos("paper_execute_daily") < pos("paper_execute_microcap") < pos("earnings_triage_daily")
    assert pos("auto_pool_analysis_daily") < pos("paper_settle_microcap") < pos("microcap_screening_daily")
    assert pos("microcap_screening_daily") < pos("auto_pool_analysis_microcap")
```

（若 `JOB_IDS` 存在既有的顺序断言测试文件——先
`grep -rn "JOB_IDS.index\|JOB_IDS) ==" tests/outcome/`——新顺序必须同时满足旧断言；
若旧断言要求相邻关系被打破，按旧断言的意图（时刻升序）调整插入位置并在提交信息
注明。）

- [ ] **Step 2: 跑红**

Run: `uv run pytest tests/outcome/test_microcap_jobs_registry.py -v`
Expected: FAIL（assert job in JOB_IDS）

- [ ] **Step 3: 实现**

`ops/jobs.py` 四张表按上述时序插入（`JOB_LABELS` 值：`"小票执行"` / `"小票结算"` / `"小票池自动分析"`；`JOB_FUNCS` 映射到 Task 4/5 函数——import 加到文件头既有 import 区）：

```python
    "paper_execute_microcap": {
        "day_of_week": "mon-fri", "hour": 16, "minute": 12, "timezone": "Asia/Shanghai",
    },
    "paper_settle_microcap": {
        "day_of_week": "mon-fri", "hour": 16, "minute": 24, "timezone": "Asia/Shanghai",
    },
    "auto_pool_analysis_microcap": {
        "day_of_week": "mon-fri", "hour": 16, "minute": 47, "timezone": "Asia/Shanghai",
    },
```

`scheduler.py`：仿 `_microcap_lane_job` 加三个 `_xxx_job`（`_with_retry(job_id, "中文名", func)`）；`start_scheduler` 内按既有 `add_job` 模式注册三处（id / `_cron_for(job_id)` / `replace_existing=True` / `**_JOB_MISFIRE_KW`）；启动日志字符串加
`"paper_execute_microcap 16:12;paper_settle_microcap 16:24;auto_pool_analysis_microcap 16:47;"`。

- [ ] **Step 4: 跑绿 + 回归 + 提交**

Run: `uv run pytest tests/outcome/ -v` → 全绿

```bash
git add src/finance_agent/outcome/ tests/outcome/test_microcap_jobs_registry.py
git commit -m "feat(ops): 注册小票执行/结算/自动分析三 job（16:12/16:24/16:47）"
```

---

### Task 7: 既有消费者兼容修复（daily_brief + report_trigger）

**Files:**
- Modify: `src/finance_agent/outcome/daily_brief.py:127`（主档行过滤 + 新增小票行输入）、`src/finance_agent/screening/report_trigger.py:205`（`_today_used` 过滤）
- Test: `tests/outcome/test_daily_brief_microcap.py`、`tests/screening/test_report_trigger_budget.py`（新建）

**Interfaces:**
- Consumes: Task 1 的 pool 列与 `list_pool_analysis_runs(pool=...)`
- Produces: 日报输入含 `microcap_line`（池规模/分析数/跳过数/派发失败/小票净值——净值取
  `data/execution_microcap.db` 的 `paper_equity` 最新行，无表/无行 → None → prompt 侧写「无数据」）；`_today_used` 仅计 main

- [ ] **Step 1: 写失败测试**

```python
"""日报主档行隔离 + 预算过滤（add-microcap-lane-analysis T7）。"""
from finance_agent.screening.pool_analysis import (
    POOL_MICROCAP,
    record_pool_analysis_run,
)


def test_today_used_ignores_microcap(tmp_path, monkeypatch):
    db = tmp_path / "screening.db"
    for i in range(8):
        record_pool_analysis_run("2026-11-10", f"60000{i}", "s", "queued", db_path=db)
    for i in range(10):
        record_pool_analysis_run(
            "2026-11-10", f"00274{i}", "s", "queued", pool=POOL_MICROCAP, db_path=db
        )
    monkeypatch.setenv("SCREENING_DB_PATH", str(db))
    from finance_agent.screening.report_trigger import _today_used

    assert _today_used("2026-11-10", db) == 8  # 仅 main 行


def test_brief_runs_query_pools_split(tmp_path):
    """daily_brief 的运行行读取：主档行与 microcap 行分开统计。"""
    db = tmp_path / "screening.db"
    record_pool_analysis_run("2026-11-10", "600001", "s1", "queued", db_path=db)
    record_pool_analysis_run("2026-11-10", "002749", "s2", "queued", pool=POOL_MICROCAP, db_path=db)
    from finance_agent.screening.pool_analysis import list_pool_analysis_runs

    assert len(list_pool_analysis_runs("2026-11-10", pool="main", db_path=db)) == 1
    assert len(list_pool_analysis_runs("2026-11-10", pool=POOL_MICROCAP, db_path=db)) == 1
```

- [ ] **Step 2: 跑红**

Run: `uv run pytest tests/outcome/test_daily_brief_microcap.py tests/screening/test_report_trigger_budget.py -v`
Expected: `_today_used` 断言 FAIL（当前返回 18）

- [ ] **Step 3: 实现**

`report_trigger.py` `_today_used` 内两条 SQL 加 ` AND pool='main'`：

```python
            pool_rows = conn.execute(
                "SELECT COUNT(*) FROM pool_analysis_runs WHERE trade_date=? AND pool='main'",
                (day,),
            ).fetchone()[0]
```

`daily_brief.py:127` 的运行行查询改 `... WHERE trade_date = ? AND pool = 'main' ORDER BY symbol`；紧随其后加小票行查询（microcap 行 + 小票账本净值）：

```python
    microcap_runs: list[dict[str, Any]] = []
    try:
        raw_mc = _read(
            path,
            "SELECT symbol, session_id, status, error FROM pool_analysis_runs "
            "WHERE trade_date = ? AND pool = 'microcap' ORDER BY symbol",
            (as_of,),
        )
        microcap_runs = [
            {
                "symbol": row.get("symbol"),
                "session_id": row.get("session_id"),
                "dispatch_status": "enqueued" if row.get("status") == "queued" else row.get("status"),
                "error": row.get("error"),
            }
            for row in raw_mc
        ]
    except sqlite3.Error as exc:
        logger.warning("读取小票运行行失败（%s）", exc)
    microcap_equity = _read_microcap_equity()  # 新助手：读 execution_microcap.db 最新 paper_equity 行，无 → None
```

`_read_microcap_equity` 实现（同文件，模式照抄 `_read`）：

```python
def _read_microcap_equity() -> dict[str, Any] | None:
    """小票账本最新净值（add-microcap-lane-analysis）；库/表缺失 → None（日报写「无数据」）。"""
    path = Path(os.getenv("EXECUTION_MICROCAP_DB_PATH", "data/execution_microcap.db"))
    if not path.exists():
        return None
    try:
        rows = _read(
            path,
            "SELECT trade_date, cash, positions_value, equity FROM paper_equity "
            "ORDER BY trade_date DESC LIMIT 1",
            (),
        )
        return rows[0] if rows else None
    except sqlite3.Error as exc:
        logger.warning("读取小票净值失败（%s）", exc)
        return None
```

（`_read` 的签名以 `daily_brief.py` 现有定义为准——先读再套。）小票行数据并入
brief 组装 dict（找到主档 runs 传入 prompt 组装的位置，加 `microcap_runs` /
`microcap_equity` 同级键；prompt 模板加一句「小票池自动分析（lane=microcap）：
…无数据时写无数据」——prompt 文件在 `src/finance_agent/prompts/`，先定位 daily_brief
对应 prompt 再最小追加，**不改既有段落**）。

- [ ] **Step 4: 跑绿 + 回归 + 提交**

Run: `uv run pytest tests/outcome/ tests/screening/ -v` → 全绿

```bash
git add src/finance_agent/outcome/daily_brief.py src/finance_agent/screening/report_trigger.py tests/outcome/test_daily_brief_microcap.py tests/screening/test_report_trigger_budget.py src/finance_agent/prompts/
git commit -m "fix(daily-brief/report-trigger): 主档口径 pool='main' 过滤 + 日报小票行"
```

---

### Task 8: execution 只读端点 `book` 参数

**Files:**
- Modify: `src/finance_agent/api.py:5506`（`/api/broker/positions`）、`:5512`（`/api/broker/orders`）及同区 cash/equity 端点（`grep -n "@app.get" src/finance_agent/api.py | sed -n '/broker/p'` 全列）
- Test: `tests/api/test_broker_book_param.py`（新建，模式照抄既有 broker 端点测试——先 `grep -rln "api/broker" tests/`）

**Interfaces:**
- Produces: 端点签名 `async def broker_positions(book: str = "main")` 等；book ∈ {main, microcap}，非法 422（raise HTTPException(422, ...)）；`_broker_db(book) -> Path` 助手
- Consumes: Task 4 的 env `EXECUTION_MICROCAP_DB_PATH`

- [ ] **Step 1: 写失败测试**

```python
"""execution 只读端点 book 参数（add-microcap-lane-analysis T8）。"""
import sqlite3

from finance_agent.execution.store import EXECUTION_DDL


def test_book_param_rejects_invalid(client):
    resp = client.get("/api/broker/positions", params={"book": "foo"})
    assert resp.status_code == 422


def test_book_microcap_reads_second_db(client, tmp_path, monkeypatch):
    exec_db = tmp_path / "exec-mc.db"
    conn = sqlite3.connect(exec_db)
    conn.executescript(EXECUTION_DDL)
    conn.commit()
    conn.close()
    monkeypatch.setenv("EXECUTION_MICROCAP_DB_PATH", str(exec_db))
    resp = client.get("/api/broker/positions", params={"book": "microcap"})
    assert resp.status_code == 200
    assert resp.json() == []  # 空账本返回空列表，不报错
```

（`client` fixture 以既有 API 测试的 conftest 为准——先看
`grep -rn "TestClient\|client" tests/api/conftest.py | head -5`。）

- [ ] **Step 2: 跑红 → Step 3: 实现**

```python
def _broker_db(book: str) -> Path:
    if book == "microcap":
        return Path(os.getenv("EXECUTION_MICROCAP_DB_PATH", "data/execution_microcap.db"))
    if book == "main":
        return Path(os.getenv("EXECUTION_DB_PATH", "data/execution.db"))
    raise HTTPException(status_code=422, detail=f"book 非法: {book!r}（须 main/microcap）")
```

各 broker 只读端点签名加 `book: str = "main"`，函数内 `PaperBroker()` /
`PaperBroker(db_path=...)` 改 `PaperBroker(db_path=_broker_db(book))`（**缺省路径显式化**，
与既有缺省 env 一致）。净值曲线端点（`/api/v1/track-record/equity-curve` 若走主库
paper_equity，则按同一模式加 book 参数读第二库；先核该端点当前读哪个库——grep
`paper_equity` in api.py——若它读 track_record 库而非执行库，本任务不动它，小票净值
由 Task 7 日报行披露）。

- [ ] **Step 4: 跑绿 + 提交**

Run: `uv run pytest tests/api/test_broker_book_param.py -v` → PASS

```bash
git add src/finance_agent/api.py tests/api/test_broker_book_param.py
git commit -m "feat(api): broker 只读端点 book=microcap 参数（缺省 main 不变，非法 422）"
```

---

### Task 9: 全量回归 + 静态检查 + 首跑验证清单

**Files:**
- Test: 全量；报告: `tests/validation/2026-11-XX-microcap-lane-first-run.md`（部署后人工填）

- [ ] **Step 1: 全量测试**

Run: `uv run pytest -v`（worktree 根；Langfuse 未起时先 `docker compose up -d langfuse`，见 memory「全量 pytest 卡 2% 先查 Docker/Langfuse」）
Expected: 全绿——**主档回归红线**：任何主档用例红即停止、修复、不得绕过

- [ ] **Step 2: 静态检查**

Run: `uv run ruff check && uv run ruff format --check && uv run mypy`
Expected: 0 错误（format 不符先 `uv run ruff format` 再 diff 复查）

- [ ] **Step 3: 主档摘要逐字段比对**

用 Task 1-8 前的 fixture 回放：对同一份合成快照+观点库，`run_screening_daily` /
`run_execution_daily`（lane 默认）/ `run_paper_settle_daily` 的摘要 dict 在
`git stash` 前后逐字段一致（写一次性脚本 `tests/scripts/compare_main_baseline.py`
对比，输出入验证报告）。

- [ ] **Step 4: 提交 + 首跑清单**

```bash
git add tests/scripts/compare_main_baseline.py
git commit -m "test(execution): 主档基线逐字段比对脚本"
```

首跑清单（部署后填入 `tests/validation/`）：
1. 次一交易日 16:12 `paper_execute_microcap` ok 且 `signals_created=0`（首日空转）
2. 16:47 `auto_pool_analysis_microcap` ok：selected=10、跳过数=主档前10∩小票前10
3. `pool_analysis_runs` 出现 `pool='microcap'` 行；主档行 pool='main'
4. 次日 16:12 小票账本产生信号与订单（5% 上限、滑点档 +1 生效）；主档库 mtime 无小票写入痕迹
5. 20:30 日报含小票行、主档行统计=10 不混入
6. `data/execution_microcap.db` 文件存在；ops 巡检/备份脚本覆盖确认（不覆盖则补脚本）

- [ ] **Step 5: PR 准备（不 push，由用户决定时机）**

`git log --oneline origin/main..HEAD` 确认提交链干净；PR 描述引用 delta
`add-microcap-lane-analysis` 与设计文档；**merge 后手动核关**（仓库纪律：无 merge
commit、须 PR）。

---

## Self-Review 记录

- **Spec 覆盖**：delta 六份 spec 的每个 Scenario → T1（pool 列 2 场景）、T2（lane 4 场景）、T3+T4（执行 9 场景）、T5（编排 4+2 场景）、T7（日报 2 场景 + 预算 1 场景）、T8（book 3 场景）✓；「小票池每日自动分析」的 stale_pool/失败重派场景 → T5 测试说明引用既有主档测试模式 ✓
- **占位符扫描**：无 TBD/TODO；两处「以现有代码为准」的定位指令（MatchingConfig 构造、`_read` 签名）均为「先读后写」的显式步骤，非占位
- **类型一致性**：`pool`/`lane` 字符串常量统一来自 `POOL_MAIN`/`POOL_MICROCAP` 与 DDL 默认 `'main'`；`tier_bump` 全链 int；`single_cap` 全链 float（constraints 导入）✓
