# Delta for stock-screening

## ADDED Requirements

### Requirement: 精选池自动深度分析

系统 SHALL 在每个工作日选股日批结束后，自动对**当日**成功精选池执行深度分析，无需人工触发。

- 分析名单 MUST 取自 `trade_date == 当日` 的最新成功批次；最近成功批次早于当日时 SHALL 跳过
  （`skipped_stale_pool`），MUST NOT 用过期池冒充当日结果。
- 单次自动分析标的数 SHALL 受 `AUTO_POOL_ANALYSIS_MAX_SYMBOLS` 约束（默认 10，正整数）；按池内
  `rank` 升序取前 N。
- 每条 `(trade_date, symbol)` SHALL 至多创建一次自动分析会话（幂等）；人工补跑同一日 MUST NOT
  重复建会话或重复入队。
- 自动分析前 SHALL 执行既有「信号时效性校验」；不通过者移除并落 `screening_removals` 审计，
  其余标的继续。
- 自动分析 SHALL 复用 `AnalyzeQueue` 与 `SCREENING_ANALYZE_CONCURRENCY` 并发上限，并复用既有
  SSE/journal 通道；MUST NOT 绕过队列另起一套非流式执行路径。
- 当日精选池为空、当日批次失败或最近成功批次过期时，任务 SHALL 以显式 `skipped_*` 原因收尾，
  MUST NOT 调用 LLM、MUST NOT 建会话、MUST NOT 以成功空跑掩盖上游失败。

#### Scenario: 当日池自动出票并分析

- **GIVEN** 当日选股批次 `status=ok`、`pool_size>0`，且 `AUTO_POOL_ANALYSIS_ENABLED=1`
- **WHEN** `auto_pool_analysis_daily` 在 16:22 触发
- **THEN** 系统按 rank 取前 N 个标的，为每个标的创建独立 `analysis` 会话并入队
- **AND** `pool_analysis_runs` 为每条 `(trade_date, symbol)` 写入一行（含 `session_id`）
- **AND** 运行摘要包含 `pool_size/selected/started/skipped_existing/removed` 计数

#### Scenario: 空池不消费 LLM

- **GIVEN** 当日选股批次成功但 `pool_size=0`
- **WHEN** 自动分析任务触发
- **THEN** 不创建会话、不发起任何 LLM 调用
- **AND** 摘要 `status=skipped_empty_pool`，任务不标记失败

#### Scenario: 当日批次失败不分析旧池

- **GIVEN** 当日选股批次 `status=failed`，最近成功批次为前一交易日
- **WHEN** 自动分析任务触发
- **THEN** 摘要 `status=skipped_stale_pool` 且 `batch_date` 为前一交易日
- **AND** 不创建会话、不消费 LLM

#### Scenario: 同日补跑幂等

- **GIVEN** 同一 `(trade_date, symbol)` 已有 `pool_analysis_runs` 行
- **WHEN** 任务被手动补跑
- **THEN** 该标的计入 `skipped_existing`，不重复建会话、不重复入队

#### Scenario: 时效性校验移除停牌标的

- **GIVEN** 池内标的 Z 在分析前停牌
- **WHEN** 自动分析任务执行时效性校验
- **THEN** Z 被移除并记录 `SUSPENDED` 审计行
- **AND** 其余标的照常创建会话与入队

#### Scenario: 单日上限截断

- **GIVEN** 当日精选池有 50 个标的，`AUTO_POOL_ANALYSIS_MAX_SYMBOLS=10`
- **WHEN** 自动分析任务触发
- **THEN** 只处理 rank 1–10 的标的
- **AND** 摘要 `selected=10`，其余不入队
