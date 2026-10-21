# Design: add-report-triggered-analysis

## Approach

### 编排形状（复用自动池分析）

新增 `prepare_report_analysis_batch(doc_keys, *, dry_run=False)`，与 `prepare_pool_analysis_batch` 同构：

1. 读取指定文档的抽取结果；`stocks[]` 为空直接跳过（这份 UBS 策略报告就停在这里）。
2. 时效校验：用抽取 `report_date`，缺失回退 `report_documents.publish_date`；两者都缺失或早于 `REPORT_TRIGGER_MAX_AGE_DAYS`（默认 7）→ skip `stale`。
3. 范围过滤：把 `stocks[]` 的 6 位代码与 scope 集合求交；scope 默认 `pool,watchlist,holdings`，可配置。
4. 每日预算：`daily_used = pool_analysis_runs(当日) + report_analysis_runs(当日)`；达到 `REPORT_TRIGGER_DAILY_MAX` 时全部 skip `daily_cap`。
5. 单篇上限：按 `stocks[]` 顺序取前 `REPORT_TRIGGER_MAX_SYMBOLS`（默认 3），其余 skip `per_report_cap`。
6. 幂等：`(doc_key, symbol)` 已存在 → skip `duplicate`；当日已有该标的的分析（pool 表或本表）→ skip `already_analyzed_today`。
7. 建会话（复用 `create_session + append_chat + set_pipeline_anchor + update_session_for_clarify`），写 `report_analysis_runs`，再通过主循环桥接 `AnalyzeQueue.enqueue`。
8. 任一步失败：`session_failed` / `dispatch_failed` 落表；其余标的继续。

`dry_run=True` 走 1–6 步后返回 `{would_trigger, skipped}`，**不建会话、不写库、不调 LLM**。

### 范围解析

- **pool**：`latest_successful_pool()` 的最新成功批次；批次过期（非当日）时该来源为空。
- **watchlist**：`event_watchlist` 中未关闭条目的 symbol 集合。
- **holdings**：`execution` 库 `positions` 中 `qty > 0` 的 symbol。
- 三个来源任一不可用只记 WARNING，按空集处理；全部不可用且 scope 仅含这三者时，本批次不下发任何会话。

### 队列与线程模型

- job 在 APScheduler 线程运行，只做同步编排；真正执行在 uvicorn 主循环的 `AnalyzeQueue`。
- 复用 `api._enqueue_pool_analysis` 的 `call_soon_threadsafe` 桥接；为实现通用性把函数改名为 `_enqueue_analysis`（行为不变），pool 与 report 两个调用方共用。
- 并发上限仍是 `SCREENING_ANALYZE_CONCURRENCY`（默认 2）；触发批次不会提高并发。
- API 进程未起时桥接返回 False → 记 `dispatch_failed`，不重试（下一次日批会重建）。

### 触发入口

- 日批：投喂扫描 job 在抽取完成后，对本次新入库的 `doc_key` 调一次 `prepare_report_analysis_batch`（默认关闭时只记录 `would_trigger` 摘要，不建会话）。
- 手动：`POST /api/research/reports/{doc_key}/trigger-analysis`，支持 `?dry_run=1`；dry-run 返回 will_trigger/skipped，便于先看成本。
- 不做文件监听自动触发：避免 PDF 半写入时被解析；统一由扫描 job 或显式端点触发。

### 失败与可观测

- 表 `report_analysis_runs`：`doc_key`、`symbol`、`session_id`、`status`（queued/running/done/failed/skipped）、`reason`、`created_at`、`updated_at`；主键 `(doc_key, symbol)`。
- 每次批次摘要写入 job 运行记录：`docs_scanned / candidates / triggered / skipped_{stale,out_of_scope,duplicate,daily_cap,per_report_cap,already_analyzed_today} / failed`。
- 状态轮询复用 `AnalyzeQueue` 的终态判定；本表由队列回调或轮询更新，不新增 SSE 通道。

## Alternatives Considered

- **在投喂扫描 job 里直接跑管线**：否决。分析是分钟级、多次 LLM 调用；会阻塞调度线程、绕过并发上限，且 API 未起时没有事件循环。
- **文件系统监听即时触发**：否决。PDF 可能未写完；且绕过抽取完成这一前置条件。
- **无范围限制，所有 stocks 全触发**：否决。一份覆盖表可列几十只，成本不可控。
- **把触发记录塞进 `pool_analysis_runs`**：否决。主键是 `(trade_date, symbol)`，无法表达 `doc_key` 维度，会互相覆盖。
- **触发后自动下单**：否决。执行链仍只消费已审批观点，触发只负责产生分析。

## Risks

- **重复分析**：同一标的可能同时被池分析和研报触发。对策：当日 `(date, symbol)` 去重，先到先得；池分析 16:22 先跑，研报触发 16:58 后跑，后者通常被跳过。
- **范围来源失效**：pool 过期、watchlist 空、持仓库不可用会导致 0 触发。对策：摘要显式列出每个来源的 symbol 数与失败原因，不静默。
- **成本失控**：默认关闭 + 单篇 3 + 每日上限；dry-run 先行。
- **数据错误**：抽取 `stocks[]` 误列披露表会放大触发面。对策：由 delta `add-macro-strategy-report-context` 的「披露表不得入 stocks」规则前置约束；本 delta 再加范围求交兜底。
