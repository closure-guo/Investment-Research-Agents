# Delta for report-triggered-analysis

## MODIFIED Requirements

### Requirement: 复用既有分析队列
触发产生的分析 SHALL 复用既有 `AnalyzeQueue`、并发上限与 SSE/journal 通道。

- 调度线程 MUST NOT 直接启动管线；SHALL 通过主事件循环的线程安全桥接投递入队。
- 并发上限 SHALL 沿用运行时最大并发分析数设置（设置中心「批量分析最大并发数」可配，`SCREENING_ANALYZE_CONCURRENCY` 为未保存设置时的回退）；触发批次 MUST NOT 提高并发。
- API 进程未起或主循环不可用时 SHALL 记 `dispatch_failed`，MUST NOT 静默成功。

(Previously: 并发上限 SHALL 沿用 `SCREENING_ANALYZE_CONCURRENCY`；触发批次 MUST NOT 提高并发。)

#### Scenario: 复用并发上限
- **GIVEN** 最大并发分析数为 2，队列已有 2 个运行中分析
- **WHEN** 研报触发 3 只标的
- **THEN** 新标的 SHALL 进入 queued 状态
- **AND** 同一时刻运行中的分析数 MUST NOT 超过 2

#### Scenario: 主循环不可用显式失败
- **GIVEN** API 主事件循环未启动
- **WHEN** 触发编排尝试投递
- **THEN** 该标的 SHALL 记 `dispatch_failed`
- **AND** 摘要 SHALL 包含失败计数
