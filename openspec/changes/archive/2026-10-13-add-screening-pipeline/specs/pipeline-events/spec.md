# pipeline-events delta: add-screening-pipeline

## ADDED Requirements

### Requirement: 批量分析进度复用

选股页触发的批量深度分析 SHALL 为每个标的创建独立 session 并复用现有 SSE 流式进度（终态事件优先送达等韧性纪律不变）；批量并发上限默认 2，超出部分排队。

#### Scenario: 并发上限

- **GIVEN** 精选池 5 只标的，并发上限 2
- **WHEN** 触发批量深度分析
- **THEN** 同时进行中的分析 ≤ 2，其余处于排队态并在前端展示排队标识
