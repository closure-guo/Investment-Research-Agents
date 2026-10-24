# composer-stat-pills Specification

## Purpose
TBD - created by archiving change add-composer-stat-pills. Update Purpose after archive.
## Requirements
### Requirement: 输入框计量条组成与显隐

系统 SHALL 在聊天输入框卡片下方渲染一行计量条，包含三个可点开的控件：会话统计、Token 用量、上下文已用。每个控件的数据不可用时 MUST NOT 渲染该控件（不得以 0 或占位符冒充真值），剩余控件按原顺序排列。

#### Scenario: 三项数据齐备

- **GIVEN** 当前会话已完成至少一次深度分析，且用量记账成功
- **WHEN** 渲染输入框计量条
- **THEN** 依次渲染「会话统计」「Token 用量」「上下文已用」三个控件

#### Scenario: 缺少 token 记账

- **GIVEN** 当前会话没有 `tokens_total` 记录（旧会话或 provider 未返回 usage）
- **WHEN** 渲染输入框计量条
- **THEN** 只渲染「会话统计」与「上下文已用」
- **AND** 不渲染 Token 用量控件

#### Scenario: 无会话

- **GIVEN** 当前没有选中会话
- **WHEN** 渲染输入框计量条
- **THEN** 不渲染任何控件

### Requirement: 会话统计控件

「会话统计」控件 SHALL 以「{turns} 轮 {steps} 步」展示会话轮次与步骤数；点击后 SHALL 打开面板，展示最近一次分析的管线总用时与步骤数。`turns` 取会话 `chat_history` 中 user 消息条数；`steps` 取该会话事件日志中 `node_start` 的去重节点数；**quick 会话（`session_type='chat'`）没有管线节点，`steps` SHALL 回退为 assistant 消息条数**（每条 assistant 回复即一次模型步）。

#### Scenario: 已完成会话显示轮次与步骤

- **GIVEN** 会话含 2 条 user 消息，最近一次分析执行了 12 个管线节点
- **WHEN** 渲染「会话统计」控件
- **THEN** 控件文本为「2 轮 12 步」

#### Scenario: quick 会话回退为模型步

- **GIVEN** `session_type='chat'` 的会话含 2 条 user、2 条 assistant 消息，且无任何 `node_start` 事件
- **WHEN** 请求会话 stats
- **THEN** `steps` 为 2，控件文本为「2 轮 2 步」

#### Scenario: 打开统计面板

- **GIVEN** 会话统计数据可用
- **WHEN** 用户点击「会话统计」控件
- **THEN** 在控件上方打开面板，展示管线总用时与步骤数
- **AND** 再次点击控件或按 Escape 关闭面板

### Requirement: Token 用量控件

「Token 用量」控件 SHALL 展示会话累计 token 总量（输入 + 输出），数值为 provider 返回的真实 usage 逐次累加，MUST NOT 使用字符数估算。点击后 SHALL 展示输入、输出与合计的精确值。

#### Scenario: 累计 token 展示

- **GIVEN** 会话用量记账为 prompt=120000、completion=8000
- **WHEN** 渲染 Token 用量控件
- **THEN** 控件展示 128000 的紧凑形式（如「12.8万」）
- **AND** 面板展示输入 120000、输出 8000、合计 128000

#### Scenario: 多次分析累加

- **GIVEN** 同一会话先后完成两次分析
- **WHEN** 第二次分析结束并完成记账
- **THEN** 会话累计 token 为两次 usage 之和

### Requirement: 上下文已用控件

「上下文已用」控件 SHALL 以 14px 环形进度 + 百分比展示最近一次分析的上下文占用率：`percent = min(100, round(context_peak_tokens / context_window * 100))`。`context_window` 优先取当前激活 profile 的 `contextLength`，缺省回退 128000。点击后 SHALL 展示 `~{used} / {window}` 与占用条。`context_peak_tokens` 或 `context_window` 缺失时 MUST NOT 渲染该控件。

#### Scenario: 正常占用率

- **GIVEN** `context_peak_tokens=45000`、`context_window=200000`
- **WHEN** 渲染上下文已用控件
- **THEN** 环形按 23% 填充，文本显示「23%」
- **AND** 面板显示「~4.5万 / 20万」

#### Scenario: 超窗钳制

- **GIVEN** `context_peak_tokens=260000`、`context_window=200000`
- **WHEN** 渲染上下文已用控件
- **THEN** 百分比钳制为 100%，环形满格

#### Scenario: 窗口未知

- **GIVEN** 当前 profile 未设置 `contextLength` 且后端未提供默认窗口
- **WHEN** 渲染计量条
- **THEN** 不渲染上下文已用控件

### Requirement: 计量条弹层交互

三个控件的弹层 SHALL 互斥：同一时刻至多一个打开；Escape 或控件外 pointerdown SHALL 关闭当前弹层。弹层 SHALL 以 `position: fixed` 锚定在触发控件上方 8px，并在视口内保留 12px 边距。

#### Scenario: 切换控件

- **GIVEN** 「会话统计」弹层已打开
- **WHEN** 用户点击「Token 用量」控件
- **THEN** 会话统计弹层关闭，Token 用量弹层打开
- **AND** 页面上始终只有一个弹层

#### Scenario: Escape 关闭

- **GIVEN** 任一弹层已打开
- **WHEN** 用户按下 Escape
- **THEN** 弹层关闭，焦点回到触发控件

### Requirement: 会话计量数据端点

后端 SHALL 提供 `GET /api/sessions/{session_id}/stats`，返回字段 `turns`（整数）、`steps`（整数）、`duration_ms`（整数，最近一次管线耗时，未知为 0）、`tokens_prompt`、`tokens_completion`、`tokens_total`、`context_peak_tokens`（整数，未知为 0）。会话不存在时 SHALL 返回 404。该端点 SHALL 只读，不触发任何 LLM 调用。

#### Scenario: 已完成会话返回真值

- **GIVEN** 会话已落库 usage 与上下文峰值
- **WHEN** 请求 `/api/sessions/{id}/stats`
- **THEN** 返回 200 与上述字段，其中 token 字段来自 sessions 表而非估算

#### Scenario: 会话不存在

- **GIVEN** 请求一个不存在的 session_id
- **WHEN** 调用 stats 端点
- **THEN** 返回 404

### Requirement: 会话用量记账

深度分析在后台线程执行时 SHALL 在该线程内激活 usage 收集器（`usage_collector`），把 provider 返回的 `prompt_tokens` / `completion_tokens` / `total_tokens` 累加并落库到 `sessions` 表；`context_peak_tokens` SHALL 取会话内单次请求 `prompt_tokens` 的最大值。**quick 模式经 AG-UI 通道运行 harness Agent，不经过管线节点，用量 SHALL 由 Agent 层累计（`usage_*` 属性），并在 AG-UI / SSE 收尾时落库**。收集器未激活、Agent 无调用或 provider 未返回 usage 时 MUST NOT 以估算值填充。`TESTING=1` 的 stub 调用不产生 usage，记账保持 0。

#### Scenario: 分析结束落库

- **GIVEN** 一次深度分析产生 8 次 LLM 调用，均有 usage
- **WHEN** 管线结束
- **THEN** `sessions.tokens_total` 为 8 次调用 total_tokens 之和
- **AND** `sessions.context_peak_tokens` 为 8 次调用中 prompt_tokens 的最大值

#### Scenario: quick 会话收尾落库

- **GIVEN** 一次 quick 运行产生 2 次 harness LLM 调用并返回 usage
- **WHEN** AG-UI 运行结束
- **THEN** 会话 `tokens_total` 为两次调用之和
- **AND** `context_peak_tokens` 为两次调用 prompt_tokens 的最大值

#### Scenario: 无 usage 不造数

- **GIVEN** provider 未返回任何 usage
- **WHEN** 管线或 quick 运行结束
- **THEN** `tokens_*` 与 `context_peak_tokens` 保持 0
- **AND** stats 端点如实返回 0，前端据此隐藏对应控件

