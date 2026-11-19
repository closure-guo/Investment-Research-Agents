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

### Requirement: 决策约束字段随信号传递

系统 SHALL 在 决策→信号 转换阶段从 `rationale_snapshot` 解析决策层执行约束字段——条件入场触发位（`trigger_high`/`trigger_low`）、仓位档位（`position_size` 档位名 light/moderate/heavy）、`exit_schedule` 首批比例——并随 `Signal` 传递至订单规划层。任一字段缺失或不可解析时，SHALL 置空并退化为既有行为（等权、全额退出），MUST NOT 因字段缺失拒绝信号或阻断批次；退化 SHALL 在执行摘要留痕。

#### Scenario: 完整申报的 buy 信号

- **GIVEN** 一条 rationale_snapshot 含 `trigger_high=22.35`、`position_size=light` 的 open buy 预测
- **WHEN** 执行日批执行 决策→信号 转换
- **THEN** 生成的 Signal 携带 trigger_high=22.35 与 position_tier=light
- **AND** 该信号进入订单规划时按 light 档位参与（不按等权）

#### Scenario: 旧格式信号退化

- **GIVEN** 一条 rationale_snapshot 无 trigger_high 且 position_size 为百分比串（落库为 REAL）的存量 buy 预测
- **WHEN** 执行日批执行转换
- **THEN** Signal 的 trigger_high 与 position_tier 为空
- **AND** 订单规划按等权处理（既有行为），执行摘要留痕 `no_position_tier`

### Requirement: 条件入场未触发不建仓

订单规划层 SHALL 对申报了 `trigger_high` 的 buy 信号执行触发门：执行日 bar 收盘价未突破 `trigger_high` 时 MUST NOT 生成买入订单，且 MUST 以结构化明细留痕（标的 + 原因码 `TRIGGER_NOT_MET` + 触发位与实际收盘），供台账回查；触发满足时正常进入下单流程。留痕 MUST NOT 静默丢弃或仅计裸计数。`trigger_low`（下破型入场）本期不实现，消费侧忽略该字段（设计文档已披露）。

#### Scenario: 触发未满足

- **GIVEN** 一条 Signal trigger_high=22.35（条件入场），执行日 bar 收盘 20.79
- **WHEN** 订单规划运行
- **THEN** 该标的不产生任何买入订单
- **AND** 执行摘要 skipped 明细含该标的、原因码 `TRIGGER_NOT_MET`、trigger 22.35 与收盘 20.79

#### Scenario: 触发满足

- **GIVEN** 同一 Signal，后续执行日 bar 收盘 22.61（> 22.35）
- **WHEN** 订单规划运行
- **THEN** 该标的按其档位生成买入订单
- **AND** 无 `TRIGGER_NOT_MET` 留痕

### Requirement: 仓位档位映射目标权重

订单规划层 SHALL 将 `position_tier` 映射为目标权重下沿锚：light=10%、moderate=30%、heavy=50%（口径与 `src/finance_agent/prompts/trader.md` 档位定义 light=试探 10-20%、moderate=标准 30-50%、heavy=50%+ 对齐，取下沿为保守锚）。映射后的权重 SHALL 仍受单票上限与总仓上限约束（既有 clip 规则不变）；未申报档位的信号 SHALL 按既有等权聚合处理并留痕 `no_position_tier`。

#### Scenario: light 档位下单

- **GIVEN** equity 100 万，一条 position_tier=light 的有效 buy 信号（触发已满足）
- **WHEN** 订单规划计算目标仓位
- **THEN** 该标的目标权重为 10%（目标市值 10 万），而非等权 1/n×上限
- **AND** 目标值不超过单票上限

#### Scenario: 多档位并存

- **GIVEN** 同批两条 long 信号，分别为 light 与 moderate
- **WHEN** 订单规划聚合
- **THEN** 两条信号目标权重分别为 10% 与 30%
- **AND** 总仓不超过总仓上限；超限时按既有 clip 规则截断并留痕

### Requirement: exit_schedule 首批比例执行

对申报了 `exit_schedule` 首批减仓比例的 exit 决策，首个执行日 SHALL 仅卖出 `min(首批比例, 可卖持仓)`（整手取整）；剩余持仓 SHALL 保留并按 schedule 后续触发条件在后续执行日评估。首批是否已执行的状态 SHALL 持久化，同一决策 MUST NOT 重复执行首批。`exit_schedule` 不可解析或未申报首批比例时，SHALL 退化为既有全额退出并留痕 `schedule_unparsed`。

#### Scenario: 首批 70% 减仓

- **GIVEN** 一条 exit 决策 exit_schedule 首批 70%，当前持仓 9,100 股
- **WHEN** 首个执行日运行
- **THEN** 卖出订单数量为 6,300 股（9,100×70% 整手取整）
- **AND** 剩余 2,800 股保留，不生成卖出订单

#### Scenario: 首批不重复

- **GIVEN** 上述决策首批已于前一执行日卖出
- **WHEN** 本执行日运行且 schedule 后续价格触发均未满足
- **THEN** 不产生新的卖出订单
- **AND** 持仓剩余批次不变

### Requirement: 执行对计划偏差留痕

执行日批摘要 SHALL 含 `plan_deviation` 段，逐单记录：实际成交均价 vs 决策 `entry_price` 的偏差、实际目标权重 vs 档位声明、分批执行进度 vs `exit_schedule`。该段 SHALL 可经执行台账只读接口回查；偏差本身不阻断成交（记录性约束，非门禁）。

#### Scenario: 成交价偏差记录

- **GIVEN** 一笔 buy 计划 entry_price=22.40，实际成交均价 21.68
- **WHEN** 执行日批落摘要
- **THEN** plan_deviation 含该单的 entry 偏差（-3.2%）与档位声明对照
- **AND** 台账查询可获取该记录

