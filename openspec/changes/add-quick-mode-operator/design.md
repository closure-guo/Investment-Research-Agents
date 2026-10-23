## Context

快速模式当前链路是 `QuickThread → POST /api/agui/quick → build_agent(mode="quick") → web_search → AG-UI SSE`，Agent 只有 `web_search` 一个工具、`max_iterations=3`。系统其余能力（深度分析、选股、战绩、持仓、日批、记忆、因子）都是独立 HTTP 端点，模型不可见。

快速通道有三个硬约束：

1. AG-UI 通道是请求内联驱动，不经过 `StreamRegistry`/journal；客户端断开即取消，切会话时前端会主动 abort。
2. 既有 `chat-stream` 规范要求「AG-UI 通道只承载 quick 模式对话；深度模式分析、管线时间线事件不得经由 AG-UI 传输」。
3. 深度管线耗时 2–5 分钟（超时上限 40 分钟），且 `translate_to_agui` 会静默丢弃 `PROGRESS` 事件。

因此「在快速模式里直接跑深度分析」在协议、生命周期和用户体验三方面都不成立。P1 选择派单模型。

## Goals / Non-Goals

**Goals:**

- 快速模式能识别「分析某只股票」并派单到独立 `analysis` 会话，立刻返回可追溯的回执。
- 保持 AG-UI 双轨隔离：管线事件、报告、节点时间线不进入快速通道。
- 保留请求级 `llm_config`，派单后的分析与手工深度分析使用同一模型配置。
- 工具名单一来源（`tool_registry.py`），工具调用可观测（Langfuse span + 结构化日志）。
- 工具能力缺失（`tool_call=false`）时显式披露，不静默失败。

**Non-Goals:**

- 不做选股池、战绩、持仓、日批、记忆、因子的查询工具（P2）。
- 不做任何写操作（触发日批、确认记忆、因子开关、交易订单）——P3 另立 delta。
- 不改 QuickThread 的结构化结果卡与「打开会话」按钮（P3.5）。
- 不让快速模式在 AG-UI 通道内运行或转发深度管线。
- 不新增数据库表。

## Decisions

### D1: 派单，不代跑

`start_stock_analysis` 只做四件事：建 `analysis` 会话、写用户消息与管线锚点、启动既有管线、返回回执。工具在管线启动后立即返回，不等待报告。

备选方案：直接复用 `run_deep_analysis` 流式工具。否决理由——违反 `chat-stream` 双轨隔离；2–5 分钟无进度；切换会话即中断；`PROGRESS` 事件被翻译层丢弃。

### D2: 复用 `/api/analyze` fast path 的装配，而非 AnalyzeQueue

从 `api.py` 的 fast path 抽取一个共享启动函数：`create analysis session → append_chat → set_pipeline_anchor → update_session_for_clarify → append session_created → PipelineRunner.start(_run_graph_streaming(..., llm_config=...))`。HTTP fast path 与 operator 工具共用它，避免两套派单逻辑漂移。

备选方案：投递 `AnalyzeQueue`（并发上限 2）。否决理由——队列条目当前只带 `(symbol, session_id)`，不携带请求级 `llm_config`；为一个工具扩展队列载荷会改动被批量分析依赖的调度组件。单次用户派单与手工深度分析语义相同，后者本来也不受队列并发上限约束。风险由 prompt 的「一次请求只派一单」与 `max_iterations=6` 约束；P2 若需要全局并发上限，再单独扩展。

### D3: 工具层放在新模块，复用既有股票解析

新增 `src/finance_agent/operator/`：

- `analysis.py`：`make_start_stock_analysis(...)` 工具工厂、沉余派单防护、回执格式化。
- `gating.py`：`operator_enabled_from_capability(...)`，把前端 probe 事实翻译成是否注册系统操作工具。

`search_stock` 复用 `agent_factory._make_search_stock`（深度模式同一实现），不复制解析逻辑。`start_stock_analysis` 的启动函数从 `api.py` 导入（函数内 import，避免 `api → agent_factory → operator → api` 的模块级环）。

### D4: 工具注册与能力门禁

- `tool_registry.py` 新增 `TOOL_START_STOCK_ANALYSIS`，并把工具分为 `READ_TOOLS` / `WRITE_TOOLS`；`start_stock_analysis` 归写操作。
- `build_agent(mode="quick", operator_enabled=...)`：`True` 时注册 `search_stock` + `start_stock_analysis`；`False` 时只注册 `web_search`。
- capability 来源：前端把 probe 得到的 `capability` 一并放进 AG-UI `forwardedProps.llmConfig`；端点读 `tool_call`。`None`/缺失 → 放行（不误伤），与前端门禁语义一致。
- 环境变量 `QUICK_OPERATOR_TOOLS` 作为总开关（默认开启），用于回滚。

### D5: 提示词与回执语义

`quick_mode.md` 增加：

- 意图分类：明确股票分析/对比请求 → `search_stock` → `start_stock_analysis`；模糊/多候选 → 只列候选并等待确认。
- 派单后只复述回执（会话名 + 任务号 + 去哪里看），禁止声称「分析已完成」或编造进度/结论。
- `{operator_note}` 占位：能力不可用时由后端注入「当前模型不支持工具调用，系统操作不可用」。

提示词是权威源，改完必须 `uv run python scripts/deploy_prompts.py` 发布。

### D6: 可观测与幂等

- 工具经 `_trace_tool` 生成 Langfuse span。
- 派单成功写结构化日志 `operator_dispatch`（`session_id`、`symbol`、`status`、`timestamp`）。
- 同一 Agent 实例内对同一 `symbol` 的重复派单返回首次回执，不建第二个会话（防模型重复调用）。

### D7: 测试策略

- 先写失败测试：工具注册（开关两种状态）、派单创建 `analysis` 会话并调用 `PipelineRunner.start`、失败路径不返回 `session_id`、重复派单幂等、回执字段完整。
- 快速模式 prompt 契约测试：包含派单政策与「禁止编造回执」条款。
- AG-UI 集成测试：工具事件序列 + 管线事件不出现在快速通道。
- E2E：真实浏览器快速模式输入「分析茅台」，断言出现工具横幅与回执，且左侧会话列表出现新分析会话（P1 收尾项，需真实跑栈）。

## Risks / Trade-offs

- [模型跳过工具、直接编造回执] → prompt 硬约束 + 工具结果才带 `session_id`；测试断言未调用工具时无会话创建。
- [模糊请求被误派单] → 工具入口校验 6 位代码；多候选只返回候选清单；prompt 明确等待确认。
- [同一请求重复派单] → Agent 实例内 `symbol → receipt` 幂等表；重复调用返回首次回执。
- [派单不受批量并发上限约束] → 与手工深度分析一致；prompt 限制一次一单；P2 再评估全局上限。
- [provider 不支持工具调用] → capability 门禁 + `{operator_note}` 披露 + `QUICK_OPERATOR_TOOLS` 回滚开关。
- [股票解析数据源失败（AKShare/东财封禁）] → 复用既有 `search_stock_tool` 的失败语义，工具返回错误，不派单。
- [提示词发布遗漏] → tasks 明确包含 `deploy_prompts.py`；未发布时 eval 门禁会拒绝运行（既有 prompt-deploy-consistency 规范）。

## Migration Plan

1. 合并后默认开启 `QUICK_OPERATOR_TOOLS=1`；异常时置 0 即可退回「只聊天」。
2. 无 DB 迁移；旧快速会话不受影响。
3. 前端仅新增 capability 透传字段，旧后端忽略未知字段，可独立回退。

## Open Questions

- P2 是否需要一个跨会话的全局派单并发上限（当前与手工深度分析同为无上限）？
- P3 写操作的确认令牌形态（对话内两次确认 vs 前端确认卡）留待写操作 delta 决定。

## P2/P3 Design Addendum

### D8: 只读工具复用既有端点载荷，做紧凑整形

只读工具不复制业务口径：直接调用既有端点函数（`screening_pool` / `track_record_overview` / `broker_overview` / `broker_positions` / `factor_library_list` / `factor_research_status` / `memory_clause_list`）或 `ops_api._jobs_payload`，再由纯函数做字段裁剪。列表一律截断（池 20、持仓 20、条款 20、因子 20、队列 10），不返回 IC 曲线、paper 净值序列、日志尾或完整历史。缺数据/样本不足/陈旧的状态字段原样保留，不换算为 0。

备选：为新工具单独写查询 SQL。否决——会形成第二套口径，且与页面读数漂移。

### D9: 写操作确认协议（跨 run）

写工具统一签名 `(业务参数, confirm=False, token="")`：

- `confirm=false`：只生成预览摘要 + token，写入进程内 pending 表（TTL 300s，绑定 session/action/params），零副作用。
- `confirm=true`：校验 pending 记录（显式 token 或按 session+action+params 匹配最近一条未过期预览）、参数完全一致、且**当前用户消息**含确认词（确认/确定/执行/同意/可以/好的）后才执行；成功后删除 pending 记录（单次使用）。
- 当前用户消息由 `build_agent(current_user_message=...)` 注入工具闭包；AG-UI 端点传最新 user message，旧 `/api/chat` 传 `req.message`。
- 不依赖 `permission_required`（AG-UI 翻译层丢弃该事件），也不依赖模型自述。

备选：前端确认卡。需要新增 AG-UI 自定义事件与前端状态机，P3 先不做；文本确认 + 服务端校验已能阻止模型自确认。

### D10: 因子研发控制只写期望态

`set_factor_research_running` 调用既有 `_factor_research_control_update`，只写布尔 `desired_running`。回执原样带 `control.desired_running` / `pending` / `honor` / `honor_eta_seconds`；409 `control_unavailable` 原样转述。MUST NOT 触碰研发进程、MUST NOT 暴露命令/脚本/循环参数。

### D11: 日批补跑后台执行

`run_ops_job` 仅接受 `JOB_IDS`。执行时用 `asyncio.create_task(asyncio.to_thread(run_job, job_id, source="manual"))` 启动，短暂等待 0.2s：

- 任务极快完成 → 直接返回真实 `status/run_id/summary`；
- 仍在运行 → 返回 `status=triggered` 回执，提示用 `get_daily_ops_status` 查询；
- `JobAlreadyRunning` / `CohortDisabled` → 明确错误，不重复启动。

后台任务持有在模块级 set 中防止 GC。备选：同步等待端点返回。否决——收割/选股等任务可能数分钟，会卡住快速流。

### D12: 记忆条款与精选池批量分析

- `review_memory_clause`：先 `list_clauses()` 找到条款做预览（含文本前 120 字），确认后调用 `confirm_clause` / `reject_clause`；未知 ID 明确报错。
- `start_pool_analysis`：先读 `screening_pool()` 拿池大小，空池直接报错；确认后调用既有 `screening_analyze()`（复用 AnalyzeQueue 并发上限），返回 started/queued/removed 摘要。

### D13: 工具面膨胀的控制

快速模式工具从 3 个增至 14 个。`max_iterations=6` 足以完成「一次查询/一次预览 + 复述」；prompt 按「股票分析 / 只读查询 / 写操作」三组给出选择规则，避免模型在单轮里连续调用多个写工具。`QUICK_OPERATOR_TOOLS` 环境开关仍可整体退回「只聊天」。

### P2/P3 Risks

- [模型在预览后自行 confirm] → 当前用户消息确认词 + 参数绑定 + 单次 token；单测覆盖拒绝路径。
- [token 跨 run 丢失] → 服务端按 session+action+params 匹配 pending，token 可省略；显式 token 仍支持。
- [写操作误触发日批/消耗配额] → 预览披露任务标签与影响 + 用户确认词；job 白名单；后台执行留痕。
- [因子控制被误读为立即生效] → 回执强制披露 honor/pending/ETA，不得说「已启动/已停止」。
- [工具结果过长] → 只读整形截断；prompt 要求摘要复述。
- [工具面过大导致模型选错] → 三组工具命名清晰 + prompt 选择规则；E2E 覆盖分析派单与只读查询。
