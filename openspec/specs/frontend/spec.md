# Frontend Specification

> 来源：incident 010（缺少行为 spec 约束）、ADR-0012（session 流式与自然输入）、ADR-0017（意图澄清对话流）
> 基线构建日期：2026-07-24
> 说明：本 spec 从 `frontend/src/App.tsx`、`frontend/src/types.ts` 现有实现反推，捕获当前行为作为真相来源。

## Purpose

定义 React 前端（React 18 + Vite + TailwindCSS）的用户交互行为契约。覆盖空状态首页、API Key 管理、会话管理、深度/快速两种模式的 SSE 流式渲染、管线进度展示、报告渲染与文件下载。
## Requirements

> 以下是前端 35 个行为契约，按 6 个行为域分组。每组覆盖一个独立的交互主题，可独立审阅。

| 行为域 | 需求数 | 通俗说明 |
|--------|--------|----------|
| A. 首页与输入 | 5 | 用户第一次打开页面看到什么、怎么选模式、怎么配 API Key |
| B. 会话管理 | 7 | 左侧栏的会话列表：搜索、切换、删除、重命名、新建 |
| C. 深度分析流 | 11 | 深度模式的核心：从发起到报告生成，SSE 事件如何驱动 UI 变化 |
| D. 快速对话流 | 2 | 快速模式的入口和搜索结果展示 |
| E. 对话流公共机制 | 3 | 两种模式共用的思考/工具/错误处理和 SSE 中断逻辑 |
| F. 渲染与展示 | 7 | 报告卡片、思考横幅、工具调用横幅等 UI 组件的渲染规则 |

---

<!-- A. 首页与输入：用户第一次打开页面看到什么、怎么选模式、怎么配 API Key -->

### Requirement: Empty State Landing Page

当无活动会话时，系统 SHALL 显示空状态首页，包含品牌标识、模式选择器、文本输入框和 API Key 状态指示。

#### Scenario: 首次进入显示空状态

- **GIVEN** 应用刚加载且无 currentSessionId
- **WHEN** 页面渲染
- **THEN** 显示 logo、标题"Finance Analysis Agent"、副标题"AI 驱动的 A 股投研分析系统"
- **AND** 显示模式选择器（默认选中"深度研究"）
- **AND** 显示文本输入框，placeholder 随模式变化
- **AND** 显示 4 个特性卡片（4 维并行分析、Bull/Bear 辩论、Risk 压力测试、结构化报告）

#### Scenario: 空状态下输入并发送

- **GIVEN** 空状态首页，API Key 已配置
- **WHEN** 用户在输入框输入文本并按 Enter（非 Shift+Enter）
- **THEN** 系统根据当前模式调用对应流程（深度 -> startAnalysis，快速 -> quickChat）
- **AND** 输入框清空
- **AND** 应用状态从 'empty' 切换到 'clarifying'

#### Scenario: 空状态下未配置 API Key 时发送

- **GIVEN** 空状态首页，API Key 未配置
- **WHEN** 用户输入文本并按 Enter
- **THEN** 不发起请求
- **AND** 弹出 API Key 配置弹窗

### Requirement: Mode Selection

系统 SHALL 提供两种分析模式：深度研究（deep）和快速模式（quick），模式决定后续的请求端点和交互流程。

#### Scenario: 空状态下切换模式

- **GIVEN** 空状态首页
- **WHEN** 用户点击模式下拉选择器并选择"快速模式"或"深度研究"
- **THEN** 当前模式更新为所选模式
- **AND** 输入框 placeholder 更新为对应模式的提示文案

#### Scenario: 快速模式 placeholder

- **GIVEN** 模式为 'quick'
- **THEN** 输入框 placeholder 为"输入问题，如：茅台、宁德时代怎么样"

#### Scenario: 深度模式 placeholder

- **GIVEN** 模式为 'deep'
- **THEN** 输入框 placeholder 为"输入股票名称或代码，如 茅台、300750"

### Requirement: Mode Locking After Session Creation

系统 SHALL 在会话创建后锁定模式切换，防止用户在已有会话中切换模式导致行为不一致。

#### Scenario: 会话存在时模式锁定

- **GIVEN** currentSessionId 不为 null
- **WHEN** 渲染底部输入栏（ChatInputBar）
- **THEN** 深度/快速模式切换按钮处于禁用状态
- **AND** 显示锁定图标提示"会话模式已锁定"

#### Scenario: 加载已有会话时按类型设置模式

- **GIVEN** 用户选择一个已有会话
- **WHEN** 会话详情加载完成
- **THEN** 若 session_type 为 'chat'，模式锁定为 'quick'
- **AND** 若 session_type 不为 'chat'（analysis），模式锁定为 'deep'

#### Scenario: 新建分析时解锁模式

- **GIVEN** 用户点击"新建分析"
- **WHEN** 应用状态重置为 'empty'
- **THEN** currentSessionId 为 null，模式切换按钮恢复可用

### Requirement: Dropdown Outside-Click Dismissal

系统 SHALL 在「模式切换」与「LLM 切换」下拉框展开后，支持点击下拉框以外的页面区域将其关闭，且同一输入栏内的两个下拉框互斥展开（打开一个时自动关闭另一个）。

#### Scenario: 空状态首页模式下拉框点击外部关闭

- **GIVEN** 空状态首页，模式切换下拉框已展开
- **WHEN** 用户点击下拉框以外的页面区域
- **THEN** 模式切换下拉框关闭
- **AND** 当前模式不变，不触发任何输入发送动作

#### Scenario: 会话底部输入栏模式下拉框点击外部关闭

- **GIVEN** 会话视图，底部输入栏的模式切换下拉框已展开
- **WHEN** 用户点击下拉框以外的页面区域
- **THEN** 模式切换下拉框关闭
- **AND** 当前模式与会话不变，不触发新会话

#### Scenario: 空状态首页 LLM 切换下拉框点击外部关闭

- **GIVEN** 空状态首页，已配置至少一个 LLM profile，LLM 切换下拉框已展开
- **WHEN** 用户点击下拉框以外的页面区域
- **THEN** LLM 切换下拉框关闭
- **AND** 当前 LLM profile 不切换

#### Scenario: 会话底部输入栏 LLM 切换下拉框点击外部关闭

- **GIVEN** 会话视图，已配置至少一个 LLM profile，底部输入栏的 LLM 切换下拉框已展开
- **WHEN** 用户点击下拉框以外的页面区域
- **THEN** LLM 切换下拉框关闭
- **AND** 当前 LLM profile 不切换

#### Scenario: 同一输入栏两个下拉框互斥展开

- **GIVEN** 空状态首页，模式切换下拉框已展开
- **WHEN** 用户点击同一输入栏内的 LLM 切换触发按钮
- **THEN** 模式切换下拉框关闭，LLM 切换下拉框展开
- **AND** 反向操作同样生效：LLM 切换下拉框展开时点击模式切换触发按钮，LLM 切换下拉框关闭、模式切换下拉框展开

#### Scenario: 再次点击触发按钮收起

- **GIVEN** 模式切换或 LLM 切换下拉框已展开
- **WHEN** 用户再次点击同一触发按钮
- **THEN** 下拉框关闭

#### Scenario: 点击选项执行动作并关闭

- **GIVEN** 模式切换或 LLM 切换下拉框已展开
- **WHEN** 用户点击下拉框内的某个选项
- **THEN** 执行该选项对应动作（切换模式 / 切换 LLM profile）
- **AND** 下拉框关闭
- **AND** 模式选项的 capability 门禁（`canEnterMode`）逻辑保持现状：门禁不允许的模式选项仍禁用且点击不生效

### Requirement: API Key Management

系统 SHALL 通过浏览器 localStorage 管理 DeepSeek API Key，未配置 Key 时阻止发送请求。

#### Scenario: 持久化 API Key

- **GIVEN** 用户在配置弹窗中输入 API Key 并点击确认
- **WHEN** 确认按钮被点击
- **THEN** API Key 保存到 localStorage（key: 'fa_api_key'）
- **AND** 关闭配置弹窗
- **AND** 空状态首页更新为"API Key 已配置"

#### Scenario: 清空 API Key

- **GIVEN** 用户在配置弹窗中清空输入框并点击确认
- **WHEN** 确认按钮被点击
- **THEN** localStorage 中的 'fa_api_key' 被移除
- **AND** 关闭配置弹窗

#### Scenario: 弹窗遮罩关闭

- **GIVEN** API Key 配置弹窗已打开
- **WHEN** 用户点击弹窗外部遮罩区域
- **THEN** 弹窗关闭，不保存当前输入

#### Scenario: 页面加载时恢复 API Key

- **GIVEN** 页面刷新或重新加载
- **WHEN** 应用初始化
- **THEN** 从 localStorage 读取 API Key 并恢复到应用状态

### Requirement: User Identity Persistence

系统 SHALL 在浏览器 localStorage 中持久化匿名用户 ID，用于标识发起请求的用户。

#### Scenario: 首次访问生成用户 ID

- **GIVEN** localStorage 中不存在 'fa_user_id'
- **WHEN** 应用初始化
- **THEN** 生成格式为 `user-{uuid}` 的唯一 ID 并保存到 localStorage

#### Scenario: 后续访问复用用户 ID

- **GIVEN** localStorage 中已存在 'fa_user_id'
- **WHEN** 应用初始化
- **THEN** 复用已存的用户 ID

---

<!-- B. 会话管理：左侧栏的会话列表：搜索、切换、删除、重命名、新建 -->

### Requirement: Session List Loading

系统 SHALL 在应用初始化时从后端加载会话历史列表并显示在侧边栏中。

#### Scenario: 初始化加载会话列表

- **GIVEN** 应用刚加载
- **WHEN** 应用初始化
- **THEN** 向 GET /api/sessions 发起请求
- **AND** 将返回的 sessions 数组填充到侧边栏列表

#### Scenario: 会话列表为空

- **GIVEN** 后端返回空会话列表
- **WHEN** 侧边栏渲染
- **THEN** 显示"暂无历史会话"提示

### Requirement: Session Search

系统 SHALL 支持在侧边栏按股票名称、股票代码或显示名称搜索会话。

#### Scenario: 输入搜索关键词过滤会话

- **GIVEN** 侧边栏已加载会话列表
- **WHEN** 用户在搜索框输入关键词
- **THEN** 会话列表实时过滤，匹配 stock_name、stock_code 或 display_name（大小写不敏感）的会话保留显示

### Requirement: Session Selection

系统 SHALL 在用户选择已有会话时加载该会话的完整历史并切换到报告视图。选择会话 SHALL NOT 中断该会话或其他会话的后台生成任务；若目标会话正在运行，前端 SHALL 经恢复端点重连其事件流，使输出内容继续增长。切出当前会话时仅断开本地订阅连接。

#### Scenario: 选择会话加载历史

- **GIVEN** 侧边栏会话列表中有一个会话
- **WHEN** 用户点击该会话
- **THEN** 断开当前会话的本地 SSE 订阅连接（不调用后端取消，不影响后台任务）
- **AND** 向 GET /api/sessions/{sessionId} 发起请求获取会话详情
- **AND** 非活跃会话的 messages 在切换时丢弃，切回时从后端重建
- **AND** 设置 currentSessionId 并将 appState 切换为 'report'
- **AND** 按 session_type 锁定模式

#### Scenario: 切换会话不中断生成

- **GIVEN** 会话 A 的深度分析或快速对话正在流式输出
- **WHEN** 用户点击切换到会话 B
- **THEN** 会话 A 的后台生成任务继续运行
- **AND** 侧边栏会话 A 保持"生成中"指示
- **AND** 不向会话 A 发送任何取消/中断请求

#### Scenario: 切回运行中的会话并续传

- **GIVEN** 用户从正在流式输出的会话 A 切出，期间任务继续产出事件
- **WHEN** 用户再次点击会话 A
- **THEN** 加载会话快照后，经 GET /api/sessions/A/stream（携带最后已消费的 seq）重连事件流
- **AND** 重放事件与实时事件经同一处理函数消费，UI 幂等重建
- **AND** 输出内容从切出时的位置继续增长，不重复、不遗漏

#### Scenario: 切回已中断的会话

- **GIVEN** 会话 A 的生成被显式取消或服务重启导致中断
- **WHEN** 用户点击会话 A
- **THEN** 展示已落库的半截回复及"输出已中断，可追问继续"标记
- **AND** 不显示无限转圈的 streaming 状态

#### Scenario: 恢复会话对话历史

- **GIVEN** 加载的会话详情包含 chat_history
- **WHEN** 构建消息列表
- **THEN** 按 chat_history 顺序重建消息：role='user' 的条目渲染为用户消息，其余渲染为助手消息
- **AND** 助手消息包含 thinking 内容和 tool_calls 记录（若历史中存在）
- **AND** 非 chat 类型的会话在第一个用户消息后插入报告消息（含 report_markdown、chart_data、stock_name、duration_ms）

#### Scenario: 会话时间格式化兜底

- **GIVEN** 会话的 created_at 字段缺失、非法或为 epoch 占位值（年份 <= 1970）
- **WHEN** 在侧边栏渲染会话时间
- **THEN** 显示"未知时间"而非 "Invalid Date"

#### Scenario: 多轮澄清会话的报告插入位置

- **GIVEN** 非 chat 会话的 chat_history 为 [用户提问, 助手搜索思考, 用户确认股票]，且 pipeline_anchor 指向最后一条用户消息之后
- **WHEN** 构建消息列表
- **THEN** 消息顺序为：用户提问 → 助手思考/工具调用 → 用户确认 → 管线完成时间轴 → 报告消息
- **AND** 报告消息 SHALL NOT 出现在任何用户消息之前

#### Scenario: 报告后追问会话的报告插入位置

- **GIVEN** 非 chat 会话的 chat_history 为 [用户提问, 用户追问, 助手追问回复]，且 pipeline_anchor 指向第一条用户消息之后
- **WHEN** 构建消息列表
- **THEN** 消息顺序为：用户提问 → 管线完成时间轴 → 报告消息 → 用户追问 → 助手追问回复

### Requirement: Session Deletion

系统 SHALL 支持删除会话，删除当前活动会话时重置到空状态。

#### Scenario: 删除非当前会话

- **GIVEN** 侧边栏会话列表中有多个会话，用户删除非当前会话
- **WHEN** 用户点击删除按钮
- **THEN** 向 DELETE /api/sessions/{sessionId} 发起请求
- **AND** 从侧边栏列表中移除该会话

#### Scenario: 删除当前活动会话

- **GIVEN** 用户删除当前 currentSessionId 对应的会话
- **WHEN** 删除请求完成
- **THEN** 中断进行中的 SSE 流
- **AND** 重置 currentSessionId 为 null、清空消息列表、appState 切换为 'empty'

### Requirement: Session Renaming

系统 SHALL 支持通过双击会话显示名称进行内联重命名。

#### Scenario: 双击进入重命名编辑

- **GIVEN** 侧边栏会话列表中有一个会话
- **WHEN** 用户双击会话的 display_name
- **THEN** display_name 区域变为文本输入框，预填当前名称并自动聚焦

#### Scenario: 确认重命名

- **GIVEN** 重命名输入框已激活
- **WHEN** 用户按 Enter 或输入框失焦，且输入内容非空
- **THEN** 向 PATCH /api/sessions/{sessionId} 发起请求（body: { display_name }）
- **AND** 更新侧边栏列表中对应会话的 display_name

#### Scenario: 取消重命名

- **GIVEN** 重命名输入框已激活
- **WHEN** 用户按 Escape
- **THEN** 退出编辑模式，不发起请求，display_name 保持原值

### Requirement: New Analysis Reset

系统 SHALL 在用户点击"新建分析"时中断进行中的 SSE 流并完全重置应用状态。

#### Scenario: 新建分析重置状态

- **GIVEN** 应用处于任意状态（analyzing/report/clarifying）
- **WHEN** 用户点击侧边栏"新建分析"按钮
- **THEN** 中断进行中的 SSE 流
- **AND** 重置 currentSessionId 为 null
- **AND** 清空消息列表
- **AND** appState 切换为 'empty'

### Requirement: Sidebar Collapse

系统 SHALL 支持侧边栏折叠/展开，折叠时仅显示菜单按钮。

#### Scenario: 折叠侧边栏

- **GIVEN** 侧边栏处于展开状态（宽度 256px）
- **WHEN** 用户点击关闭按钮或菜单切换按钮
- **THEN** 侧边栏折叠为窄条（宽度 48px），仅显示菜单按钮
- **AND** 主内容区域左边距相应调整

#### Scenario: 展开侧边栏

- **GIVEN** 侧边栏处于折叠状态
- **WHEN** 用户点击窄条上的菜单按钮
- **THEN** 侧边栏展开恢复完整宽度
- **AND** 主内容区域左边距相应调整

### Requirement: Session Running Indicator

系统 SHALL 在侧边栏为正在运行生成任务的会话展示"生成中"指示（如呼吸灯/旋转图标），任务结束（完成、中断、出错）后移除。指示状态 SHALL 由 session 的 status 与实时事件流共同驱动，切换页面视图不影响其准确性。

#### Scenario: 运行中的会话显示指示

- **GIVEN** 会话 A 的生成任务正在运行
- **WHEN** 渲染侧边栏会话列表
- **THEN** 会话 A 条目显示"生成中"指示，无论其是否为当前选中会话

#### Scenario: 任务结束后移除指示

- **GIVEN** 会话 A 显示"生成中"指示
- **WHEN** 收到 done / interrupted / error 终态事件，或会话快照 status 非 running
- **THEN** 移除该会话的"生成中"指示

#### Scenario: 运行中会话拒绝输入并提示

- **GIVEN** 当前选中会话正在运行生成任务
- **WHEN** 用户在输入框提交新消息
- **THEN** 展示"该会话正在生成中，可停止后再发"提示
- **AND** 不发送请求（后端 409 为兜底，前端应先拦截）

---

<!-- C. 深度分析流：从发起到报告生成，SSE 事件如何驱动 UI 变化 -->

### Requirement: Deep Analysis Entry

系统 SHALL 通过 POST /api/analyze 发起深度分析，请求体包含 query、api_key、user_id、analysis_type，以及可选的 session_id、stock_code、stock_name、focus。

#### Scenario: 从空状态发起深度分析

- **GIVEN** 空状态首页，模式为 'deep'，API Key 已配置
- **WHEN** 用户输入文本并发送
- **THEN** 向 POST /api/analyze 发起请求，body 包含 query、api_key、user_id、analysis_type='comprehensive'
- **AND** appState 从 'empty' 切换到 'clarifying'
- **AND** 用户消息添加到消息列表

#### Scenario: 澄清阶段后续发送

- **GIVEN** appState 为 'clarifying' 或 'report'，已有 currentSessionId
- **WHEN** 用户在底部输入栏输入文本并发送
- **THEN** 向 POST /api/analyze 发起请求，body 包含当前 session_id
- **AND** 用户消息添加到消息列表

### Requirement: Clarification Conversation Flow

深度模式下，系统 SHALL 将意图澄清阶段的交互（search_stock、web_search、thinking）走对话消息流，不触发管线 UI。仅当 Agent 调用 run_deep_analysis 工具时才进入管线 UI。澄清阶段的思考/工具调用按时间序列写入对话流消息的 `agentTimeline`。

澄清/对话流事件（thinking_token、thinking_replace、thinking_to_answer、search_*、tool_call、chat_token）的路由 SHALL 按**事件自身归属**判定，而非「管线消息是否已创建」这一全局状态：`thinking_token` 仅当事件携带管线节点标识（`node` 字段）时才写入管线消息的节点时序，否则一律写入对话流消息的 `agentTimeline`——即使管线消息已存在（如多轮会话中上一轮已触发管线）。`thinking_replace` / `thinking_to_answer` 作用于对话流消息末尾 thinking item，SHALL 始终路由到对话流，SHALL NOT 因管线消息存在而被丢弃。

> 来源：ADR-0017 D1 - 深度模式入口统一走 ReAct Agent 对话流

#### Scenario: 澄清阶段走对话流

- **GIVEN** 深度分析 SSE 流进行中，尚未收到 run_deep_analysis tool_call
- **WHEN** 收到 search_stock / web_search / batch_web_search 的 tool_call 事件
- **THEN** 工具调用作为 `{type:'tool_call', ...}` TimelineItem 追加到对话流消息的 `agentTimeline`，不创建管线消息
- **AND** appState 保持 'clarifying'

#### Scenario: 思考过程在澄清阶段走对话流

- **GIVEN** 深度分析 SSE 流进行中，pipelineMsgRef 为空（未进入管线模式）
- **WHEN** 收到 thinking_token / thinking_replace / thinking_to_answer 事件
- **THEN** 思考 token 追加到对话流消息 `agentTimeline` 末尾的 thinking item（若末尾非 thinking item 则新建），不写入管线消息

#### Scenario: 管线消息存在时不携带 node 的思考仍走对话流

- **GIVEN** 深度分析 SSE 流进行中，管线消息已创建（pipelineMsgRef 非空，如多轮会话上一轮已触发管线，或本轮 run_deep_analysis 已发出后 Agent 继续澄清）
- **WHEN** 收到不携带 `node` 字段的 thinking_token 事件
- **THEN** 该思考 token SHALL 写入当前对话流消息的 `agentTimeline`，SHALL NOT 写入管线消息的节点时序
- **AND** 对话流消息的思考横幅正常显示，不错位到管线卡片

#### Scenario: 管线节点思考按 node 归属进管线 UI

- **GIVEN** 管线运行中（pipelineMsgRef 非空）
- **WHEN** 收到携带 `node` 字段的 thinking_token 事件（管线节点的思考）
- **THEN** 该 token SHALL 写入管线消息对应节点的时序（nodeTimelines[node]）
- **AND** 不写入对话流消息

#### Scenario: thinking_replace / thinking_to_answer 不被管线状态丢弃

- **GIVEN** 深度分析 SSE 流进行中，管线消息已创建（pipelineMsgRef 非空）
- **WHEN** 收到 thinking_replace 事件（DSML 清理）或 thinking_to_answer 事件
- **THEN** 事件 SHALL 路由到对话流处理（替换/切割对话流消息末尾 thinking item）
- **AND** SHALL NOT 被静默丢弃
- **AND** DSML 清理后对话流思考横幅不残留原始 DSML 文本或回复前缀

#### Scenario: Agent 文本回复走对话流

- **GIVEN** 深度分析 SSE 流进行中，pipelineMsgRef 为空
- **WHEN** 收到 chat_token 事件
- **THEN** token 追加到助手消息的 chatResponse（对话流）

#### Scenario: run_deep_analysis 触发管线 UI

- **GIVEN** 深度分析 SSE 流进行中
- **WHEN** 收到 tool_call 事件且 name 为 'run_deep_analysis'
- **THEN** 创建管线消息（pipelineMsg），内容为"开始深度分析..."
- **AND** appState 切换为 'analyzing'
- **AND** 后续的 parsing/resolved/node_start/node_complete/report_chunk/report_ready 事件写入管线消息

#### Scenario: awaiting_input 切换到澄清等待

- **GIVEN** 深度分析 SSE 流进行中
- **WHEN** 收到 awaiting_input 事件
- **THEN** appState 切换为 'clarifying'
- **AND** 助手消息停止流式状态（streaming = false）

#### Scenario: 管线完成后 pipelineMsgRef 收口

- **GIVEN** 管线消息已创建，深度分析管线运行中
- **WHEN** 收到 report_ready 事件（管线完成）或 done 终态事件
- **THEN** 前端 SHALL 将 pipelineMsgRef 置 null
- **AND** 后续轮次的澄清思考不再被路由到已完成的管线消息

### Requirement: SSE Event: session_created

系统 SHALL 在收到 session_created 事件时设置当前会话 ID 并刷新会话列表。

#### Scenario: 会话创建事件处理

- **GIVEN** 深度分析 SSE 流进行中
- **WHEN** 收到 session_created 事件
- **THEN** 设置 currentSessionId 为事件中的 session_id
- **AND** 调用 loadSessions() 刷新侧边栏会话列表

### Requirement: SSE Event: stock_resolved

系统 SHALL 在收到 stock_resolved 事件时，根据当前是否处于管线模式分别处理。

#### Scenario: 管线模式下股票识别

- **GIVEN** pipelineMsgRef 不为空（已进入管线模式）
- **WHEN** 收到 stock_resolved 事件
- **THEN** 更新管线消息内容为"已识别：{stock_name} ({stock_code})"

#### Scenario: 澄清阶段股票识别

- **GIVEN** pipelineMsgRef 为空（澄清阶段）
- **WHEN** 收到 stock_resolved 事件
- **THEN** 将识别结果作为 search_stock 工具的 `{type:'tool_call', name:'search_stock', ...}` TimelineItem 追加到对话流消息的 `agentTimeline`

### Requirement: SSE Event: search_result (Deep Mode)

系统 SHALL 在深度分析澄清阶段收到 search_result 事件时，更新对话流消息 `agentTimeline` 中对应 search TimelineItem 的结果与状态，由独立搜索横幅展示，与快速模式保持一致的 Kimi-style 交互。

#### Scenario: 搜索结果由独立搜索横幅展示

- **GIVEN** 深度分析 SSE 流进行中，pipelineMsgRef 为空（澄清阶段）
- **WHEN** 收到 search_result 事件
- **THEN** 对话流消息 `agentTimeline` 中对应 `{type:'search', ...}` item 的 status 设为 'done'，results 设为事件中的结果列表
- **AND** 渲染独立搜索横幅，显示"搜索了 N 个网页"
- **AND** 搜索横幅可展开查看网页列表（标题 + URL + 摘要 + favicon）
- **AND** 不将搜索结果摘要附加到工具调用横幅

### Requirement: Deep Mode Search Banner

系统 SHALL 在深度分析澄清阶段将搜索结果生成为 `search` 类型 TimelineItem，渲染为独立的搜索横幅，与快速模式保持一致的 Kimi-style 交互，按 `agentTimeline` 数组顺序排列。

#### Scenario: 澄清阶段搜索结果以搜索横幅展示

- **GIVEN** 深度分析 SSE 流进行中，pipelineMsgRef 为空（澄清阶段）
- **WHEN** 收到 `search_result` 事件
- **THEN** 更新 `agentTimeline` 中对应 search item 的 `status='done'`，`results` 设为事件中的结果列表
- **AND** 渲染为独立 SearchBanner，显示"搜索了 N 个网页"
- **AND** SearchBanner 可展开查看网页列表（标题 + URL + 摘要 + favicon）

#### Scenario: 澄清阶段搜索开始显示搜索中状态

- **GIVEN** 深度分析 SSE 流进行中，pipelineMsgRef 为空（澄清阶段）
- **WHEN** 收到 `search_start` 事件
- **THEN** 新建 `{type:'search', query, status:'searching'}` item 追加到 `agentTimeline`
- **AND** 渲染为独立 SearchBanner，显示"正在搜索网页"（可附查询词）
- **AND** 不显示"思考中"

### Requirement: Pipeline Progress Display

系统 SHALL 在深度分析期间以**分层时间轴**展示管线进度：顶层为 6 个 layer 节点（PREP / Layer I / Layer II / Trader / Risk / Fund），每个 layer 可展开显示其子节点列表。每个 layer 与子节点 SHALL 显示状态图标（等待/运行/完成/失败）与耗时，当前运行的子节点 SHALL 显示已运行时长并被高亮。无论 fast path 还是 agent 路径，后端 SHALL 为每个图节点发送 `node_start` 与 `node_complete` 事件对。节点计时 SHALL 优先使用后端提供的真实生命周期时间戳（server_start_ts / server_end_ts / server_duration_ms）；当后端未提供时（stub、fast path、历史会话），SHALL 回退到前端事件到达时间戳计算，保持向后兼容。

#### Scenario: 分层时间轴结构

- **GIVEN** 深度分析管线 UI 已渲染
- **THEN** 展示 6 个 layer 节点：PREP、Layer I、Layer II、Trader、Risk、Fund Manager
- **AND** 每个 layer 节点显示状态图标（○ 等待 / ◐ 运行 / ●✓ 完成 / ✗ 失败）、层名与层耗时
- **AND** 展开的 layer 内按执行顺序列出子节点（角色名中文化），各显示状态图标与节点耗时

#### Scenario: layer 与子节点映射

- **GIVEN** 分层时间轴已渲染
- **THEN** PREP 子节点为 check_cache、fetch_data、compute_metrics、validate_financials、verify_citations
- **AND** Layer I 子节点为 fundamental_analyst、technical_analyst、macro_analyst、sentiment_analyst（并行）
- **AND** Layer II 子节点为 bull_r1、bear_r1、bull_r2、bear_r2、research_manager
- **AND** Trader 子节点为 trader，Risk 子节点为风控辩论各节点，Fund 子节点为 fund_manager

#### Scenario: 节点开始时更新状态

- **GIVEN** 管线 UI 已渲染
- **WHEN** 收到 node_start 事件
- **THEN** 对应子节点状态更新为 'running'，所在 layer 状态更新为 'running'
- **AND** 该子节点显示已运行时长（每秒刷新）
- **AND** 该子节点所在 layer 自动展开并高亮当前节点

#### Scenario: 节点完成时更新状态

- **GIVEN** 管线 UI 已渲染
- **WHEN** 收到 node_complete 事件
- **THEN** 对应子节点状态更新为 'completed'，显示节点总耗时
- **AND** 该 layer 全部子节点完成时，layer 状态更新为 'completed' 并显示层总耗时
- **AND** 整体进度条按已完成节点数/总节点数更新

#### Scenario: Layer I 并行分析师独立状态

- **GIVEN** 管线进入 Layer I，4 个分析师并行执行
- **WHEN** 收到某分析师（如 fundamental_analyst）的 node_start / node_complete
- **THEN** 仅该分析师子节点状态更新，其余分析师状态不受影响
- **AND** 各分析师完成后显示各自的一句话摘要（来自 node_complete 内容），摘要与分析师角色对应不错位

#### Scenario: layer 展开折叠行为

- **GIVEN** 管线运行中
- **THEN** 当前运行 layer 默认展开，已完成 layer 默认折叠（显示层摘要行），未到 layer 折叠
- **WHEN** 用户手动展开/折叠某 layer
- **THEN** 本次会话内记住该偏好，不再自动改变该 layer 展开状态

#### Scenario: 自动滚动定位当前节点

- **GIVEN** 管线运行中，时间轴内容超出可视区域
- **WHEN** 当前运行节点切换
- **THEN** 时间轴平滑滚动使新当前节点进入可视区域
- **AND** 若用户最近 3 秒内手动滚动过，则暂停自动滚动

#### Scenario: agent 路径发送 node_start

- **GIVEN** 深度分析经 agent 路径（harness ReAct）执行 run_deep_analysis 流式工具
- **WHEN** graph.stream 迭代中某图节点的 updates chunk 首次出现
- **THEN** 后端 SHALL 先发送该节点的 node_start 事件（携带 node、layer、desc），再发送 node_complete
- **AND** 同一节点重复出现时不重复发送 node_start
- **AND** Layer I 并行分析师各自的 chunk 拆分发送独立节点事件；无法拆分时回退整层 node_complete

#### Scenario: 节点计时优先使用后端真实时间戳

- **GIVEN** 管线 UI 已渲染且收到 node_complete 事件
- **WHEN** 该事件携带 server_duration_ms（或 server_start_ts + server_end_ts）
- **THEN** 对应节点的 durationMs SHALL 采用后端真实耗时
- **AND** 快速节点显示真实毫秒级耗时（<1s 时 formatDurationMs 显示 "0:00"）
- **AND** 慢节点（LLM）显示真实秒级耗时

#### Scenario: 节点开始时间戳优先使用后端入口时间

- **GIVEN** 收到 node_start 事件
- **WHEN** 该事件携带 server_start_ts
- **THEN** 节点的 startedAt SHALL 采用 server_start_ts 而非前端 Date.now()
- **AND** 当前运行节点的实时已运行时长基于该时间戳计算

#### Scenario: 无后端时间戳时回退前端计算

- **GIVEN** 收到 node_complete 事件
- **WHEN** 该事件未携带任何 server_* 时间戳字段（stub / fast path / 历史会话）
- **THEN** 前端 SHALL 回退到现有 Date.now() 到达时间戳计算 durationMs
- **AND** 现有 E2E 与单测行为不回归

### Requirement: Pipeline Message Visibility Filter

系统 SHALL 仅在 appState 为 'analyzing' 时显示 pipeline 类型的消息，其他状态下隐藏。

#### Scenario: 分析中显示管线消息

- **GIVEN** appState 为 'analyzing'
- **WHEN** 渲染消息列表
- **THEN** pipeline 类型的消息显示在列表中

#### Scenario: 非分析状态隐藏管线消息

- **GIVEN** appState 为 'report' 或 'clarifying'
- **WHEN** 渲染消息列表
- **THEN** pipeline 类型的消息从列表中过滤掉，不显示

### Requirement: Pipeline Thinking Display

系统 SHALL 将各 agent 的思考过程归入分层时间轴的对应子节点下展示。当前运行节点 SHALL 内联显示实时思考摘要（单行预览），点击可展开完整 timeline；已完成节点的思考折叠到节点条目下。

#### Scenario: 当前节点内联实时思考摘要

- **GIVEN** 管线运行中，节点 N 为当前 running 节点
- **WHEN** 收到节点 N 的 thinking_token 事件
- **THEN** 节点 N 条目下方内联显示最新思考内容单行预览（流式更新，超长截断）

#### Scenario: 展开节点完整思考

- **GIVEN** 管线 UI 已渲染，节点 N 有思考内容
- **WHEN** 用户点击节点 N 条目
- **THEN** 展开该节点的完整 timeline（思考/搜索/工具调用横幅按时间序列）
- **AND** 再次点击折叠

#### Scenario: 节点完成时思考横幅显式折叠

- **GIVEN** 管线运行中，节点 N 的 thinking item 处于流式活动态
- **WHEN** 收到节点 N 的 node_complete 事件
- **THEN** 节点 N timeline 末尾的 thinking item 置为完成态（done=true）并折叠为"思考已完成"样式

#### Scenario: 下一节点思考开始时上一节点横幅收口

- **GIVEN** 管线运行中，节点 A 的 thinking item 因故未收到 node_complete 收口
- **WHEN** 收到节点 B（B ≠ A）的 thinking_token 事件
- **THEN** 节点 A 末尾未完成的 thinking item 置为完成态并折叠

#### Scenario: 历史会话兼容

- **GIVEN** 加载无节点级事件数据的旧会话
- **WHEN** 渲染管线消息
- **THEN** 回退为按既有 agentTimeline 分组渲染（角色名标题分隔），不报错

### Requirement: Pipeline Expandable Log

系统 SHALL 在管线 UI 中提供可展开的实时输出日志，展示已完成节点的摘要。

#### Scenario: 查看实时日志

- **GIVEN** 管线 UI 已渲染
- **WHEN** 用户点击"查看实时输出日志"
- **THEN** 展开日志区域，显示所有已完成节点的记录（节点名、时间戳、摘要）
- **AND** 若有当前运行节点，显示运行中指示器

### Requirement: Report Streaming Render

系统 SHALL 在深度分析管线完成后，通过 report_chunk 事件渐进渲染报告，最终由 report_ready 事件完成；报告消息 SHALL 同时记录股票名称与股票代码，供报告名等展示使用。
(Previously: 系统 SHALL 在深度分析管线完成后，通过 report_chunk 事件渐进渲染报告，最终由 report_ready 事件完成。报告消息仅记录 stockName，不含股票代码。)

#### Scenario: 报告流式分块累积

- **GIVEN** 管线 UI 已渲染
- **WHEN** 收到 report_chunk 事件
- **THEN** 若尚无报告消息，创建一条 streaming=true 的报告消息，初始内容为事件文本
- **AND** 若已有报告消息，将事件文本追加到 reportMarkdown
- **AND** 报告消息显示"正在生成报告"流式指示器

#### Scenario: 报告就绪完成渲染

- **GIVEN** 报告消息正在流式生成
- **WHEN** 收到 report_ready 事件
- **THEN** 更新报告消息：reportMarkdown 替换为最终完整版、chartData、filePaths、stockName、stockCode、durationMs、sessionId、webSources，streaming=false
- **AND** appState 切换为 'report'
- **AND** 设置 currentSessionId 为事件中的 session_id
- **AND** 刷新侧边栏会话列表
- **AND** 添加系统消息"分析完成 · 耗时 {N} 秒"

#### Scenario: 无流式分块直接就绪

- **GIVEN** 管线 UI 已渲染，尚未收到任何 report_chunk
- **WHEN** 直接收到 report_ready 事件
- **THEN** 创建一条完整的报告消息（streaming=false），包含所有最终数据（含 stockName 与 stockCode）

### Requirement: Quick Chat Entry

系统 SHALL 通过 POST /api/agui/quick（AG-UI 协议）发起快速对话：请求体为 AG-UI RunAgentInput 形态（messages 含 user 消息、threadId；新增会话时 threadId 为空，服务端新建会话并经 RUN_STARTED.threadId 回传绑定）；api_key 与 llm_config 经 forwardedProps 透传。后续发送 SHALL 携带由 RUN_STARTED 回传的会话 id 作为 threadId，且追问 SHALL 挂载于当前对话末尾（RunAgentInput 携带完整历史上下文）。

quick 模式消息流的渲染实现 SHALL 使用 assistant-ui Thread + AG-UI runtime：流式增量逐块呈现、RUN_FINISHED 后无残留流式指示器、思考段与工具调用以消息 parts 按时间顺序呈现；历史消息恢复 SHALL 沿用 MessageItem 快照渲染路径（rebuildSession），Thread 只接管本 mount 发起的新 run。发送入口、回复完成态、会话切换守卫行为 SHALL 与替换前一致；深度模式相关测试零修改通过。

#### Scenario: 从空状态发起快速对话

- **GIVEN** 空状态首页，模式为 'quick'，API Key 已配置
- **WHEN** 用户输入文本并发送
- **THEN** 向 POST /api/agui/quick 发起请求，body 为 RunAgentInput 形态且 threadId 为空
- **AND** appState 从 'empty' 切换到 'clarifying'
- **AND** 用户消息与助手回复经 assistant-ui Thread 渲染，RUN_STARTED.threadId 回传后绑定新会话

#### Scenario: 对话中后续发送

- **GIVEN** 会话视图，已有 currentSessionId
- **WHEN** 用户在底部输入栏输入文本并发送
- **THEN** 向 POST /api/agui/quick 发起请求，threadId 为当前会话 id
- **AND** 追问挂载于当前对话末尾，第二轮回复为独立消息（不串入第一轮）

#### Scenario: 刷新恢复渲染历史

- **GIVEN** 某 quick 会话已有历史对话
- **WHEN** 用户刷新页面后自动恢复该会话
- **THEN** 历史消息由 MessageItem 快照路径渲染（含思考/工具横幅时序），顺序与落库一致
- **AND** assistant-ui Thread 重挂载后为空壳，不重复渲染历史

### Requirement: Quick Chat Search Events

quick 模式下搜索类工具（web_search 等）SHALL 经 AG-UI `TOOL_CALL_START`/`TOOL_CALL_ARGS`/`TOOL_CALL_END`/`TOOL_CALL_RESULT` 事件流承载，并渲染为工具调用横幅（「调用工具 · {工具名}」单行横幅），按事件时间顺序与思考段交错呈现；历史快照恢复时以 `tool_call` 类型 TimelineItem 渲染工具调用横幅（label 按工具名映射）。独立搜索横幅（SearchBanner）语义 SHALL 保留给深度模式管线/澄清流。

#### Scenario: 搜索工具调用实时呈现

- **GIVEN** quick 模式对话流式输出进行中，Agent 调用 web_search
- **WHEN** 收到 TOOL_CALL_* 事件族（以 TOOL_CALL_END 闭合）
- **THEN** assistant 消息内按时间顺序渲染「调用工具 · web_search」横幅
- **AND** 工具调用处于 active 态时 run 终止事件 SHALL NOT 被客户端校验拒绝

#### Scenario: 刷新恢复含工具调用时序

- **GIVEN** 某 quick 会话历史中含带工具调用的回复（agentTimeline 结构化落库）
- **WHEN** 用户刷新页面恢复该会话
- **THEN** 历史快照按落库 agentTimeline 渲染思考/工具横幅时序
- **AND** 搜索类工具步骤 SHALL NOT 被恢复路径丢弃

### Requirement: Conversation Stream Common Events

系统 SHALL 在深度模式的澄清阶段和快速模式中共用同一套对话流事件处理逻辑，将 thinking、search、tool_call、chat 类事件写入 `agentTimeline` 数组（按事件时序）和 `chatResponse`（回答正文）。思考内容来源于 DeepSeek 原生 `reasoning_content`（通过 `thinking_token` 事件下发），与回答内容（`chat_token` 事件）天然分离，不再使用 `thinking_to_answer` 剥离机制。搜索类工具（`web_search` / `batch_web_search`）的调用与结果 SHALL NOT 进入工具调用横幅，仅由独立搜索横幅承载。思考横幅 SHALL 在思考结束（出现后续事件）时显式置为完成态并自动折叠。

#### Scenario: thinking_token 累积原生思考内容

- **GIVEN** 对话流进行中
- **WHEN** 收到 `thinking_token` 事件（来源为 LLM 原生 `reasoning_content`）
- **THEN** 若 `agentTimeline` 末尾是 `thinking` 类型 item，将 token 累加到该 item 的 `content`
- **AND** 否则新建 `{type:'thinking', content: token, done:false}` item 追加到 `agentTimeline`
- **AND** 思考链（thinking）是独立的思维内容，与回答（`chatResponse`，content）分离

#### Scenario: 思考后接工具调用时思考横幅折叠

- **GIVEN** 对话流进行中，`agentTimeline` 末尾是未完成（done=false）的 thinking item
- **WHEN** 收到 `tool_call` 事件
- **THEN** 该 thinking item 置为完成态（done=true）
- **AND** ThinkingBanner 从"思考中"切换为"思考已完成"并自动折叠

#### Scenario: 思考后接回答 token 时思考横幅折叠

- **GIVEN** 对话流进行中，`agentTimeline` 末尾是未完成（done=false）的 thinking item
- **WHEN** 收到首个 `chat_token` 事件
- **THEN** 该 thinking item 置为完成态（done=true）并自动折叠

#### Scenario: thinking_to_answer 事件不再下发

- **GIVEN** 对话流进行中，LLM 输出 reasoning_content 后输出 content
- **WHEN** 思考与回答流式下发完成
- **THEN** SHALL NOT 收到 thinking_to_answer 事件（reasoning 与 content 天然分离）
- **AND** 前端无需执行思考剥离为回答的逻辑

#### Scenario: thinking_replace 事件不再下发

- **GIVEN** 对话流进行中
- **WHEN** LLM 原生 reasoning_content 流式输出
- **THEN** SHALL NOT 收到 thinking_replace 事件（原生 reasoning 无需 DSML 清理后处理）
- **AND** 思考内容直接流式展示，无需覆盖

#### Scenario: tool_call 记录到工具调用横幅

- **GIVEN** 对话流进行中
- **WHEN** 收到 `tool_call` 事件，且 name 不为 `run_deep_analysis`、`web_search`、`batch_web_search`
- **THEN** 新建 `{type:'tool_call', name, args, done:false}` item 追加到 `agentTimeline`，渲染为 ToolCallEntry（含图标、标签、参数摘要、done=false）
- **AND** 工具调用与思考过程分离展示

#### Scenario: 搜索类 tool_call 不进入工具调用横幅

- **GIVEN** 对话流进行中
- **WHEN** 收到 `tool_call` 事件，且 name 为 `web_search` 或 `batch_web_search`
- **THEN** SHALL NOT 在 `agentTimeline` 创建 tool_call item / ToolCallEntry
- **AND** 该工具的状态与结果由 `search_start` / `search_result` 事件驱动搜索横幅展示
- **AND** 不触发管线 UI

#### Scenario: tool_result 附加到对应工具调用

- **GIVEN** 对话流进行中，助手消息已有工具调用记录
- **WHEN** 收到 `tool_result` 事件
- **THEN** 若事件对应 `web_search` / `batch_web_search` 工具，SHALL NOT 附加到任何工具调用记录、SHALL NOT 新建记录（其结果由 `search_result` 事件驱动搜索横幅展示）
- **AND** 否则，优先附加到同名且未完成的最近一次工具调用记录（设 `result` 和 `done=true`）
- **AND** 若无同名未完成记录，回退到最近未完成的任意工具调用
- **AND** 若该记录已完成（已被 search_result/stock_resolved 等结构化事件先行附加），跳过避免重复
- **AND** 若无任何匹配记录且结果非空，新建一条仅含结果的工具调用记录

#### Scenario: search 事件生成 search item

- **GIVEN** 对话流进行中
- **WHEN** 收到 `search_start` / `search_result` / `search_error` 事件
- **THEN** 新建或更新 `search` 类型 TimelineItem（见 Agent Timeline Display 需求）
- **AND** 搜索类工具不生成 tool_call item

#### Scenario: chat_token 累积回答

- **GIVEN** 对话流进行中
- **WHEN** 收到 `chat_token` 事件（来源为 LLM content，与 reasoning 分离）
- **THEN** token 追加到助手消息的 `chatResponse`

#### Scenario: chat_done 结束流式

- **GIVEN** 对话流进行中，助手消息 streaming=true
- **WHEN** 收到 `chat_done` 事件
- **THEN** 助手消息 streaming 设为 false
- **AND** 所有 thinking item 置为完成态（done=true），用 `extractThinkingTitle` 提取标题写入 `title` 字段

#### Scenario: 对话流 error 事件

- **GIVEN** 对话流进行中
- **WHEN** 收到 `error` 事件
- **THEN** 助手消息 `chatResponse` 设为"❌ {message}"，streaming 设为 false
- **AND** 所有未完成 thinking item 置为完成态（done=true）

### Requirement: SSE Stream Abort Control

系统 SHALL 使用 AbortController 管理前端 SSE 订阅连接的生命周期。AbortController 仅控制本地订阅的断开，SHALL NOT 作为终止后端生成任务的手段；终止生成 SHALL 通过显式取消操作调用 `POST /api/sessions/{id}/cancel`。切换会话、新建分析不再触发对生成任务的中断语义。

#### Scenario: 显式停止生成

- **GIVEN** 某会话的生成任务正在运行
- **WHEN** 用户点击"停止"按钮
- **THEN** 调用 POST /api/sessions/{id}/cancel 取消后台任务
- **AND** 收到 interrupted 终态事件后结束本地流式状态
- **AND** 半截回复保留展示并带中断标记

#### Scenario: 切换会话仅断开本地订阅

- **GIVEN** 深度分析或快速对话 SSE 流正在进行
- **WHEN** 用户选择另一个会话或新建分析
- **THEN** 调用本地 abort 断开当前订阅连接（AbortError 静默退出）
- **AND** SHALL NOT 调用后端取消端点
- **AND** 后台生成任务不受影响

#### Scenario: 删除当前会话时取消生成

- **GIVEN** SSE 流正在进行，用户删除当前活动会话
- **WHEN** 删除操作执行
- **THEN** 断开本地订阅连接
- **AND** 该会话的后台任务随会话删除而终止（后端删除会话时取消其活跃任务）
- **AND** 重置 currentSessionId 为 null、清空消息列表、appState 切换为 'empty'

#### Scenario: 每会话独立的订阅连接

- **GIVEN** 上一轮 SSE 订阅已断开或完成
- **WHEN** 发起新的 startAnalysis、quickChat 或恢复端点订阅
- **THEN** StreamStore 为该会话创建新的 AbortController
- **AND** 不同会话的 AbortController 互不影响

#### Scenario: 非中断的连接错误显示错误消息

- **GIVEN** SSE 订阅连接进行中
- **WHEN** 发生非 AbortError 的连接错误
- **THEN** 深度模式下添加 error 类型消息显示"连接错误: {message}"
- **AND** 快速模式下将助手消息更新为 error 类型
- **AND** 错误消息注明可按 after_seq 重连恢复（若该会话任务仍在运行）

### Requirement: Streaming State Defensive Cleanup

系统 SHALL 保证 SSE 流结束时（reader 正常结束或连接异常断开）助手消息的流式状态被清除，使流式游标不依赖单一终态事件。当 SSE 流结束但前端未收到终态事件（done/interrupted/error）时，前端 SHALL 将当前轮次的助手消息 `streaming` 置为 false。终态事件处理与防御性清理对 `streaming` 的设置 SHALL 幂等，重复设置无副作用。

#### Scenario: 流结束未收到终态事件时清除游标

- **GIVEN** 深度分析或快速对话 SSE 流进行中，助手消息 streaming=true
- **WHEN** SSE reader 正常结束（读到流末尾）但未收到 done/interrupted/error 终态事件
- **THEN** 前端 SHALL 将助手消息 streaming 置为 false
- **AND** 流式游标消失，用户可继续追问

#### Scenario: 连接异常断开时清除游标

- **GIVEN** 深度分析或快速对话 SSE 流进行中，助手消息 streaming=true
- **WHEN** 发生非 AbortError 的连接错误导致流中断
- **THEN** 前端 SHALL 将助手消息 streaming 置为 false
- **AND** 展示连接错误消息

#### Scenario: 终态事件与防御性清理幂等

- **GIVEN** SSE 流进行中，助手消息 streaming=true
- **WHEN** 先收到 done 终态事件（streaming 置 false），随后 reader 结束触发防御性清理
- **THEN** 防御性清理再次设置 streaming=false 无副作用
- **AND** 游标保持消失状态，不闪烁或复活

> **关于原 REMOVED 段（2026-09-06 rebase 说明）**：原 delta 声明移除「前端流状态快照层」「ref 镜像同步机制」「手写守卫函数族」三个需求，但经查（git -S 全历史）三者从未进入主规范库（属 Aug-5 时代未归档链路的产物），故无 REMOVED 操作可言。移除理由仍有存档价值：
>
> - **快照层**：后端事件日志（session_events 表 + stream?after_seq= 断点续传）就绪后，前端快照层冗余且双事实源互相打架；改为切换时丢弃非活跃会话 messages、切回时从后端重建。
> - **ref 镜像**：19 个 ref 镜像的存在理由（闭包防旧值/切换快照）被 StreamStore 单一事实源消除。
> - **手写守卫族**：saveCurrentStreamState / ensureSingleReader 等 7+ 守卫的功能由 store 内部机制（switchSession 原子协议、applyEvent seq 守门、pump 单读取器）替代，3 处手写 getReader() 循环收敛为 1 处 pump。

### Requirement: Deep Mode chat_done Event Routing

深度模式（`startAnalysis`）的 SSE 事件循环 SHALL 将 `chat_done` 事件路由到对话流共享处理函数（`handleChatStreamEvent`），与快速模式（`quickChat`）保持一致。`chat_done` 事件经 `applyChatStreamEvent` 处理后 SHALL 将助手消息 `streaming` 置为 false 并收口所有 thinking item。

#### Scenario: 深度模式澄清阶段收到 chat_done 清除流式状态

- **GIVEN** 深度分析 SSE 流进行中，pipelineMsgRef 为空（澄清阶段），助手消息 streaming=true
- **WHEN** 收到 `chat_done` 事件
- **THEN** 事件 SHALL 经 `handleChatStreamEvent` 处理，助手消息 streaming 置为 false
- **AND** 所有 thinking item 收口并提取标题写入 title 字段
- **AND** 流式游标消失

#### Scenario: 深度模式管线运行期间不误处理 chat_done

- **GIVEN** 深度分析 SSE 流进行中，pipelineMsgRef 不为空（管线模式）
- **WHEN** 收到 `chat_done` 事件
- **THEN** 事件 SHALL 经 `handleChatStreamEvent` 处理（写入对话流消息 timeline）
- **AND** 管线消息不受影响，管线进度继续由管线事件驱动

### Requirement: Deep Mode Error Handling

系统 SHALL 在深度分析流的 error 事件中，根据是否处于管线模式分别处理错误展示。

#### Scenario: 管线模式下的错误

- **GIVEN** 深度分析 SSE 流进行中，pipelineMsgRef 不为空
- **WHEN** 收到 error 事件
- **THEN** 管线消息类型更新为 'error'，内容为"错误: {message}"

#### Scenario: 非管线模式下的错误

- **GIVEN** 深度分析 SSE 流进行中，pipelineMsgRef 为空
- **WHEN** 收到 error 事件
- **THEN** 添加一条 error 类型的消息到消息列表，内容为"错误: {message}"

---

<!-- F. 渲染与展示：报告卡片、思考横幅、工具调用横幅等 UI 组件的渲染规则 -->

### Requirement: Report Card Rendering

系统 SHALL 在报告消息中渲染报告头部、财务图表、Markdown 正文、参考资料和免责声明；报告头部 SHALL NOT 承载任何文件导出按钮或「全部文件」入口。
(Previously: 系统 SHALL 在报告消息中渲染报告头部、文件导出、财务图表、Markdown 正文、参考资料和免责声明。报告头部显示「全部文件」入口横幅（图标 + "全部文件"文案），点击打开右侧文件导出抽屉，抽屉内依据 filePaths 的 docx/pptx/pdf/md 键列出导出文件。)

#### Scenario: 流式报告显示生成指示器

- **GIVEN** 报告消息 streaming=true
- **THEN** 顶部显示"正在生成报告 · 流式输出中"指示器（脉冲动画）

#### Scenario: 报告头部展示

- **GIVEN** 报告消息 streaming=false
- **THEN** 显示「股票名称（股票代码）」标题（如「贵州茅台（600519）」；名称缺失或等于代码时仅显示代码）、"深度分析"标签、耗时信息
- **AND** 报告头部不显示任何文件导出按钮或「全部文件」入口

#### Scenario: 打开导出抽屉

- **GIVEN** 任一会话级导出入口（报告名横幅 / 全部文件横幅 / 顶部栏「查看全部文件」按钮）已渲染（见「会话级文件导出入口」）
- **WHEN** 用户点击该入口
- **THEN** 右侧文件导出抽屉滑出打开
- **AND** 抽屉内仅列出该会话报告已生成的可下载文件（依据 filePaths 各条目，带格式徽标与文件名），不展示缺失格式的现场生成入口

#### Scenario: 财务图表展示

- **GIVEN** 报告消息包含 chartData 且 chartData.annual 非空
- **THEN** 在报告头部下方渲染 ChartsSection（ECharts 交互图表）

#### Scenario: Markdown 正文渲染

- **GIVEN** 报告消息包含 reportMarkdown
- **THEN** 使用 react-markdown + remark-gfm 渲染 Markdown
- **AND** 图片标签（img）被忽略不渲染
- **AND** 链接在新标签页打开
- **AND** 渲染区域最大高度 600px，可滚动

#### Scenario: 参考资料信源卡片

- **GIVEN** 报告消息 streaming=false 且包含 webSources（非空数组）
- **THEN** 在 Markdown 正文下方渲染信源卡片网格
- **AND** 每张卡片显示序号、标题、摘要、域名、favicon
- **AND** 标题显示"参考资料（N 个信源）"

#### Scenario: 免责声明

- **GIVEN** 报告消息 streaming=false
- **THEN** 在底部显示免责声明"本报告由 AI 系统基于公开数据自动生成，仅供参考研究，不构成投资建议。投资有风险，入市需谨慎。"

### Requirement: Agent Timeline Display

系统 SHALL 将助手消息内的思考、搜索、工具调用按 SSE 事件到达的真实时序纵向排列，存储为 `agentTimeline: TimelineItem[]` 数组，替代原有的分离字段（`thinkingContent` / `searchStatus` / `searchResults` / `searchQuery` / `toolCalls`）。每个 TimelineItem 渲染为对应类型的独立可折叠横幅，response（chatResponse）在 timeline 之后以 Markdown 渲染，不框起。

#### Scenario: TimelineItem 联合类型定义

- **GIVEN** 助手消息的数据结构
- **THEN** `UIMessage` SHALL 包含 `agentTimeline: TimelineItem[]` 字段
- **AND** `TimelineItem` 为联合类型，包含三种：
  - `{type:'thinking', content: string, title?: string}` 思考片段
  - `{type:'search', query: string, results?: Array<{title,url,content}>, status: 'searching'|'done'|'error'}` 搜索
  - `{type:'tool_call', name: string, args: string, result?: string, done: boolean}` 其他工具调用
- **AND** 废弃分离字段 `thinkingContent` / `thinkingTitle` / `searchStatus` / `searchResults` / `searchQuery` / `toolCalls`

#### Scenario: 按 SSE 事件时序追加 TimelineItem

- **GIVEN** 对话流（快速模式或深度模式澄清阶段）进行中
- **WHEN** 收到 `thinking_token` / `search_start` / `search_result` / `search_error` / `tool_call` / `tool_result` 事件
- **THEN** 按事件到达顺序向 `agentTimeline` 数组追加或更新 TimelineItem
- **AND** 渲染顺序与 `agentTimeline` 数组顺序一致，反映 agent 实际执行时序

#### Scenario: 思考片段断开逻辑

- **GIVEN** 对话流进行中，`agentTimeline` 数组已存在
- **WHEN** 收到 `thinking_token` 事件
- **THEN** 若 `agentTimeline` 末尾是 `thinking` 类型 item，则将该 token 累加到该 item 的 `content`
- **AND** 若 `agentTimeline` 末尾是 `search` 或 `tool_call` 类型 item，或 timeline 为空，则新建一个 `thinking` item 追加到数组末尾
- **AND** 同一段思考流式累加，遇到 `tool_call` / `search_start` 事件则断开成新思考片段

#### Scenario: 每个 TimelineItem 渲染为独立可折叠横幅

- **GIVEN** 助手消息包含 `agentTimeline` 数组
- **WHEN** 渲染助手消息
- **THEN** 遍历 `agentTimeline`，按 `item.type` 分发渲染：
  - `thinking` -> 渲染为独立的 ThinkingBanner（可折叠，标题由该段思考独立提取）
  - `search` -> 渲染为独立的 SearchBanner（可折叠，显示搜索状态/查询/结果）
  - `tool_call` -> 渲染为独立的 ToolCallBanner（可折叠，单条目）
- **AND** 每个横幅独立管理自己的折叠/展开状态
- **AND** 横幅之间无外层汇总折叠框，无汇总标题

#### Scenario: Response 在 timeline 之后不框起

- **GIVEN** 助手消息包含 `agentTimeline` 和 `chatResponse`
- **WHEN** 渲染助手消息
- **THEN** `agentTimeline` 的所有横幅渲染在前
- **AND** `chatResponse` 在所有横幅之后以 Markdown 渲染
- **AND** `chatResponse` 不被任何框体包裹，直接展示为正文

#### Scenario: 工具执行期间各横幅显示对应状态文案

- **GIVEN** 对话流进行中，`agentTimeline` 末尾是 `search` 类型 item 且 `status='searching'`
- **THEN** 对应 SearchBanner 显示"正在搜索网页"（脉冲动画），而非"思考中"
- **AND** 思考横幅（若有未完成的 thinking item）在该期间不显示"思考中"（agent 当前在执行搜索而非思考）

- **GIVEN** 对话流进行中，`agentTimeline` 末尾是 `tool_call` 类型 item 且 `done=false`
- **THEN** 对应 ToolCallBanner 显示"正在调用工具"（脉冲动画），而非"思考中"
- **AND** 思考横幅（若有未完成的 thinking item）在该期间不显示"思考中"（agent 当前在调用工具而非思考）

#### Scenario: 思考横幅仅在 agent 实际思考时显示"思考中"

- **GIVEN** 对话流进行中，`agentTimeline` 末尾是 `thinking` 类型 item 且正在流式接收 `thinking_token`
- **THEN** 该 ThinkingBanner 显示"思考中"（脉冲动画）
- **AND** 当 agent 转为执行搜索或工具调用（timeline 末尾变为 search/tool_call item）时，该 ThinkingBanner 不再显示"思考中"

#### Scenario: 搜索事件生成 search 类型 item

- **GIVEN** 对话流进行中
- **WHEN** 收到 `search_start` 事件
- **THEN** 新建 `{type:'search', query, status:'searching'}` item 追加到 `agentTimeline`
- **WHEN** 收到 `search_result` 事件
- **THEN** 更新该 search item 的 `status` 为 'done'，`results` 设为事件中的结果列表
- **WHEN** 收到 `search_error` 事件
- **THEN** 更新该 search item 的 `status` 为 'error'
- **AND** `web_search` / `batch_web_search` 不生成 `tool_call` 类型 item（沿用 isSearchTool 过滤逻辑）

#### Scenario: 其他工具调用生成 tool_call 类型 item

- **GIVEN** 对话流进行中
- **WHEN** 收到 `tool_call` 事件（非 run_deep_analysis，非搜索类工具）
- **THEN** 新建 `{type:'tool_call', name, args, done:false}` item 追加到 `agentTimeline`
- **WHEN** 收到 `tool_result` 事件
- **THEN** 更新对应 tool_call item 的 `result` 和 `done=true`
- **AND** 每次 tool call 生成独立的 tool_call item（不合并到同一横幅）

#### Scenario: 管线模式按 agent 阶段分组 timeline

- **GIVEN** 管线 UI 已渲染（深度分析模式）
- **WHEN** 渲染管线消息
- **THEN** 保留阶段进度条（6 阶段：PREP、Layer I、Layer II、Trader、Risk、Fund Manager）
- **AND** 每个 agent 阶段的 timeline items 归在该阶段下，阶段间用角色名标题分隔（非折叠框，纯文本标题）
- **AND** 阶段内按时间序列排列该 agent 的思考/搜索/工具调用横幅
- **AND** 当前活动阶段的横幅展开，已完成阶段的横幅折叠

#### Scenario: 历史会话恢复重建 agentTimeline

- **GIVEN** 加载已有会话，从 `chat_history` 恢复助手消息
- **WHEN** 构建助手消息的 `agentTimeline`
- **THEN** 从 `chat_history.thinking`（合并字符串）构建 thinking item
- **AND** 从 `chat_history.tool_calls`（数组）按序构建 tool_call items
- **AND** 按"思考在前、工具调用在后"的顺序排列（历史数据无完整时序信息，近似还原）
- **AND** 每个 thinking item 独立用 `extractThinkingTitle` 提取标题

### Requirement: Thinking Banner Display

系统 SHALL 在助手消息中将思考过程展示为可折叠横幅，每个思考片段（TimelineItem type='thinking'）独立一个横幅实例。流式时自动展开，完成后可手动折叠。标题展示规则沿用 thinking-stream-banner-display 变更：思考中显示"思考中"（脉冲动画）；完成折叠态有标题显示标题、无标题显示"思考已完成"；完成展开态固定"思考已完成"，框内标题加粗置顶（若有）。

#### Scenario: 流式思考自动展开

- **GIVEN** 助手消息 streaming=true，`agentTimeline` 末尾是 `thinking` 类型 item 且有 content
- **THEN** 该 ThinkingBanner 自动展开，显示脉冲动画指示"思考中"
- **AND** 内容区域自动滚动到底部
- **AND** 仅当 agent 实际在思考（timeline 末尾为 thinking item）时显示"思考中"；当 agent 转为执行搜索/工具调用时，该横幅不再显示"思考中"

#### Scenario: 思考完成折叠态有标题

- **GIVEN** 助手消息 streaming 从 true 变为 false，某 thinking item 的 `title` 非空
- **AND** 该 ThinkingBanner 处于折叠状态
- **THEN** 脉冲动画停止，显示勾选图标
- **AND** 横幅标题显示该 thinking item 的 `title`
- **AND** 不显示"· {N} 字"字数信息

#### Scenario: 思考完成折叠态无标题

- **GIVEN** 助手消息 streaming 从 true 变为 false，某 thinking item 的 `title` 为空
- **AND** 该 ThinkingBanner 处于折叠状态
- **THEN** 脉冲动画停止，显示勾选图标
- **AND** 横幅标题显示"思考已完成"

#### Scenario: 思考完成展开态

- **GIVEN** 助手消息 streaming 从 true 变为 false
- **AND** 某 ThinkingBanner 处于展开状态
- **THEN** 横幅标题固定显示"思考已完成"（不论是否有标题）
- **AND** 框内若该 thinking item 有 `title`，将标题以加粗样式置顶展示于思考正文之上
- **AND** 思考正文按 Markdown 渲染（支持 `##` 层级标题与 `**加粗**` 分段）

#### Scenario: 多段思考独立横幅

- **GIVEN** `agentTimeline` 包含多个 `thinking` 类型 item（被工具调用断开）
- **WHEN** 渲染助手消息
- **THEN** 每个 thinking item 渲染为独立的 ThinkingBanner 实例
- **AND** 每个 ThinkingBanner 独立提取标题、独立管理折叠状态
- **AND** 横幅按 `agentTimeline` 数组顺序纵向排列

#### Scenario: 手动折叠/展开

- **GIVEN** ThinkingBanner 已渲染
- **WHEN** 用户点击横幅标题
- **THEN** 切换该横幅的展开/折叠状态
- **AND** 折叠时内容区域高度为 0，展开时最大高度 240px
- **AND** 其他横幅的折叠状态不受影响

### Requirement: Tool Call Banner Display

系统 SHALL 在助手消息中将每次工具调用展示为独立的可折叠横幅（TimelineItem type='tool_call'），与思考过程分离。每个 tool_call item 渲染为一个独立的 ToolCallBanner 实例（单条目），按 `agentTimeline` 数组顺序排列。

#### Scenario: 每次工具调用独立横幅

- **GIVEN** `agentTimeline` 包含 `tool_call` 类型 item
- **WHEN** 渲染助手消息
- **THEN** 每个 tool_call item 渲染为独立的 ToolCallBanner 实例
- **AND** 显示工具图标、标签、参数摘要
- **AND** 已完成的工具调用显示结果摘要
- **AND** 未完成的工具调用显示"执行中..."旋转动画
- **AND** 横幅按 `agentTimeline` 数组顺序排列（穿插在 thinking item 之间）

#### Scenario: 工具调用横幅状态

- **GIVEN** 某 tool_call item `done=false`
- **THEN** 对应 ToolCallBanner 标题显示"正在调用工具"，脉冲动画
- **AND** 不显示"思考中"
- **GIVEN** 某 tool_call item `done=true`
- **THEN** 对应 ToolCallBanner 标题显示"已调用工具"，勾选图标

### Requirement: Message Auto-Scroll

系统 SHALL 在消息列表变化时自动滚动到页面底部。

#### Scenario: 新消息添加时滚动

- **GIVEN** 消息列表中有消息
- **WHEN** 消息列表发生变化（新消息添加或现有消息更新）
- **THEN** 页面平滑滚动到 body 底部

### Requirement: Chat Input Bar

系统 SHALL 在非空状态下显示固定的底部输入栏，包含模式切换、文本输入和发送功能。

#### Scenario: 底部输入栏渲染

- **GIVEN** appState 不为 'empty'
- **THEN** 底部固定显示输入栏，包含深度/快速模式切换按钮、textarea 输入框、发送按钮
- **AND** 底部显示"AI 生成仅供参考，不构成投资建议"

#### Scenario: 输入栏发送

- **GIVEN** 底部输入栏，当前模式为 'deep'
- **WHEN** 用户输入文本并按 Enter（非 Shift+Enter）
- **THEN** 调用 startAnalysis 发送文本
- **AND** 输入框清空

#### Scenario: 输入栏模式切换锁定

- **GIVEN** currentSessionId 不为 null（已有会话）
- **THEN** 模式切换按钮禁用，显示锁定图标
- **GIVEN** currentSessionId 为 null
- **THEN** 模式切换按钮可用

### Requirement: Message Type Rendering

系统 SHALL 根据消息类型（user/pipeline/report/chat/system/error）渲染不同样式的消息卡片。chat 类型消息遍历 `agentTimeline` 渲染横幅，response 在后。

#### Scenario: 用户消息右对齐

- **GIVEN** 消息类型为 'user'
- **THEN** 消息右对齐，使用品牌色背景

#### Scenario: 错误消息展示

- **GIVEN** 消息类型为 'error'
- **THEN** 消息左对齐，显示红色错误图标和错误文本

#### Scenario: 系统消息展示

- **GIVEN** 消息类型为 'system' 且内容不为 'typing'
- **THEN** 消息左对齐，显示机器人图标和成功勾选标记

#### Scenario: 助手消息按时间序列渲染

- **GIVEN** 消息类型为 'chat'
- **THEN** 消息左对齐，显示机器人图标
- **AND** 遍历 `agentTimeline` 数组，按 item.type 渲染对应横幅（ThinkingBanner / SearchBanner / ToolCallBanner）
- **AND** 横幅按 `agentTimeline` 数组顺序纵向排列
- **AND** `chatResponse` 在所有横幅之后以 Markdown 渲染，不框起
- **AND** 若 streaming=true 且无 chatResponse，在最后显示"思考中..."旋转动画

### Requirement: Chat History Restore With Tool Calls

系统 SHALL 在加载已有会话时恢复助手消息的 `agentTimeline`：**优先**使用 `chat_history` 条目中持久化的 `agentTimeline` 字段（结构化 TimelineItem 数组，含思考/搜索/工具调用的真实交错时序）原样重建；仅当该字段缺失（旧会话）时回退 `buildTimelineFromHistory` 的「思考在前、工具调用在后」近似恢复。

#### Scenario: 优先用持久化 agentTimeline 原样恢复

- **GIVEN** 加载的会话 `chat_history` 中某条助手消息包含 `agentTimeline` 字段
- **WHEN** 构建助手消息
- **THEN** 系统 SHALL 将 `agentTimeline` 反序列化后直接作为消息的 agentTimeline（保留 thinking/search/tool_call 的真实交错顺序）
- **AND** 搜索记录（type='search'）按其真实时序位置恢复为搜索横幅
- **AND** SHALL NOT 再走"思考在前、工具调用在后"的拍平近似

#### Scenario: 旧数据回退近似恢复

- **GIVEN** 加载的会话 `chat_history` 中某条助手消息仅有 `thinking`/`tool_calls`，无 `agentTimeline`
- **WHEN** 构建助手消息
- **THEN** 系统 SHALL 回退 buildTimelineFromHistory：从 thinking（合并字符串）构建 thinking item、从 tool_calls 按序构建 tool_call items
- **AND** 恢复过程不报错、消息正常显示

#### Scenario: 恢复搜索记录

- **GIVEN** 加载的会话 `chat_history` 中某条助手消息包含搜索相关记录（若后端存储）
- **WHEN** 构建助手消息
- **THEN** 构建 `{type:'search', query, results, status:'done'}` item 插入 `agentTimeline` 对应位置
- **AND** 若后端未存储搜索时序，按"思考 -> 搜索 -> 工具调用"的近似顺序排列

### Requirement: 管线时序恢复

系统 SHALL 在加载已有会话时，从会话详情的 `pipeline_timelines`（JSON：`{node: [TimelineItem]}`）恢复深度分析管线消息的 `nodeTimelines`，使各节点的思考/工具调用记录切换会话后完整可见。

#### Scenario: 切回会话恢复管线节点时序

- **GIVEN** 某深度分析会话的 pipeline_timelines 已持久化（含各节点思考/工具时序）
- **WHEN** 用户切换到该会话（运行中或已完成）
- **THEN** 前端 SHALL 反序列化 pipeline_timelines 为管线消息的 nodeTimelines
- **AND** 各节点的思考内容、网络搜索、工具调用记录按原时序展示
- **AND** 非法/缺失的 pipeline_timelines 回退为空（时间轴树仍由 pipeline_snapshot 恢复）

### Requirement: 跨会话恢复管线 UI

系统 SHALL 在用户切换会话时恢复目标会话的管线 UI 状态：运行中的管线恢复实时分层时间轴，已完成的管线恢复报告与静态分层时间轴。切换离开运行中的深度管线会话时 SHALL NOT 中断该管线（由后端后台续跑）。

#### Scenario: 切回运行中的管线会话恢复实时时间轴

- **GIVEN** 某深度分析会话的管线正在后台运行（status=running 且有 pipeline_snapshot）
- **WHEN** 用户切换到该会话
- **THEN** 前端 SHALL 从 pipeline_snapshot 重建分层时间轴（各节点 status/耗时）
- **AND** 当前运行节点 SHALL 正确高亮，其已运行时长基于 server_start_ts 实时递增
- **AND** 前端 SHALL 轮询会话快照以更新节点完成进度，直至 status 变 completed

#### Scenario: 切回已完成的管线会话恢复报告与静态时间轴

- **GIVEN** 某会话的管线已完成（status=completed）
- **WHEN** 用户切换到该会话
- **THEN** 前端 SHALL 显示报告消息（report）与已完成的静态分层时间轴
- **AND** 静态时间轴各节点 SHALL 显示真实耗时与摘要（供回看）
- **AND** 前端 SHALL NOT 继续轮询该会话快照

#### Scenario: 切换离开运行中的深度管线不中断

- **GIVEN** 某深度分析会话的管线正在运行
- **WHEN** 用户切换到其他会话
- **THEN** 前端 abortStreaming 仅断开 SSE 订阅，SHALL NOT 中断后端管线执行
- **AND** 管线在后台继续，切回时可恢复

#### Scenario: 切回失败的管线会话显示失败状态

- **GIVEN** 某会话的管线已失败（status=failed，含后端重启悬挂的 running 被标记）
- **WHEN** 用户切换到该会话
- **THEN** 前端 SHALL 显示分析失败状态而非误认为仍在运行

> **MVP 决策**：MVP 阶段 failed 分支不专门处理 UI（回退到现有 report/chat_history 恢复逻辑），失败态 UI 为后续改进。

#### Scenario: 快速模式会话切换行为不变

- **GIVEN** 用户从快速模式（chat）会话切换离开
- **WHEN** 该会话无深度管线
- **THEN** 前端维持现有 abortStreaming 行为（快速模式流无恢复价值）

### Requirement: Pipeline ETA Display

系统 SHALL 在管线进度区域显示动态预估时间，包括已用时长与预估剩余时间，替代静态文案。预估 SHALL 基于历史运行数据并随进度收敛。

#### Scenario: 显示已用时长与预估剩余

- **GIVEN** 深度分析管线运行中
- **THEN** 进度区域显示"已用时 M:SS · 预计剩余 ~M:SS"格式文本，每秒刷新
- **AND** 不显示硬编码静态预估文案（如"~90s"）

#### Scenario: 基于历史中位数的初始预估

- **GIVEN** localStorage 存在最近 N 次（最多 10 次）完整管线运行耗时记录
- **WHEN** 新一次管线启动
- **THEN** 初始预估总时长取历史记录的中位数
- **AND** 若无历史记录，使用默认值 240 秒

#### Scenario: 预估随进度线性收敛

- **GIVEN** 管线运行中，已完成节点数占比 p = completed/total
- **WHEN** 实际进度比例 p 超过 已用时长/预估总时长 所隐含的进度
- **THEN** 用 已用时长/p 重新估算总时长
- **AND** 预估剩余时间 = max(0, 重估总时长 - 已用时长)

#### Scenario: 管线完成后记录耗时

- **GIVEN** 管线运行中
- **WHEN** 收到 report_ready 事件（管线完成）
- **THEN** 将本次总耗时写入 localStorage 历史记录（最多保留 10 条，超出时淘汰最旧记录）

#### Scenario: localStorage 不可用回退

- **GIVEN** 浏览器环境 localStorage 不可用（隐私模式等）
- **THEN** 预估使用默认值 240 秒，ETA 显示功能不阻塞、不报错

### Requirement: 内容区居中收窄

主内容区 SHALL 以约 `max-w-3xl` 宽度水平居中，消息流、输入区与报告摘要均在居中栏内；视口收窄时 SHALL 自适应为全宽并保留合理内边距。

#### Scenario: 桌面端居中

- **GIVEN** 桌面端视口（≥1280px）
- **WHEN** 打开任意会话
- **THEN** 消息流与输入框 SHALL 居中显示，两侧留白对称

### Requirement: 空态首页

会话无消息时 SHALL 显示空态：居中大号问候语、说明可输入股票名称/代码/自然语言指令的副标题，以及 2–4 张建议卡片；卡片点击 SHALL 将示例文本填入输入框而不直接发送。

#### Scenario: 卡片填入不发送

- **GIVEN** 新会话显示空态
- **WHEN** 用户点击建议卡片
- **THEN** 示例文本 SHALL 填入输入框
- **AND** 不发出分析请求

#### Scenario: 历史会话不显示空态

- **GIVEN** 含消息的会话
- **WHEN** 切换到该会话
- **THEN** SHALL NOT 显示空态，直接显示消息流

### Requirement: 空态退场

首条消息发送后空态 SHALL 以约 200ms 淡出切换为消息流，无布局跳动。

#### Scenario: 发送后空态淡出

- **WHEN** 用户在空态会话发送首条消息
- **THEN** 空态 SHALL 淡出，消息流就位，内容区无跳动

### Requirement: 暗色模式

系统 SHALL 提供浅色/深色/跟随系统三种主题选择，入口位于侧边栏底部；选择 SHALL 持久化；暗色下所有页面（含 ECharts 图表与报告）SHALL 保持可读。

#### Scenario: 主题持久化与跟随系统

- **GIVEN** 用户选择深色
- **WHEN** 刷新页面
- **THEN** 深色主题 SHALL 保持；选择「跟随系统」时 SHALL 随系统主题变化

#### Scenario: 暗色下图表可读

- **WHEN** 暗色模式下查看含 ECharts 的报告
- **THEN** 图表文字、坐标轴、网格线 SHALL 清晰可辨

### Requirement: 命令面板

Cmd/Ctrl+K SHALL 打开命令面板：可按标题搜索会话并跳转；SHALL 提供快捷动作（新建会话、打开下载管理、切换主题）；面板底部 SHALL 列出可用快捷键。

#### Scenario: 搜索并跳转会话

- **GIVEN** 存在多个历史会话
- **WHEN** 用户在命令面板输入关键词并选择结果
- **THEN** 应用 SHALL 切换到对应会话

### Requirement: 快捷键与输入态抑制

Ctrl/Cmd+Shift+N SHALL 新建会话；`/` 在输入框未聚焦时 SHALL 聚焦输入框；输入框聚焦时单键快捷键 SHALL NOT 触发。

#### Scenario: 输入中不误触

- **GIVEN** 输入框处于聚焦状态
- **WHEN** 用户输入 `/`
- **THEN** 字符 SHALL 正常输入，不触发快捷键

### Requirement: 管线步骤时间线

管线进度 SHALL 以步骤时间线渲染，节点含四态：等待、运行中（高亮 + 呼吸动画）、完成（勾选标记）、失败（红色标记）；时间线 SHALL 随 SSE 管线节点事件实时推进。

#### Scenario: 事件驱动状态推进

- **GIVEN** 深度分析管线运行中
- **WHEN** 某节点完成事件到达
- **THEN** 该节点 SHALL 变为完成态，下一节点变为运行中
- **AND** 无需刷新页面

### Requirement: 节点已用时

当前运行节点 SHALL 显示已用时，计时源 SHALL 为快照 `pipeline_start_ts`；快照缺该字段时回退本地时间。

#### Scenario: 刷新后已用时不归零

- **GIVEN** 管线已运行若干分钟后刷新页面
- **WHEN** 时间线重建
- **THEN** 已用时 SHALL 以快照时间戳计算，不归零

### Requirement: 节点展开与完成折叠

运行中/完成节点 SHALL 可展开查看阶段摘要；管线完成后时间线 SHALL 整体折叠为单行摘要条（阶段数 + 总用时），点击可再展开。

#### Scenario: 完成后折叠

- **WHEN** 管线完成并发出 report_ready
- **THEN** 时间线 SHALL 折叠为摘要条
- **AND** 点击摘要条可展开查看全部节点

### Requirement: 行内引用上标

报告正文中的引用标记 SHALL 渲染为行内上标编号；编号锚定后端下发的稳定 id；旧报告无引用数据时 SHALL 不渲染任何标记。

#### Scenario: 上标渲染

- **GIVEN** 报告含引用数组且正文有对应标记
- **WHEN** 渲染报告
- **THEN** 引用处 SHALL 显示上标编号，编号与后端 id 一一对应

### Requirement: 引用预览卡

hover 行内上标 SHALL 显示预览卡：claim 摘要、来源、校验状态标识（verified 绿 / failed 红 / unchecked 灰）；预览卡 SHALL 懒渲染。

#### Scenario: hover 显示状态

- **WHEN** 用户 hover 某上标
- **THEN** 预览卡 SHALL 显示该引用的 claim、来源与对应颜色的校验状态

### Requirement: 引用与校验列表

报告末尾 SHALL 提供「引用与校验」列表，按编号列出来源与校验状态；failed 项 SHALL 在视觉上可一眼区分。

#### Scenario: 列表状态可辨

- **GIVEN** 报告含 verified 与 failed 混合引用
- **WHEN** 查看列表
- **THEN** failed 项 SHALL 以红色标识，与 verified 项明显区分

### Requirement: 会话级文件导出入口

系统 SHALL 在会话存在可导出报告（已完成 streaming=false 的报告消息且其 filePaths 含至少一个已生成文件）时，在对话区内渲染「报告名横幅」与「全部文件横幅」两个入口：报告名横幅位于产出该报告的对话轮次底部，位次先于「全部文件」横幅；「全部文件」横幅位于对话尾部（最后一条消息之后）。二者与全局顶部栏「查看全部文件」按钮均可打开右侧文件列表抽屉。抽屉 SHALL 自上而下仅列出该会话报告已生成的可下载文件（filePaths 各条目），不展示 pdf/docx/markdown 固定格式行，也不提供缺失格式的现场生成入口；预览与关闭行为沿用现有实现。

#### Scenario: 报告名横幅在报告产出轮次底部显示

- **GIVEN** 对话中存在一条已完成（streaming=false）且 filePaths 含至少一个已生成文件的报告消息
- **WHEN** 渲染对话消息列表
- **THEN** 在该报告产出轮次的底部（紧随该报告消息及其后的「分析完成」系统消息）渲染「报告名横幅」
- **AND** 横幅标题为该报告的「股票名称（股票代码）」（如「贵州茅台（600519）」）
- **AND** 该横幅位次先于「全部文件」横幅

#### Scenario: 股票名称缺失时报告名横幅回退显示

- **GIVEN** 报告消息 stockName 缺失或等于 stockCode（名称未解析到）
- **WHEN** 渲染报告名横幅
- **THEN** 横幅标题仅显示股票代码，不重复组合（不显示「600519（600519）」）

#### Scenario: 历史会话恢复后报告名横幅用会话元数据兜底

- **GIVEN** 从 chat_history / 会话详情恢复的报告消息无 stockCode 字段（旧会话）
- **WHEN** 渲染报告名横幅
- **THEN** 标题中的股票代码从会话元数据（stock_code）兜底获取
- **AND** 会话元数据亦缺失时，仅显示报告消息的 stockName
- **AND** 恢复的报告消息 SHALL 携带会话持久化的 filePaths（file_paths 非空时报告名横幅与全部文件横幅照常显示）

#### Scenario: 报告名横幅仅在报告产出轮次出现

- **GIVEN** 对话中某轮次未产出报告（澄清对话、快速对话、或分析进行中 streaming=true）
- **WHEN** 渲染对话消息列表
- **THEN** 该轮次底部不渲染报告名横幅
- **AND** 快速对话会话与空状态首页不渲染任何报告名横幅

#### Scenario: 全部文件横幅位于对话尾部

- **GIVEN** 当前会话存在可导出报告（已完成且 filePaths 含已生成文件）
- **WHEN** 渲染对话消息列表
- **THEN** 在最后一条消息之后渲染「全部文件」横幅
- **AND** 该横幅位次于所有报告名横幅之后

#### Scenario: 无可导出文件时不显示全部文件横幅

- **GIVEN** 当前会话无可导出报告（报告无已生成文件、空状态首页、快速对话会话、或深度分析进行中 streaming=true）
- **WHEN** 渲染对话消息列表
- **THEN** 对话尾部不显示「全部文件」横幅

#### Scenario: 点击报告名横幅打开该报告的文件列表

- **GIVEN** 报告名横幅已显示
- **WHEN** 用户点击该横幅
- **THEN** 右侧文件列表抽屉滑出打开
- **AND** 抽屉列出该报告消息 filePaths 对应的可下载文件

#### Scenario: 点击全部文件横幅打开右侧文件列表

- **GIVEN** 「全部文件」横幅已显示
- **WHEN** 用户点击该横幅
- **THEN** 右侧文件列表抽屉滑出打开

#### Scenario: 全局顶部栏按钮显示

- **GIVEN** 当前会话存在可导出报告（已完成且 filePaths 含已生成文件）
- **WHEN** 渲染全局顶部栏
- **THEN** 顶部栏右侧（设置按钮旁）显示「查看全部文件」按钮

#### Scenario: 无可导出文件时顶部栏按钮隐藏

- **GIVEN** 当前会话无可导出文件，或处于空状态首页（无 currentSessionId）
- **WHEN** 渲染全局顶部栏
- **THEN** 顶部栏不显示「查看全部文件」按钮

#### Scenario: 点击顶部栏按钮打开右侧文件列表

- **GIVEN** 全局顶部栏「查看全部文件」按钮已显示
- **WHEN** 用户点击该按钮
- **THEN** 右侧文件列表抽屉滑出打开

#### Scenario: 抽屉仅列出已生成的可下载文件

- **GIVEN** 抽屉已打开，报告消息 filePaths 含若干条目（如 docx/pdf 已生成，md 缺失）
- **WHEN** 渲染文件列表
- **THEN** 自上而下仅排列已存在的可下载文件条目（每项显示文件名与下载动作，图标按文件扩展名区分）
- **AND** 可下载文件名形如「{股票名称}_{股票代码}_{时间戳}_report.{扩展名}」（股票名称缺失时回退仅代码）
- **AND** 不显示 pdf/docx/markdown 固定三类格式行
- **AND** 不显示缺失格式的下载按钮（无现场生成入口）

#### Scenario: 无可下载文件时展示空态

- **GIVEN** 抽屉已打开，报告消息无 filePaths（或所有条目为空）
- **THEN** 文件列表显示空态提示（如「暂无已生成文件」）
- **AND** 预览功能仍可用

#### Scenario: 抽屉关闭与下载行为

- **GIVEN** 文件列表抽屉已由任一入口（报告名横幅 / 全部文件横幅 / 顶部栏按钮）打开
- **WHEN** 用户点击关闭按钮、点击遮罩、或按 Esc
- **THEN** 抽屉关闭
- **WHEN** 用户点击某文件的下载动作
- **THEN** 触发该文件下载，URL 为 /api/files/{basename}

#### Scenario: 会话切换后入口按新会话刷新

- **GIVEN** 用户从有可导出文件的会话切换到无可导出文件的会话（或反向）
- **WHEN** 渲染新会话的消息列表
- **THEN** 报告名横幅、全部文件横幅与顶部栏按钮按新会话的报告状态刷新显示或隐藏
- **AND** 若文件抽屉处于打开状态，随会话切换自动关闭（沿用现有行为）

### Requirement: 报告右侧面板

报告就绪后 SHALL 可从右侧滑出面板展示完整报告（Markdown + ECharts + 引用标记）；面板宽度 SHALL 可拖拽调节；开合状态在同一会话内 SHALL 保持。

#### Scenario: 面板打开与宽度调节

- **GIVEN** 报告已生成
- **WHEN** 用户点击「打开报告」
- **THEN** 面板 SHALL 从右侧滑出（约 300ms）
- **AND** 拖拽边缘可调节宽度

### Requirement: 消息流摘要卡片

消息流中报告 SHALL 收敛为摘要卡片：结论要点 + 「打开报告」按钮；点击 SHALL 打开右侧面板定位到完整报告。

#### Scenario: 摘要卡跳转面板

- **WHEN** 用户点击摘要卡片「打开报告」
- **THEN** 右侧面板 SHALL 打开并显示该会话完整报告

### Requirement: 面板操作栏

面板顶部 SHALL 固定操作栏：导出（docx/pptx/pdf/md）与关闭；导出行为与既有契约一致。

#### Scenario: 面板内导出

- **WHEN** 用户在面板操作栏选择导出 Word
- **THEN** 系统 SHALL 触发既有导出流程，行为与消息流内导出一致

### Requirement: 移动端回退

视口小于 768px 时 SHALL NOT 显示右侧面板，报告在消息流内全宽展示。

#### Scenario: 移动端无面板

- **GIVEN** 移动端视口
- **WHEN** 报告生成
- **THEN** 报告 SHALL 在消息流内全宽展示，不出现面板

### Requirement: 设计令牌为样式唯一来源

所有颜色、圆角、阴影 SHALL 引用 shadcn 主题 CSS 变量（`--background/--foreground/--muted/--primary/--border/--radius` 等）；组件内 SHALL NOT 出现硬编码色值（ECharts option 除外，其 SHALL 从变量取值后注入）。

#### Scenario: 重构后组件无硬编码色值

- **GIVEN** 任意完成重构的组件
- **WHEN** 检查其样式定义
- **THEN** 颜色与圆角 SHALL 来自主题变量或 Tailwind 语义类（bg-background、text-muted-foreground 等）
- **AND** 不存在十六进制色值硬编码

### Requirement: 通用控件统一为 shadcn 原语

按钮、输入框、多行输入、对话框、toast、tooltip、下拉菜单 SHALL 使用 `components/ui/` 下的 shadcn 原语；替换 SHALL 保持既有交互行为（提交、校验、快捷键、禁用态、加载态）不变。

#### Scenario: 控件替换后行为一致

- **GIVEN** 某手写控件已替换为 shadcn 原语
- **WHEN** 用户执行点击/输入/禁用/hover 交互
- **THEN** 行为与原实现一致，包括加载态与禁用态表现

#### Scenario: 现有测试无修改通过

- **GIVEN** 重构完成
- **WHEN** 运行前端全量测试
- **THEN** 所有既有测试 SHALL 在不修改断言语义的前提下通过

### Requirement: 全局视觉打底

全局 SHALL 应用主题变量定义的背景色、前景色与统一字体栈；header、输入区、内容区底色 SHALL 取自 `--background/--muted` 语义层级。

#### Scenario: 全局底色与字体生效

- **WHEN** 打开任意页面
- **THEN** 背景与文字颜色来自主题变量
- **AND** 字体栈全局统一，无页面级字体覆盖

### Requirement: 图表配色对齐主题

报告区 ECharts SHALL 保留且交互不变；其 option 中的主色、坐标轴色、网格线色 SHALL 从主题 CSS 变量取值注入，与页面视觉一致。

#### Scenario: 图表色随主题变量变化

- **GIVEN** 主题变量被调整（如切换配色方案）
- **WHEN** 渲染报告图表
- **THEN** 图表主色与新主题一致，无需修改图表代码

### Requirement: 明日方舟 UI 主题配色

系统 SHALL 以明日方舟 UI 色卡（#2F3132 深灰面板、#FCFBFB 纯白、#228EBF 强调蓝、#CBB54C 点缀金）作为全部主题设计令牌的主取值来源；罗德岛阵营色卡（#0A0B0D 罗德岛黑、#2A5BA8 罗德岛蓝、#3FC6E0 亮青蓝、#888A8C 工业灰、#F5F5F5 医疗白）SHALL 仅作点缀取值。取值 SHALL 集中于 `frontend/src/index.css` 的明暗两套 CSS 变量；对比度不足处 SHALL 使用同色相衍生色阶（如深色链接提亮、浅色链接加深），衍生规则 SHALL 在令牌注释中说明。历史 TRAE 品牌紫（#4B3FE3/#6C5FF0/#8F84F5）SHALL NOT 出现于任何前端源文件。

#### Scenario: 浅色主题映射

- **GIVEN** 浅色主题生效
- **WHEN** 渲染任意页面
- **THEN** 页面画布 SHALL 为 #FCFBFB 系纯白、正文 SHALL 为罗德岛黑 #0A0B0D
- **AND** 按钮/链接/焦点环 SHALL 为强调蓝 #228EBF，警告与中性标签 SHALL 为点缀金 #CBB54C

#### Scenario: 深色主题映射

- **GIVEN** 深色主题生效
- **WHEN** 渲染任意页面
- **THEN** 页面底色 SHALL 为深灰面板 #2F3132（卡片/浮层为其亮阶衍生），文字 SHALL 为纯白 #FCFBFB
- **AND** 按钮/气泡/标签主色 SHALL 为强调蓝 #228EBF，分析节点运行态 SHALL 以亮青蓝 #3FC6E0 点缀（边框 + 辉光）

#### Scenario: 调色板契约被测试冻结

- **GIVEN** themePalette 冻结测试（frontend/src/test/themePalette.test.ts）
- **WHEN** 任何人对 index.css 引入历史品牌紫或偏离色卡的主令牌值
- **THEN** 前端测试 SHALL 变红阻止合入

#### Scenario: 图表配色对齐色卡

- **GIVEN** 图表 option 从主题 CSS 变量取色（既有「图表配色对齐主题」机制）
- **WHEN** 渲染报告图表
- **THEN** 冷色系列 SHALL 取强调蓝（--chart-sky）、罗德岛蓝（--chart-violet）、亮青蓝（--chart-teal），琥珀系列 SHALL 取点缀金（--chart-amber）
- **AND** 涨跌语义暖色（coral/mint/rose）SHALL 保持原值不变
- **AND** 深色主题下冷色系 SHALL 使用提亮衍生值保证曲线可读

#### Scenario: 组件无散落硬编码色

- **GIVEN** 任意前端组件
- **WHEN** 检查其样式来源
- **THEN** 语义状态色 SHALL 来自 CSS 变量或语义令牌类（如 text-[color:var(--status-error-default)]），SHALL NOT 使用 Tailwind 调色板类（text-red-500 等）或 ECharts 外的十六进制字面量

### Requirement: 视觉回归基线

重构前 SHALL 对主要页面（会话页、报告渲染态、空态）截图存档至 `tests/validation/`；重构后 SHALL 逐页对比，确认无布局破损。

#### Scenario: 重构后无布局破损

- **GIVEN** 重构完成且基线截图已存档
- **WHEN** 逐页对比基线与新截图
- **THEN** 仅视觉风格变化，无元素错位、遮挡或溢出

### Requirement: 侧边栏可折叠

侧边栏 SHALL 支持展开态（约 260px）与收起态（约 52px 图标栏）切换；触发方式 SHALL 包括折叠按钮与 Ctrl/Cmd + B 快捷键；宽度变化 SHALL 带约 200ms 过渡；折叠状态 SHALL 持久化，刷新与重开后保持。

#### Scenario: 按钮与快捷键均可切换

- **WHEN** 用户点击折叠按钮或按下 Ctrl/Cmd + B
- **THEN** 侧边栏 SHALL 在展开/收起间切换，过渡平滑无抖动

#### Scenario: 折叠状态持久化

- **GIVEN** 用户已收起侧边栏
- **WHEN** 刷新页面
- **THEN** 侧边栏 SHALL 保持收起态

### Requirement: 收起态图标栏

收起态下 SHALL 仅保留图标：Logo 缩小、「新建会话」显示为图标按钮；所有图标 hover SHALL 显示 tooltip 文字；新建会话功能在收起态 SHALL 可用。

#### Scenario: 收起态新建会话可用

- **GIVEN** 侧边栏处于收起态
- **WHEN** 用户点击新建会话图标
- **THEN** 系统 SHALL 创建新会话，行为与展开态一致

### Requirement: 会话项操作形态

会话项 hover 时 SHALL 在右侧出现「···」菜单，含重命名与删除；重命名 SHALL 为原地输入框；删除 SHALL 二次确认；操作的后端语义与现状一致。

#### Scenario: hover 菜单操作

- **GIVEN** 会话列表存在会话
- **WHEN** 用户 hover 某会话并打开「···」菜单选择重命名
- **THEN** 标题 SHALL 变为原地输入框，确认后生效
- **AND** 选择删除时 SHALL 先弹确认，确认后从列表移除

### Requirement: 移动端抽屉

视口宽度小于 768px 时侧边栏 SHALL 变为抽屉：默认隐藏，从左侧滑入；点击遮罩或选中会话后 SHALL 自动关闭。

#### Scenario: 移动端选中后收起

- **GIVEN** 移动端视口且抽屉已打开
- **WHEN** 用户点击某会话
- **THEN** 抽屉 SHALL 自动关闭并切换到该会话

### Requirement: Restore Current Session On Refresh

系统 SHALL 在浏览器刷新/重新加载后自动恢复用户此前正在查看的会话，无需用户手动点击侧边栏。前端 SHALL 将 `currentSessionId` 持久化到 localStorage（key：`fa_current_session_id`），并在会话选中、创建、删除、新建分析时同步维护该值。应用初始化时，若持久化的 `currentSessionId` 存在且对应会话仍存在于后端，前端 SHALL 自动执行与「手动点击会话」等价的恢复逻辑（加载会话详情、重建消息列表、若会话 running 则重连事件流），直接恢复到该会话视图而非停留在空状态首页。

#### Scenario: 刷新后自动恢复进行中的会话

- **GIVEN** 用户在某会话（深度分析或快速对话）进行中刷新了页面，localStorage 已持久化该会话的 `currentSessionId`
- **WHEN** 应用初始化并完成会话列表加载
- **THEN** 前端 SHALL 自动选中该会话，向 GET /api/sessions/{sessionId} 请求详情
- **AND** 从 chat_history（含 agentTimeline）重建消息列表（工具调用、思考、assistant 文本）
- **AND** 若会话 status 为 running，经 GET /api/sessions/{id}/stream 重连事件流，输出从断点继续增长
- **AND** 全程无需用户手动点击侧边栏

#### Scenario: 刷新后自动恢复已完成的会话

- **GIVEN** 用户在某已完成（completed）会话视图刷新了页面
- **WHEN** 应用初始化
- **THEN** 前端 SHALL 自动恢复该会话的报告/对话视图
- **AND** 按 session_type 锁定模式，appState 切换为对应视图（report / 对话）

#### Scenario: 持久化会话已删除时回退空态

- **GIVEN** localStorage 存在持久化的 `currentSessionId`，但该会话在后端已被删除
- **WHEN** 应用初始化并尝试恢复
- **THEN** 前端 SHALL 清除该 localStorage 项
- **AND** 回退到空状态首页，不报错、不显示无效会话

#### Scenario: 无持久化会话时保持空态首页

- **GIVEN** localStorage 不存在 `fa_current_session_id`（首次访问或已清空）
- **WHEN** 应用初始化
- **THEN** 前端 SHALL 显示空状态首页（维持现有 Empty State Landing Page 行为）

#### Scenario: currentSessionId 生命周期同步

- **GIVEN** 应用运行中
- **WHEN** 用户选中会话、创建新会话（session_created）、删除当前会话、或点击「新建分析」
- **THEN** 前端 SHALL 同步更新或清除 localStorage 的 `fa_current_session_id`
- **AND** 删除当前会话、新建分析时清除该项（对应 currentSessionId 置 null）

### Requirement: 会话运行中（含工具执行中）禁止发送

会话处于运行中（含澄清阶段工具执行中）时，前端 SHALL 拦截发送并提示；追问路径（后端不重发 `session_created`）下拦截同样生效。拦截主层 SHALL 迁移至 assistant-ui runtime 状态判定（运行中发送入口切换为停止按钮、提交通道关闭），App 层守卫（`isSessionRunning` / quick run 单飞守卫）保留为兜底。
(Previously: 拦截主层为 App 层 `isSessionRunning` 守卫 + streamStore 单读取器登记。)

#### Scenario: 澄清工具执行中发送被拦截

- **GIVEN** 某会话澄清阶段 agent 正在执行工具（SSE 流存活）
- **WHEN** 用户在该会话输入框发送消息
- **THEN** 前端 SHALL 判定 `isSessionRunning(sessionId)` 为 true
- **AND** 运行中发送入口切换为停止按钮（提交通道关闭），App 层守卫命中时显示「该会话正在生成中」toast
- **AND** 不发出新的分析/对话请求

#### Scenario: 追问路径登记 abort 使拦截生效

- **GIVEN** 一次追问（已有 sessionId，后端不重发 `session_created`）
- **WHEN** 前端发起 SSE 请求并创建 `AbortController`
- **THEN** 前端 SHALL 在 fetch 发出前将其登记为该会话的活跃读取器（单读取器保证）
- **AND** 运行状态判定据此生效

#### Scenario: 运行中拦截（验收基准不变）

- **GIVEN** 某会话正在生成（含工具执行中）
- **WHEN** 用户发送消息
- **THEN** 前端 SHALL 拦截发送并显示顶部 toast 提示

### Requirement: 「会话生成中」警告为顶部 toast

「该会话正在生成中」警告 SHALL 以 fixed 顶部 toast 呈现（浮于 header 与输入框之上、水平居中），3 秒自动消失；不再锚定在底部停止按钮容器内。

#### Scenario: 警告置顶显示

- GIVEN 触发「该会话正在生成中」拦截
- WHEN 警告渲染
- THEN 其 SHALL 为 `position: fixed`、位于视口顶部、z-index 高于 header（z-50）与输入框（z-40）
- AND 3 秒后自动消失

### Requirement: 澄清回复实时流式与落库格式一致

澄清阶段回复的实时流式渲染 SHALL 保留落库文本的换行/列表结构，不丢失单 `\n`；刷新后重建显示与实时流式显示一致。

#### Scenario: 流式渲染保留列表换行

- GIVEN 澄清回复为多行 markdown 列表（每项独立一行，单 `\n` 分隔）
- WHEN 前端实时流式渲染该回复
- THEN 渲染结果 SHALL 与落库文本一致（列表项分行，不粘连）
- AND 刷新后重建显示与流式显示一致

### Requirement: 消息流由 assistant-ui 渲染

聊天消息区 SHALL 使用 assistant-ui Thread 组件渲染：token 级流式输出、生成中自动跟随滚动（用户上翻时暂停跟随）、中断后保留已生成内容。

#### Scenario: 流式渲染与滚动跟随

- **GIVEN** 一次分析请求正在流式返回
- **WHEN** token 持续到达
- **THEN** 正文 SHALL 渐进渲染且视图自动跟随最新内容
- **AND** 用户主动上翻后 SHALL 暂停跟随，回到底部后恢复

#### Scenario: 中断保留已生成内容

- **WHEN** 用户在生成中点击停止
- **THEN** 已生成内容 SHALL 完整保留，不消失、不回退

### Requirement: SSE 事件适配层

前端 SHALL 提供单一 adapter 翻译层，将后端全部 SSE 事件类型映射为 assistant-ui 消息部件；每类事件 SHALL 有对应单测；未知事件类型 SHALL 安全忽略且不崩溃。

#### Scenario: 事件类型全覆盖

- **GIVEN** 后端 SSE 事件类型枚举（chat_token、thinking、工具调用、管线节点、report_ready 等）
- **WHEN** 检查 adapter 实现与单测
- **THEN** 每个事件类型 SHALL 存在映射逻辑与对应测试

#### Scenario: 未知事件安全忽略

- **WHEN** adapter 收到未定义的事件类型
- **THEN** 前端 SHALL 忽略该事件并继续处理后续事件，不抛错、不中断流

### Requirement: 思考过程折叠卡片

思考内容 SHALL 渲染为可折叠卡片：流式中显示进行态（如"思考中…"），完成后默认收起、可展开查看完整推理内容。

#### Scenario: 思考卡流式与收起

- **GIVEN** 模型输出含 thinking 流
- **WHEN** 流式进行中
- **THEN** 显示思考卡片进行态
- **AND** 流结束后卡片默认收起，点击展开完整内容

### Requirement: 工具调用卡片

工具调用 SHALL 渲染为卡片：调用中显示 loading 态，完成后可展开查看参数与结果；澄清阶段的工具执行中 SHALL 沿用运行中拦截语义（禁止发送）。

#### Scenario: 工具调用状态展示

- **WHEN** agent 调用工具（如股票识别）
- **THEN** 卡片 SHALL 先显示 loading 态，完成后显示结果摘要
- **AND** 调用期间发送消息 SHALL 被拦截并提示

### Requirement: 输入区 Composer

输入区 SHALL 使用 assistant-ui Composer：Enter 发送、Shift+Enter 换行、输入为空时发送键禁用、会话运行中发送键变为停止按钮。

#### Scenario: 发送与停止切换

- **WHEN** 会话进入运行中
- **THEN** 发送按钮 SHALL 切换为停止按钮
- **AND** 点击停止 SHALL 中断当前流

### Requirement: 消息操作与独有部件

assistant 消息 hover SHALL 显示复制与重新生成操作；管线进度时间线、ECharts 图表、报告导出入口 SHALL 以自定义消息部件挂载，能力与现状一致。

#### Scenario: 独有部件能力不丢

- **WHEN** 深度分析完成
- **THEN** 消息区 SHALL 正常渲染管线时间线、报告图表与导出按钮
- **AND** 导出交互与 add-download-center 之前的既有行为一致

### Requirement: 下载管理入口与路由

侧边栏底部区域 SHALL 提供「下载管理」菜单项（下载图标 + 文字），点击跳转 `/downloads` 路由，主区域渲染下载管理页；直接访问/刷新 `/downloads` SHALL 正常渲染，不丢路由状态。

#### Scenario: 从侧边栏进入下载管理

- **GIVEN** 用户位于任意会话页面
- **WHEN** 点击侧边栏「下载管理」
- **THEN** 路由切换为 `/downloads`，主区域渲染文件列表
- **AND** 侧边栏折叠/展开状态保持不变

#### Scenario: 刷新页面路由保持

- **WHEN** 用户在 `/downloads` 页面刷新浏览器
- **THEN** 页面仍渲染下载管理页而非回退到会话页

### Requirement: 文件列表展示

下载管理页 SHALL 以行列表展示文件：类型图标（docx/pptx/pdf/md 可区分配色）、文件名（超出省略，hover 显示完整名）、格式化大小（KB/MB）、创建时间（当日显示 HH:mm，更早显示 YYYY-MM-DD）、下载按钮与删除按钮（删除按钮 hover 才出现）。列表超屏时内部滚动，标题栏固定。

#### Scenario: 元信息正确渲染

- **GIVEN** 接口返回一个 1.5MB、创建于昨日的 docx 文件
- **WHEN** 页面渲染列表
- **THEN** 该行显示 docx 图标、完整文件名、「1.5 MB」、昨日日期（YYYY-MM-DD）

#### Scenario: 删除按钮 hover 显现

- **GIVEN** 列表存在文件行
- **WHEN** 鼠标未悬停该行
- **THEN** 删除按钮不可见；悬停后淡入显示，行布局不发生位移

### Requirement: 搜索与类型筛选

页面 SHALL 提供文件名搜索框（模糊匹配，实时过滤）与类型筛选（全部/Word/PPT/PDF/Markdown）；两者 SHALL 可叠加生效，筛选切换不重播列表入场动画。

#### Scenario: 搜索叠加类型筛选

- **GIVEN** 列表含 `茅台分析报告.docx` 与 `宁德分析报告.pptx`
- **WHEN** 用户选择「Word」tab 并在搜索框输入「茅台」
- **THEN** 列表仅显示 `茅台分析报告.docx`

### Requirement: 下载与删除交互

点击下载 SHALL 使按钮进入 loading 态（图标转圈 + 禁用），收到响应后恢复并 toast 提示「已开始下载」。删除 SHALL 先弹确认对话框；确认后该行以高度收起 + 淡出动画移除，再调用删除接口；接口失败 SHALL 恢复该行并 toast 报错。

#### Scenario: 删除确认与失败回滚

- **GIVEN** 列表含文件 `a.docx`
- **WHEN** 用户点击删除并在对话框确认，但接口返回 500
- **THEN** 行动画移除后恢复显示
- **AND** toast 提示删除失败，文件仍在列表中

#### Scenario: 取消删除无副作用

- **WHEN** 用户点击删除但在对话框选择取消
- **THEN** 不发出删除请求，列表不变

### Requirement: 空态、加载态与错误态

接口返回空列表时 SHALL 显示空态（占位图形 + 「暂无导出文件」文案 + 返回聊天的按钮）；加载中 SHALL 显示骨架屏；接口失败 SHALL toast 报错且不以空态冒充。

#### Scenario: 无文件时显示空态

- **GIVEN** `GET /api/files` 返回空数组
- **WHEN** 页面加载完成
- **THEN** 显示空态文案与「返回聊天」按钮，点击跳转会话页

#### Scenario: 接口失败不冒充空态

- **GIVEN** `GET /api/files` 返回 500
- **WHEN** 页面加载失败
- **THEN** toast 报错，不显示「暂无导出文件」空态

### Requirement: 交互动效规范

下载管理页动效 SHALL 遵守统一规范：时长三档（150ms 微交互 / 200ms 元素级 / 300ms 页面级）；缓动 ease-out（入场）与 ease-in-out（状态切换）；列表首次进入逐行 stagger 淡入上移（行间隔 30ms），筛选切换不重播；系统开启 `prefers-reduced-motion` 时全部动效 SHALL 退化为无动画。

#### Scenario: 首次进入逐行入场

- **WHEN** 用户首次进入 `/downloads`
- **THEN** 文件行以 30ms 间隔依次淡入并上移归位（fade-in + translateY(8px)→0，200ms ease-out）

#### Scenario: 减弱动效降级

- **GIVEN** 操作系统开启「减弱动态效果」
- **WHEN** 页面渲染与交互
- **THEN** 入场、hover、删除收起等动画全部禁用，状态即时切换

### Requirement: 跑赢指数对比卡片

战绩页(track-record)SHALL 提供「跑赢指数对比」卡片:调用 `GET /api/v1/track-record/index-compare`,展示选定跨度内组合区间收益与各指数区间收益的横向对比条(组合条置顶,指数条按收益降序),每条含指数名、区间收益百分比与跑赢标记(跑赢 ↑ 绿 / 跑输 ↓ 红 / 无数据灰显);卡片标题 SHALL 展示「跑赢 N/M 个指数」摘要(N=beat=true 数,M=beat 非 null 数;M=0 时展示空态文案)。时间跨度 SHALL 复用战绩页既有跨度偏好(fa_track_prefs.timeSpan,进页时与净值图同源读取一次;偏好变更经重新进入页面生效,与净值图行为一致);指数 effective_start_date 非窗口首日时 SHALL 在该指数条上标注实际起算日。

#### Scenario: 正常渲染

- **GIVEN** index-compare 返回窗口内组合收益 5.2%,五只指数中三只低于 5.2%
- **WHEN** 用户进入战绩页
- **THEN** 卡片标题展示「跑赢 3/5 个指数」,组合条置顶,指数条按收益降序排列
- **AND** 跑赢指数条带绿色↑标记,跑输带红色↓标记

#### Scenario: 跨度偏好应用

- **GIVEN** 用户在设置中心把默认时间跨度设为「近 3 月」
- **WHEN** 进入战绩页
- **THEN** 对比卡片以 span=3m 请求并渲染,与净值图使用同一跨度偏好

#### Scenario: 指数缺数灰显

- **GIVEN** 某指数 return/beat 为 null
- **WHEN** 卡片渲染
- **THEN** 该指数条灰显并标注「无数据」,不参与 N/M 摘要分母

#### Scenario: 组合净值不足空态

- **GIVEN** index-compare 返回 agent_return 为 null
- **WHEN** 卡片渲染
- **THEN** 展示空态文案(如「净值数据积累中」),SHALL NOT 渲染对比条与 N/M 摘要

### Requirement: 总览 β/α 指标位

战绩页总览指标区 SHALL 新增两个指标位:「β 市场敞口」与「α 年化超额」。β 显示两位小数(如 0.85 / −1.20,无百分号);α 沿 Delta 组件带符号百分比格式;两格 SHALL 各配一句口径副标题(β:接近 1 满仓跟随大盘,负值=反向敞口;α:剔除大盘影响后的独立判断收益)。后端 beta/jensen_alpha 为 null(样本不足)时两格 SHALL 显示 "—" 且不渲染副标题数字误导。

#### Scenario: 正常渲染

- **GIVEN** overview 返回 portfolio.beta = 0.85、portfolio.jensen_alpha = 0.031
- **WHEN** 用户打开战绩页
- **THEN** 「β 市场敞口」格显示 0.85,「α 年化超额」格显示 +3.10%
- **AND** 两格副标题可见

#### Scenario: 样本不足置空

- **GIVEN** overview 返回 beta/jensen_alpha 为 null
- **WHEN** 用户打开战绩页
- **THEN** 两格显示 "—",页面其余指标不受影响

#### Scenario: 负 β 展示

- **GIVEN** overview 返回 beta = −1.20(净空结构)
- **WHEN** 用户打开战绩页
- **THEN** β 格显示 −1.20,不带百分号

### Requirement: 观点日志标题与状态 tab

战绩页观点表 SHALL 增加区块标题「观点日志」(副标题注明:每条 = 一次分析结论;当前持有 = 仍在判定窗口内，窗口长短见「窗口」列)与三个状态 tab:**当前持有 / 已判定 / 全部**。缺省 SHALL 为「当前持有」(status=open);tab 映射:当前持有 → status=open,已判定 → status=resolved(非 open 全集),全部 → 不过滤。切换 tab SHALL 重置分页并按服务端过滤重新拉取;当前 active tab SHALL 有视觉标识;表格行数变化时徽标(当前 tab 的 total)SHALL 随响应更新。副标题 SHALL NOT 硬编码具体窗口天数（窗口口径由「窗口」列逐行承载，旧口径 252 行同样被副标题覆盖，不得产生「均在 20 日窗口内」的误导）。
(Previously: 副标题为「当前持有 = 仍在 20 日判定窗口内」——硬编码 20 日，对混入的 252 旧口径 open 行不成立（update-track-record-display-clarity 审计 §一）。)

#### Scenario: 缺省当前持有

- **GIVEN** 库内有 57 条 open 与 51 条已判定观点
- **WHEN** 用户进入战绩页
- **THEN** 「当前持有」tab 为 active,请求携带 status=open,表格仅显示 open 记录,分页 total 为 57

#### Scenario: 切换已判定

- **WHEN** 用户点击「已判定」tab
- **THEN** 请求携带 status=resolved,分页重置为第 1 页,total 更新为已判定子集大小

#### Scenario: 全部审计视图

- **WHEN** 用户点击「全部」tab
- **THEN** 请求不携带 status 参数,表格显示全部状态记录

#### Scenario: 副标题不硬编码窗口天数

- **GIVEN** 当前持有 tab 内混有 T+20 与 T+252 两种窗口的 open 行
- **WHEN** 渲染区块副标题
- **THEN** 副标题 SHALL 表述为「仍在判定窗口内」且窗口长短指向「窗口」列
- **AND** SHALL NOT 出现「20 日判定窗口」字样

### Requirement: 选股页

系统 SHALL 提供 `/screening` 路由页，展示最近成功批次的候选池表格，列含：标的、综合分、连续在榜天数、L3 verdict 与 reason、批次日期；综合分列 SHALL 支持 hover 展示因子贡献分解（factor_id 与 contribution）；页面 SHALL 提供「对精选池跑深度分析」按钮，触发后复用现有 SSE 管线时间轴展示各标的分析进度；选择模式为 `fallback` 的批次 SHALL 在页面展示降级披露横幅（明示「因子库无可打分因子、按近 20 日日均成交额排序、非因子信号」），且综合分列 SHALL 切换为「近 20 日日均成交额」语义呈现（标题与数值口径同步切换）。

#### Scenario: 贡献分解 hover

- **GIVEN** 候选池表格中标的 C 综合分 1.823
- **WHEN** 用户 hover 综合分单元格
- **THEN** 浮层展示 C 的 factor_contribs 列表（factor_id + contribution），按 contribution 绝对值降序

#### Scenario: 降级披露横幅

- **GIVEN** 最近成功批次选择模式为 fallback
- **WHEN** 用户打开选股页
- **THEN** 页面展示降级披露横幅，文案含「非因子信号」
- **AND** 综合分列标题切换为「近 20 日日均成交额（亿元）」且数值按成交额口径呈现

### Requirement: 涨跌语义令牌（A 股惯例）

设计令牌 SHALL 提供 `rise`（红系）/ `fall`（绿系）/ `flat`（灰）三个涨跌语义色，浅色与深色主题下语义一致；页面组件 SHALL 通过语义令牌引用涨跌色，SHALL NOT 直接书写 `text-red-*` / `text-green-*` 等裸色值表达涨跌。

#### Scenario: 涨跌色渲染

- **GIVEN** 一个数值 +2.31% 与一个数值 -1.05%
- **WHEN** 以数字规范组件渲染
- **THEN** 前者显示为「+2.31%」且使用 rise 令牌色（红），后者显示为「-1.05%」且使用 fall 令牌色（绿）
- **AND** 深色主题下语义不变

### Requirement: 状态徽章五态

系统 SHALL 提供统一的 StatusBadge 组件，状态枚举限五种：`active`（实心绿底）、`degraded`（琥珀描边）、`retired`（灰底）、`running`（蓝底 + 呼吸动画）、`failed`（红底）；任何页面的状态展示 SHALL 复用该组件，禁止自绘状态样式。

#### Scenario: 五态渲染

- **GIVEN** 分别以五种状态渲染 StatusBadge
- **WHEN** 截图对比
- **THEN** 五态配色与规范一致；running 态含呼吸动画，prefers-reduced-motion 时动画禁用

### Requirement: AI 生成内容标识

凡 LLM 生成的内容（L3 粗筛理由、语义记忆条款、分析结论等）SHALL 携带 AiBadge 紫系角标，hover 时展示提示「由模型生成，仅供参考」；确定性数据（数字、状态、时间）SHALL NOT 携带该角标。

#### Scenario: 角标展示

- **GIVEN** 选股页一行候选，含 L3 理由文本与综合分数字
- **WHEN** 渲染该行
- **THEN** L3 理由带 AiBadge 角标且 hover 出现规定提示文案；综合分数字无角标

### Requirement: 四种页面壳

系统 SHALL 提供 PageShellA（指标条+主图表+底部 Tab 表）、PageShellB（筛选栏+数据表+行展开详情）、PageShellC（左列表+右详情+确认/驳回）、PageShellD（批次列表+步骤级状态图标）四种布局壳组件；后续新增功能页面 SHALL 从四种壳中选择组装，禁止自由布局。

#### Scenario: 壳组件空态

- **GIVEN** PageShellB 的数据为空
- **WHEN** 渲染
- **THEN** 展示统一空态：图标 + 一句说明 + 主操作按钮
- **AND** 加载中展示 5 行骨架屏

### Requirement: 动效规范

表格行入场 SHALL 为 fade-in + translateY(8px)→0，stagger 间隔 30ms；Tab 切换内容区 SHALL 150ms 淡入；徽章状态变化 SHALL 200ms ease-out 颜色过渡；`prefers-reduced-motion` 时上述动画 SHALL 全部禁用。

#### Scenario: 动效降级

- **GIVEN** 系统偏好设置为 reduce motion
- **WHEN** 列表页渲染
- **THEN** 无任何入场/过渡动画，内容直接呈现

### Requirement: 本 change 零视觉变更红线

本 change SHALL NOT 改变任何现有页面的视觉与行为；打底前后对现有全部页面（浅色+深色）截图对比 SHALL 逐页零差异。

#### Scenario: 视觉基线对比

- **GIVEN** 打底前的全页面截图已存档于 `tests/validation/ui-baseline/`
- **WHEN** 本 change 完成后重新截图对比
- **THEN** 全部页面像素级无差异（允许的抗锯齿噪声容差 ≤ 0.1% 像素）

### Requirement: 记忆确认页

系统 SHALL 提供 `/memory` 路由页，采用 PageShellC（左列表 + 右详情 + 确认/驳回）形态：左侧为语义记忆条款列表并支持 待确认 / 已确认 / 已驳回 三态筛选（默认待确认），右侧为条款详情（完整正文、来源、提炼时间）与「确认」「驳回」动作；条款正文 SHALL 携带 AiBadge（LLM 提炼产物）；条款状态 SHALL 使用 StatusBadge；确认动作旁 SHALL 明示「确认后即刻生效注入风控辩论」；列表为空时 SHALL 如实说明等待月批提炼（MUST NOT 以加载中或错误态冒充）。页面入口 SHALL 同源提供于侧边栏与命令面板。

#### Scenario: 确认流

- **GIVEN** 待确认列表含条款 C
- **WHEN** 用户选中 C 并点击「确认」
- **THEN** 待确认列表移除 C、已确认列表出现 C（状态徽章为已确认）
- **AND** 无页面级报错

#### Scenario: 空态如实

- **GIVEN** 库中无任何 pending 条款
- **WHEN** 打开页面
- **THEN** 展示空态文案（等待月批提炼），MUST NOT 显示骨架屏或错误提示冒充

### Requirement: 因子库页

系统 SHALL 提供 `/factors` 路由页，采用 PageShellB（筛选栏 + 数据表 + 行展开详情）形态：筛选栏 SHALL 支持状态（全部 / active / retired）与来源（全部 / builtin / llm）过滤；表格列 SHALL 含 名称、表达式（截断）、状态、来源、ICIR 与衰减标记；状态 SHALL 使用 StatusBadge，`llm` 来源 SHALL 携带 AiBadge；行展开 SHALL 展示完整表达式、IC 序列与事件历史；空库时 SHALL 如实展示空态文案。页面入口 SHALL 同源提供于侧边栏与命令面板。

#### Scenario: 筛选与徽章

- **GIVEN** 库中含 active/llm 与 retired/builtin 两条因子
- **WHEN** 选择来源筛选 `llm`
- **THEN** 仅显示 active/llm 因子，其来源带 AiBadge、状态为 StatusBadge active
- **AND** retired 因子被过滤

#### Scenario: 行展开详情

- **GIVEN** 列表中的一条因子
- **WHEN** 展开该行
- **THEN** 展示完整 DSL 表达式、IC 序列与事件历史（含时间与事件类型）

### Requirement: 执行台账页

系统 SHALL 提供 `/broker` 路由页，采用 PageShellA（指标条 + 主图表 + 底部 Tab 表）形态：指标条 SHALL 含 总资产 / 可用资金 / 持仓市值 / 浮动盈亏（市值不可得时显式标注而非显示 0）；主图表 SHALL 为持仓市值构成（按标的）；底部 Tab 表 SHALL 含 持仓 / 订单 / 门禁拒绝 三个页签，订单状态 SHALL 使用 StatusBadge（pending→running、filled→active、rejected→failed、expired→retired）；空账户时 SHALL 如实展示空态。页面入口 SHALL 同源提供于侧边栏与命令面板。

#### Scenario: 台账齐备

- **GIVEN** 账户含 2 条持仓与 4 条不同状态订单
- **WHEN** 打开页面
- **THEN** 指标条与持仓构成图渲染；订单 Tab 含四态徽章
- **AND** 门禁拒绝 Tab 含被拒订单与原因码

#### Scenario: 空账户空态

- **GIVEN** 账户无持仓、无订单
- **WHEN** 打开页面
- **THEN** 指标条显示资金，图表与表格显示空态文案
- **AND** MUST NOT 渲染伪持仓或伪订单

### Requirement: 日批运维页

系统 SHALL 提供 `/ops` 路由页，采用 PageShellD（批次列表 + 步骤级状态图标）形态：**当日时间线** SHALL 按排程时刻数据驱动渲染全部日批任务（不硬编码任务清单），每项含 状态徽章（成功 / 失败 / 未跑 / 运行中 / 跳过）、最近运行摘要与失败原因（逐字展示）；每项 SHALL 提供一键补跑动作（复用既有补跑通道，已在运行或关闭态按端点语义如实提示）；**选股批次历史表** SHALL 展示近 N 批次（日期 / 状态 / 各层计数 / 池数 / 错误）；页面 SHALL 提供至评估运维（全量控制台）的跳转。空数据时各区块 SHALL 如实展示空态。页面入口 SHALL 同源提供于侧边栏与命令面板。

#### Scenario: 时间线齐备且数据驱动

- **GIVEN** 注册表含 10 个日批任务
- **WHEN** 打开页面
- **THEN** 时间线按排程时刻升序展示 10 项，各含状态徽章与最近运行摘要
- **AND** 失败项展示错误信息原文

#### Scenario: 一键补跑

- **GIVEN** 当日某任务失败
- **WHEN** 点击该任务补跑
- **THEN** 触发既有补跑通道并刷新该任务最近运行（或按 409/关闭语义如实提示）

#### Scenario: 选股批次历史

- **GIVEN** 含 1 成功 1 失败批次
- **WHEN** 打开页面
- **THEN** 历史表含两行（日期倒序），成功行含各层计数、失败行含原因

### Requirement: 因子研发监控页

系统 SHALL 提供 `/factor-research` 路由页，展示外部因子研发（RD-Agent 持续循环）的运行状态
（数据面只读）与研发看护启停开关（控制面），形态为「看护状态区 + 当前会话进度区 + 会话历史表」：
状态区 SHALL 含 看护状态徽章（运行中 / 已停止，复用 StatusBadge 五态）、看护 pid、启动时刻、
已运行时长、累计会话数与候选实现数；进度区 SHALL 含 当前会话开始时刻与时长、循环序号、当前
步骤名、步骤进度（x/5）、本会话因子名列表与日志尾（等宽字体，最近 N 行）；历史表 SHALL 列出
各会话（开始时刻 / 时长 / 状态，新→旧）。页面 SHALL 轮询自动刷新（间隔 ≤ 15 秒）；快照陈旧
（端点 `stale=true`）SHALL 在页面明示「数据已陈旧」（含最近采集时刻）且 MUST NOT 冒充实时；
快照缺失或损坏（`available=false`）SHALL 如实展示「采集器未接入」/「快照损坏」两种空态，
MUST NOT 显示骨架屏或错误提示冒充。页面 SHALL 提供研发看护启停开关（常驻可见，含状态文案）：
开关位置 SHALL 反映快照实际运行状态（运行中 = 开；MUST NOT 乐观翻转——位置仅在快照刷新后
改变）。点击「停止」SHALL 经二次确认（明示将终止当前研发会话）后调用
`PUT /api/factor-research/control`；点击「启动」直接提交。提交成功且 `control.pending=true`
期间 SHALL 展示「切换中」并禁用重复操作，MUST NOT 把切换中冒充为已生效。快照缺失/损坏/陈旧或
端点返回 409（`control_unavailable`）时开关 SHALL 禁用并明示理由（如「控制通道未接入」）；
请求失败 SHALL 如实提示原因，开关位置保持快照实际状态。页面其余部分仍只读（MUST NOT 增设其它
控制入口）。页面入口 SHALL 同源提供于侧边栏与命令面板。

(Previously: 页面 MUST NOT 提供启停/控制入口（只读页）。)

#### Scenario: 运行中状态与进度展示

- **GIVEN** 快照显示看护运行中、当前循环序号 4、步骤 coding、本会话已出现 5 个因子名、日志尾非空
- **WHEN** 打开监控页
- **THEN** 状态徽章为运行中（含 pid 与已运行时长）
- **AND** 进度区展示循环序号、步骤名与步骤进度 x/5，因子列表含 5 个名称，日志尾区块渲染最近日志行

#### Scenario: 陈旧快照披露

- **GIVEN** 端点返回 `stale=true`
- **WHEN** 打开监控页
- **THEN** 页面展示「数据已陈旧」提示（含最近采集时刻）
- **AND** 快照内容仍可见（标注为陈旧，MUST NOT 隐藏）

#### Scenario: 未接入与损坏空态

- **GIVEN** 端点返回 `available=false, reason=missing`
- **WHEN** 打开监控页
- **THEN** 展示「采集器未接入」空态文案，MUST NOT 显示骨架屏或错误提示冒充

- **GIVEN** 端点返回 `available=false, reason=corrupt`
- **WHEN** 打开监控页
- **THEN** 展示「快照损坏」空态文案（与未接入文案区分）

#### Scenario: 开关两态与切换中

- **GIVEN** 快照 `supervisor.running=true` 且 `control.pending=false`
- **WHEN** 打开监控页
- **THEN** 开关为开且可操作
- **AND** 用户关闭并在二次确认中确认后发出控制请求，`pending=true` 期间开关禁用并显示「切换中」
- **AND** 快照刷新为 `running=false` 后开关为关、恢复可操作

#### Scenario: 停止需二次确认

- **GIVEN** 开关处于开（看护运行中）
- **WHEN** 点击停止
- **THEN** 出现二次确认（含「将终止当前研发会话」语义）
- **AND** 未确认前 MUST NOT 发出控制请求

#### Scenario: 控制通道未接入禁用

- **GIVEN** 快照缺失 / 损坏 / 陈旧（任一）
- **WHEN** 打开监控页
- **THEN** 开关禁用且明示理由（如「控制通道未接入」）
- **AND** MUST NOT 可点击发出控制请求

#### Scenario: 请求失败如实提示

- **GIVEN** 发出控制请求但端点返回 409 或网络失败
- **WHEN** 请求返回
- **THEN** 页面提示失败原因
- **AND** 开关位置保持快照实际状态（MUST NOT 翻转冒充成功）

