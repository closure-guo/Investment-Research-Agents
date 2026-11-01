# Delta for report-triggered-analysis

## ADDED Requirements

### Requirement: 触发幂等与当日去重

系统 SHALL 以 `(doc_key, symbol)` 为幂等键，并避免同一标的当日重复分析。

- 同一 `(doc_key, symbol)` 已存在触发记录时 SHALL skip `duplicate`；`dispatch_failed` 行重派是
  唯一例外——SHALL 创建**全新会话**重新投递（终态会话 MUST NOT 被复用重新入队：快路径不重置
  会话终态，复用入队即被队列终态轮询判死，issue #546），旧会话保留作审计，触发行 `session_id`
  由幂等 upsert 更新为新会话并回写 `queued`。
- 同一标的当日已被自动池分析或本触发流程建过会话时 SHALL skip `already_analyzed_today`。
- 幂等键 SHALL 落 `report_analysis_runs` 表，主键 `(doc_key, symbol)`。

(Previously: 同一 `(doc_key, symbol)` 已存在触发记录时 SHALL skip `duplicate`；若上次为
`dispatch_failed`，SHALL 复用原 `session_id` 重新投递，MUST NOT 新建会话——终态会话从不被快路径
重置为 `running`，复用入队即被队列终态轮询判死（10-28 恢复实操实证，issue #546）；且复用路径
成功派发后触发行不回写 `queued`，行永远停在 `dispatch_failed` 会被无限重派。)

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

#### Scenario: 投递失败重派全新会话

- **GIVEN** 某 `(doc_key, symbol)` 上次状态为 `dispatch_failed` 且已有旧 `session_id`
- **WHEN** 重试触发
- **THEN** SHALL 创建全新会话重新入队，触发行 `session_id` 更新为新会话且状态回写 `queued`
- **AND** 旧会话保留原状（终态与历史事件不变），MUST NOT 被复用入队
