# broker-execution delta: add-execution-chain-driver

## ADDED Requirements

### Requirement: 门禁拒绝留痕

被确定性风控门禁拒绝的订单 SHALL 以 `rejected` 状态与结构化原因码落库（`orders` 表，含 `decision_id` 与 `signal_id`），供台账与门禁日志回查；MUST NOT 静默丢弃。原因码语义与门禁返回逐字一致（如 `ST_FLAG`、`INSUFFICIENT_CASH`、`LIMIT_UP_UNFILLABLE`、`T1_SELL_BLOCKED`、`ODD_LOT`）。

#### Scenario: 门禁拒绝可回查

- **GIVEN** 一条目标买入订单因标的状态为 ST 被门禁拒绝
- **WHEN** 查询订单台账
- **THEN** 该订单以 `rejected` 状态出现，原因码 `ST_FLAG`，且关联 `decision_id` 与 `signal_id`
- **AND** 账户资金与持仓零变动

#### Scenario: 拒绝不阻断其余订单

- **GIVEN** 同批订单中一条被门禁拒绝、其余通过
- **WHEN** 执行日批运行
- **THEN** 通过者正常成交
- **AND** 被拒者落库留痕，摘要含 `rejected` 计数
