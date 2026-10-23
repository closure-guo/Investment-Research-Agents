# Proposal: add-analysis-concurrency-setting

## Why
批量深度分析（选股池、自动池、研报触发、事件观察清单）共用 `AnalyzeQueue` 的并发上限，现状只能通过部署期环境变量 `SCREENING_ANALYZE_CONCURRENCY` 调整（默认 2），设置中心「会话管理」分区没有对应设置项，用户无法在运行期调整并发。用户明确要求新增该设置项，并将最大并发数设为 10。

## What Changes
- 设置中心「会话管理」分区新增「批量分析最大并发数」设置项（1–20，默认 **10**），保存后立即作用于当前进程队列并持久化。
- 新增读写端点：`GET/PUT /api/settings/analysis-concurrency`；读取响应如实披露生效值与来源（`file` / `env` / `default`）。
- 来源优先级：设置文件 > 环境变量 `SCREENING_ANALYZE_CONCURRENCY` > 默认值 10；环境变量存在且被覆盖时在响应中披露。
- `AnalyzeQueue` 支持运行期调整上限：提高后立即补位排队条目；降低不终止已运行分析，仅约束后续补位。
- 批量并发上限默认值由 2 调整为 10（含主规范同步修改）。

## Capabilities
- **New Capabilities**: 无
- **Modified Capabilities**:
  - `settings-center`：新增「批量分析并发上限设置」需求（设置项 + 端点 + 来源披露 + 运行期生效语义）
  - `pipeline-events`：批量分析进度复用需求改为运行时设置（默认 10，1–20）
  - `report-triggered-analysis`：并发上限来源由单一环境变量改为运行时设置（env 为回退）

## Impact
- 后端：新增 `src/finance_agent/analysis_concurrency.py`；`api.py` 新增端点与队列应用逻辑；`screening/queue.py` 新增 `set_max_concurrency`
- 前端：`frontend/src/pages/settings/panes/SessionsPane.tsx` 新增设置区块
- 测试：后端单测 / 前端 Vitest / Playwright E2E（`settings-concurrency.spec.ts`）
- 运行时数据：`data/settings/analysis_concurrency.json`（gitignored；env `ANALYSIS_CONCURRENCY_PATH` 可覆盖）
- 运维注意：默认并发由 2 提升到 10，LLM 调用峰值同步上升；可按需在设置中心下调
