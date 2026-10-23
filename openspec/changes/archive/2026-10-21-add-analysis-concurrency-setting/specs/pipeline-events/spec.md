# Delta for pipeline-events

## MODIFIED Requirements

### Requirement: 批量分析进度复用
选股页触发的批量深度分析 SHALL 为每个标的创建独立 session 并复用现有 SSE 流式进度（终态事件优先送达等韧性纪律不变）；批量并发上限 SHALL 取自运行时设置（设置中心「批量分析最大并发数」，默认 10，合法区间 1–20；环境变量 `SCREENING_ANALYZE_CONCURRENCY` 仅作为未保存设置时的回退），超出部分排队。

(Previously: 批量并发上限默认 2，超出部分排队；`SCREENING_ANALYZE_CONCURRENCY` 可配置。)

#### Scenario: 并发上限
- **GIVEN** 最大并发分析数为 2
- **WHEN** 精选池 5 只标的触发批量深度分析
- **THEN** 同时进行中的分析 ≤ 2，其余处于排队态并在前端展示排队标识

#### Scenario: 修改设置后批次按新上限调度
- **GIVEN** 用户已将最大并发分析数改为 10
- **WHEN** 精选池 15 只标的触发批量深度分析
- **THEN** 同时进行中的分析 ≤ 10
- **AND** 其余标的处于排队态并在前端展示排队标识
