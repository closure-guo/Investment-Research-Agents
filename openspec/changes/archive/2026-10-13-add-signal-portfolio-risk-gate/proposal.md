# Proposal: add-signal-portfolio-risk-gate

## Why

- 当前 TradeDecision 是"报告级"产物，无法直接驱动执行：缺标准化 Signal 契约、缺组合层仓位分配、缺执行前的确定性风控门禁。
- 风控约束若交由 LLM 自觉遵守必然失效（采样抖动、prompt 注入、幻觉），门禁必须是 LLM 无绕过路径的纯代码——这与项目 validate_trade_prices / 勾稽校验 fail-fast 的既有哲学一致。
- 多来源信号（选股管线、用户手动分析）未来会同时存在，需要一个聚合层统一仲裁。

## What Changes

- 新增 Signal 契约：`{signal_id, symbol, direction(long/flat), strength(0-1), horizon_days, source(pipeline/manual), decision_id, created_at}`，由 TradeDecision 转换生成并落库。
- 新增 `execution/portfolio.py`：信号聚合成目标持仓（初版两档：等权 / 按 strength 加权，可配置），输出目标仓位列表。
- 新增 `execution/risk_gate.py` 纯代码门禁：单票仓位上限（默认 20%）、组合总仓位上限（默认 80%）、T+1 与涨跌停/停牌/ST 检查（import 复用 `screening/constraints.py`）、订单数量 100 股整数倍；门禁拒绝返回结构化原因码，LLM 输出仅作为门禁输入。
- 每笔通过门禁的订单挂 `decision_id` + `signal_id`，保证证据链可回溯。
- 决策滞回机制（`decision-hysteresis`）从落库决策层复用到信号生成前，防信号日际抖动。

非目标（Out of scope）：

- 不实现 Broker 与撮合（属 `add-paper-broker`）。
- 不做做空/融券信号（direction 仅 long/flat）。
- 不做组合优化器（风险平价等留待评估证明需要）。

## Capabilities

### New Capabilities
- `trade-execution`: 信号契约、组合仓位聚合与确定性风控门禁。

### Modified Capabilities
- `decision-outcome`: TradeDecision 增加向 Signal 的转换出口。
- `decision-hysteresis`: 滞回机制复用到信号生成路径。

## Impact

- **后端**：`src/finance_agent/execution/signals.py`、`execution/portfolio.py`、`execution/risk_gate.py`（新增）。
- **测试**：Signal 合同单测；门禁逐规则正反单测；门禁无 LLM 依赖的静态保证；滞回复用单测。
- **验证**：构造 20 个边界场景（满仓/超上限/涨跌停/T+1）人工核对门禁判定，报告落 `tests/validation/`。
