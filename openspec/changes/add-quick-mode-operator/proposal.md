## Why

快速模式（`POST /api/agui/quick`）当前只注册 `web_search` 一个工具，模型能聊天但看不见系统其他能力。用户想分析股票、查选股池、看持仓、看日批、审批记忆、控制因子研发时，都必须切页面或切模式。

本变更把快速模式做成系统的统一对话入口：先打通「分析某只股票」的派单链路（P1），再补齐只读查询（P2）与受控写操作（P3）。长任务不进入 AG-UI 通道，写操作必须经领域层确认与审计。

## What Changes

### P1：分析派单（已实施）

- 新增 `search_stock` 与 `start_stock_analysis` 两个工具；派单到独立 `analysis` 会话，立刻返回回执，不在快速通道内运行管线。
- 快速模式 `max_iterations` 由 3 提到 6。

### P2：只读系统查询（本次新增）

- 新增 7 个只读工具：
  - `get_screening_pool`：最近成功选股池 + 批次状态 + 分析队列；
  - `get_track_record_overview`：战绩总览（胜率/超额/组合指标/回避正确率/paper 损耗）；
  - `get_broker_overview`：资金、持仓市值、总资产、浮盈 + 持仓明细；
  - `get_daily_ops_status`：调度器状态 + 各日批任务下次触发/最近运行；
  - `get_memory_clauses`：语义记忆条款（默认 pending）；
  - `get_factor_library`：因子库列表（名称/状态/ICIR/最近闸门结论）；
  - `get_factor_research_status`：因子研发看护状态（运行/陈旧/进度/控制期望态）。
- 所有只读工具返回紧凑摘要，不返回大数组/完整曲线；缺数据如实披露，不用 0 冒充。

### P3：受控写操作（本次新增）

- 新增 4 个写工具：
  - `set_factor_research_running`：写因子研发看护期望态（布尔），回执带 `honor` / `pending` / ETA，不承诺立即生效；
  - `run_ops_job`：手动补跑日批任务（白名单 = `JOB_IDS`），后台执行并立即返回回执；
  - `review_memory_clause`：确认/驳回语义记忆条款；
  - `start_pool_analysis`：对精选池批量发起深度分析。
- 统一确认协议：第一次调用只返回预览 + 一次性 token；用户明确回复确认词后，第二次调用带 `confirm=true` + token 才执行。token 5 分钟过期、绑定 session 与参数、单次使用。
- 写操作全部落结构化审计日志（`operator_write`），并经 `_trace_tool` 进 Langfuse。
- 不暴露交易下单、任意命令/脚本、RD-Agent 内部循环参数。

### 共同

- 前端把 probe capability 透传到 AG-UI forwardedProps；`tool_call=false` 时不注册系统操作工具并显式披露。
- `quick_mode.md` 增加只读/写操作政策与确认协议；提示词经 `deploy_prompts.py` 发布。
- 明确不改动：AG-UI 通道仍只承载 quick 对话，分析/管线事件继续走 analysis 会话的 journal/SSE。

## Capabilities

### New Capabilities

- `quick-mode-operator`: 快速模式的系统操作工具层——只读查询、分析派单、受控写操作、确认协议、回执与失败披露。

### Modified Capabilities

- `chat-stream`: 快速通道工具面扩展为「搜索 + 系统读写」；写操作确认可跨两轮对话，但管线事件仍 MUST NOT 经 AG-UI 传输。
- `llm-mode-gating`: quick 模式在 `tool_call=false` 时仍可聊天，但系统操作工具不可用且必须披露。

## Impact

- 后端：`agent_factory.py`（工具注册/提示词渲染）、`tool_registry.py`（工具名与读写分类）、新增 `src/finance_agent/operator/`（reads/writes/confirmation/history/analysis）、`api.py`（共享派单服务）。
- 提示词：`src/finance_agent/prompts/quick_mode.md`，经 `deploy_prompts.py` 发布。
- 前端：仅 capability 透传字段；无 UI 改动。
- 数据：不新增持久化表；确认令牌为进程内存（单 worker 前提同 StreamRegistry），写操作审计走日志。
- 测试：单测覆盖每个工具的成功/失败/缺数据/确认协议；E2E 覆盖分析派单与只读查询；写操作以单测 + 人工抽验为主。
