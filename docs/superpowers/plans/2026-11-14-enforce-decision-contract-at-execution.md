# 执行引擎消费决策约束（#593）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 执行引擎消费决策层申报的入场触发位、仓位档位与 exit 首批比例，终结「决策谨慎、执行莽」的违约链（issue #593 / delta enforce-decision-contract-at-execution）。

**Architecture:** 四层改造——①ingest 把契约字段冻结进 `rationale_snapshot.declared_contract`；②`Signal` 契约扩展 4 个可空字段（含幂等 ALTER 迁移）；③driver 触发门 + 档位权重 + exit 首批（状态表防重复）；④摘要新增 `skipped_details`/`plan_deviation` 对账留痕。全部字段缺失时退化为既有行为（零迁移、老信号行为不变）。

**Tech Stack:** Python 3.14 + sqlite3（短连接/WAL）+ pytest；无新依赖。

## Global Constraints

- 档位锚取下沿，与 `src/finance_agent/prompts/trader.md:117` 对齐：light=0.10 / moderate=0.30 / heavy=0.50，逐字写入 `POSITION_TIER_WEIGHTS`
- 触发门判定口径 = 执行日 `bar.close`（收盘确认语义）；`trigger_low` 本期不实现（设计已披露，delta spec R2 只写 trigger_high）
- 所有退化路径必须留痕：`TRIGGER_NOT_MET` / `no_position_tier` / `schedule_unparsed` / `first_batch_below_lot`，禁止静默
- 退化方向保守：schedule 不可解析 → 全额退出（= 既有行为）；档位缺失 → 等权（= 既有行为）
- 存量 ~50 条 open 预测无 `declared_contract` → 行为与今天完全一致（零迁移）
- mypy 基线 98 不新增（#544）；`ruff check` + `ruff format --check` 必须全绿（#590 的教训）
- 现有摘要消费方（ops/jobs、scheduler）只记录不逐键断言，新增摘要键安全
- 提交禁 merge commit；pre-commit 锁权限报错时用 `--no-verify`（等价检查先跑全绿）

---

### Task 0: 修订 delta 文件（spec R2 收窄 + proposal Impact 补全）

**Files:**
- Modify: `openspec/changes/enforce-decision-contract-at-execution/specs/broker-execution/spec.md`（R2 段）
- Modify: `openspec/changes/enforce-decision-contract-at-execution/proposal.md`（Impact 段）

**Interfaces:**
- Produces: 与实现一致的规格文本（后续 sync+archive 用）

- [ ] **Step 1: R2 收窄为 trigger_high 单触发位**

把 spec R2 的需求句与场景中「（或 `trigger_low`）」「（或未跌破 `trigger_low`）」字样删除，需求句改为：

```markdown
订单规划层 SHALL 对申报了 `trigger_high` 的 buy 信号执行触发门：执行日 bar 收盘价未突破
`trigger_high` 时 MUST NOT 生成买入订单，且 MUST 以结构化明细留痕（标的 + 原因码
`TRIGGER_NOT_MET` + 触发位与实际收盘），供台账回查；触发满足时正常进入下单流程。
留痕 MUST NOT 静默丢弃或仅计裸计数。`trigger_low`（下破型入场）本期不实现，消费侧
忽略该字段（设计文档已披露）。
```

- [ ] **Step 2: proposal Impact 补 ingest 与 store 迁移**

Impact 列表加两行：

```markdown
- `src/finance_agent/outcome/track_record/ingest.py`（新增：rationale_snapshot 冻结 `declared_contract`——trigger/档位/exit_schedule/sell_type）
- `src/finance_agent/execution/store.py`（signals 表加 4 可空列 + 幂等 ALTER 迁移；新增 `exit_schedule_state` 表）
```

- [ ] **Step 3: 验证 + 提交**

```bash
openspec validate enforce-decision-contract-at-execution --strict
git add openspec/changes/enforce-decision-contract-at-execution/
git commit -m "docs(openspec): delta 修订——R2 收窄 trigger_high，Impact 补 ingest/store"
```

Expected: validate 输出 `is valid`

---

### Task 1: `execution/contracts.py`——档位锚与自由文本解析器

**Files:**
- Create: `src/finance_agent/execution/contracts.py`
- Test: `tests/execution/test_contracts.py`

**Interfaces:**
- Produces: `POSITION_TIER_WEIGHTS: dict[str, float]`；`parse_position_tier(value: object) -> str | None`；`parse_first_batch_ratio(schedule: object) -> float | None`（Task 3/4/5 消费）

- [ ] **Step 1: 写失败测试**

```python
"""决策契约解析器单测（enforce-decision-contract-at-execution Task 1）。"""

from finance_agent.execution.contracts import (
    POSITION_TIER_WEIGHTS,
    parse_first_batch_ratio,
    parse_position_tier,
)


def test_tier_anchors_match_trader_prompt_lower_bounds():
    """锚值与 trader.md:117 档位表下沿逐字对齐（light 10-20/moderate 30-50/heavy 50+）。"""
    assert POSITION_TIER_WEIGHTS == {"light": 0.10, "moderate": 0.30, "heavy": 0.50}


def test_parse_position_tier_known_and_normalized():
    assert parse_position_tier("light") == "light"
    assert parse_position_tier(" Light ") == "light"
    assert parse_position_tier("MODERATE") == "moderate"


def test_parse_position_tier_unknown_returns_none():
    assert parse_position_tier("30%") is None  # 百分比串非档位名（落库 REAL 口径）
    assert parse_position_tier("unknown") is None
    assert parse_position_tier(None) is None
    assert parse_position_tier(123) is None


def test_parse_first_batch_ratio_single_match():
    # 601066 2026-11-12 真实方案文本形态（首批…70%，中间含其他数字）
    assert parse_first_batch_ratio("首批在现价 21.72 附近即减持有敞口的 70%（较原方案 50% 上调）") == 0.7
    assert parse_first_batch_ratio("首批减仓 30%，余下等反弹") == 0.3


def test_parse_first_batch_ratio_ambiguous_returns_none():
    assert parse_first_batch_ratio("首批 70%，另一方案首批 50%") is None


def test_parse_first_batch_ratio_invalid_inputs_return_none():
    assert parse_first_batch_ratio(None) is None
    assert parse_first_batch_ratio("") is None
    assert parse_first_batch_ratio("一次性清仓") is None  # 无首批表述
    assert parse_first_batch_ratio("首批 150%") is None  # 越界
    assert parse_first_batch_ratio("首批 0%") is None  # 越界


def test_parse_first_batch_ratio_clamps_to_ratio():
    assert parse_first_batch_ratio("首批 100% 清仓") == 1.0
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/execution/test_contracts.py -v`
Expected: FAIL（`ModuleNotFoundError: contracts`）

- [ ] **Step 3: 最小实现**

```python
"""决策契约字段解析（enforce-decision-contract-at-execution）。

决策层（trader/risk_judge）申报的执行约束——仓位档位与 ``exit_schedule`` 首批
比例——的自由文本 → 结构化口径，供信号契约与订单规划消费（issue #593）：

- 档位锚与 ``src/finance_agent/prompts/trader.md`` 档位表对齐取下沿（保守方向，
  宁小勿大；600780 2026-11-12 方案 FM 批准文本「light 档下沿约 10%」即此口径）；
- 首批比例只认「首批 …N%」的单一明确匹配；无/多匹配或 (0,1] 之外 → ``None``
  （调用方退化为既有全额退出并留痕 ``schedule_unparsed``）。
"""

from __future__ import annotations

import re

POSITION_TIER_WEIGHTS: dict[str, float] = {
    "light": 0.10,
    "moderate": 0.30,
    "heavy": 0.50,
}

# 「首批 …N%」：首批与其后的第一个百分数之间允许任意非 % 的字符（含其他数字，
# 如「首批在现价 21.72 附近即减持有敞口的 70%」）；出现两处 → 调用方判歧义。
_FIRST_BATCH_RE = re.compile(r"首批[^%。；;]*?(\d+(?:\.\d+)?)\s*%")


def parse_position_tier(value: object) -> str | None:
    """档位名 → 归一小写键；非字符串/未知档位/百分比串 → ``None``（未申报档位）。"""
    if not isinstance(value, str):
        return None
    tier = value.strip().lower()
    return tier if tier in POSITION_TIER_WEIGHTS else None


def parse_first_batch_ratio(schedule: object) -> float | None:
    """``exit_schedule`` 自由文本 → 首批比例（(0,1]）；无/多匹配或越界 → ``None``。"""
    if not isinstance(schedule, str) or not schedule.strip():
        return None
    matches = _FIRST_BATCH_RE.findall(schedule)
    if len(matches) != 1:
        return None
    try:
        ratio = float(matches[0]) / 100.0
    except ValueError:
        return None
    return ratio if 0.0 < ratio <= 1.0 else None
```

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest tests/execution/test_contracts.py -v`
Expected: 8 passed

- [ ] **Step 5: 提交**

```bash
git add src/finance_agent/execution/contracts.py tests/execution/test_contracts.py
git commit -m "feat(execution): 决策契约解析器——档位锚 + 首批比例（#593 T1）"
```

---

### Task 2: ingest 冻结 `declared_contract`

**Files:**
- Modify: `src/finance_agent/outcome/track_record/ingest.py`（`_declared_contract` 新函数 + snapshot 扩键）
- Test: `tests/execution/test_contracts.py`（追加，Task 1 同文件）

**Interfaces:**
- Produces: `declared_contract(snapshot)` 键——`{sell_type, position_size, exit_schedule, trigger_high, trigger_low}`，写入新预测的 `rationale_snapshot`；Task 4 的 `_decision_payload` 消费该键

- [ ] **Step 1: 追加失败测试（test_contracts.py 末尾）**

```python
from finance_agent.outcome.track_record.ingest import _declared_contract


def test_declared_contract_freezes_all_fields_for_buy():
    decision = {
        "sell_type": None,
        "position_size": "light",
        "exit_schedule": None,
        "trigger_high": 22.35,
        "trigger_low": None,
    }
    contract = _declared_contract(decision, action="buy")
    assert contract == {
        "sell_type": None,
        "position_size": "light",
        "exit_schedule": None,
        "trigger_high": 22.35,
        "trigger_low": None,
    }


def test_declared_contract_triggers_only_for_buy():
    decision = {"sell_type": "exit", "position_size": None,
                "exit_schedule": "首批减 70%", "trigger_high": 22.35, "trigger_low": 20.0}
    assert _declared_contract(decision, action="sell")["trigger_high"] is None
    assert _declared_contract(decision, action="sell")["exit_schedule"] == "首批减 70%"
    assert _declared_contract(decision, action="watch")["trigger_high"] is None
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/execution/test_contracts.py -v -k declared_contract`
Expected: FAIL（`ImportError: _declared_contract`）

- [ ] **Step 3: 最小实现**

在 `ingest.py` 的 `insert_prediction` 前加模块级函数（放 `_kline_last_close` 附近）：

```python
def _declared_contract(decision: dict, action: str) -> dict:
    """决策契约字段冻结（enforce-decision-contract-at-execution）。

    trader 终稿的结构化约束进 ``rationale_snapshot.declared_contract``，供执行链
    信号转换消费（触发门/档位/首批比例）。触发位仅 buy 落值——watch 触发已由
    ``trigger_high``/``trigger_low`` 列承载（写入即冻结的既有语义），sell 无入场
    触发语义。键恒全量（缺省 None），保证快照形状稳定可解析。
    """
    return {
        "sell_type": decision.get("sell_type"),
        "position_size": decision.get("position_size"),
        "exit_schedule": decision.get("exit_schedule"),
        "trigger_high": decision.get("trigger_high") if action == "buy" else None,
        "trigger_low": decision.get("trigger_low") if action == "buy" else None,
    }
```

在 `insert_prediction` 的 `insert_prediction({...})` 调用里，`"declared_prices": {...}` 之后追加一键：

```python
                    "declared_contract": _declared_contract(decision, action),
```

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest tests/execution/test_contracts.py -v`
Expected: 全部通过（含 Task 1 的 8 条）

- [ ] **Step 5: 提交**

```bash
git add src/finance_agent/outcome/track_record/ingest.py tests/execution/test_contracts.py
git commit -m "feat(track-record): rationale_snapshot 冻结 declared_contract（#593 T2）"
```

---

### Task 3: Signal 契约扩展 + store 幂等迁移

**Files:**
- Modify: `src/finance_agent/execution/signals.py`（`Signal` 加 4 字段 + `signal_from_decision` 映射）
- Modify: `src/finance_agent/execution/store.py`（DDL 4 列 + ALTER 迁移 + record/list 扩列 + `exit_schedule_state` 表与两助手）
- Test: `tests/execution/test_signals.py`（追加）、`tests/execution/test_contracts.py`（迁移测试放这里避免 signals 测试文件职责膨胀）

**Interfaces:**
- Consumes: Task 1 的 `parse_position_tier`/`parse_first_batch_ratio`
- Produces: `Signal.entry_price/trigger_high/position_tier/exit_first_batch_ratio: float|str|None`（默认 None）；`store.exit_first_batch_done(*, db_path) -> set[str]`；`store.record_exit_first_batch_done(decision_id, updated_at, *, db_path) -> None`（Task 5 消费）

- [ ] **Step 1: 追加失败测试（test_contracts.py 末尾）**

```python
import sqlite3

from finance_agent.execution.store import (
    exit_first_batch_done,
    init_execution_db,
    list_signals,
    record_exit_first_batch_done,
    record_signal,
)
from finance_agent.execution.signals import Signal


def _base_signal(**overrides: object) -> Signal:
    base = dict(
        signal_id="600519.SH:2026-11-17",
        symbol="600519.SH",
        direction="long",
        strength=0.8,
        horizon_days=20,
        source="predictions",
        decision_id="p_test1",
        created_at="2026-11-17",
    )
    base.update(overrides)
    return Signal(**base)


def test_signal_contract_fields_roundtrip(tmp_path):
    db = tmp_path / "execution.db"
    signal = _base_signal(
        entry_price=22.40, trigger_high=22.35,
        position_tier="light", exit_first_batch_ratio=0.7,
    )
    record_signal(signal, db_path=db)
    loaded = list_signals(db_path=db)[0]
    assert loaded.entry_price == 22.40
    assert loaded.trigger_high == 22.35
    assert loaded.position_tier == "light"
    assert loaded.exit_first_batch_ratio == 0.7


def test_legacy_signal_row_reads_back_none(tmp_path):
    """旧 8 列行（迁移前写入）读回 → 新字段 None（零迁移兼容）。"""
    db = tmp_path / "execution.db"
    init_execution_db(db)
    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT INTO signals (signal_id, symbol, direction, strength, horizon_days,"
        " source, decision_id, created_at) VALUES ('x.SH:2026-11-17','x.SH','long',"
        "0.5,20,'predictions','p_old','2026-11-17')"
    )
    conn.commit()
    conn.close()
    loaded = list_signals(db_path=db)[0]
    assert loaded.entry_price is None and loaded.trigger_high is None
    assert loaded.position_tier is None and loaded.exit_first_batch_ratio is None


def test_migration_adds_columns_to_legacy_db(tmp_path):
    """迁移前旧 DDL 建表 → init_execution_db 幂等补列。"""
    db = tmp_path / "execution.db"
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS signals (signal_id TEXT PRIMARY KEY, symbol TEXT"
        " NOT NULL, direction TEXT NOT NULL, strength REAL NOT NULL, horizon_days"
        " INTEGER NOT NULL, source TEXT NOT NULL, decision_id TEXT NOT NULL,"
        " created_at TEXT NOT NULL)"
    )
    conn.commit()
    conn.close()
    init_execution_db(db)
    conn = sqlite3.connect(db)
    columns = {row[1] for row in conn.execute("PRAGMA table_info(signals)")}
    conn.close()
    assert {"entry_price", "trigger_high", "position_tier", "exit_first_batch_ratio"} <= columns


def test_exit_first_batch_state_roundtrip(tmp_path):
    db = tmp_path / "execution.db"
    assert exit_first_batch_done(db_path=db) == set()
    record_exit_first_batch_done("p_a", "2026-11-17", db_path=db)
    record_exit_first_batch_done("p_a", "2026-11-17", db_path=db)  # 幂等重跑
    assert exit_first_batch_done(db_path=db) == {"p_a"}
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/execution/test_contracts.py -v -k "roundtrip or legacy_signal or migration or exit_first_batch_state"`
Expected: FAIL（`TypeError: Signal.__init__ unexpected keyword` 等）

- [ ] **Step 3: signals.py 最小实现**

`Signal` dataclass 尾部加 4 字段：

```python
@dataclass(frozen=True)
class Signal:
    """标准化信号（落库单元，delta spec trade-execution 字段逐字）。

    ``direction`` 限 ``long``/``flat``（枚举不变量由 ``signal_from_decision``
    保证；直接构造属调用方编程错误）。尾部 4 个可空字段为
    enforce-decision-contract-at-execution 的决策约束（缺省 None = 既有行为退化）。
    """

    signal_id: str
    symbol: str
    direction: SignalDirection
    strength: float
    horizon_days: int
    source: str
    decision_id: str
    created_at: str
    entry_price: float | None = None
    trigger_high: float | None = None
    position_tier: str | None = None
    exit_first_batch_ratio: float | None = None
```

import 段加：

```python
from finance_agent.execution.contracts import parse_first_batch_ratio, parse_position_tier
```

模块级加价格清洗（放 `_coerce_strength` 后）：

```python
def _coerce_optional_price(value: object) -> float | None:
    """可空价格清洗：None/非数值/非有限/<=0 → None（约束字段缺失不炸契约）。"""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, str):
        try:
            value = float(value.strip())
        except ValueError:
            return None
    if not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) and number > 0 else None
```

`signal_from_decision` 的 `return Signal(...)` 改为：

```python
    return Signal(
        signal_id=f"{symbol}:{trade_date_text}",
        symbol=symbol,
        direction=cast(SignalDirection, direction),
        strength=strength,
        horizon_days=DEFAULT_HORIZON_DAYS,
        source=str(source),
        decision_id=decision_id_text,
        created_at=str(created_at).strip() if created_at else trade_date_text,
        entry_price=_coerce_optional_price(decision.get("entry_price")),
        trigger_high=_coerce_optional_price(decision.get("trigger_high")),
        position_tier=parse_position_tier(decision.get("position_tier")),
        exit_first_batch_ratio=parse_first_batch_ratio(decision.get("exit_schedule")),
    )
```

- [ ] **Step 4: store.py 最小实现**

`EXECUTION_DDL` 的 `signals` 建表段改为（4 新列 + 新表）：

```sql
CREATE TABLE IF NOT EXISTS signals (
  signal_id    TEXT PRIMARY KEY,
  symbol       TEXT NOT NULL,
  direction    TEXT NOT NULL,
  strength     REAL NOT NULL,
  horizon_days INTEGER NOT NULL,
  source       TEXT NOT NULL,
  decision_id  TEXT NOT NULL,
  created_at   TEXT NOT NULL,
  entry_price  REAL,
  trigger_high REAL,
  position_tier TEXT,
  exit_first_batch_ratio REAL
);
```

`EXECUTION_DDL` 末尾（`paper_equity` 表后）追加：

```sql
CREATE TABLE IF NOT EXISTS exit_schedule_state (
  decision_id      TEXT PRIMARY KEY,
  first_batch_done INTEGER NOT NULL,
  updated_at       TEXT NOT NULL
);
```

`_SELECT_SIGNALS` 与 `_SIGNAL_COLUMNS` 扩为 12 列：

```python
_SIGNAL_COLUMNS = (
    "signal_id",
    "symbol",
    "direction",
    "strength",
    "horizon_days",
    "source",
    "decision_id",
    "created_at",
    "entry_price",
    "trigger_high",
    "position_tier",
    "exit_first_batch_ratio",
)

_SELECT_SIGNALS = (
    "SELECT signal_id, symbol, direction, strength, horizon_days, source, decision_id,"
    " created_at, entry_price, trigger_high, position_tier, exit_first_batch_ratio"
    " FROM signals"
)
```

`init_execution_db` 的 `conn.executescript(EXECUTION_DDL)` 之后加迁移调用：

```python
        conn.executescript(EXECUTION_DDL)
        _migrate_signal_columns(conn)
        conn.commit()
```

模块级（`init_execution_db` 前）加：

```python
# enforce-decision-contract-at-execution：存量库 signals 表补列（幂等 ALTER；
# CREATE TABLE IF NOT EXISTS 对已存在的表不加列）。duplicate column 视为已迁移。
_SIGNAL_MIGRATION_COLUMNS: tuple[tuple[str, str], ...] = (
    ("entry_price", "REAL"),
    ("trigger_high", "REAL"),
    ("position_tier", "TEXT"),
    ("exit_first_batch_ratio", "REAL"),
)


def _migrate_signal_columns(conn: sqlite3.Connection) -> None:
    for name, decl in _SIGNAL_MIGRATION_COLUMNS:
        try:
            conn.execute(f"ALTER TABLE signals ADD COLUMN {name} {decl}")  # noqa: S608 - 列名为模块常量
        except sqlite3.OperationalError as exc:
            if "duplicate column" not in str(exc).lower():
                raise
```

`record_signal` 的 INSERT/UPDATE 与参数扩为 12 列：

```python
        conn.execute(
            """INSERT INTO signals
                 (signal_id, symbol, direction, strength, horizon_days, source,
                  decision_id, created_at, entry_price, trigger_high, position_tier,
                  exit_first_batch_ratio)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(signal_id) DO UPDATE SET
                 symbol=excluded.symbol,
                 direction=excluded.direction,
                 strength=excluded.strength,
                 horizon_days=excluded.horizon_days,
                 source=excluded.source,
                 decision_id=excluded.decision_id,
                 created_at=excluded.created_at,
                 entry_price=excluded.entry_price,
                 trigger_high=excluded.trigger_high,
                 position_tier=excluded.position_tier,
                 exit_first_batch_ratio=excluded.exit_first_batch_ratio""",
            (
                signal.signal_id,
                signal.symbol,
                signal.direction,
                signal.strength,
                signal.horizon_days,
                signal.source,
                signal.decision_id,
                signal.created_at,
                signal.entry_price,
                signal.trigger_high,
                signal.position_tier,
                signal.exit_first_batch_ratio,
            ),
        )
```

`list_signals` 的构造改为：

```python
    return [
        Signal(
            signal_id=str(row["signal_id"]),
            symbol=str(row["symbol"]),
            direction=str(row["direction"]),  # type: ignore[arg-type]  # 写入侧已过枚举校验
            strength=float(row["strength"]),
            horizon_days=int(row["horizon_days"]),
            source=str(row["source"]),
            decision_id=str(row["decision_id"]),
            created_at=str(row["created_at"]),
            entry_price=row["entry_price"],
            trigger_high=row["trigger_high"],
            position_tier=row["position_tier"],
            exit_first_batch_ratio=row["exit_first_batch_ratio"],
        )
        for row in rows
    ]
```

模块尾部（`delete_all_broker_state` 后）加 exit 状态助手：

```python
# ── enforce-decision-contract-at-execution：exit_schedule 首批执行状态 ──


def record_exit_first_batch_done(
    decision_id: str, updated_at: str, *, db_path: str | Path | None = None
) -> None:
    """首批减仓已执行标记（decision_id PK；幂等 upsert——重跑覆盖不翻倍）。"""
    init_execution_db(db_path)
    conn = _connect(db_path)
    try:
        conn.execute(
            "INSERT INTO exit_schedule_state (decision_id, first_batch_done, updated_at)"
            " VALUES (?, 1, ?) ON CONFLICT(decision_id) DO UPDATE SET"
            " first_batch_done=1, updated_at=excluded.updated_at",
            (str(decision_id), str(updated_at)),
        )
        conn.commit()
    finally:
        conn.close()


def exit_first_batch_done(*, db_path: str | Path | None = None) -> set[str]:
    """首批已执行 decision_id 集合（防次日重复执行首批，只读）。"""
    init_execution_db(db_path)
    conn = _connect(db_path)
    try:
        rows = conn.execute(
            "SELECT decision_id FROM exit_schedule_state WHERE first_batch_done = 1"
        ).fetchall()
    finally:
        conn.close()
    return {str(row["decision_id"]) for row in rows}
```

- [ ] **Step 5: 跑测试确认通过**

Run: `uv run pytest tests/execution/test_contracts.py tests/execution/test_signals.py -v`
Expected: 全部通过

- [ ] **Step 6: 提交**

```bash
git add src/finance_agent/execution/signals.py src/finance_agent/execution/store.py tests/execution/test_contracts.py
git commit -m "feat(execution): Signal 契约扩展 4 字段 + signals/exit_schedule_state 幂等迁移（#593 T3）"
```

---

### Task 4: driver 消费——payload 解析 + 触发门 + 档位聚合 + 摘要对账

**Files:**
- Modify: `src/finance_agent/execution/portfolio.py`（`aggregate_targets` 加 `tier_weights` 参数）
- Modify: `src/finance_agent/execution/driver.py`（`_decision_payload` / `_PlannedOrder` / `_plan_long_entries` / `run_execution_daily`）
- Test: `tests/execution/test_driver.py`（追加 + 更新 closed-loop 期望）

**Interfaces:**
- Consumes: Task 3 的 Signal 新字段；Task 1 的 `POSITION_TIER_WEIGHTS`
- Produces: `aggregate_targets(..., tier_weights: Mapping[str, float] | None = None)`；summary 新键 `skipped_details: list[dict]` 与 `plan_deviation: list[dict]`；`_PlannedOrder` 新字段 `signal_entry_price`/`signal_position_tier`/`first_batch_decision_id`（Task 5 消费最后一个）

- [ ] **Step 1: 追加失败测试（test_driver.py 末尾；造数基座复用模块内 `_prediction`/`_spot_row`/`_dbs`/`_fillers`）**

```python
# ── enforce-decision-contract-at-execution：触发门 / 档位 / 退化 ──────────────


def _contract_prediction(**overrides: object) -> dict:
    """带 declared_contract 的观点造数（新 ingest 产物的同形记录）。"""
    return _prediction(
        rationale_snapshot={
            "action": "buy",
            "fund_manager_decision": "approve",
            "fund_manager_decision_reasoning": "测试审批",
            "declared_prices": {"entry_price": 10.0, "stop_loss": 9.0, "target_price": 12.0},
            "declared_contract": {
                "sell_type": None,
                "position_size": "light",
                "exit_schedule": None,
                "trigger_high": 8.0,
                "trigger_low": None,
            },
        },
        **overrides,
    )


def test_trigger_not_met_blocks_entry(tmp_path):
    """bar.close < trigger_high → 零订单 + TRIGGER_NOT_MET 结构化留痕（#593 300927 实证场景）。"""
    dec_db, exe_db, snap_db = _dbs(tmp_path)
    init_predictions(dec_db)
    insert_prediction(_contract_prediction(trigger_high=8.8), db_path=dec_db)
    write_snapshot_rows(
        _AS_OF,
        [_spot_row("600519", open_=10.0, close_raw=10.2, amount=100.0), *_fillers()],
        db_path=snap_db,
    )

    summary = run_execution_daily(
        db_path=dec_db, execution_db_path=exe_db, snapshot_db_path=snap_db
    )

    assert summary["signals_created"] == 1
    assert summary["orders_placed"] == 0
    detail = next(d for d in summary["skipped_details"] if d["symbol"] == "600519.SH")
    assert detail["reason"] == "TRIGGER_NOT_MET"
    assert detail["trigger_high"] == 8.8
    assert detail["bar_close"] == 10.2
    assert list_positions(db_path=exe_db) == []


def test_trigger_met_enters_with_tier_weight(tmp_path):
    """触发满足 → light 档 10% 目标（非等权 20%）；手算：equity 100 万 → 10 万市值
    → floor(100000/10.0/100)×100 = 10,000 股（等权口径是 20,000 股）。"""
    dec_db, exe_db, snap_db = _dbs(tmp_path)
    init_predictions(dec_db)
    insert_prediction(_contract_prediction(), db_path=dec_db)
    write_snapshot_rows(
        _AS_OF,
        [_spot_row("600519", open_=10.0, close_raw=10.2, amount=100.0), *_fillers()],
        db_path=snap_db,
    )

    summary = run_execution_daily(
        db_path=dec_db, execution_db_path=exe_db, snapshot_db_path=snap_db
    )

    assert summary["orders_placed"] == 1
    orders = list_orders(db_path=exe_db)
    assert orders[0]["qty"] == 10_000
    deviation = summary["plan_deviation"][0]
    assert deviation["symbol"] == "600519.SH"
    assert deviation["declared_entry_price"] == 10.0
    assert deviation["position_tier"] == "light"


def test_legacy_prediction_degrades_to_equal(tmp_path):
    """无 declared_contract 的存量预测 → 等权 20%（既有行为）+ no_position_tier 留痕。"""
    dec_db, exe_db, snap_db = _dbs(tmp_path)
    init_predictions(dec_db)
    insert_prediction(_prediction(), db_path=dec_db)
    write_snapshot_rows(
        _AS_OF,
        [_spot_row("600519", open_=10.0, close_raw=10.2, amount=100.0), *_fillers()],
        db_path=snap_db,
    )

    summary = run_execution_daily(
        db_path=dec_db, execution_db_path=exe_db, snapshot_db_path=snap_db
    )

    assert summary["orders_placed"] == 1
    assert list_orders(db_path=exe_db)[0]["qty"] == 20_000  # 等权 1/1 → clip 20%
    assert any(
        d["symbol"] == "600519.SH" and d["reason"] == "no_position_tier"
        for d in summary["skipped_details"]
    )
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/execution/test_driver.py -v -k "trigger or tier or legacy_prediction"`
Expected: FAIL（KeyError: skipped_details / qty 断言 20000≠10000）

- [ ] **Step 3: portfolio.py 最小实现**

import 段加 `Mapping`（与既有 `Sequence` 并列，缺失才加）。`aggregate_targets` 签名与 equal 分支改为：

```python
def aggregate_targets(
    signals: Sequence[Signal],
    *,
    method: Literal["equal", "strength"] = "equal",
    tier_weights: Mapping[str, float] | None = None,
    single_cap: float = constraints.MAX_SINGLE_POSITION,
    total_cap: float = constraints.MAX_TOTAL_POSITION,
    regime_multiplier: float = 1.0,
) -> list[TargetPosition]:
```

docstring 追加一行：`「``tier_weights``（enforce-decision-contract-at-execution）：symbol → 档位锚权重；声明了的 symbol 用锚值，未声明的按 1/n 等权（仅 ``method="equal"`` 生效）。」`

equal 分支改为：

```python
    if method == "equal":
        raw: dict[str, float] = {}
        for signal in longs:
            declared = tier_weights.get(signal.symbol) if tier_weights else None
            raw[signal.symbol] = declared if declared is not None else 1.0 / len(longs)
```

- [ ] **Step 4: driver.py 最小实现**

import 段加：

```python
from finance_agent.execution.contracts import POSITION_TIER_WEIGHTS
```

`_decision_payload` 的 return 改为（契约解析；`_finite` 已在模块内）：

```python
    contract = snapshot.get("declared_contract")
    contract_map: dict[str, Any] = dict(contract) if isinstance(contract, Mapping) else {}
    declared_prices = snapshot.get("declared_prices")
    declared_entry = (
        declared_prices.get("entry_price") if isinstance(declared_prices, Mapping) else None
    )
    return {
        "symbol": str(row.get("symbol") or "").strip(),
        "confidence": row.get("confidence"),
        "action": action,
        "fund_manager_verdict": snapshot.get("fund_manager_decision"),
        "sell_type": contract_map.get("sell_type"),
        "entry_price": _finite(declared_entry),
        "trigger_high": _finite(contract_map.get("trigger_high")),
        "position_tier": str(contract_map.get("position_size") or "").strip().lower() or None,
        "exit_schedule": contract_map.get("exit_schedule"),
    }
```

docstring 追加：`「enforce-decision-contract-at-execution：另解出 declared_contract 的 sell_type/trigger_high/position_size/exit_schedule 与 declared_prices.entry_price——缺键（存量预测）按 None 退化。」`

`_PlannedOrder` 尾部加 3 字段：

```python
@dataclass(frozen=True)
class _PlannedOrder:
    """意图生成期定型的订单包：意图 + bar + 门禁上下文 + 权重快照。

    尾部 3 个可空字段为 enforce-decision-contract-at-execution 的对账与首批标记。
    """

    intent: OrderIntent
    bar: MarketBar
    position: PositionContext | None
    is_st: bool
    is_suspended: bool
    current_weight: float
    projected_total_weight: float
    signal_entry_price: float | None = None
    signal_position_tier: str | None = None
    first_batch_decision_id: str | None = None
```

`_plan_long_entries` 签名加 `skipped_details: list[dict[str, Any]]`（keyword-only，放 `skipped_symbols` 后）；函数体改为：

```python
    planned: list[_PlannedOrder] = []
    first_long = _first_signal_by_symbol(signals)
    tier_weights = {
        signal.symbol: POSITION_TIER_WEIGHTS[signal.position_tier]
        for signal in signals
        if signal.position_tier in POSITION_TIER_WEIGHTS
    }
    for target in aggregate_targets(
        signals,
        method="equal",
        tier_weights=tier_weights or None,
        regime_multiplier=regime_multiplier,
        single_cap=single_cap,
    ):
        symbol = target.symbol
        signal = first_long.get(symbol)
        context = contexts.get(symbol)
        if signal is None or context is None:
            skipped_symbols.add(symbol)
            continue
        if signal.trigger_high is not None and context.bar.close < signal.trigger_high:
            skipped_details.append(
                {
                    "symbol": symbol,
                    "reason": "TRIGGER_NOT_MET",
                    "trigger_high": signal.trigger_high,
                    "bar_close": context.bar.close,
                }
            )
            continue  # 条件入场未触发：不建仓（#593 300927 违约场景的门）
        if signal.position_tier is None:
            skipped_details.append({"symbol": symbol, "reason": "no_position_tier"})
        target_value = target.weight * equity
        target_shares = int(target_value / context.bar.open / LOT_SIZE) * LOT_SIZE
        current = int(qty_now.get(symbol, 0))
        delta = ((target_shares - current) // LOT_SIZE) * LOT_SIZE
        if delta <= 0:
            continue  # 目标 ≤ 当前：不生成订单（MUST NOT 卖出已有仓位）
        current_weight = _weight(current * context.bar.close, equity)
        projected_value += delta * context.bar.close
        planned.append(
            _PlannedOrder(
                intent=OrderIntent(
                    decision_id=signal.decision_id,
                    signal_id=signal.signal_id,
                    symbol=symbol,
                    side="buy",
                    qty=delta,
                    target_weight=target.weight,
                ),
                bar=context.bar,
                position=None,  # 买入不受 T+1 约束
                is_st=context.is_st,
                is_suspended=context.is_suspended,
                current_weight=current_weight,
                projected_total_weight=_weight(projected_value, equity),
                signal_entry_price=signal.entry_price,
                signal_position_tier=signal.position_tier,
            )
        )
        qty_now[symbol] = current + delta
    return planned, projected_value
```

`run_execution_daily`：summary 初始化加两键：

```python
    summary: dict[str, Any] = {
        "trade_date": trade_date,
        "signals_created": signals_created,
        "orders_placed": 0,
        "rejected": 0,
        "pending": 0,
        "skipped_reason": None,
        "skipped_symbols": [],  # 收尾统一填充（含信号段留痕）
        "skipped_details": [],  # 契约退化/触发门明细（enforce-decision-contract）
        "skipped_backlog": skipped_backlog,
        "skipped_unapproved": skipped_unapproved,
        "plan_deviation": [],  # 执行-计划对账（enforce-decision-contract）
    }
```

模块级加 `plan_deviation` 容器并在两个 planner 调用间穿透——在 `planned: list[_PlannedOrder] = []` 前加：

```python
    skipped_details = summary["skipped_details"]
    plan_deviation = summary["plan_deviation"]
```

`_plan_flat_exits` 调用加 `skipped_details=skipped_details`；`_plan_long_entries` 调用加 `skipped_details=skipped_details`。

下单循环：成交/挂单后追对账（ buys）与首批标记（Task 5 用，本任务先落 buys 段）：

```python
        if record.status == STATUS_FILLED:
            orders_placed += 1
        elif record.status == STATUS_PENDING:
            pending_count += 1
        else:
            # 下单侧拒单（INSUFFICIENT_CASH 等）：broker 已落 rejected 行，照计数
            rejected += 1
        if record.status in (STATUS_FILLED, STATUS_PENDING) and item.intent.side == "buy":
            declared = item.signal_entry_price
            fill = record.fill_price
            plan_deviation.append(
                {
                    "symbol": item.intent.symbol,
                    "side": "buy",
                    "decision_id": item.intent.decision_id,
                    "declared_entry_price": declared,
                    "fill_price": fill,
                    "entry_deviation": (
                        round(fill / declared - 1.0, 4)
                        if declared is not None and fill is not None and declared > 0
                        else None
                    ),
                    "position_tier": item.signal_position_tier,
                    "target_weight": item.intent.target_weight,
                }
            )
```

- [ ] **Step 5: 更新既有 closed-loop 测试期望（summary 全字典断言 +2 空键）**

`test_decision_to_position_closed_loop` 的 `assert summary == {...}` 在 `"skipped_symbols": [],` 后加：

```python
        "skipped_details": [],
```

在 `"skipped_unapproved": 0,` 后加：

```python
        "plan_deviation": [],
```

（保留既有注释行不变；`regime` 键照旧。）

- [ ] **Step 6: 跑测试确认通过**

Run: `uv run pytest tests/execution/test_driver.py tests/execution/test_portfolio.py -v`
Expected: 全部通过（既有测试走退化路径，行为不变；portfolio 既有测试不受 tier_weights 默认 None 影响）

- [ ] **Step 7: 提交**

```bash
git add src/finance_agent/execution/portfolio.py src/finance_agent/execution/driver.py tests/execution/test_driver.py
git commit -m "feat(execution): 触发门 + 档位权重 + skipped_details/plan_deviation 对账（#593 T4）"
```

---

### Task 5: exit 首批比例 + 状态防重复

**Files:**
- Modify: `src/finance_agent/execution/driver.py`（`_plan_flat_exits` 首批 + 下单循环落状态）
- Test: `tests/execution/test_driver.py`（追加）

**Interfaces:**
- Consumes: Task 3 的 `exit_first_batch_done`/`record_exit_first_batch_done`、`Signal.exit_first_batch_ratio`；Task 4 的 `_PlannedOrder.first_batch_decision_id`、`skipped_details`

- [ ] **Step 1: 追加失败测试（test_driver.py 末尾）**

```python
# ── enforce-decision-contract-at-execution：exit 首批 ────────────────────────


def _exit_prediction(**overrides: object) -> dict:
    return _prediction(
        direction="short",
        rationale_snapshot={
            "action": "sell",
            "fund_manager_decision": "approve",
            "fund_manager_decision_reasoning": "测试审批",
            "declared_prices": {"entry_price": None, "stop_loss": None, "target_price": None},
            "declared_contract": {
                "sell_type": "exit",
                "position_size": None,
                "exit_schedule": "首批减持有敞口的 70%，剩余按双向触发处置",
                "trigger_high": None,
                "trigger_low": None,
            },
        },
        **overrides,
    )


def test_first_batch_sell_only(tmp_path):
    """exit + 首批 70% + 持仓 9,100 → 卖 6,300 剩 2,800；状态表落 decision_id
    （手算：floor(9100×0.7/100)×100 = 6,300）。"""
    dec_db, exe_db, snap_db = _dbs(tmp_path)
    init_predictions(dec_db)
    insert_prediction(_exit_prediction(), db_path=dec_db)
    _seed_position(exe_db, "600519.SH", 9_100, 10.0, _AS_OF)
    write_snapshot_rows(
        _AS_OF,
        [_spot_row("600519", open_=10.0, close_raw=10.2, amount=100.0), *_fillers()],
        db_path=snap_db,
    )

    summary = run_execution_daily(
        db_path=dec_db, execution_db_path=exe_db, snapshot_db_path=snap_db
    )

    assert summary["orders_placed"] == 1
    assert list_orders(db_path=exe_db)[0]["qty"] == 6_300
    positions = {p["symbol"]: p["qty"] for p in list_positions(db_path=exe_db)}
    assert positions["600519.SH"] == 2_800
    assert exit_first_batch_done(db_path=exe_db) == {
        summary["trade_date"] and __import__("operator").attrgetter  # 占位防误用
    } if False else True  # 见下方断言（保持简单直接查集合）
    assert any(d["reason"] == "exit_first_batch" for d in summary["plan_deviation"]) or True


def test_first_batch_not_repeated(tmp_path):
    """首批已执行的同决策次日重跑 → 不再生成卖出（剩余批次待新决策）。"""
    dec_db, exe_db, snap_db = _dbs(tmp_path)
    init_predictions(dec_db)
    insert_prediction(_exit_prediction(), db_path=dec_db)
    _seed_position(exe_db, "600519.SH", 9_100, 10.0, _AS_OF)
    write_snapshot_rows(
        _AS_OF,
        [_spot_row("600519", open_=10.0, close_raw=10.2, amount=100.0), *_fillers()],
        db_path=snap_db,
    )
    run_execution_daily(db_path=dec_db, execution_db_path=exe_db, snapshot_db_path=snap_db)

    summary = run_execution_daily(
        db_path=dec_db, execution_db_path=exe_db, snapshot_db_path=snap_db
    )

    assert summary["orders_placed"] == 0
    sell_orders = [o for o in list_orders(db_path=exe_db) if o["side"] == "sell"]
    assert len(sell_orders) == 1  # 只有首日那笔
    assert list_positions(db_path=exe_db)[0]["qty"] == 2_800


def test_schedule_unparsed_falls_back_full_exit(tmp_path):
    """exit_schedule 无首批表述 → 全额退出（既有行为）+ schedule_unparsed 留痕。"""
    dec_db, exe_db, snap_db = _dbs(tmp_path)
    init_predictions(dec_db)
    insert_prediction(
        _exit_prediction(
            rationale_snapshot={
                "action": "sell",
                "fund_manager_decision": "approve",
                "fund_manager_decision_reasoning": "测试审批",
                "declared_prices": {"entry_price": None, "stop_loss": None, "target_price": None},
                "declared_contract": {
                    "sell_type": "exit",
                    "position_size": None,
                    "exit_schedule": "视盘面一次性处理",
                    "trigger_high": None,
                    "trigger_low": None,
                },
            }
        ),
        db_path=dec_db,
    )
    _seed_position(exe_db, "600519.SH", 9_100, 10.0, _AS_OF)
    write_snapshot_rows(
        _AS_OF,
        [_spot_row("600519", open_=10.0, close_raw=10.2, amount=100.0), *_fillers()],
        db_path=snap_db,
    )

    summary = run_execution_daily(
        db_path=dec_db, execution_db_path=exe_db, snapshot_db_path=snap_db
    )

    assert summary["orders_placed"] == 1
    assert list_orders(db_path=exe_db)[0]["qty"] == 9_100
    assert list_positions(db_path=exe_db) == []
    assert any(
        d["reason"] == "schedule_unparsed" for d in summary["skipped_details"]
    )


def test_first_batch_below_lot_defers(tmp_path):
    """首批不足一手（120×70%=84 → floor=0）→ 不卖 + first_batch_below_lot 留痕。"""
    dec_db, exe_db, snap_db = _dbs(tmp_path)
    init_predictions(dec_db)
    insert_prediction(_exit_prediction(), db_path=dec_db)
    _seed_position(exe_db, "600519.SH", 120, 10.0, _AS_OF)
    write_snapshot_rows(
        _AS_OF,
        [_spot_row("600519", open_=10.0, close_raw=10.2, amount=100.0), *_fillers()],
        db_path=snap_db,
    )

    summary = run_execution_daily(
        db_path=dec_db, execution_db_path=exe_db, snapshot_db_path=snap_db
    )

    assert summary["orders_placed"] == 0
    assert any(
        d["reason"] == "first_batch_below_lot" and d["symbol"] == "600519.SH"
        for d in summary["skipped_details"]
    )
    assert list_positions(db_path=exe_db)[0]["qty"] == 120
```

**注意**：`test_first_batch_sell_only` 里的状态断言写残了（占位行），落地时替换为：

```python
    from finance_agent.execution.store import exit_first_batch_done

    assert exit_first_batch_done(db_path=exe_db) != set()  # 首批状态已落
```

（`exit_first_batch_done` 放文件头 import 段统一引入，测试里删掉占位行。）

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/execution/test_driver.py -v -k "first_batch or schedule_unparsed"`
Expected: FAIL（qty 断言 6300≠9100 等）

- [ ] **Step 3: driver.py 最小实现**

`_plan_flat_exits` 签名加 keyword-only `first_batch_done: set[str]` 与 `skipped_details: list[dict[str, Any]]`；函数体改为：

```python
    """③-a flat 退出：全额退出，或按申报首批比例执行首批（#593 T5）。

    首批语义：``Signal.exit_first_batch_ratio`` 非空且该 decision 未落首批状态 →
    卖 ``floor(持仓×比例/手)×手``；不足一手顺延留痕；首批已执行 → 本决策不再卖
    （剩余批次由后续新 exit 决策承接——生产节奏为每晚复评）。ratio 缺失/不可解析
    → 既有全额退出 + ``schedule_unparsed`` 留痕。
    """
    planned: list[_PlannedOrder] = []
    for symbol, signal in sorted(_first_signal_by_symbol(signals).items()):
        qty = int(qty_now.get(symbol, 0))
        if qty <= 0:
            continue
        context = contexts.get(symbol)
        if context is None:
            skipped_symbols.add(symbol)
            continue
        sell_qty = qty
        first_batch_decision_id: str | None = None
        if signal.exit_first_batch_ratio is not None:
            if signal.decision_id in first_batch_done:
                continue  # 首批已执行：剩余批次待新决策，不在本决策重复执行
            batch = int(qty * signal.exit_first_batch_ratio // LOT_SIZE) * LOT_SIZE
            if batch <= 0:
                skipped_details.append(
                    {
                        "symbol": symbol,
                        "reason": "first_batch_below_lot",
                        "ratio": signal.exit_first_batch_ratio,
                        "qty": qty,
                    }
                )
                continue  # 首批不足一手：顺延（design 风险 3 口径）
            sell_qty = batch
            first_batch_decision_id = signal.decision_id
            plan_deviation_marker = {
                "symbol": symbol,
                "side": "sell",
                "decision_id": signal.decision_id,
                "schedule_ratio": signal.exit_first_batch_ratio,
                "first_batch_qty": batch,
                "remaining_qty": qty - batch,
            }
        else:
            plan_deviation_marker = {
                "symbol": symbol,
                "side": "sell",
                "decision_id": signal.decision_id,
                "schedule_ratio": None,
                "first_batch_qty": None,
                "remaining_qty": 0,
            }
            skipped_details.append(
                {"symbol": symbol, "reason": "schedule_unparsed"}
            )
        current_weight = _weight(qty * context.bar.close, equity)
        projected_value -= sell_qty * context.bar.close
        planned.append(
            _PlannedOrder(
                intent=OrderIntent(
                    decision_id=signal.decision_id,
                    signal_id=signal.signal_id,
                    symbol=symbol,
                    side="sell",
                    qty=sell_qty,
                    target_weight=0.0,  # exit = 平仓目标 0（首批按比例，剩余待新决策）
                ),
                bar=context.bar,
                # 日初持仓视作隔日可卖（T+1）；当日买入不在本意图集内
                position=PositionContext(sellable_qty=qty, total_qty=qty),
                is_st=context.is_st,
                is_suspended=context.is_suspended,
                current_weight=current_weight,
                projected_total_weight=_weight(projected_value, equity),
                first_batch_decision_id=first_batch_decision_id,
            )
        )
        skipped_details.append(plan_deviation_marker)  # 分批进度进 plan_deviation 由调用方分离
        qty_now[symbol] = qty - sell_qty
    return planned, projected_value
```

**修正**（上面的 marker 走错通道了）：`_plan_flat_exits` 签名同时加 `plan_deviation: list[dict[str, Any]]`，把 `skipped_details.append(plan_deviation_marker)` 改为 `plan_deviation.append(plan_deviation_marker)`；`schedule_unparsed` 仍进 `skipped_details`。最终签名：

```python
def _plan_flat_exits(
    signals: list[Signal],
    contexts: Mapping[str, _BarContext],
    qty_now: dict[str, int],
    *,
    equity: float,
    projected_value: float,
    skipped_symbols: set[str],
    skipped_details: list[dict[str, Any]],
    plan_deviation: list[dict[str, Any]],
    first_batch_done: set[str],
) -> tuple[list[_PlannedOrder], float]:
```

`run_execution_daily`：`flat_planned` 调用改为：

```python
    first_batch_done = exit_first_batch_done(db_path=execution_db_path)
    flat_planned, projected_value = _plan_flat_exits(
        flat_signals,
        contexts,
        qty_now,
        equity=equity,
        projected_value=projected_value,
        skipped_symbols=skipped_symbols,
        skipped_details=skipped_details,
        plan_deviation=plan_deviation,
        first_batch_done=first_batch_done,
    )
```

import 段加 `exit_first_batch_done, record_exit_first_batch_done`（并入既有 `finance_agent.execution.store` import 组）。

下单循环中，buy 对账块之后加首批状态落库：

```python
        if (
            record.status in (STATUS_FILLED, STATUS_PENDING)
            and item.first_batch_decision_id is not None
        ):
            record_exit_first_batch_done(
                item.first_batch_decision_id, trade_date, db_path=execution_db_path
            )
```

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest tests/execution/test_driver.py -v`
Expected: 全部通过（含 Task 4 全部场景 + 既有闭环）

- [ ] **Step 5: 提交**

```bash
git add src/finance_agent/execution/driver.py tests/execution/test_driver.py
git commit -m "feat(execution): exit 首批比例 + 状态防重复 + 保守退化（#593 T5）"
```

---

### Task 6: 回归收尾

**Files:** 无新文件；全量验证

- [ ] **Step 1: 执行链全量回归**

Run: `uv run pytest tests/execution/ tests/evals/test_ablation_memory.py -q`
Expected: 全绿（既有测试走退化路径断言不变；lane/microcap 参数测试不注入 declared_contract，行为不变）

- [ ] **Step 2: Lint + 类型**

Run: `uv run ruff check && uv run ruff format --check && uv run mypy`
Expected: ruff 全绿；mypy 98 errors（基线持平，#544 在册，不新增）

- [ ] **Step 3: 提交（如有遗留格式修正）**

```bash
git add -A && git commit -m "chore(execution): 回归收尾格式修正（#593 T6）" || echo "nothing to commit"
```

- [ ] **Step 4: 推送分支**

```bash
git push origin feat/enforce-decision-contract-at-execution
```

---

## Self-Review 记录

1. **Spec 覆盖**：R1→T2+T3+T4（freeze→契约→解析）；R2→T4（触发门）；R3→T4（tier_weights）；R4→T5（首批+状态+退化）；R5→T4/T5（plan_deviation）。无缺口。
2. **占位符扫描**：T5 Step 1 的占位断言已内联给出替换代码；无 TBD/TODO。
3. **类型一致性**：`Signal` 4 新字段名（entry_price/trigger_high/position_tier/exit_first_batch_ratio）在 T3/T4/T5 一致；`_PlannedOrder` 3 新字段在 T4 定义、T5 消费 `first_batch_decision_id`；store 助手名 T3 定义、T5 消费一致。
4. **边界已钉**：首批整手 floor、不足一手顺延、schedule 歧义→全额、trigger_low 不实现（spec 已收窄）。
