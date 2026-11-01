# Delta for pipeline-events

## ADDED Requirements

### Requirement: 自动批量分析进度复用

自动选股触发的批量深度分析 SHALL 与人工批量分析复用同一套会话、队列与流式通道：

- 每个标的 SHALL 创建独立 `analysis` 会话，并写入 `session_created` 事件；
- 入队 SHALL 经既有 `AnalyzeQueue`，并发上限 SHALL 沿用 `SCREENING_ANALYZE_CONCURRENCY`
  （默认 2），超出部分排队；
- 进度 SHALL 经既有 journal / subscribe SSE 通道送达；MUST NOT 为自动批次另起不写 journal 的
  旁路执行；
- 选股页的队列徽标 SHALL 同时展示自动批次与人工批次的 `queued/running/done/failed` 状态。

#### Scenario: 自动批次写入会话事件

- **GIVEN** 自动选股分析任务为标的 X 创建会话并入队
- **WHEN** 分析启动
- **THEN** 该会话的 `session_events` 含 `session_created` 事件
- **AND** 选股页队列徽标出现 X 的 `running` 或 `queued` 状态

#### Scenario: 自动批次遵守并发上限

- **GIVEN** `SCREENING_ANALYZE_CONCURRENCY=2` 且自动批次有 5 个标的
- **WHEN** 队列开始补位
- **THEN** 同时运行的分析数不超过 2，其余保持 `queued`
