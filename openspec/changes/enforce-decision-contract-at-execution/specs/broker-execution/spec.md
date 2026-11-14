# Delta for broker-execution

## ADDED Requirements

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

订单规划层 SHALL 对申报了 `trigger_high`（或 `trigger_low`）的 buy 信号执行触发门：执行日 bar 收盘价未突破 `trigger_high`（或未跌破 `trigger_low`）时 MUST NOT 生成买入订单，且 MUST 以结构化明细留痕（标的 + 原因码 `TRIGGER_NOT_MET` + 触发位与实际收盘），供台账回查；触发满足时正常进入下单流程。留痕 MUST NOT 静默丢弃或仅计裸计数。

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
