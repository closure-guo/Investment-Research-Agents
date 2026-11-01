# Proposal: add-auto-pool-analysis-daily-brief

## Why

选股日批已经能在每个交易日 15:40 自动产出精选池，但「对精选池跑深度分析」仍是一个人工按钮
（`POST /api/screening/analyze`）。从池子到观点、再到执行链，中间这一步不接管，系统就永远停在
「选出来给人看」而不是「自己选、自己分析、自己买」。

同时，系统每天产生大量运行事实（选股批次、池子、分析结论、订单、持仓、因子 IC、失败任务），
但没有一份面向人的当日叙述。没有汇报，自动化的结果只能靠翻库，出了异常也不容易第一时间发现。

本变更把中间这段接成定时任务，并增加一份每日 LLM 运营汇报：选股池自动出票后自动分析，分析产生的
观点进入既有执行链（次日开盘口径），每天收盘后由 LLM 基于当日真实数据生成一份中文日报。

## What Changes

- 新增日批任务 `auto_pool_analysis_daily`（工作日 16:22，Asia/Shanghai）：读取**当日**成功精选池，
  按 rank 取前 N（默认 10，可配置），做时效性校验后自动创建独立分析会话并入既有 `AnalyzeQueue`；
  按 `(trade_date, symbol)` 幂等，空池/过期池/当日批次失败时显式跳过并留痕，不消费 LLM。
- 新增日批任务 `daily_brief_daily`（工作日 20:30）：汇总当日选股、分析、观点、执行、因子与失败任务
  事实，经 `purpose="daily_brief"` 调用 opencode `deepseek-v4.1-flash` 生成中文 Markdown 日报；
  落 `daily_briefs` 表并写 `reports/daily-brief-YYYY-MM-DD.md`；LLM 失败时写确定性降级摘要并标记
  失败，不以空报告冒充成功。
- 新增 `GET /api/v1/ops/daily-brief`（按日期/最新）与 `/ops` 页「每日汇报」区。
- 新增 LLM purpose `daily_brief`：`DAILY_BRIEF_MODEL/BASE_URL/API_KEY` 可选覆盖，逐字段回退
  `LLM_*`（当前即 opencode `openai/deepseek-v4.1-flash`）。
- 执行链不改口径：自动分析产出的已审批观点仍由既有 `paper_execute_daily`（次日 16:10、次日开盘价）
  消费；`watch/hold → flat`（不建仓）、`sell/short → flat`（A 股信号路径不做空），不强制买入。

## Capabilities

- **New Capabilities**
  - `daily-ops-brief`：每日 LLM 运营汇报的生成、存储、读取与失败语义。
- **Modified Capabilities**
  - `stock-screening`：新增「精选池自动深度分析」日批。
  - `pipeline-events`：自动批量分析复用既有队列/SSE/journal 通道。
  - `trade-execution`：自动选股分析观点接入既有日批执行，无特殊路径。
  - `frontend`：`/ops` 页新增每日汇报区。
  - `llm-provider-gateway`：新增 `daily_brief` purpose 的模型解析口径。

## Impact

- 代码：`src/finance_agent/screening/pool_analysis.py`（新）、`src/finance_agent/outcome/daily_brief.py`
  （新）、`src/finance_agent/prompts/daily_brief.md`（新）、`src/finance_agent/outcome/ops/jobs.py`、
  `src/finance_agent/outcome/scheduler.py`、`src/finance_agent/api.py`、`src/finance_agent/ops_api.py`、
  `src/finance_agent/llm/types.py`、`src/finance_agent/llm/resolver.py`、`frontend/src/pages/ops/DailyOpsPage.tsx`。
- 数据：`screening.db` 新增 `pool_analysis_runs`；`sessions.db` 新增 `daily_briefs`。
- 调度：任务注册表 24 → 26；`/ops`、`Settings > 评估运维`、E2E 日批页的数量/顺序断言同步更新。
- 成本：自动分析最多 10 只/日（可配置），与 cohort 18:00 可能重叠；额度已升级，仍以 `AUTO_POOL_ANALYSIS_MAX_SYMBOLS`
  约束单日上限。
- 风险：仍是纸面执行，不接真实券商；自动买入只在模型给出 `approve + buy` 时发生，系统不强制下单。
