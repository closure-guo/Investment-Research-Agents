# Session Persistence Specification

## Purpose

定义对话与管线时序的结构化持久化契约，确保思考/搜索/工具调用的真实交错时序在会话存储与恢复过程中不丢失。
## Requirements
### Requirement: 对话时序结构化持久化

系统 SHALL 将 assistant 消息的思考/搜索/工具调用按真实交错时序持久化到 `chat_history` 条目的 `agentTimeline` 字段（TimelineItem 数组），替代仅靠拍平 `thinking` 字符串的恢复。后端在流式消费时构建结构化时序，语义与前端 `applyChatStreamEvent` 等价。

TimelineItem 结构（与前端 `types.ts` 同构）：
- `{type:'thinking', content, title?, done?}`
- `{type:'search', query, results?, status}`
- `{type:'tool_call', name, args, result?, done}`

#### Scenario: assistant 消息持久化 agentTimeline

- **GIVEN** 一次分析/对话产生思考、搜索、工具调用的交错事件流
- **WHEN** 后端将该 assistant 消息写入 chat_history
- **THEN** 条目 SHALL 包含 `agentTimeline` 字段，按事件到达顺序记录 thinking/search/tool_call items
- **AND** thinking 片段在 search/tool_call 处断开为多段（与前端流式构建一致）
- **AND** 既有 `thinking`/`tool_calls` 字段保留（向后兼容）

#### Scenario: 旧会话无 agentTimeline 字段兼容

- **GIVEN** 历史 chat_history 条目仅有 thinking/tool_calls，无 agentTimeline
- **WHEN** 前端恢复该消息
- **THEN** 前端 SHALL 回退 buildTimelineFromHistory 近似重建，不报错

### Requirement: 管线时序持久化

系统 SHALL 将深度分析管线各节点的思考/工具时序持久化到 sessions 表 `pipeline_timelines` 列（JSON：`{node: [TimelineItem]}`），管线运行中按节点事件与 `pipeline_snapshot` 同节奏写入。

#### Scenario: 管线节点时序持久化

- **GIVEN** 深度分析管线运行中产生 thinking_token（含 node 字段）/工具/搜索事件
- **WHEN** 管线执行（fast path PipelineRunner 或 ReAct run_deep_analysis 工具）
- **THEN** 系统 SHALL 按 node 分组维护时序并写入 pipeline_timelines
- **AND** 节点完成时收口该节点末段 thinking（等价前端 applyPipelineNodeComplete）

#### Scenario: 会话详情返回管线时序

- **GIVEN** 某会话的 pipeline_timelines 已写入
- **WHEN** 前端请求 GET /api/sessions/{sessionId}
- **THEN** 响应 SHALL 包含 pipeline_timelines（可解析为 {node: [TimelineItem]}）

### Requirement: 管线触发锚点持久化

系统 SHALL 在深度分析管线启动时将触发锚点持久化到 sessions 表 `pipeline_anchor` 列（INTEGER，NULL 表示无锚点）。锚点 = 启动时刻 chat_history 中最后一条 role='user' 条目的索引 + 1，即"触发本轮分析的用户消息之后"，供前端历史重建时定位报告消息插入位置。

锚点 SHALL 在两条管线启动路径上写入：fast path（已知股票代码直接启动 PipelineRunner）与 ReAct 路径（run_deep_analysis 工具实际启动管线时）。锚定 user 消息而非取 chat_history 长度，避免 ReAct 路径 assistant 在途增量 upsert 导致锚点随持久化时机抖动。

#### Scenario: fast path 写入锚点

- **GIVEN** 新会话首次输入即解析出股票代码（fast path）
- **WHEN** 用户消息追加到 chat_history 后、管线启动前
- **THEN** 系统 SHALL 将 pipeline_anchor 写为 1（chat_history 仅一条 user 消息）
- **AND** 旧库启动时经幂等 ALTER TABLE 迁移添加 pipeline_anchor 列，既有行保持 NULL

#### Scenario: ReAct 路径写入锚点

- **GIVEN** 多轮澄清会话的 chat_history 为 [user1, assistant1, user2]
- **WHEN** ReAct Agent 调用 run_deep_analysis 工具实际启动管线
- **THEN** 系统 SHALL 将 pipeline_anchor 写为 3（最后一条 user 消息 user2 的索引 + 1）
- **AND** 当前轮 assistant 在途消息的增量 upsert 不影响锚点值

#### Scenario: 会话详情返回锚点

- **GIVEN** 某会话的 pipeline_anchor 已写入
- **WHEN** 前端请求 GET /api/sessions/{sessionId}
- **THEN** 响应 SHALL 包含 pipeline_anchor 整数值
- **AND** 未写入过的会话（旧数据）返回 NULL/缺失，前端走回退逻辑

### Requirement: SQLite 瞬态错误重试

`session_store` 的连接获取层 SHALL 对瞬态 SQLite 错误（`unable to open database file`、`database is locked`、`disk I/O error`）做有界短重试（有限次数 + 短退避），覆盖全部读写函数而非仅事件 append 路径。重试耗尽 SHALL 向调用方抛出原异常（显式失败），MUST NOT 静默吞掉造成「假写成功」。终态关键路径（终态事件落库、`sessions.status` 流转、`report_markdown` 落库）的调用方 SHALL 在重试耗尽后执行兜底处置（错误日志 + 状态显式置错），MUST NOT 留下「管线已完成但会话状态缺失」且无任何日志的中间态。

#### Scenario: 瞬断自愈窗口内写成功

- **GIVEN** SQLite 数据库文件短暂不可打开（如 bind mount 瞬断，约 2 秒内自愈）
- **WHEN** 任一 `session_store` 读写函数被调用
- **THEN** 连接获取层重试后 SHALL 成功执行，调用方无感知

#### Scenario: 重试耗尽显式失败

- **GIVEN** 瞬态错误持续超过重试窗口
- **WHEN** 写函数被调用
- **THEN** SHALL 抛出原异常而非静默返回成功
- **AND** 终态关键路径调用方 SHALL 记录错误日志并显式处置（置错或重试兜底）

#### Scenario: 非 append 热路径同样受保护

- **GIVEN** `update_session_status` / `update_pipeline_timelines` / `update_pipeline_snapshot` 在调用时命中瞬态错误
- **THEN** SHALL 经同一连接获取重试机制处置，而非直接失败

### Requirement: 终稿决策持久化与同标的回溯查询

会话完成落库（`update_session_report`）时 SHALL 一并持久化 `final_trade_decision`（终稿决策 JSON，至少含 action/confidence/entry_price/stop_loss/target_price/trigger_high/trigger_low 在场字段）；终稿不存在（管线阻断/未审批）时该字段落 NULL，MUST NOT 以 trader 层 plan 冒充终稿。

系统 SHALL 提供按 `stock_code` 回溯读取「上一份已完成报告」的能力：给定 stock_code 与当前会话标识，返回最近一条 `status='completed'` 且非当前会话的行（按 `created_at` 降序取一），携带增量摘要渲染所需的结构化字段（`final_trade_decision`、`chart_data` 中的关键指标、`created_at`）。无符合条件的历史行时返回 None。查询 MUST NOT 返回 running/failed 状态的会话。

#### Scenario: 终稿决策随报告落库

- **WHEN** 一条会话管线完成并调用 `update_session_report`
- **THEN** 会话行 SHALL 持久化 `final_trade_decision` JSON（含在场价位与触发位字段）
- **WHEN** 管线被门禁阻断、无终稿决策
- **THEN** 该字段落 NULL，落库不报错

#### Scenario: 回溯取最近完成报告

- **GIVEN** 同一 `stock_code` 存在三条历史会话：09-28 completed、09-30 completed、10-08 running
- **WHEN** 为 10-08 新完成的会话查询上一份报告
- **THEN** 返回 09-30 行（最近一条 completed），MUST NOT 返回 running 行，MUST NOT 返回当前会话自身

#### Scenario: 无历史报告返回 None

- **WHEN** 某 `stock_code` 无任何其他 completed 会话
- **THEN** 回溯查询返回 None，调用方按「首份报告」处理，不报错

