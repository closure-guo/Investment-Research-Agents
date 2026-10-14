# trade-execution delta: add-execution-chain-driver

## ADDED Requirements

### Requirement: 信号到订单的日批执行

系统 SHALL 提供日批执行通道，按以下顺序编排且全程纯代码（无 LLM）：① 将审批通过且尚未生成信号的决策转换为 Signal 落库（复用信号契约转换；同一 `decision_id` 重复执行 SHALL NOT 重复生成）；② 将尚未产生订单的 Signal 按其交易日聚合为目标持仓；③ 与当前持仓差分生成订单（100 股整手；`flat` 信号 SHALL 产生平仓目标）；④ 逐单经确定性风控门禁；⑤ 通过者由 paper broker 以**执行日开盘**口径撮合（决策日次一交易日开盘，`fill=开盘价×(1±滑点)`），不可成交者沿用 pending 与次日重试。快照不可用时该次执行 SHALL 跳过并记录原因，MUST NOT 伪造成交。同一决策/信号的重复执行 SHALL NOT 产生重复订单。

#### Scenario: 决策到持仓闭环

- **GIVEN** 一条审批通过的买入决策（600519，confidence 0.8）与一份当日可成交快照
- **WHEN** 执行日批运行
- **THEN** 生成对应 Signal 落库（含 `decision_id`）、按目标权重生成买入订单并成交
- **AND** 持仓表新增 600519、资金按成交价+费用扣减、订单含 `decision_id` 与 `signal_id`

#### Scenario: 幂等重跑

- **GIVEN** 同一条决策已在上一执行日完成信号生成与下单
- **WHEN** 执行日批再次运行（重试/手动补跑）
- **THEN** 不重复生成 Signal、不产生重复订单
- **AND** 摘要中 `signals_created=0`、订单零新增

#### Scenario: flat 信号平仓

- **GIVEN** 持仓含 600519 500 股，当日出现该标的的卖出（`flat`）决策信号
- **WHEN** 执行日批运行
- **THEN** 生成卖出订单并（在可成交时）清空该持仓
- **AND** 资金按成交价扣费入账

#### Scenario: 快照不可用跳过

- **GIVEN** 快照库为空或当日快照缺失
- **WHEN** 执行日批运行
- **THEN** 跳过本次执行并记录原因（`no_snapshot_data`）
- **AND** 不产生任何订单与持仓变动，信号顺延至下一交易日
