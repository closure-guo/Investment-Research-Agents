# Delta for report-triggered-analysis

## REMOVED Requirements

### Requirement: 触发幂等与当日去重

> 整条移除，由后续 change `add-report-trigger-redispatch` 以新语义同名重建（同 PR 两段
> 归档）：原「`dispatch_failed` SHALL 复用原 `session_id`、MUST NOT 新建会话」钉死的是被
> 10-28 实操证伪的死路语义（终态会话入队即被队列终态轮询判死，issue #546），场景
> 「投递失败复用会话」随之废弃——场景级替换在 archive 守卫（MODIFIED 块 MUST 覆盖现有
> 场景集）与校验器（同名禁止 REMOVED+ADDED 并存）约束下的规则内通路即整条移除后重建。

#### Scenario: 投递失败复用会话

- **GIVEN** 某 `(doc_key, symbol)` 上次状态为 `dispatch_failed` 且已有 `session_id`
- **WHEN** 重试触发
- **THEN** SHALL 复用原 `session_id` 重新入队
- **AND** MUST NOT 新建会话

#### Scenario: 同一文档重跑幂等

- **GIVEN** 某文档的标的已触发并建会话
- **WHEN** 同一文档再次触发
- **THEN** 该标的 SHALL 计入 `duplicate`
- **AND** 会话数 SHALL 不变

#### Scenario: 当日已分析跳过

- **GIVEN** 标的 X 当日已被自动池分析建过会话
- **WHEN** 研报触发流程处理 X
- **THEN** X SHALL skip `already_analyzed_today`
- **AND** MUST NOT 创建第二个会话
