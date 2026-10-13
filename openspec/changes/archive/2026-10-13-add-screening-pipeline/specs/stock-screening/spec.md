# stock-screening delta: add-screening-pipeline

## ADDED Requirements

### Requirement: 选股日批编排

系统 SHALL 在每个交易日快照更新成功后依次执行 L1 过滤 → L2 打分 → L3 粗筛 → 精选池落库；任一步骤失败时该批次 SHALL 标记失败、不产出精选池，并触发前端横幅告警；L2 全量名单（含未入选标的）SHALL 每日完整落库。

#### Scenario: 中途失败不产残缺池

- **GIVEN** 某日 L3 粗筛因 LLM 网关整体不可用而失败
- **WHEN** 日批结束
- **THEN** 当日精选池为空且批次状态为 failed
- **AND** 前端选股页展示最近一个成功批次的精选池并标注其日期

### Requirement: 信号时效性校验

精选池标的在进入五层深度分析前，系统 SHALL 以其最新可得状态重新校验（停牌/一字涨跌停/新发重大事件）；校验不通过者 SHALL 从本次分析名单移除并记录移除原因。

#### Scenario: 盘前停牌移除

- **GIVEN** 精选池标的 Z 在深度分析触发前发布停牌公告
- **WHEN** 时效性校验执行
- **THEN** Z 被移除，原因码 `SUSPENDED`，其余标的不受影响
