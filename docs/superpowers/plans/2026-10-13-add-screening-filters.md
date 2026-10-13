# add-screening-filters Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 落地选股漏斗 L1 确定性硬过滤纯函数（ST/停牌/新股/一字板/流动性），约束常量单点定义于 `screening/constraints.py` 供后续风控门禁复用。

**Architecture:** 纯函数模块，零 IO：`filters.py` 只吃结构化输入（`CandidateRecord` 序列）产出 `FilterDecision`；阈值来自 `constraints.py` 的 `FilterThresholds`（调用期读模块属性，保证「单点修改常量 → 行为变化」）。由快照行构造输入（`build_candidate_record`）与交易日计算（`trading_days_listed`）同模块提供，输入仍是普通序列/list，便于单测。

**Tech Stack:** Python 3.14 / dataclasses / enum / pytest。

## Global Constraints

- 测试：`uv run pytest tests/screening -q`；ruff/mypy 同仓库纪律。
- TDD 红→绿；既有文件零修改（本 change 全新增）。
- 原因码（逐字，含扩展项）：`ST_FLAG` / `SUSPENDED` / `IPO_RECENT` / `LIMIT_LOCKED` / `ILLIQUID` / `DATA_MISSING`（数据不可得时的 fail-closed 码——选股纪律「宁可漏选不可误选」；spec 为「至少包含」五项，扩展码允许）。
- 剔除返回**全部**命中的原因码（确定性顺序 = 枚举声明顺序），不做「首个原因短路」。
- 常量口径：`MIN_TRADING_DAYS = 60`；`MIN_AVG_AMOUNT_20D = 50_000_000.0`；`MIN_LIQUIDITY_HISTORY_DAYS = 20`。
- 冻结区（越界即须人工拍板的诚实红线）：近 20 日成交额在快照积累不足 `MIN_LIQUIDITY_HISTORY_DAYS` 时 → `DATA_MISSING`（意味着生产快照上线后约 20 个交易日选股才能给出流动性判定；该后果写入 validation 报告与后续 pipeline change 的注意事项）。

## 关键设计决策

**F1 — 上市交易天数**：`trading_days_listed(list_date: str, as_of: str, calendar: Sequence[str]) -> int | None`：统计 `calendar` 中 `list_date <= d <= as_of` 的天数（含首日=1）；`list_date` 缺失/不可解析/`as_of` 早于 `list_date` → None（→ `DATA_MISSING`）。日历为空 → None。**不允许**用自然日近似（spec 明文「上市 < 60 交易日」）。

**F2 — 流动性**：`avg_amount_20d(amounts: Sequence[float]) -> float | None`：取序列（按日期升序）最后至多 20 个 `amount`，长度为 0 或出现非有限值 → None；`len(amounts) < MIN_LIQUIDITY_HISTORY_DAYS` → None（→ `DATA_MISSING`）。判定 `avg < MIN_AVG_AMOUNT_20D` → `ILLIQUID`（恰好等于阈值不剔除——「低于」语义）。

**F3 — 一字板**：只看 `limit_up_sealed`/`limit_down_sealed`（快照 D3 口径：开=高=低=收且幅度达 ±4.8%）；「盘中触板未封死」（high 触及但 low < high）不剔除。

**F4 — 阈值单点来源**：`filters.py` 内 `from finance_agent.screening import constraints`，调用期读 `constraints.DEFAULT_THRESHOLDS`（模块属性），可被 `filter_universe(..., thresholds=...)` 显式覆盖；测试用 monkeypatch 改 `constraints.DEFAULT_THRESHOLDS` 断言行为变化（spec「常量单点修改」场景）。

## 文件结构

| 文件 | 职责 |
|---|---|
| `src/finance_agent/screening/__init__.py`（新） | 包 |
| `src/finance_agent/screening/constraints.py`（新） | 约束常量单一来源：`MIN_TRADING_DAYS` / `MIN_AVG_AMOUNT_20D` / `MIN_LIQUIDITY_HISTORY_DAYS` / `FilterThresholds` / `DEFAULT_THRESHOLDS` |
| `src/finance_agent/screening/filters.py`（新） | `RejectReason`、`CandidateRecord`、`FilterDecision`、`filter_universe`、`trading_days_listed`、`avg_amount_20d`、`build_candidate_record` |
| `tests/screening/__init__.py` + `tests/screening/test_filters.py`（新） | 全规则正反 + 边界 + 枚举完整性 + 单点修改场景 |

## Interfaces（逐字）

```python
# constraints.py
MIN_TRADING_DAYS: int = 60
MIN_AVG_AMOUNT_20D: float = 50_000_000.0
MIN_LIQUIDITY_HISTORY_DAYS: int = 20
@dataclass(frozen=True)
class FilterThresholds:
    min_trading_days: int = MIN_TRADING_DAYS
    min_avg_amount_20d: float = MIN_AVG_AMOUNT_20D
    min_liquidity_history_days: int = MIN_LIQUIDITY_HISTORY_DAYS
DEFAULT_THRESHOLDS: FilterThresholds = FilterThresholds()

# filters.py
class RejectReason(str, enum.Enum):
    ST_FLAG = "ST_FLAG"; SUSPENDED = "SUSPENDED"; IPO_RECENT = "IPO_RECENT"
    LIMIT_LOCKED = "LIMIT_LOCKED"; ILLIQUID = "ILLIQUID"; DATA_MISSING = "DATA_MISSING"
@dataclass(frozen=True)
class CandidateRecord:
    code: str
    name: str = ""
    is_st: bool = False
    is_suspended: bool = False
    limit_up_sealed: bool = False
    limit_down_sealed: bool = False
    trading_days_listed: int | None = None
    avg_amount_20d: float | None = None
@dataclass(frozen=True)
class FilterDecision:
    code: str
    passed: bool
    reasons: tuple[RejectReason, ...]   # 空 tuple = 通过
def filter_universe(records, *, thresholds: FilterThresholds | None = None) -> list[FilterDecision]
def trading_days_listed(list_date: str | None, as_of: str, calendar: Sequence[str]) -> int | None
def avg_amount_20d(amounts: Sequence[float]) -> float | None
def build_candidate_record(*, row: dict, amounts: Sequence[float], calendar: Sequence[str], as_of: str) -> CandidateRecord
```

---

### Task 1: constraints + filters 纯函数

- [ ] **Step 1: 写失败测试**（`tests/screening/test_filters.py`）：
  - 每条规则正反各一例：ST（`is_st=True` → `[ST_FLAG]`；`False` → 通过）；停牌；一字涨停剔除、一字跌停剔除、「触板未封死」（`limit_up_sealed=False`）通过。
  - 多因同单：ST+停牌 → `reasons == (ST_FLAG, SUSPENDED)`（枚举顺序）。
  - 边界：`trading_days_listed` 59 → `IPO_RECENT`；60 → 通过；61 → 通过；`None` → `DATA_MISSING`。
  - 流动性：`avg_amount_20d == 阈值` → 通过（「低于」）；`阈值 - 0.01` → `ILLIQUID`；`None` → `DATA_MISSING`。
  - `trading_days_listed()`：`list_date == as_of`（均工作日）→ 1；区间含两端；list_date 不在日历 → None；as_of < list_date → None。
  - `avg_amount_20d()`：25 天 → 取最后 20 天均值；19 天 → None；含 NaN → None；空 → None。
  - `build_candidate_record`：快照行 dict（`is_st/is_suspended/limit_up_sealed/limit_down_sealed/list_date`）+ amounts + calendar → 完整 record。
  - **枚举完整性**：`set(RejectReason)` == 六码逐字；`filter_universe` 只可能产出枚举成员（对一批混合输入断言 reasons ⊆ set(RejectReason)）。
- [ ] **Step 2: 跑测试确认失败**（ModuleNotFoundError）。
- [ ] **Step 3: 实现**（F1–F3；纯函数；docstring 记录 F4 调用期读属性口径）。
- [ ] **Step 4: 绿 + `uv run ruff check` + `uv run mypy src/finance_agent/screening`。**
- [ ] **Step 5: Commit** `feat(screening): L1 deterministic hard filters + single-source constraints (T1)`。

### Task 2: 常量单点修改场景

- [ ] **Step 1: 写失败测试**：`monkeypatch.setattr(constraints, "DEFAULT_THRESHOLDS", FilterThresholds(min_avg_amount_20d=80_000_000.0))` → 同一批输入里 6000 万均额标的从「通过」变 `ILLIQUID`；`min_trading_days=61` → 60 天标的从「通过」变 `IPO_RECENT`。反向钉住：显式 `thresholds=` 参数覆盖模块默认。
- [ ] **Step 2–4: 红 → 实现（若 T1 已按 F4 做则只需确认）→ 绿。**
- [ ] **Step 5: Commit** `test(screening): pin single-source constant override behavior (T2)`。

### Task 3: 验证与收口

- [ ] `uv run pytest tests/screening -q` 全绿；`uv run pytest tests/ -q -m "not live"`（记录；本机已知 1 个既有 env flake）。
- [ ] 人工核对：spec 任务 3.2「2026-09 全月过滤 + 抽 50 例」因本机东财 IP 封禁（issue #325）无真实快照数据不可执行 → 以**手工构造的 50 例 fixture 表**（覆盖六码 × 正反 × 边界）逐例手算期望 + 逐行核对，报告落 `tests/validation/2026-10-13-add-screening-filters-validation.md`，如实记录替代性质与 live 缺项。
- [ ] `openspec/changes/add-screening-filters/tasks.md` 全勾（受阻项如实标注）→ `openspec validate add-screening-filters --strict` → `openspec archive add-screening-filters`（注意：主规范无 stock-screening，本 change 为新建 spec——archive 若报 MODIFIED 头找不到，按 delta 漂移规程对齐主规范重写）。
- [ ] Commit `chore(openspec): archive add-screening-filters（validation 随档）`。
