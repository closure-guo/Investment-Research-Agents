# Delta for stock-screening

## ADDED Requirements

### Requirement: 精选池自动深度分析

系统 SHALL 在每个工作日选股日批结束后，自动对最新成功精选池执行深度分析，无需人工触发。

- 分析名单 MUST 取自最新成功批次，且其 `trade_date` SHALL 为「当日」或「前一交易日」（按快照
  交易日历判定；日历缺失时不猜、退回仅当日）。批次日更旧时 SHALL 跳过（`skipped_stale_pool`），
  MUST NOT 用过期池冒充当日结果。接受前一交易日的池（选股批深夜补跑的常态产物）时 SHALL 在
  摘要 `stale_batch` 披露批次日，且信号时效性校验照常执行。
- 单次自动分析标的数 SHALL 受 `AUTO_POOL_ANALYSIS_MAX_SYMBOLS` 约束（默认 10，正整数）；按池内
  `rank` 升序取前 N。
- 每条 `(trade_date, symbol)` SHALL 至多创建一次自动分析会话（幂等）；人工补跑同一日 MUST NOT
  重复建会话或重复入队；已 `completed` 的组合 MUST NOT 重复消费分析。
- 自动分析前 SHALL 执行既有「信号时效性校验」；不通过者移除并落 `screening_removals` 审计，
  其余标的继续。
- 自动分析 SHALL 复用 `AnalyzeQueue` 与 `SCREENING_ANALYZE_CONCURRENCY` 并发上限，并复用既有
  SSE/journal 通道；MUST NOT 绕过队列另起一套非流式执行路径。
- 当日精选池为空、批次日超出上述窗口时，任务 SHALL 以显式 `skipped_*` 原因收尾，MUST NOT 调用
  LLM、MUST NOT 建会话、MUST NOT 以成功空跑掩盖上游失败。
- 运行行状态 SHALL 与会话终态对账：编排开始前，系统 SHALL 把 `queued` / `dispatch_failed` 且
  会话已终结（`completed`/`failed`/`interrupted`）的行回写为 `completed` / `failed`（error 取
  会话 `failure_reason`），在跑会话保持原状；对账失败 SHALL 只记日志、不阻断编排。运维侧
  MUST NOT 只能看到永不离开 `queued` 的运行行（issue #456 缺陷 A）。

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

#### Scenario: 前一交易日的补跑池仍被分析

- **GIVEN** 当日选股批次尚未成功，最近成功批次为前一交易日（如深夜补跑产物）
- **WHEN** 自动分析任务触发
- **THEN** 摘要 `status=ok` 且 `stale_batch` 披露该批次日
- **AND** 标的照常创建会话并入队，信号时效性校验照常执行

#### Scenario: 超过前一交易日的旧池不分析

- **GIVEN** 最近成功批次早于前一交易日（选股连续失败多日）
- **WHEN** 自动分析任务触发
- **THEN** 摘要 `status=skipped_stale_pool` 且 `batch_date` 为该旧批次日
- **AND** 不创建会话、不消费 LLM

#### Scenario: 交易日历缺失时退回仅当日

- **GIVEN** 快照库无 `trade_calendar`（日历不可得）
- **WHEN** 自动分析任务触发且最近成功批次为前一交易日
- **THEN** 摘要 `status=skipped_stale_pool`（无历不猜，维持旧严格门槛）

#### Scenario: 同日补跑幂等

- **GIVEN** 同一 `(trade_date, symbol)` 已有 `pool_analysis_runs` 行且状态为 `queued` 或
  `completed`
- **WHEN** 任务被手动补跑
- **THEN** 该标的计入 `skipped_existing`，不重复建会话、不重复入队、不重复消费分析

#### Scenario: 会话终态回写运行行

- **GIVEN** 某标的的运行行停在 `queued`，但其会话已 `completed`
- **WHEN** 下一次自动分析编排开始（或对账被调用）
- **THEN** 该行状态 SHALL 回写为 `completed`（`failed` 会话同理回写 `failed` 与失败原因）
- **AND** 对账后的已完成组合不重复入队

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
