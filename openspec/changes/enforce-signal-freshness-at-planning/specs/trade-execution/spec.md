# Delta for trade-execution

## ADDED Requirements

### Requirement: 信号执行前校验（来源状态与方向冲突）

执行规划层在消费尚未产生订单的 pending 信号前，SHALL 逐信号重验其来源决策（`decision_id` → 观点记录）的当前 `status`：非 `open`（含 `resolved_*`/`superseded`/`duplicate_of_day`/`void`）来源的信号 SHALL 跳过、不产生订单，并以结构化明细留痕（标的 + 原因码 `STALE_SOURCE` + `decision_id`）。同标的存在多条通过来源校验的 pending 信号时，SHALL 仅决策日（`signal.created_at`）最新者参与规划，更旧者 SHALL 跳过并留痕（`SUPERSEDED_BY_NEWER` + 胜者 `signal_id`）。校验对 `long` 与 `flat` 方向同等生效。已产生订单的信号与既有订单 SHALL NOT 受本校验影响（幂等锚点不变）。留痕 MUST NOT 静默丢弃。

#### Scenario: 已结算来源的遗留 flat 信号不再击穿新仓

- **GIVEN** 一条 12 天前 short 决策（已 `resolved_win`）遗留的 pending flat 信号，标的当前持仓来自更晚的 buy 决策
- **WHEN** 执行日批规划运行
- **THEN** 不产生任何卖出订单
- **AND** 摘要 `skipped_details` 含该标的、原因码 `STALE_SOURCE` 与来源 `decision_id`；buy 决策建立的持仓保留

#### Scenario: 同标的待执行信号 newest-wins

- **GIVEN** 同标的两条通过来源校验的 pending 信号：较旧决策日（11-05）的 flat 与较新决策日（11-12）的 long，来源均为 `open`
- **WHEN** 执行日批规划运行
- **THEN** 仅较新的 long 参与规划并按其约束生成订单
- **AND** 较旧的 flat 被跳过，`skipped_details` 含 `SUPERSEDED_BY_NEWER` 与胜者 `signal_id`

#### Scenario: 合规信号不受影响（回归护栏）

- **GIVEN** 单条来源 `open` 且为该标的最新决策的 long 信号
- **WHEN** 执行日批规划运行
- **THEN** 按既有流程正常建仓（含触发门/档位约束）
- **AND** `skipped_details` 不含 `STALE_SOURCE` 或 `SUPERSEDED_BY_NEWER`
