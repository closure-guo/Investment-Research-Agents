# add-signal-portfolio-risk-gate Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Signal 契约（TradeDecision→Signal 落库，仅 approve 决策生成）、组合仓位聚合（等权/强度加权两档 + 单票 20%/总仓 80% 上限与缩减记录）、确定性风控门禁（纯代码、无 LLM 依赖、复用 screening 约束常量）+ 滞回机制复用到信号路径。

**Architecture:** `execution/signals.py`（契约/转换/落库，`EXECUTION_DB_PATH` 默认 `data/execution.db`）；`execution/portfolio.py`（纯函数聚合）；`execution/risk_gate.py`（纯函数门禁，静态无 LLM import）；滞回判定从 `nodes/risk.py` 抽取为共享纯函数 `execution/hysteresis.py`（行为保持重构，risk 节点与信号路径共用同一判定）。

**Tech Stack:** Python 3.14 / sqlite3 / pytest；复用 `screening/constraints.py`、`execution/matching.py`（MarketBar/PositionContext/sealed flags）。

## Global Constraints

- 测试：`uv run pytest tests/execution -q`；ruff/mypy 同仓库纪律；TDD 红→绿。
- 既有测试零修改（唯一例外：滞回抽取若需在 `tests/nodes/test_risk*.py` 追加用例——只追加，不改既有断言）。
- 原因码（逐字）：门禁 `SINGLE_POSITION_CAP` / `TOTAL_POSITION_CAP` / `T1_SELL_BLOCKED` / `LIMIT_UP_UNFILLABLE` / `LIMIT_DOWN_UNFILLABLE` / `SUSPENDED` / `ST_FLAG` / `ODD_LOT`；聚合调整记录 `SINGLE_CAP_CLIP` / `TOTAL_CAP_SCALE_DOWN`。
- 常量单一来源：`screening/constraints.py` 增 `MAX_SINGLE_POSITION = 0.2`、`MAX_TOTAL_POSITION = 0.8`、`LOT_SIZE = 100`；`risk_gate.py` import 之（spec 明文「约束定义 import 自 screening/constraints.py」）；`matching.py` 的整手判定同步改为 import `LOT_SIZE`（单一来源，既有 matching 测试保持绿）。
- 门禁无 LLM：`risk_gate.py` 静态扫描（测试用 AST/import 扫描断言无 `llm`/`gateway`/`harness` import）。
- direction 枚举 `long/flat`（无做空）；`short` → `SignalContractError("direction 非法值: short")`（spec 逐字）。
- 滞回：信号方向翻转经过与决策方向翻转**相同的滞回判定**（共享纯函数），抖动被吸收且判定记录落库。

## 关键设计决策

**G1 — 决策→Signal 映射**：`signal_from_decision(decision: dict, *, decision_id: str, source: str, trade_date: str, created_at: str | None = None) -> Signal`：`action=buy→long`；`action=sell`：`sell_type=="short"` → `SignalContractError("direction 非法值: short")`，否则（exit/未申报）→ `flat`；`hold/watch→flat`；`strength = confidence`（0-1 校验）；`horizon_days = DEFAULT_HORIZON_DAYS`（从 `outcome.track_record.judgment` import）；仅 `fund_manager_verdict=="approve"`（或调用方显式 `approved=True`）才转换——**未 approve → `SignalContractError("未审批通过的决策不得生成信号")`**；缺字段（symbol/decision_id）→ 合同错误。

**G2 — 落库**：`execution/store.py`：`signals(signal_id PK, symbol, direction, strength, horizon_days, source, decision_id, created_at)`；`record_signal`（幂等 upsert by signal_id）、`list_signals(*, trade_date=None, symbol=None)`、`record_hysteresis_event(signal_id, detail_json, created_at)`（滞回判定记录落库——spec「滞回判定记录落库」）。`EXECUTION_DB_PATH` 默认 `data/execution.db`，连接纪律同 snapshot。

**G3 — 聚合**：`aggregate_targets(signals, *, method="equal"|"strength", single_cap=MAX_SINGLE_POSITION, total_cap=MAX_TOTAL_POSITION) -> list[TargetPosition]`：仅 `direction=="long"` 参与；等权 = 1/n；强度加权 = `strength/Σstrength`；**先单票 clip**（>20% → 20% + `SINGLE_CAP_CLIP`）；**再总仓缩减**（Σ>80% → 全体按 80%/Σ 等比缩 + `TOTAL_CAP_SCALE_DOWN`，spec 场景：96% → 80% 等比）；输出按权重降序、symbol 升序 tie-break；权重和勾稽（`Σ ≤ total_cap + 1e-9`）。

**G4 — 门禁**：`check_order(order: OrderIntent, *, current_weight: float, projected_total_weight: float, bar: MarketBar | None, position: PositionContext | None, is_st: bool = False, is_suspended: bool = False) -> GateDecision`：顺序 ① `is_st` → `ST_FLAG`；② `is_suspended` → `SUSPENDED`；③ 涨跌停（`bar.limit_up_sealed` 且 side=buy → `LIMIT_UP_UNFILLABLE`；`limit_down_sealed` 且 sell → `LIMIT_DOWN_UNFILLABLE`）；④ 单票上限（`order.target_weight > single_cap` → `SINGLE_POSITION_CAP`）；⑤ 总仓上限（`projected_total_weight > total_cap` → `TOTAL_POSITION_CAP`）；⑥ T+1（sell 且 `qty > position.sellable_qty` → `T1_SELL_BLOCKED`）；⑦ 整手（`qty % LOT_SIZE != 0` → `ODD_LOT`，卖零股全卖例外同 matching 语义）。`OrderIntent` 含 `decision_id`/`signal_id`（证据链，spec 明文）；`GateDecision(approved, reason, detail)`（detail 含全部命中项？——**取首个命中**（门禁语义：拒绝即停），detail 记检查轨迹）。

**G5 — 滞回抽取**：`execution/hysteresis.py::evaluate_direction_hysteresis(prior_direction: str | None, current_direction: str, *, balanced_zone: bool, incremental_claimed: bool) -> dict`（返回 `{flip, needs_reaffirm, applied}` 与 `nodes/risk.py::_evaluate_hysteresis` 同语义）；`nodes/risk.py` 改为调用共享函数（行为保持）；`apply_signal_hysteresis(raw_direction, prior_direction, *, balanced_zone, incremental_claimed) -> tuple[str, dict]`：`needs_reaffirm` 且 `flip` → 维持 `prior_direction`（`applied="keep_prior"`）；均衡带内执行且无增量 → 降为 `flat`（`applied="downgrade_balanced"`，与决策侧降级观望同语义）；否则原样（`applied=""`）。信号路径在 `signal_from_decision` 之后调用（`source` 记录）并把判定写入 `hysteresis_events`。

## 文件结构

| 文件 | 职责 |
|---|---|
| `src/finance_agent/execution/signals.py`（新） | Signal 契约/转换/落库入口 |
| `src/finance_agent/execution/store.py`（新） | `EXECUTION_DB_PATH` 库：signals + hysteresis_events |
| `src/finance_agent/execution/portfolio.py`（新） | 聚合纯函数 |
| `src/finance_agent/execution/risk_gate.py`（新） | 门禁纯函数（无 LLM） |
| `src/finance_agent/execution/hysteresis.py`（新） | 共享滞回判定 + 信号侧应用 |
| `src/finance_agent/nodes/risk.py`（改） | `_evaluate_hysteresis` 改调共享函数（行为保持） |
| `src/finance_agent/screening/constraints.py`（改，追加三常量） | 单一来源 |
| `src/finance_agent/execution/matching.py`（改） | 整手判定 import `LOT_SIZE` |
| `tests/execution/test_signals.py`、`test_portfolio.py`、`test_risk_gate.py`、`test_hysteresis.py`（新） | 单测 |
| `tests/validation/2026-10-13-add-signal-portfolio-risk-gate-*.py/.md`（新） | 20 边界场景核对 + 报告 |

---

### Task 1: Signal 契约 + 落库

- [ ] **Step 1: 写失败测试**：buy→long / hold→flat / sell(exit)→flat / **sell(short)→`SignalContractError` 消息含「direction 非法值: short」**；未 approve → 合同错误；缺 symbol/decision_id → 合同错误；strength 越界 → 错误；落库幂等（同 signal_id 重写不翻倍）；`list_signals` 过滤；**仅 approve 生成**（spec 场景：同日 approve+reject → 只有 approve 落库且 decision_id 指向它）。
- [ ] **Step 2–4: 红 → 实现 → 绿**（G1/G2）。
- [ ] **Step 5: Commit** `feat(execution): Signal contract with decision linkage and persistence (T1)`。

### Task 2: 组合聚合

- [ ] **Step 1: 写失败测试**：等权 3 票 → 各 1/3；强度加权 6 票合计 96% → 合计 80% 等比缩 + `TOTAL_CAP_SCALE_DOWN`（spec 场景逐数）；单票 30% → clip 20% + `SINGLE_CAP_CLIP`；flat 信号不参与；权重和 ≤ 80%+1e-9；排序确定性；空信号 → 空列表。
- [ ] **Step 2–4: 红 → 实现 → 绿**（G3）。
- [ ] **Step 5: Commit** `feat(execution): portfolio target aggregation with caps (T2)`。

### Task 3: 风控门禁

- [ ] **Step 1: 写失败测试**：逐规则正反（8 原因码各一正一反，spec 场景 ST → `ST_FLAG` 且与 L1 同常量）；首个命中即拒 + detail 轨迹；**静态扫描**：AST 解析 `risk_gate.py` 无 llm/gateway/harness import（spec 场景）；`matching.py` 整手判定改为 import `LOT_SIZE` 后既有测试全绿。
- [ ] **Step 2–4: 红 → 实现 → 绿**（G4 + constraints 三常量 + matching 单点化）。
- [ ] **Step 5: Commit** `feat(execution): deterministic risk gate reusing screening constraints (T3)`。

### Task 4: 滞回复用

- [ ] **Step 1: 写失败测试**：共享函数与 `nodes/risk.py` 旧行为一致（对既有 hysteresis 测试覆盖的输入组合逐一断言）；**信号抖动吸收**（spec 场景：前日 long、今日裸 flat 且均衡带内 → 维持 long + 判定落库）；非均衡带翻转有增量 → 不吸收；均衡带内执行无增量 → 降级 flat；`nodes/risk.py` 既有测试零修改通过。
- [ ] **Step 2–4: 红 → 实现 → 绿**（G5）。
- [ ] **Step 5: Commit** `feat(execution): hysteresis shared predicate + signal-path application (T4)`。

### Task 5: 验证与收口

- [ ] 脚本 `tests/validation/2026-10-13-add-signal-portfolio-risk-gate-acceptance.py`：**20 个边界场景**（满仓/超单票/超总仓/涨跌停买/跌停卖/停牌/ST/T+1/零股/多因同单/合法单等），每例**手算期望**（独立算术）→ 跑门禁 → 逐例对照；落 JSON + 报告。
- [ ] `uv run pytest tests/execution tests/nodes -q` + 全量 `tests/ -m "not live"`（记录；既有 env flake 归因）；ruff/mypy。
- [ ] 报告 + `tasks.md` 全勾 →（控制器 archive）。Commit `docs(openspec): add-signal-portfolio-risk-gate validation report + tasks 回填`。
