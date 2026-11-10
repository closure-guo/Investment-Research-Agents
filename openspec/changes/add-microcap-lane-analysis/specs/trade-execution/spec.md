## MODIFIED Requirements

### Requirement: 信号到订单的日批执行

系统 SHALL 提供日批执行通道，按以下顺序编排且全程纯代码（无 LLM）：① 将审批通过且尚未生成信号的决策转换为 Signal 落库（复用信号契约转换；同一 `decision_id` 重复执行 SHALL NOT 重复生成）——决策取源 SHALL 为观点记录（`predictions`）中 `status='open'`、`lane='main'`、基金审批通过（`rationale_snapshot.fund_manager_decision == 'approve'`）且决策日早于执行日的行，存量窗口默认近 5 个自然日、窗口外旧观点 SHALL NOT 回填执行（摘要 `skipped_backlog` 留痕）；信号转换 SHALL NOT 依赖行情数据，快照不可用时 SHALL 照常落库；② 将尚未产生订单的 Signal 按其交易日聚合为目标持仓；③ 与当前持仓差分生成订单（100 股整手；`flat` 信号 SHALL 产生平仓目标）；④ 逐单经确定性风控门禁；⑤ 通过者由 paper broker 以**执行日开盘**口径撮合（决策日次一交易日开盘，`fill=开盘价×(1±滑点)`），不可成交者沿用 pending 与次日重试。快照不可用时 SHALL 跳过订单撮合与持仓变动并记录原因（`no_snapshot_data`），MUST NOT 伪造成交。同一决策/信号的重复执行 SHALL NOT 产生重复订单。主档通道仅消费 `lane='main'` 观点；`lane='microcap'` 观点由小票执行通道（见「小票执行通道与独立账本」）处理，MUST NOT 进入主档账本。

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

#### Scenario: 小票观点不进主档账本

- **GIVEN** 当日存在一条 `lane='microcap'` 的审批通过 open 观点
- **WHEN** 主档执行日批（`paper_execute_daily`）运行
- **THEN** 该观点不生成 Signal、不产生订单
- **AND** 主档账本持仓与净值不含该标的变动

## ADDED Requirements

### Requirement: 小票执行通道与独立账本

系统 SHALL 提供小票 lane 独立纸面账本与执行通道，与主档账本完全隔离：

- 账本：独立执行库（`EXECUTION_MICROCAP_DB_PATH`，默认 `data/execution_microcap.db`），
  signals / hysteresis_events / orders / positions / cash / paper_equity 同 DDL 首连自建；
  两库 MUST NOT 共享写路径；
- 编排：新 job `paper_execute_microcap`（16:12，先于小票结算）仅消费 `lane='microcap'`
  的审批通过观点，编排语义与主档一致（决策日早于执行日、次一交易日开盘撮合、
  `decision_id` 幂等锚点、快照守卫 `no_snapshot_data`、失败批次上抛进 ops 历史）；
  首日无小票观点 SHALL 空转返回摘要如实披露，MUST NOT 伪造成交或报错；
- 执行经济学（仅两处与主档不同，保持两账本可比）：单票目标权重上限 **5%**
  （`MICROCAP_SUGGESTED_SINGLE_CAP` 转正为生效约束，超限缩减复用既有披露口径）；
  滑点在按成交额分位取档基础上**索引上移 1 档**（`MICROCAP_SLIPPAGE_TIER_BUMP`，
  封顶末档，`slippage_rate_for_amount` 以 `tier_bump` 参数实现）；总仓上限、regime
  系数、T+1、整手与门禁规则、初始资金（100 万）与主档一致；
- 结算：新 job `paper_settle_microcap`（16:24）按主档结算口径将小票账本净值写入
  小票库 `paper_equity`，两账本净值互不混算；
- 主档行为不变性：lane 列为 `ADD COLUMN DEFAULT 'main'` 幂等加列，主档通道加过滤后
  对存量观点的行为与变更前逐位一致；
- 「自动选股分析的执行衔接」需求约束主档 lane 衔接（行为不变）；小票 lane 的衔接由
  本需求约束（同规则、独立账本、16:12 编排）。

#### Scenario: 小票观点进独立账本

- **GIVEN** 昨日小票批产出的 approve+buy 观点（`lane='microcap'`）
- **WHEN** `paper_execute_microcap` 运行
- **THEN** 信号与订单落独立执行库，按执行日开盘价撮合
- **AND** 主档执行库五表零变动

#### Scenario: 小票滑点档位上移

- **GIVEN** 某订单成交额落入默认分位档 i（滑点率 r_i）
- **WHEN** 小票账本撮合
- **THEN** 实际滑点取档 i+1（r_{i+1}）；i 已为末档时取末档
- **AND** 主档账本同额订单仍取档 i

#### Scenario: 单票 5% 上限

- **GIVEN** 小票账本权益 100 万、某信号目标权重超 5%
- **WHEN** 聚合目标持仓
- **THEN** 该标的权重被压至 5%，超限缩减留痕
- **AND** 主档账本同额信号仍按 20% 上限聚合

#### Scenario: 首日空转如实披露

- **GIVEN** 小票 lane 无 open 审批通过观点
- **WHEN** `paper_execute_microcap` 运行
- **THEN** 摘要如实披露零信号零订单，批次 ok
- **AND** MUST NOT 伪造成交或报错

#### Scenario: 小票账本净值独立结算

- **GIVEN** 小票账本当日有成交或持仓
- **WHEN** `paper_settle_microcap` 运行
- **THEN** 净值行写入小票库 paper_equity（幂等 upsert）
- **AND** 主档 paper_equity 不含小票账本数据
