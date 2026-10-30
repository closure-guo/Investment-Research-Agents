# Delta for stock-screening

## ADDED Requirements

### Requirement: 精选池行携带股票名

选股日批落精选池行时，每行 SHALL 携带 `stock_name`（取自**同一交易日**快照记录的 `name`，
主档与小票通道同一落库契约）；池读取接口（最近成功池与按日池查询）的池行 SHALL 透传该字段。
快照行 name 缺失或为空时，池行 `stock_name` SHALL 落空串且流水线 SHALL 记 warning，批次
MUST NOT 因此失败——`stock_name` 为纯展示字段，不参与精选池资格判定与排序，不适用池行
决策字段的 fail loud 语义。存量迁移前的历史池行该字段为 NULL（读取透传为 `null`），同一
`(trade_date, pool)` 幂等补跑即自动补名；系统 MUST NOT 要求专门回填脚本。

#### Scenario: 池行透传快照名称

- **GIVEN** 某交易日快照中标的 `600519` 的 `name` 为「贵州茅台」，该标的经 L1/L3/在榜门槛入选精选池
- **WHEN** 该批次落库并读取池接口
- **THEN** 该池行 `stock_name` 为「贵州茅台」
- **AND** 池行其余字段（symbol、composite_score、rank、consecutive_days、l3_verdict、l3_reason、factor_contribs）与既有契约逐位一致

#### Scenario: 缺名降级不阻断批次

- **GIVEN** 某交易日快照中标的 `600001` 的 `name` 为空，该标的入选精选池
- **WHEN** 该批次落库
- **THEN** 该池行 `stock_name` 为空串，批次状态仍为 ok
- **AND** 流水线日志含该标的缺名 warning
- **AND** 其余池行不受影响

#### Scenario: 存量库无感迁移

- **GIVEN** 存量 `selection_pool` 表无 `stock_name` 列且含历史池行
- **WHEN** 新代码首次初始化选股库
- **THEN** 列经幂等 ALTER 补齐，历史池行保留且该字段为 NULL
- **AND** 池读取接口对历史行返回 `stock_name: null`

#### Scenario: 幂等补跑自动补名

- **GIVEN** 当日精选池已落库（迁移前落的历史行或缺名行）
- **WHEN** 同一 `(trade_date, pool)` 手动补跑且快照名称可得
- **THEN** 该日池行按 upsert 语义整体替换，`stock_name` 补为快照名称
- **AND** 无需任何专门回填操作
