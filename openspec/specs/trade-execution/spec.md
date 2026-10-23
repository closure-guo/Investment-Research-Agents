# trade-execution Specification

## Purpose
TBD - created by archiving change add-signal-portfolio-risk-gate. Update Purpose after archive.
## Requirements
### Requirement: 标准化 Signal 契约

系统 SHALL 将审批通过的 TradeDecision 转换为 Signal 落库，字段含 `signal_id/symbol/direction/strength/horizon_days/source/decision_id/created_at`；`direction` 限枚举 `long/flat`；缺字段或非法枚举 SHALL 拒收。

#### Scenario: 非法 direction 拒收

- **GIVEN** 一条 direction 为 `short` 的转换输入
- **WHEN** 执行 Signal 转换
- **THEN** 转换失败并抛出合同错误，消息含「direction 非法值: short」

### Requirement: 组合仓位聚合

系统 SHALL 将同一交易日的 active Signal 聚合为目标持仓，支持等权与 strength 加权两档（可配置）；单票目标权重 SHALL 不超过单票上限（默认 20%），组合总仓位 SHALL 不超过总上限（默认 80%），超限部分 SHALL 按比例缩减并记录调整原因。聚合产出 SHALL 再应用市场状态总仓位系数（由 crowding-regime 判定提供，取值 [0, 1]）：每只目标权重 SHALL 乘以系数，系数 = 0 时全部目标权重为 0（flat 目标）；系数 ≠ 1.0 的应用 SHALL 逐票留痕 `REGIME_SCALE`，并 SHALL 在执行摘要记录状态与系数值。市场状态数据缺失或陈旧（最新记录距执行时刻超过 7 个自然日）时系数 SHALL 按 1.0 处理并在执行摘要留痕 `no_regime_data`，MUST NOT 静默降级。

(Previously: 系统 SHALL 将同一交易日的 active Signal 聚合为目标持仓，支持等权与 strength 加权两档（可配置）；单票目标权重 SHALL 不超过单票上限（默认 20%），组合总仓位 SHALL 不超过总上限（默认 80%），超限部分 SHALL 按比例缩减并记录调整原因。)

#### Scenario: 总仓位超限缩减

- **GIVEN** 6 条 long 信号按强度加权后合计目标仓位 96%
- **WHEN** 组合聚合执行
- **THEN** 输出合计 80%，各票等比例缩减，且调整记录含「TOTAL_CAP_SCALE_DOWN」

#### Scenario: 系数缩放

- **GIVEN** 聚合后目标合计 80%，当日 regime 判定为 elevated、系数 0.5
- **WHEN** 组合聚合执行
- **THEN** 各目标权重乘 0.5、合计 40%
- **AND** 逐票调整记录含 `REGIME_SCALE`；执行摘要含状态 elevated 与系数 0.5

#### Scenario: 空仓系数产出 flat 目标

- **GIVEN** 当日 regime 判定为 danger、系数 0
- **WHEN** 组合聚合执行
- **THEN** 全部目标权重为 0（flat 目标）
- **AND** 逐票调整记录含 `REGIME_SCALE`；执行摘要含状态 danger 与系数 0

#### Scenario: 状态数据缺失披露式降级

- **GIVEN** 市场状态记录缺失或距执行时刻超过 7 个自然日
- **WHEN** 组合聚合执行
- **THEN** 系数按 1.0 处理（聚合结果与无系数时一致）
- **AND** 执行摘要留痕 `no_regime_data`（MUST NOT 静默）

#### Scenario: 系数为 1.0 时不产生调整记录

- **GIVEN** 当日 regime 判定为 normal、系数 1.0
- **WHEN** 组合聚合执行
- **THEN** 目标权重与无系数时逐项一致
- **AND** 不产生 `REGIME_SCALE` 调整记录

### Requirement: 确定性风控门禁

所有订单 SHALL 通过 `risk_gate.py` 纯代码门禁方可进入执行：检查项含单票仓位上限、组合总仓位上限、T+1、涨跌停/停牌/ST 状态（约束定义 import 自 `screening/constraints.py`）、100 股整数倍；门禁拒绝 SHALL 返回结构化原因码；门禁模块 SHALL NOT 发起或依赖任何 LLM 调用。

#### Scenario: 门禁无 LLM 依赖

- **GIVEN** 实现完成后的代码库
- **WHEN** 对 `execution/risk_gate.py` 做静态扫描
- **THEN** 无任何 LLM gateway/harness 的 import

#### Scenario: ST 状态门禁拒绝

- **GIVEN** 目标持仓调整后需买入标的 X，X 当日状态为 ST
- **WHEN** 订单进入风控门禁
- **THEN** 订单被拒绝，原因码 `ST_FLAG`，与 L1 过滤使用同一约束常量

### Requirement: 信号到订单的日批执行

系统 SHALL 提供日批执行通道，按以下顺序编排且全程纯代码（无 LLM）：① 将审批通过且尚未生成信号的决策转换为 Signal 落库（复用信号契约转换；同一 `decision_id` 重复执行 SHALL NOT 重复生成）——决策取源 SHALL 为观点记录（`predictions`）中 `status='open'`、基金审批通过（`rationale_snapshot.fund_manager_decision == 'approve'`）且决策日早于执行日的行，存量窗口默认近 5 个自然日、窗口外旧观点 SHALL NOT 回填执行（摘要 `skipped_backlog` 留痕）；信号转换 SHALL NOT 依赖行情数据，快照不可用时 SHALL 照常落库；② 将尚未产生订单的 Signal 按其交易日聚合为目标持仓；③ 与当前持仓差分生成订单（100 股整手；`flat` 信号 SHALL 产生平仓目标）；④ 逐单经确定性风控门禁；⑤ 通过者由 paper broker 以**执行日开盘**口径撮合（决策日次一交易日开盘，`fill=开盘价×(1±滑点)`），不可成交者沿用 pending 与次日重试。快照不可用时 SHALL 跳过订单撮合与持仓变动并记录原因（`no_snapshot_data`），MUST NOT 伪造成交。同一决策/信号的重复执行 SHALL NOT 产生重复订单。

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
- **THEN** 跳过本次订单撮合并记录原因（`no_snapshot_data`）
- **AND** 不产生任何订单与持仓变动；决策→信号转换不依赖行情、SHALL 照常落库（不重复生成），订单待快照恢复后的执行日撮合

#### Scenario: 未审批决策不执行

- **GIVEN** 一条 `status='open'` 但基金审批结论为 reject 的观点记录
- **WHEN** 执行日批运行
- **THEN** 不生成 Signal、不产生订单
- **AND** 摘要 `skipped_unapproved` 计数留痕

#### Scenario: 存量窗口限制回填范围

- **GIVEN** 观点库存在一条窗口外（默认近 5 个自然日之外）的 open 且审批通过的决策
- **WHEN** 执行日批运行
- **THEN** 该决策不生成 Signal、不产生订单
- **AND** 摘要 `skipped_backlog` 计数留痕；显式放宽窗口参数后可执行

