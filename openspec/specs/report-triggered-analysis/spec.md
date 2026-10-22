# report-triggered-analysis Specification

## Purpose
TBD - created by archiving change add-report-triggered-analysis. Update Purpose after archive.
## Requirements
### Requirement: 研报触发关联分析

系统 SHALL 支持在研报抽取完成后，按抽取出的个股清单触发关联标的的深度分析。

- 触发来源 SHALL 仅限 `report_extractions.stocks[]` 中 6 位股票代码；`stocks[]` 为空的文档 MUST NOT 触发任何分析。
- `report_date` SHALL 不早于 `REPORT_TRIGGER_MAX_AGE_DAYS`（默认 7 个自然日）；`report_date` 缺失时回退 `report_documents.publish_date`；两者都缺失或过期 SHALL skip `stale`。
- 标的 SHALL 落在配置的范围集合内；默认范围 `REPORT_TRIGGER_SCOPE=pool,watchlist,holdings`，范围外 SHALL skip `out_of_scope`。
- 触发 SHALL 只在 `REPORT_TRIGGER_ENABLED=1` 时生效；默认关闭时只记录 would-trigger 摘要，MUST NOT 建会话或调用 LLM。
- 策略/宏观报告 MUST NOT 按行业或板块触发个股分析。
- 摘要 SHALL 标注输入来源 `input_scope`（`all_extractions` = 批量读抽取表；`explicit_doc_keys` = 指定文档）。
- 输入为空时摘要 SHALL 带 `notice` 说明原因（批量空态以 `no_extractions` 开头、指定文档缺失以 `doc_not_found` 开头），MUST NOT 以无候选的静默成功冒充「已运行」。
- 投喂日批（`research_report_inbox_daily`）的运行摘要 SHALL 带 `extractions_total`（抽取表总行数，不受列表 limit 截断），使深度层覆盖度为 0 时在运维面可见。

#### Scenario: 深度抽取为空时披露原因

- **GIVEN** `report_extractions` 为空且未指定 `doc_keys`
- **WHEN** 触发编排运行
- **THEN** `docs_scanned=0`、`input_scope=all_extractions`
- **AND** 摘要 SHALL 带以 `no_extractions` 开头的 `notice`
- **AND** MUST NOT 建会话

#### Scenario: 指定文档无抽取时原因可区分

- **GIVEN** 指定的 `doc_key` 在 `report_extractions` 中不存在
- **WHEN** 触发编排运行
- **THEN** 摘要 SHALL 带以 `doc_not_found` 开头的 `notice`
- **AND** MUST NOT 与「库内本来没有抽取」共用同一提示

#### Scenario: 个股研报触发范围内标的

- **GIVEN** 一篇 3 天前的个股研报抽取含 2 只标的，其中 1 只在当日精选池
- **WHEN** 触发编排运行且开关开启
- **THEN** 只对池内 1 只创建分析会话
- **AND** 另一只记 skip `out_of_scope`

#### Scenario: 策略报告不触发

- **GIVEN** 一份策略报告 `stocks=[]`
- **WHEN** 触发编排运行
- **THEN** `candidates=0`、`triggered=0`
- **AND** MUST NOT 以行业或披露表条目替代个股清单

#### Scenario: 过期研报不触发

- **GIVEN** 一篇 20 天前的研报，`REPORT_TRIGGER_MAX_AGE_DAYS=7`
- **WHEN** 触发编排运行
- **THEN** 全部标的 skip `stale`
- **AND** MUST NOT 建会话

#### Scenario: 默认关闭只留摘要

- **GIVEN** `REPORT_TRIGGER_ENABLED=0`
- **WHEN** 投喂扫描完成抽取
- **THEN** 摘要 SHALL 记录 `would_trigger` 名单与数量
- **AND** MUST NOT 创建会话、MUST NOT 调用 LLM

### Requirement: 触发上限与预算

系统 SHALL 对触发批次施加单篇与全局上限。

- 单篇 SHALL 至多触发 `REPORT_TRIGGER_MAX_SYMBOLS`（默认 3，正整数）只标的，按 `stocks[]` 顺序取前 N，其余 skip `per_report_cap`。
- 全局每日触发 SHALL 受 `REPORT_TRIGGER_DAILY_MAX` 约束；当日已用预算 SHALL 计入 `pool_analysis_runs` 与 `report_analysis_runs` 两个来源的当日行数。
- 达到上限时 SHALL skip `daily_cap` 并在摘要中显式计数，MUST NOT 静默丢弃。

#### Scenario: 单篇截断

- **GIVEN** 一篇研报含 8 只范围内标的，`REPORT_TRIGGER_MAX_SYMBOLS=3`
- **WHEN** 触发编排运行
- **THEN** 只取前 3 只建会话
- **AND** 其余 5 只 skip `per_report_cap`

#### Scenario: 日预算共享

- **GIVEN** 当日自动池分析已产生 8 个会话，`REPORT_TRIGGER_DAILY_MAX=10`
- **WHEN** 研报触发了 2 只后再次触发
- **THEN** 后续标的 SHALL skip `daily_cap`
- **AND** 摘要 SHALL 显示当日已用与上限

### Requirement: 触发幂等与当日去重

系统 SHALL 以 `(doc_key, symbol)` 为幂等键，并避免同一标的当日重复分析。

- 同一 `(doc_key, symbol)` 已存在触发记录时 SHALL skip `duplicate`；若上次为 `dispatch_failed`，SHALL 复用原 `session_id` 重新投递，MUST NOT 新建会话。
- 同一标的当日已被自动池分析或本触发流程建过会话时 SHALL skip `already_analyzed_today`。
- 幂等键 SHALL 落 `report_analysis_runs` 表，主键 `(doc_key, symbol)`。

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

#### Scenario: 投递失败复用会话

- **GIVEN** 某 `(doc_key, symbol)` 上次状态为 `dispatch_failed` 且已有 `session_id`
- **WHEN** 重试触发
- **THEN** SHALL 复用原 `session_id` 重新入队
- **AND** MUST NOT 新建会话

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

### Requirement: Dry-run 与可观测

系统 SHALL 提供不产生副作用的 dry-run，并让每次批次可审计。

- `dry-run` SHALL 返回 `would_trigger` 与 `skipped`（含原因），MUST NOT 建会话、写触发记录、调 LLM。
- 每次批次 SHALL 记录 `docs_scanned / candidates / triggered / skipped_* / failed` 计数。
- `report_analysis_runs` SHALL 记录 `status` 与 `reason`，失败不得静默。
- 触发前 SHALL 复用既有信号时效性校验；停牌/一字涨跌停标的 SHALL 不入队并留审计。

#### Scenario: Dry-run 无副作用

- **GIVEN** 一篇含 3 只范围内标的的研报
- **WHEN** 调用 dry-run
- **THEN** 返回 3 只 `would_trigger`
- **AND** 会话数、`report_analysis_runs` 行数、LLM 调用数 SHALL 均不变

#### Scenario: 失败留痕

- **GIVEN** 某标的建会话失败
- **WHEN** 触发编排运行
- **THEN** 该标的 SHALL 记 `session_failed` 与错误原因
- **AND** 其余标的 SHALL 继续处理

