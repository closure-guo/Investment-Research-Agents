## Why

新的个股研报投喂/采集入库后，相关标的要等下一次精选池日批（16:22）才可能被分析，信息到决策至少延迟一个交易日；若标的不在池内，则可能永远不会被分析。需要一条「入库即触发」的链路，但必须限范围、限上限，否则一份策略报告或覆盖表就能触发几十个会话，打爆 LLM 配额与并发队列。

## What Changes

- 新增 `report_analysis_runs` 幂等表：`(doc_key, symbol)` 为主键，记录 `session_id`、状态、错误与时间。
- 新增触发编排：投喂抽取完成后，取 `stocks[]` 非空且 `report_date` 在 7 天内的文档，按范围过滤后入队分析。
- **范围过滤**：默认只触发当前精选池、自选、持仓的并集（`REPORT_TRIGGER_SCOPE` 可配置）；不在范围内的标的不触发。
- **上限**：单篇最多 3 个标的（可配）、全局每日上限与自动池分析共享配额；超限显式 skip 并留痕。
- **幂等与当日去重**：同一 `(doc_key, symbol)` 只触发一次；同一标的当日已被自动池分析或其他触发分析过则跳过。
- **队列复用**：只建会话并投递到既有 `AnalyzeQueue`，不新起执行路径、不绕过并发上限与 SSE/journal。
- **默认关闭 + dry-run**：`REPORT_TRIGGER_ENABLED=0` 为默认；dry-run 只返回「将会触发哪些标的」，不建会话、不调 LLM。
- 失败语义：单标的失败不阻断其他标的；失败分 `session_failed` / `dispatch_failed` 留痕，ops 可见。
- 不做：自动下单、自动改执行口径、绕过时效性校验、对策略/宏观报告按行业触发个股。

## Capabilities

### New Capabilities

- `report-triggered-analysis`: 研报入库后的关联标的触发编排、范围/上限/幂等/去重、dry-run 与失败留痕。

### Modified Capabilities

（无。复用 `stock-screening` 的 `AnalyzeQueue` 与自动池分析编排范式，不改变其行为。）

## Impact

- 数据：`data/screening.db` 新增 `report_analysis_runs`（与 `pool_analysis_runs` 并列）。
- 代码：`src/finance_agent/screening/report_trigger.py`（新，编排）、`src/finance_agent/outcome/scheduler.py`（触发 job 或挂到投喂 job 后）、`src/finance_agent/api.py`（复用 `_enqueue_pool_analysis` 桥接）、`src/finance_agent/outcome/ops/jobs.py`。
- 配置：`REPORT_TRIGGER_ENABLED`（默认 0）、`REPORT_TRIGGER_SCOPE`（默认 pool,watchlist,holdings）、`REPORT_TRIGGER_MAX_SYMBOLS`（默认 3）、`REPORT_TRIGGER_MAX_AGE_DAYS`（默认 7）。
- 测试：范围过滤、上限、幂等、当日去重、dry-run、入队失败留痕、空 stocks 不触发。
- 非目标：不做前端页面（v1 用 ops/job 摘要与 dry-run 端点）。
