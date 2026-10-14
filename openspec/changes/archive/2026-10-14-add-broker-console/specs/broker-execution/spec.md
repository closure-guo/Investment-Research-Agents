# broker-execution delta: add-broker-console

## ADDED Requirements

### Requirement: 台账只读查询

系统 SHALL 提供执行台账只读接口：资金与总览（可用资金 / 持仓市值 / 总资产 / 浮动盈亏）、持仓列表、订单列表（含状态 `pending` / `filled` / `rejected` / `expired`、原因码、`decision_id` 与 `signal_id`）与门禁拒绝视图（被风控门禁拒绝的订单及其原因码）。快照不可用导致持仓市值不可得时，SHALL 返回 `null` 并附可判读标注，MUST NOT 以 0 冒充。接口 SHALL NOT 暴露下单 / 撤单等写操作。

#### Scenario: 空账户如实

- **GIVEN** 账户无持仓、无订单
- **WHEN** 请求总览与列表
- **THEN** 返回初始资金、持仓与订单为空列表
- **AND** 持仓市值与浮动盈亏为 null（无快照）或 0（有快照且无持仓），不伪数据

#### Scenario: 门禁拒绝可回查

- **GIVEN** 一条因 `ST_FLAG` 被门禁拒绝的订单
- **WHEN** 请求门禁拒绝视图
- **THEN** 该订单出现且含原因码 `ST_FLAG`、标的与关联 `decision_id`
