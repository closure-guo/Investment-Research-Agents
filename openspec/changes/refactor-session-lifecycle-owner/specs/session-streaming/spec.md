# Delta for session-streaming

## ADDED Requirements

### Requirement: 会话状态流转单一 owner

会话状态流转（clarifying→running→completed/failed/interrupted）SHALL 经统一生命周期模块（`sessions/lifecycle`）执行；api/pipeline 层 SHALL NOT 直接调用 session_store 的状态 setter 手写 transition（读取状态做订阅恢复语义的 stream_registry 除外）。流转产生的事件（session_created / 终态事件）与既有发布语义 SHALL 保持不变；终态事件优先送达契约（本文件既有 Requirement）不变。前端消费的事件流 SHALL 零变化。

#### Scenario: 分析启动一段式

- **GIVEN** 任一分析入口（/api/analyze、screening_analyze、events_watchlist_analyze）
- **WHEN** 发起新分析
- **THEN** 会话创建、chat 落库、pipeline anchor、clarify→running 与 session_created 事件 SHALL 由 `begin_analysis` 一次调用完成
- **AND** 三个入口的启动序列 SHALL 无逐行重复实现

#### Scenario: 终态流转统一入口

- **GIVEN** 分析管线到达任一终态（completed/failed/interrupted）
- **WHEN** 状态流转
- **THEN** SHALL 经 `mark_complete / mark_failed / mark_interrupted` 执行
- **AND** 落库、终态事件写入与既有重试语义不变
