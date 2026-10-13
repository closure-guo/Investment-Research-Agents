# add-paper-broker Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Broker 抽象 + paper 仿真 broker（订单经 matching-engine 撮合、持仓/资金 SQLite 落库、涨跌停挂单次日记重试/过期）、每日结算复用 outcome 日批、战绩页虚拟 vs 仿真双曲线与执行损耗标注、README 免责声明。

**Architecture:** `execution/brokers/base.py`（ABC：place_order/query_positions/query_cash/cancel）；`execution/brokers/paper.py`（PaperBroker：`match_order` 撮合 → orders/positions/cash 三表落库；挂单状态机 pending/filled/rejected/expired）；结算 `settle_daily(as_of)` 注册为 ops 日批（16:20，`_with_retry`）；战绩接口在既有 track-record overview 上追加 `paper` 块（虚拟净值序列 + 仿真净值序列 + 损耗），前端在既有净值图上加第二条序列与图例。

**Tech Stack:** Python 3.14 / sqlite3 / pandas / React 18 + Vitest / Playwright。

## Global Constraints

- 测试：`uv run pytest tests/execution -q`；`cd frontend && npx vitest run`；E2E `npx playwright test tests/paper-broker.spec.ts`（默认 config）。
- TDD 红→绿；既有测试零修改（ops 注册表计数断言 7→8 最小更新，先例同 change 1；track-record 前端既有测试零修改，新断言只追加）。
- 原因码/状态（逐字）：订单状态 `pending`/`filled`/`rejected`/`expired`；未成交原因复用 matching 码（`LIMIT_UP_UNFILLABLE` 等）；损耗标注文案「执行损耗」。
- 初始资金 100 万元（spec 场景）；`place_order` 必填 `decision_id`/`signal_id`（证据链）。
- 实盘接口仅留抽象：`base.py` 只定义接口；本 change 不实现任何 QMT/PTrade。
- README 免责声明逐字要素：「仅供研究，不构成投资建议」。
- live 依赖：无（回放核对用合成面板；2026-09 真实回放受东财 IP 封禁 issue #325 → 合成替代 + 如实记录）。

## 关键设计决策

**B1 — PaperBroker 接口与落库**：`PaperBroker(db_path=None, *, config: MatchingConfig | None = None, initial_cash: float = 1_000_000.0)`：
- `place_order(order: OrderIntent, *, bar: MarketBar, position: PositionContext | None = None, market_amounts=None) -> OrderRecord`：调 `match_order`；成交 → 扣/加资金（成交额 + fee；卖出加回并扣印花税）、更新持仓（加权成本价）、订单 `filled`；未成交 → 订单 `pending` + 原因码落库（不产生持仓/资金变动，spec 场景）。
- `retry_pending(as_of, *, bars: dict[str, MarketBar]) -> dict`：对 `pending` 订单逐单重撮合；仍不成交 → 记 `attempts+1`；超过 `pending_expire_days=3`（配置）→ `expired`。
- `query_positions() -> list[dict]`（symbol/qty/avg_cost/updated_at）、`query_cash() -> float`、`cancel(order_id)`（仅 pending 可撤）。
- 三表：`orders(order_id PK, symbol, side, qty, status, reason, fill_price, fee, decision_id, signal_id, created_at, attempts)`、`positions(symbol PK, qty, avg_cost, updated_at)`、`cash(id PK=1, balance, updated_at)`；`EXECUTION_DB_PATH` 复用（与 signals 同库不同表）。
- 资金不足（买入成交额+fee > cash）→ 订单 `rejected` + `reason="INSUFFICIENT_CASH"`（结构化，非抛错）。

**B2 — 每日结算**：`settle_daily(as_of, *, close_prices: dict[str, float], db_path=None) -> dict`：把持仓按收盘价 mark 成 `paper_equity(trade_date PK, cash, positions_value, equity)` 曲线（快照收盘价注入，生产由 job 从快照库取）；幂等 upsert；注册 ops job `paper_settle_daily`（16:20 mon-fri，`_with_retry`，注册表 7→8 + 计数断言最小更新）。

**B3 — 双曲线接口**：`GET /api/track-record/overview` 追加 `paper` 块：`{"series": [{"date", "virtual_equity", "paper_equity"}...], "loss_pct": <最新 (paper/virtual-1)*100 保留 1 位>, "base_date": ..., "note": "两条曲线同一批决策同一基准期；差值为执行损耗"}`；无 paper 数据 → `paper: null`（前端隐藏系列）。虚拟净值序列取既有 equity_curve（真实口径），paper 序列取 `paper_equity` 表；对齐规则：仅共同日期；**基期只向过去回退的防前视纪律不变**（两序列都只用 ≤ as_of 数据）。

**B4 — 前端**：`TrackRecordPage` 的净值图加第二条序列（图例「虚拟结算」「仿真成交」）+ 损耗标注（图例区文本「执行损耗: -1.2%」）；Vitest 断言两序列与标注文本；E2E 用 TESTING seed 造 paper_equity 行（seed 扩展 `track_record.paper_equity`）→ 断言双曲线与损耗文案。

## 文件结构

| 文件 | 职责 |
|---|---|
| `src/finance_agent/execution/brokers/__init__.py`、`base.py`、`paper.py`（新） | 抽象 + paper 实现 |
| `src/finance_agent/execution/store.py`（改，追加 orders/positions/cash/paper_equity 表与读写） | 落库 |
| `src/finance_agent/outcome/ops/jobs.py`、`outcome/scheduler.py`（改） | `paper_settle_daily` 16:20 注册 |
| `src/finance_agent/api.py`（改） | overview 追加 `paper` 块；TESTING seed 扩展 `paper_equity` |
| `frontend/src/pages/trackRecord/TrackRecordPage.tsx`（改）+ `frontend/src/test/paperCurve.test.tsx`（新） | 双曲线 + 损耗标注 |
| `tests/execution/test_paper_broker.py`、`tests/outcome/test_scheduler*.py`（追加） | 单测 |
| `tests/e2e/playwright/tests/paper-broker.spec.ts`（新） | E2E |
| `tests/validation/2026-10-13-add-paper-broker-*.py/.md`（新） | 回放核对 + 报告 |

---

### Task 1: Broker 抽象 + paper 全链路

- [ ] **Step 1: 写失败测试**：抽象接口存在且 paper 实现之（`isinstance` 检查）；**spec 场景**（初始 100 万、买入 600519×100、快照可成交 → 持仓 +100、资金按成交价+fee 扣减、订单含 decision_id/signal_id）；**一字涨停买入 → `pending` + `LIMIT_UP_UNFILLABLE` + 零持仓变动**（spec 场景）；资金不足 → `rejected` + `INSUFFICIENT_CASH`；卖出路径（T+1 通过时资金加回）；`query_positions/query_cash/cancel`（pending 可撤、filled 不可撤）；`retry_pending`（次日可成交 → filled；3 日不成交 → expired）；落库幂等（同 order_id 重跑不翻倍）。
- [ ] **Step 2–4: 红 → 实现 → 绿**（B1）。
- [ ] **Step 5: Commit** `feat(execution): Broker abstraction + paper broker via matching engine (T1)`。

### Task 2: 每日结算 + 日批注册 + 双曲线接口

- [ ] **Step 1: 写失败测试**：`settle_daily` 幂等 upsert + 净值 = cash + Σ(qty×close)；ops 注册（id/16:20/重试包装；计数 7→8 最小更新）；overview `paper` 块（对齐共同日期、loss_pct 计算、无数据 → null、as_of 防前视）；seed 扩展。
- [ ] **Step 2–4: 红 → 实现 → 绿**（B2/B3）。
- [ ] **Step 5: Commit** `feat(execution): paper daily settlement + dual-curve overview block (T2)`。

### Task 3: 前端双曲线 + Vitest

- [ ] **Step 1: 写失败测试**（Vitest，fetch mock）：两条序列渲染 + 图例「虚拟结算」「仿真成交」；损耗标注文本（spec 场景 1.043/1.031 → 「-1.2%」）；`paper: null` → 单曲线不报错。
- [ ] **Step 2–4: 红 → 实现 → 绿**（B4）；`npm run build`。
- [ ] **Step 5: Commit** `feat(frontend): virtual vs paper equity curves with execution-loss annotation (T3)`。

### Task 4: E2E + 回放核对 + 收口

- [ ] E2E `paper-broker.spec.ts`（默认 config，端口预检）：seed `track_record.paper_equity` + equity_curve → 打开战绩页 → 双曲线图例与损耗标注可见（web-first 断言，无 route mock）。
- [ ] 回放核对脚本（合成面板回放一个月、≥10 笔成交）：逐笔核对成交价/费用 vs 独立手算（独立算术），落 JSON + 报告；如实记录 2026-09 真实回放受阻（#325）与合成替代。
- [ ] README 免责声明增补（逐字要素）。
- [ ] `uv run pytest tests/execution tests/outcome -q` + 全量 + vitest + E2E；报告 + `tasks.md` 全勾 →（控制器 archive）。
