# Delta: session-persistence（fix-timeline-write-amplification）

## MODIFIED Requirements

### Requirement: 管线时序持久化

系统 SHALL 将深度分析管线各节点的思考/工具时序持久化到 sessions 表 `pipeline_timelines` 列（JSON：`{node: [TimelineItem]}`），管线运行中按节点事件与 `pipeline_snapshot` 同节奏写入。

每次写入 SHALL 为当前**全量**时序结构（不截断、不分块增量）。thinking 洪峰期的定时中间写 SHALL 按自适应间隔节流：间隔 = clamp(上次序列化字节数 ÷ 写带宽上限 256KB/s, 下限 0.5s, 上限 30s)，使中间写带宽有界（incident 039 写放大根因）；节点边界事件（node_complete/node_timing）与管线结束 flush SHALL 即时全量写入，不经自适应间隔。

管线消费侧 SHALL 以 O(1) 摊销成本累积 thinking token（可变分片缓冲），物化产出的持久化结构与前端 `applyPipelineThinkingToken` / `applyPipelineNodeComplete` 语义同构。

#### Scenario: 管线节点时序持久化

- **GIVEN** 深度分析管线运行中产生 thinking_token（含 node 字段）/工具/搜索事件
- **WHEN** 管线执行（fast path PipelineRunner 或 ReAct run_deep_analysis 工具）
- **THEN** 系统 SHALL 按 node 分组维护时序并写入 pipeline_timelines
- **AND** 节点完成时收口该节点末段 thinking（等价前端 applyPipelineNodeComplete）

#### Scenario: thinking 洪峰期中间写带宽有界

- **GIVEN** 管线处于 thinking 洪峰（单节点累积内容达 MB 级）
- **WHEN** 定时中间写触发
- **THEN** 写入间隔 SHALL 按上次序列化字节数 ÷ 256KB/s 伸缩（ clamp 至 [0.5s, 30s] ）
- **AND** 每次写入仍为全量 `{node: [TimelineItem]}` 结构
- **AND** 节点边界事件与结束 flush SHALL 即时写入，不受该间隔限制

#### Scenario: 会话详情返回管线时序

- **GIVEN** 某会话的 pipeline_timelines 已写入
- **WHEN** 前端请求 GET /api/sessions/{sessionId}
- **THEN** 响应 SHALL 包含 pipeline_timelines（可解析为 {node: [TimelineItem]}）
