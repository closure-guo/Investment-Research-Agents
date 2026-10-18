# Delta for track-record

> 基线说明：本 MODIFIED 块已按**主规范现文全文**重写（保全部既有场景，含
> horizon 存量不追溯、结算入场价等后续演进条款），仅并入 session_id 落库与
> 触发位两列增量（2026-10-18 archive 前对齐，见 openspec-archive-delta-drift 教训）。

## MODIFIED Requirements

### Requirement: 观点数据模型（append-only + 快照冻结）

系统 SHALL 建立 `predictions` 表记录每条观点（Agent 一次可判定输出的最小单元：标的、方向、时间窗口，缺任一要素不进入统计）。表含 `source_type`（backtest/live，回测实盘分离）、`direction`（long/short/neutral）、`confidence`（0-1，校准用）、`rationale_snapshot`（**观点摘要字段**：`action`、`fund_manager_decision` 及其 reasoning、**TradeDecision 申报价位 entry/stop/target**；完整分析原文 SHALL 存于会话报告并可由该观点的 `session_id` 关联，不重复存于快照）、`session_id`（关联会话报告——此前为规范承诺但未落库，本次补齐）、`trigger_high`/`trigger_low`（watch 观点的双向触发位，取自终稿决策结构化申报，供后续触发监控消费；未申报或非 watch 决策时为 NULL，存为数值）、`entry_price`（**参考价：决策时点实时价或最近收盘，供展示与盯市**）、`horizon_days`（默认取配置项 `OUTCOME_DEFAULT_HORIZON_DAYS`，默认值 20 交易日，上限 1 年）、`avoidance_status`（neutral 观点回避判定结果，系统计算写入，可空）、`settle_entry_price`（**结算入场价：判定时按标准化口径从行情派生**——决策归属日收盘；收盘后决策取次一交易日收盘；非交易日归属其后首个交易日；判定前 NULL，判定产出写入后冻结）。观点快照（rationale_snapshot/direction/entry_price/created_at）写入后 SHALL 冻结，禁止任何接口修改；`session_id`/`trigger_high`/`trigger_low` 同为写入即冻结的快照字段。判定结果由系统计算，只允许状态流转（open → resolved_* / unresolvable；neutral 观点 → 终态 `status="avoidance"`，细粒度结果写 `avoidance_status`）。`PREDICTIONS_STATUSES` SHALL 含终态 `avoidance`。存量观点的新列 SHALL 为 NULL 且不追溯回填（同存量观点不追溯先例）。

(Previously: 表无 `session_id`/`trigger_high`/`trigger_low` 列——`session_id` 关联为规范承诺但列从未落库；watch 触发位仅存于 `rationale_snapshot` 自由文本，机器不可追踪。)

#### Scenario: 观点写入即冻结

- **WHEN** 一条观点写入 predictions
- **THEN** rationale_snapshot SHALL 含观点摘要字段（`action`、`fund_manager_decision` 及其 reasoning）与申报价位（如有）
- **AND** `session_id` SHALL 落库为写入该观点的会话 ID，完整分析原文 SHALL 存于会话报告并可由该列关联（不重复存于快照）
- **AND** 后续任何接口（含服务端内部接口）尝试修改 direction/entry_price/rationale_snapshot/created_at/session_id SHALL 失败并留记录

#### Scenario: watch 触发位落库

- **WHEN** 一条 watch 观点落库，终稿决策申报 `trigger_high=24.6`、`trigger_low=22.91`
- **THEN** 该行 `trigger_high`/`trigger_low` SHALL 落库为 24.6/22.91
- **WHEN** 终稿未申报触发位，或决策为 buy/sell
- **THEN** 两列落 NULL，MUST NOT 从 `reeval_triggers` 文本解析回填

#### Scenario: 回测实盘分离

- **WHEN** 任何对外接口返回战绩数据
- **THEN** 响应 SHALL 按 `source_type` 区分 backtest 与 live
- **AND** SHALL 不存在合并 backtest 与 live 的服务端接口

#### Scenario: 缺要素观点不入统计

- **GIVEN** 一条观点缺标的、方向或时间窗口任一要素
- **WHEN** 写入
- **THEN** SHALL 存档但不进入战绩统计

#### Scenario: horizon 默认值显式落库

- **WHEN** 观点落库且未自带 horizon
- **THEN** horizon_days SHALL 显式写入配置默认值（20 交易日）
- **AND** SHALL NOT 依赖表级默认值隐式生效

#### Scenario: 判定基准为派生入场价

- **GIVEN** 某观点参考价（entry_price）已落库
- **WHEN** 判定任务结算该观点
- **THEN** 区间收益 SHALL 基于 `settle_entry_price`（行情派生）计算，SHALL NOT 基于参考价
- **AND** 派生值 SHALL 随判定结果落库并冻结

#### Scenario: 存量观点不追溯

- **GIVEN** 切点日前落库的 open 观点（horizon_days=252）
- **WHEN** 判定任务处理或口径切点生效
- **THEN** 该观点 SHALL 按其落库时的 horizon 判定，SHALL NOT 被改写为新默认值
- **AND** 已结算存量行 SHALL NOT 重算

#### Scenario: 存量观点新列不追溯

- **GIVEN** 切点日前落库的 open 观点（无 session_id/trigger 列值）
- **WHEN** DDL 迁移生效
- **THEN** 存量行新列 SHALL 为 NULL，SHALL NOT 回填或重算
- **AND** 已结算存量行 SHALL NOT 重算
