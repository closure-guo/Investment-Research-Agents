# broker-execution Specification

## Purpose
TBD - created by archiving change add-paper-broker. Update Purpose after archive.
## Requirements
### Requirement: Broker 抽象接口

系统 SHALL 定义 Broker 抽象接口（place_order/query_positions/query_cash/cancel）；全部执行调用 SHALL 面向该接口编程；实盘实现（QMT/PTrade 等） SHALL 仅通过新增接口实现接入，禁止绕过抽象直连。

#### Scenario: paper broker 下单闭环

- **GIVEN** paper 账户初始资金 100 万元，风控门禁通过一条买入 600519 共 100 股的订单
- **WHEN** 调用 `place_order` 且当日快照可成交
- **THEN** 持仓表新增 600519 共 100 股，资金按成交价+费用扣减
- **AND** 订单记录含 decision_id 与 signal_id

### Requirement: 仿真撮合走统一引擎

paper broker 的成交判定 SHALL 调用 matching-engine 的撮合函数，禁止在 broker 内重新实现成交逻辑；无法成交（涨跌停等）的订单 SHALL 保持挂单状态并在次日重试或按配置过期。

#### Scenario: 涨跌停订单挂单

- **GIVEN** 一条买入订单对应标的一字涨停
- **WHEN** paper broker 执行撮合
- **THEN** 订单状态为 pending，原因码 `LIMIT_UP_UNFILLABLE`
- **AND** 不产生持仓变动

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

