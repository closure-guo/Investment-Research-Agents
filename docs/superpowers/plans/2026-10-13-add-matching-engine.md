# add-matching-engine Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 建立 A 股单一撮合引擎（纯函数，T+1/涨跌停/整手/滑点分档/成本外置配置），并把 `evals/backtest/` 的回放入场从「决策日收盘成交」切换为「次一交易日开盘 + 撮合约束 + 滑点」。

**Architecture:** 引擎为无状态纯函数模块 `execution/matching.py`（唯一成交判定实现处）；配置从 JSON 文件加载（`config/matching_engine.json`，env 可覆盖路径），滑点按全市场成交额分位取档；三模式（backtest/paper/live）经 `execution/modes.py` 的数据源抽象共享同一 `match_order`，live 仅留 `NotImplementedError`。回放侧在 `replay.py` 取次一交易日 bar 调引擎；未成交 → `settlement=None` + `unfilled_reason`（聚合侧天然不计收益）；成交 → 经 `resolve_prediction` 新增的 `entry_override` 用真实成交价结算。

**Tech Stack:** Python 3.14 / pandas / pytest；无新依赖。

## Global Constraints

- 测试：`uv run pytest tests/execution tests/evals/backtest -q`；lint `uv run ruff check`；类型 `uv run mypy src/finance_agent/execution`。
- TDD：先失败测试后实现；既有测试**只允许新增**（judgment 的 `entry_override` 用新增用例钉，既有断言不动）。
- 时点截断（`data_snapshot`）与「空 K 线显式报错」（`_settlement_kline` 口径护栏）纪律**不变**：引擎只在已通过护栏的 hfq 全量 K 线上取 bar。
- 口径护栏：回测引擎输入 bar 一律取自 hfq 全量 K 线（与结算同源）；paper 侧取 `market_snapshot.close_raw` 等真实盘面价。两者只共享引擎，不共享价格序列。
- 原因码（逐字，跨模块契约）：`LIMIT_UP_UNFILLABLE` / `LIMIT_DOWN_UNFILLABLE` / `T1_SELL_BLOCKED` / `ODD_LOT`；`FillResult.reason is None` 表示成交。
- 成本与滑点参数不得硬编码于函数体：`MatchingConfig` 由 `load_matching_config()` 从 JSON 加载；函数签名接收 config。
- 新代码注释中文，风格对齐既有模块（`data/snapshot.py`）。

## 关键设计决策（实施者必读，不得自行改口径）

**M1 — 成交价基准**：买单成交价 = 当日 bar `open × (1 + slippage_rate)`；卖单 = `open × (1 − slippage_rate)`，`round(..., 2)`。回放语义：决策在 D 收盘后产出 → 最早可执行价是 D+1 开盘；D+1 一字涨停买单不成交。（bar 的 `open` 由调用方给：回测=hfq K 线「开盘」；paper=快照 `open`。）

**M2 — 一字板判定**：`sealed_flags_from_ohlc(open, high, low, close, prev_close, *, threshold=0.048) -> tuple[bool, bool]` 返回 `(limit_up_sealed, limit_down_sealed)`：条件 = `open==high==low==close` 且 `pct_chg = close/prev_close-1` 达 ±threshold（**含边界**：`>= ±4.8%`，与 `data/snapshot.py` D3 同源）。`prev_close is None`（或 <= 0）→ `(False, False)`。阈值覆盖 ST 5%（与 `data/snapshot.py` D3 的 4.8 规则同源，注意注释交叉引用；快照侧仍用交易所 pct 字段，不强行合并实现）。

**M3 — 校验顺序**（回答「同单多因」）：① `qty` 非 100 整数倍 → `ODD_LOT`（例外：卖单 `qty == position.total_qty` 且 `total_qty % 100 != 0` 时允许零股全卖）→ ② 涨跌停 → ③ T+1（卖单 `qty > position.sellable_qty` → `T1_SELL_BLOCKED`）。`qty <= 0` → `ValueError`（调用方编程错误，不是业务原因码）。

**M4 — 滑点分档**：`slippage_rate_for_amount(amount, market_amounts, config) -> float`：`amount` 在 `market_amounts` 中的分位落入 `config.slippage_tiers[i].quantile_max` 区间取该档 `slippage_rate`；`market_amounts` 为 None/空 或 `amount` 为 None → 取**费率最大档**（默认 JSON 三档下 = 0.002），不是「末档」。分档切点与费率均在 JSON 配置。

**M5 — 费用**：`fee = 佣金 + 印花税`；`commission = max(min_commission, fill_price × qty × commission_rate)`（买卖双向）；`stamp_tax = fill_price × qty × stamp_tax_rate`（仅卖出）；`round(..., 2)`。`fee` 计入 `FillResult` 供后续度量（回放收益序列暂不折算费用，报告披露）。

**M6 — T+1 在回放中的传参**：回放是单笔决策级，不建持仓账本；卖单传 `PositionContext(sellable_qty=qty, total_qty=qty)`（视作已有隔日仓位，公开注释说明），买单传 `None`。T+1 语义由引擎单测全量覆盖。

**M7 — 未成交的聚合语义**：`replay_decision` 未成交返回 `{"settlement": None, "entry_price": None, "unfilled_reason": <码>, ...}`；`run_backtest._trade_daily_returns` 对 `settlement=None` 已返回 `[]`（既有行为，天然不计收益）——**不改既有分支**，只在聚合处新增 `unfilled_counts` 统计并在 methodology 披露（md 报告渲染「未成交笔数（按原因码）」，JSON 键缺失时如实写「未提供」）。

**评审修正记录（实施期裁定，2026-10-13；偏差以本节为准，已回填上文）**：

1. **M2 阈值判定为含边界**：`pct_chg >= ±threshold`（即 `>= ±4.8%`，与 `data/snapshot.py` D3 同源）；原「超过 ±threshold」措辞不含边界，实施采含边界（`prev_close <= 0` 亦同 `None` 处理）。
2. **M4 缺失样本取费率最大档**：`market_amounts`/`amount` 缺失时取全部档位中 `slippage_rate` 最大者（默认 JSON 三档下 = 0.002）；原括号「末档 = 最大 slippage」在逐字默认配置（末档 quantile_max=1.0、滑点 0.0005）下不自洽，实施以「最保守 = 费率最大」为准（宁可高估成本、不美化收益）。
3. **Task 2 配置错误契约**：**文件缺失 → 默认 + WARN；文件存在但内容非法（坏 JSON/负费率/档位非升序或末档非 1.0）→ `ValueError` fail-loud**——配置错误不静默（原「坏 JSON/负费率 → 回落默认 + WARN」不采用）。

## 文件结构

| 文件 | 职责 |
|---|---|
| `src/finance_agent/execution/__init__.py`（新） | 包 |
| `src/finance_agent/execution/matching.py`（新） | 订单/bar/结果/配置 dataclass、`match_order`、`slippage_rate_for_amount`、`sealed_flags_from_ohlc` |
| `src/finance_agent/execution/config.py`（新） | `load_matching_config(path=None) -> MatchingConfig`（JSON，env `MATCHING_CONFIG_PATH` 覆盖，缺文件回落默认） |
| `config/matching_engine.json`（新） | 三档滑点 + 佣金/印花税参数（可改配置不改代码） |
| `src/finance_agent/execution/modes.py`（新） | `MarketDataSource` Protocol、`BacktestDataSource`/`PaperDataSource`/`LiveDataSource`、`match_for_mode` |
| `src/finance_agent/outcome/track_record/judgment.py`（改，纯加参） | `resolve_prediction(..., entry_override: tuple[str, float] | None = None)` |
| `evals/backtest/replay.py`（改） | 入场走引擎：D+1 bar + 未成交短路 + `entry_override` 结算 |
| `evals/backtest/run_backtest.py`（改） | `unfilled_counts` 聚合 + methodology 披露 |
| `tests/execution/test_matching.py`（新） | 引擎逐约束单测（每约束正反各 ≥1 例） |
| `tests/execution/test_matching_config.py`（新） | 配置加载/覆盖/分档边界 |
| `tests/execution/test_modes.py`（新） | 三模式数据源 + 跨模式一致性 |
| `tests/evals/backtest/test_replay.py`（追加） | 回放入场引擎化：成交/未成交/口径护栏不变 |
| `tests/evals/backtest/test_run_backtest.py`（追加） | unfilled_counts 不计收益 |
| `tests/outcome/test_track_record_judgment.py`（追加） | entry_override 生效 + 缺省行为不变 |

---

### Task 1: 引擎骨架 — dataclass + 约束 + 成交

**Files:** Create `src/finance_agent/execution/__init__.py`、`src/finance_agent/execution/matching.py`、`tests/execution/__init__.py`、`tests/execution/test_matching.py`

**Interfaces（逐字）:**
```python
class Order:  # frozen dataclass
    code: str; side: Literal["buy", "sell"]; qty: int
class MarketBar:  # frozen
    trade_date: str; open: float; high: float; low: float; close: float
    prev_close: float | None = None; amount: float | None = None
    limit_up_sealed: bool = False; limit_down_sealed: bool = False
class PositionContext:  # frozen
    sellable_qty: int = 0; total_qty: int = 0
class FillResult:  # frozen
    filled_qty: int; fill_price: float | None; fee: float; reason: str | None; slippage_rate: float
def match_order(order, bar, *, config: MatchingConfig, position: PositionContext | None = None,
                market_amounts: Sequence[float] | None = None) -> FillResult
```
（`MatchingConfig`/`SlippageTier` 在 Task 2 定义，本任务先用最小字段集 `commission_rate/min_commission/stamp_tax_rate/slippage_tiers` 占位一同落地即可。）

- [ ] **Step 1: 写失败测试**（`tests/execution/test_matching.py`）：一字涨停买单 → `filled_qty=0, reason="LIMIT_UP_UNFILLABLE", fee=0`；一字跌停卖单 → `LIMIT_DOWN_UNFILLABLE`；普通日买单 → `filled_qty=qty`、`fill_price == round(open*(1+rate),2)`；T+1（sell qty 500 / sellable 0）→ `T1_SELL_BLOCKED`；`qty=150` 买单 → `ODD_LOT`；卖零股（total 350/sellable 350/qty 350）→ 成交；`qty=0` → `ValueError`；卖单费用含印花税、买单不含（数值断言）。
- [ ] **Step 2: 跑测试确认失败**（ImportError）。
- [ ] **Step 3: 实现**（校验顺序与成交价按 M1/M3/M5；纯函数，无 IO）。
- [ ] **Step 4: 跑测试确认通过** + `uv run ruff check` + `uv run mypy src/finance_agent/execution`。
- [ ] **Step 5: Commit** `feat(matching): A-share order matching with constraints and cost model (T1)`。

### Task 2: 滑点分档 + 配置外置

**Files:** Create `src/finance_agent/execution/config.py`、`config/matching_engine.json`；Modify `matching.py`；Test `tests/execution/test_matching_config.py`（+ test_matching.py 追加分档用例）

**Interfaces:**
```python
class SlippageTier:  # frozen: quantile_max: float; slippage_rate: float
class MatchingConfig:  # frozen: commission_rate, min_commission, stamp_tax_rate, slippage_tiers: tuple[SlippageTier, ...]
def default_matching_config() -> MatchingConfig            # 三档 0.002/0.001/0.0005，佣金 0.00025/最低 5 元，印花税 0.0005
def load_matching_config(path: str | Path | None = None) -> MatchingConfig   # env MATCHING_CONFIG_PATH；文件缺失 → 默认 + WARN；文件存在但内容非法 → ValueError fail-loud
def slippage_rate_for_amount(amount, market_amounts, config) -> float
```
`config/matching_engine.json` 内容（逐字）：`{"commission_rate": 0.00025, "min_commission": 5.0, "stamp_tax_rate": 0.0005, "slippage_tiers": [{"quantile_max": 0.2, "slippage_rate": 0.002}, {"quantile_max": 0.6, "slippage_rate": 0.001}, {"quantile_max": 1.0, "slippage_rate": 0.0005}]}`（`slippage_tiers` 必须升序且末档 `quantile_max==1.0`，否则 `ValueError`）。

- [ ] **Step 1: 写失败测试**：默认三档；文件覆盖（tmp_path 写 JSON，改最低档为 0.0008 → `load_matching_config(path)` 读出该值；**并断言低流动性档买单成交价 = open×(1+0.0008) 的整链**——spec「改配置不改代码」场景）；分档边界（分位恰在切点取上/下档的钉法按实现注释；`market_amounts=None` → 费率最大档）；文件缺失 → 默认 + WARN；文件存在但坏 JSON/负费率 → `ValueError` fail-loud（配置错误不静默，见「评审修正记录」3）。
- [ ] **Step 2-4: 红 → 实现 → 绿**（`match_order` 的 slippage 由 `market_amounts` 经 `slippage_rate_for_amount` 决定；`FillResult.slippage_rate` 记录实取档）。
- [ ] **Step 5: Commit** `feat(matching): slippage tiers + externalized config file (T2)`。

### Task 3: 三模式数据源接口

**Files:** Create `src/finance_agent/execution/modes.py`；Test `tests/execution/test_modes.py`

**Interfaces:**
```python
class MarketDataSource(Protocol):
    mode: str
    def bar(self, code: str, trade_date: str) -> MarketBar | None: ...
    def next_trading_day(self, trade_date: str) -> str | None: ...
class BacktestDataSource:   # __init__(kline_by_code: dict[str, pd.DataFrame])：hfq 全量 K 线（日期/开盘/最高/最低/收盘）
class PaperDataSource:      # __init__(bars_by_code: dict[str, dict[str, MarketBar]])：当日快照行转 bar（真实盘面价）
class LiveDataSource:       # 全方法 raise NotImplementedError("实盘接口未实现（本 change 仅留抽象）")
def match_for_mode(mode: str, order, *, source: MarketDataSource, config: MatchingConfig,
                   position: PositionContext | None = None,
                   market_amounts: Sequence[float] | None = None) -> FillResult
   # mode ∈ {"backtest","paper"} → 同一 match_order；mode == "live" → NotImplementedError；未知 mode → ValueError
```
`BacktestDataSource.bar` 由 K 线行构造并把 `prev_close` 取为区间上一行收盘，`sealed_flags_from_ohlc` 填 sealed 位（M2）；`next_trading_day` = 该序列中 `> trade_date` 的首行日期；缺数据返回 None。

- [ ] **Step 1: 写失败测试**：backtest 源从 K 线行构造 bar（含 sealed 位与 prev_close）；paper 源从快照行构造；**跨模式一致性**（spec 场景）：同一份数值喂两源 → 同 Order 同 config → `FillResult` 逐字段相等（`dataclasses.astuple` 相等）；live → `NotImplementedError`；未知 mode → `ValueError`；缺数据 → None。
- [ ] **Step 2-4: 红 → 实现 → 绿。**
- [ ] **Step 5: Commit** `feat(matching): backtest/paper/live data-source abstraction, live NotImplemented (T3)`。

### Task 4: resolve_prediction 加 entry_override（纯加参）

**Files:** Modify `src/finance_agent/outcome/track_record/judgment.py`；Test 追加 `tests/outcome/test_track_record_judgment.py`

**Interfaces:** `resolve_prediction(prediction, kline, benchmark=None, neutral_band=DEFAULT_NEUTRAL_BAND, *, entry_override: tuple[str, float] | None = None) -> Resolution | None`；给定时跳过 `derive_entry`，用 `(entry_date, entry_price)` 继续既有全部逻辑（horizon/中性带/超额/回避语义不变）；`entry_override` 的 `entry_price <= 0` → None（与既有同）。

- [ ] **Step 1: 写失败测试（追加）**：override 生效（entry_date/entry_price 反映 override，exit 仍 = entry_date 后第 horizon 个交易日收盘）；缺省路径与既有行为一致（同一输入，有/无显式 None 结果相同）。
- [ ] **Step 2-4: 红 → 实现 → 绿**（`uv run pytest tests/outcome/test_track_record_judgment.py -q` 全绿，既有断言零改动）。
- [ ] **Step 5: Commit** `feat(judgment): optional entry_override for engine-filled backtest entries (T4)`。

### Task 5: 回放入场引擎化

**Files:** Modify `evals/backtest/replay.py`；Test 追加 `tests/evals/backtest/test_replay.py`

**行为（钉死）:**
1. `action ∈ {buy, sell}` 且 `kline` 可用时：取 `rows = df[df["日期"] > decision_date]` 首行 = D+1；无 D+1（数据末尾）→ 保持既有「无结算」返回（`settlement=None`）。
2. bar 字段：`open/high/low/close` 取该行；**缺「开盘」列 → 回落该行「收盘」并在结果记 `fill_reference="close_fallback"`**（有开盘记 `"open"`）；`prev_close` 取前一交易日收盘（决策日在序列内时即决策日收盘）；sealed 位由 `sealed_flags_from_ohlc` 计算。
3. `order = Order(code=code, side=action, qty=_REPLAY_ORDER_QTY(=100))`；`config = load_matching_config()`；卖单 `position=PositionContext(sellable_qty=100, total_qty=100)`（M6）。
4. 未成交 → 返回 `{**base, "settlement": None, "entry_price": None, "unfilled_reason": res.reason}`。
5. 成交 → `resolve_prediction(prediction, kline, benchmark, entry_override=(D1日期, res.fill_price))`；返回体新增 `"fill": {"price": res.fill_price, "fee": res.fee, "slippage_rate": res.slippage_rate, "reference": "open"|"close_fallback"}`。
6. `action` 非 buy/sell（neutral/unknown）→ 完全走既有路径（不调引擎）。
7. `_settlement_kline` 口径护栏与 `full_kline` 传递逻辑不动。

- [ ] **Step 1: 写失败测试（追加）**：D+1 普通日买单 → `entry_price == round(D+1 开盘×(1+档位滑点),2)` 且 `settlement.entry_date == D+1`；D+1 一字涨停（构造 o=h=l=c 且 prev_close 低 10%）→ `settlement is None` + `unfilled_reason="LIMIT_UP_UNFILLABLE"`；D+1 一字跌停卖单 → `LIMIT_DOWN_UNFILLABLE`；数据末尾无 D+1 → 既有 None 路径；neutral 决策不触引擎（spy 断言 `match_order` 未被调用或 `fill` 键缺失）；缺「开盘」列 → `close_fallback` 且成交价 = 收盘×(1+滑点)。
- [ ] **Step 2-4: 红 → 实现 → 绿**（既有 replay 测试零改动通过）。
- [ ] **Step 5: Commit** `feat(backtest): replay entries via matching engine with limit-up block (T5)`。

### Task 6: 聚合侧未成交统计

**Files:** Modify `evals/backtest/run_backtest.py`；Test 追加 `tests/evals/backtest/test_run_backtest.py`

- [ ] **Step 1: 写失败测试**：构造含 `unfilled_reason` 的结果行 → 该笔不进 `system_returns`（既有 `[]` 路径）且聚合表新增 `unfilled_counts`（`{"LIMIT_UP_UNFILLABLE": 1}`）；methodology 文本含「未成交笔数按原因码披露、不计入收益序列」。
- [ ] **Step 2-4: 红 → 实现 → 绿**（`_trade_daily_returns` 不动）。
- [ ] **Step 5: Commit** `feat(backtest): disclose unfilled trades by reason in aggregation (T6)`。

### Task 7: 验证与收口

- [ ] `uv run pytest tests/execution tests/evals/backtest tests/outcome/test_track_record_judgment.py -q` 全绿。
- [ ] `uv run pytest tests/ -q -m "not live"`（记录结果；本机环境限制如实标注）。
- [ ] `uv run ruff check` + `uv run mypy src/finance_agent/execution`。
- [ ] 10 个涨跌停案例人工核对表：从 fixtures 构造 10 组（每板各有/无一字涨跌停、ST 5% 档、正常日×三档滑点），**期望值独立手算**（不复用被测函数），核对表与结论落 `tests/validation/2026-10-13-add-matching-engine-validation.md`；实盘数据核对受本机东财 IP 封禁（issue #325）限制，如实记录并挂 follow-up。
- [ ] `openspec/changes/add-matching-engine/tasks.md` 全勾（受阻项如实标注）→ `openspec validate add-matching-engine --strict` → `openspec archive add-matching-engine`。
- [ ] Commit `chore(openspec): archive add-matching-engine（validation 随档）`。

## Self-Review 备注

- spec 覆盖：单一撮合实现→T1/T3（跨模式一致性场景 T3）；A 股约束四码→T1（每约束正反例）；分档滑点与成本+配置场景→T2；decision-backtest MODIFIED（回放经涨跌停约束、不计收益、时点截断/空 K 线纪律不变）→T5/T6/T4。
- 类型一致性：`FillResult.reason` 的码值与 spec 逐字一致；`MarketBar` 在 T1 定义、T3/T5 消费；`entry_override` 关键字参数（避免既有位置参数调用受影响）。
