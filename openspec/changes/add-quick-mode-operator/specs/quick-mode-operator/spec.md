## ADDED Requirements

### Requirement: 快速模式系统操作工具注册

快速模式 Agent SHALL 在既有 `web_search` 之外注册系统操作工具，P1 至少包含 `search_stock` 与 `start_stock_analysis`。工具名 MUST 经 `tool_registry.py` 统一登记，并标注读/写类别；`start_stock_analysis` MUST 归类为写操作（有副作用），`search_stock` MUST 归类为只读。快速模式 `max_iterations` SHALL 不低于 6，以容纳「解析代码 → 派单 → 复述回执」的最短链路。

#### Scenario: 快速模式可解析股票名称

- **GIVEN** 快速模式 Agent 已构建
- **WHEN** 用户输入「分析茅台」
- **THEN** 模型可调用 `search_stock("茅台")` 并得到唯一 6 位代码 `600519`
- **AND** 工具结果包含股票名称、代码与行情快照（快照缺失时如实披露，不编造价格）

#### Scenario: 工具名单一来源

- **WHEN** 检查 `tool_registry.py` 的已注册工具集合
- **THEN** `search_stock` 与 `start_stock_analysis` 均在其中
- **AND** Agent 注册名与注册表常量一致，不出现第二份硬编码名称

### Requirement: 分析派单与回执

`start_stock_analysis` SHALL 创建独立的 `analysis` 会话（非当前快速 `chat` 会话），写入用户消息、设置管线锚点，并通过既有分析执行设施启动管线（与手工深度分析同一路径，保留请求级 LLM 配置），随后立即返回回执。工具 MUST NOT 在快速通道内运行深度管线，MUST NOT 等待管线完成，MUST NOT 经 AG-UI 通道传输任何管线时间线事件。回执 SHALL 至少包含 `session_id`、`display_name`、`status`（`queued` 或 `running`）与 `symbol`。

#### Scenario: 名称解析后派单成功

- **GIVEN** `search_stock` 已确认唯一标的 `600519`（贵州茅台）
- **WHEN** 模型调用 `start_stock_analysis("600519", "贵州茅台")`
- **THEN** 系统创建新的 `analysis` 会话并投递到分析队列
- **AND** 工具在管线启动前返回回执，含 `session_id`、`display_name`、`status`、`symbol`
- **AND** 快速会话的 journal 中不出现该管线的 `node_start` / `node_complete` / 报告事件

#### Scenario: 代码直达派单

- **GIVEN** 用户输入 6 位代码 `600519`
- **WHEN** 模型直接调用 `start_stock_analysis("600519")`
- **THEN** 系统跳过名称解析并完成派单
- **AND** 回执 `symbol` 为 `600519`

#### Scenario: 启动后立即回执

- **GIVEN** 管线需要 2–5 分钟完成
- **WHEN** 用户请求分析一只已确认的股票
- **THEN** 工具在管线完成前返回回执
- **AND** 回执 `status` 为 `running`（已启动）或 `queued`（排队），不等待报告生成

#### Scenario: 请求级 LLM 配置透传

- **GIVEN** 快速模式请求携带请求级 `llm_config`
- **WHEN** 派单创建分析会话
- **THEN** 该分析会话的管线使用同一份 `llm_config`
- **AND** 不因派单而回退到环境变量默认模型

#### Scenario: 会话分离

- **WHEN** 派单成功
- **THEN** 当前快速会话的 `session_type` 仍为 `chat`
- **AND** 新建分析会话的 `session_type` 为 `analysis`
- **AND** 用户在既有会话列表可看到该分析会话并按既有管线视图查看进度

### Requirement: 派单边界与失败披露

工具 MUST 在无法确定唯一标的、标的不合法、会话创建失败或管线启动失败时显式返回错误或澄清请求，MUST NOT 伪造派单成功。模糊请求（板块、热点、多候选）MUST NOT 直接派单，SHALL 先返回候选摘要并等待用户确认。派单产生的副作用 MUST 记录结构化日志（至少 `session_id`、`symbol`、`status`、时间），供审计与故障排查。

#### Scenario: 多候选不派单

- **GIVEN** `search_stock` 返回多个候选
- **WHEN** 用户尚未确认具体标的
- **THEN** 模型只返回候选清单与确认请求
- **AND** 不创建分析会话、不产生队列条目

#### Scenario: 启动失败显式报错

- **GIVEN** 会话创建或管线启动抛出异常
- **WHEN** 工具执行
- **THEN** 返回含失败原因的错误结果
- **AND** 不返回带 `session_id` 的成功回执

#### Scenario: 派单留痕

- **WHEN** 派单成功
- **THEN** 结构化日志包含 `session_id`、`symbol`、`status` 与时间戳
- **AND** 工具调用 span 进入 Langfuse，可回溯入参与结果摘要

### Requirement: 工具能力缺失时的降级披露

当请求级或环境级 LLM profile 的 `tool_call` 能力为 false 时，快速模式 SHALL 仍允许纯对话，但系统操作工具 MUST NOT 以可执行状态注册或 MUST 在执行前被拦截，并向用户显式说明「当前模型不支持工具调用，系统操作不可用」。系统 MUST NOT 静默失败或让模型误以为已完成派单。

#### Scenario: 无工具能力时不静默

- **GIVEN** 当前 profile 的 probe 事实 `tool_call=false`
- **WHEN** 用户请求「分析茅台」
- **THEN** 系统不创建分析会话
- **AND** 回复显式说明系统操作不可用及可行动建议（切换支持工具调用的模型）

### Requirement: 双轨隔离不回退

快速模式新增系统操作工具后，AG-UI 通道 SHALL 仍仅承载快速对话与工具调用事件；深度分析管线、报告与节点时间线 MUST NOT 经该通道传输。移除或停用系统操作工具后，快速对话与深度模式 MUST 仍可独立工作。

#### Scenario: 派单后管线事件不串流

- **GIVEN** 快速模式已成功派单
- **WHEN** 分析管线在该 analysis 会话中运行
- **THEN** 快速会话的 SSE 流只包含本次对话的文本、思考与工具事件
- **AND** 管线事件只出现在 analysis 会话的既有通道

## MODIFIED Requirements

（无）

## REMOVED Requirements

（无）

### Requirement: 只读系统查询工具

快速模式 SHALL 注册只读系统查询工具，至少覆盖：选股池（`get_screening_pool`）、战绩总览（`get_track_record_overview`）、执行台账（`get_broker_overview`）、日批状态（`get_daily_ops_status`）、记忆条款（`get_memory_clauses`）、因子库（`get_factor_library`）与因子研发状态（`get_factor_research_status`）。只读工具 MUST NOT 产生任何写副作用，MUST 返回紧凑摘要而非完整数组/曲线，MUST 对缺失、陈旧或样本不足的数据显式披露，MUST NOT 用 0 或猜测值冒充真实读数。

#### Scenario: 选股池查询

- **GIVEN** 存在最近成功选股批次
- **WHEN** 用户询问「今天池子里有什么」
- **THEN** 工具返回批次日期、批次状态、是否 stale、池内标的（代码/分数/排名/L3 结论）与分析队列
- **AND** 不返回完整因子贡献矩阵或历史批次全量

#### Scenario: 无成功批次如实披露

- **GIVEN** 选股库无成功批次
- **WHEN** 用户询问选股池
- **THEN** 返回空池与 `batch_date=null`、`stale` 状态
- **AND** MUST NOT 编造标的或把失败批次冒充成功批次

#### Scenario: 持仓快照缺失不冒充 0

- **GIVEN** 有持仓但最新快照不可得
- **WHEN** 用户询问持仓
- **THEN** 持仓市值/总资产/浮盈返回 `null` 并附缺失标注
- **AND** MUST NOT 以 0 冒充不可得读数

#### Scenario: 战绩样本不足

- **GIVEN** 已结算样本少于 10
- **WHEN** 查询战绩总览
- **THEN** 胜率等统计按既有口径返回 `null` 与 `insufficient_sample` 标注
- **AND** 不把「样本不足」表述为「表现差」

#### Scenario: 因子研发状态陈旧

- **GIVEN** 因子研发快照缺失或超过陈旧阈值
- **WHEN** 查询研发状态
- **THEN** 返回 `available=false` 或 `stale=true` 及原因
- **AND** 不把陈旧快照当作实时运行状态

### Requirement: 受控写操作与确认协议

所有写工具（因子研发启停、日批补跑、记忆条款审批、精选池批量分析）MUST 使用统一的两步确认协议：第一次调用（`confirm=false`）MUST 只返回预览摘要与一次性 token，MUST NOT 执行任何副作用；第二次调用 MUST 同时满足 `confirm=true`、token 有效、动作与参数完全一致、且**当前用户消息**包含明确确认词（`确认`/`确定`/`执行`/`同意`/`可以`/`好的`）才可执行。token SHALL 绑定 session、动作与参数，TTL SHALL 为 300 秒，MUST 单次使用。系统 MUST NOT 仅凭模型自行调用 `confirm=true` 执行写操作。每次成功或失败的写操作 MUST 记录结构化审计日志（至少 session_id、action、params、result、timestamp）。

#### Scenario: 首次调用只预览

- **GIVEN** 用户说「停止因子研发」
- **WHEN** 模型首次调用 `set_factor_research_running(false)`
- **THEN** 返回 `status=confirmation_required`、预览摘要与 token
- **AND** 控制文件未被修改、无副作用

#### Scenario: 未经用户确认不执行

- **GIVEN** 模型已取得预览 token
- **WHEN** 模型在用户未回复确认词时自行调用 `confirm=true`
- **THEN** 工具拒绝执行并返回原因
- **AND** MUST NOT 修改任何状态

#### Scenario: 用户确认后执行一次

- **GIVEN** 用户回复「确认执行」
- **WHEN** 模型带同一 token 与参数调用 `confirm=true`
- **THEN** 写操作执行一次并返回真实结果
- **AND** 同一 token 再次使用 MUST 被拒绝

#### Scenario: 参数被篡改拒绝

- **GIVEN** 预览针对 `desired_running=false`
- **WHEN** 第二次调用改为 `desired_running=true`
- **THEN** token 校验失败，工具拒绝执行

#### Scenario: token 过期

- **GIVEN** 预览 token 已超过 300 秒
- **WHEN** 模型调用 `confirm=true`
- **THEN** 工具拒绝并要求重新预览

### Requirement: 因子研发控制语义

`set_factor_research_running` SHALL 只写布尔期望态，MUST NOT 直接启动/停止研发进程，MUST NOT 暴露任意命令/脚本/参数。回执 MUST 包含 `desired_running`、`pending`、`honor`（`live` / `launcher`）与 `honor_eta_seconds`，MUST NOT 把「期望态已写入」表述为「已启动/已停止」。控制通道无受理能力（快照与启动器均不可用）时 MUST 原样返回 `control_unavailable`，MUST NOT 谎称成功。

#### Scenario: 快照新鲜时受理

- **GIVEN** 因子研发快照新鲜
- **WHEN** 用户确认写入 `desired_running=false`
- **THEN** 返回 `honor="live"`、`pending=true`（若实态仍运行）
- **AND** 明确说明「期望态已写入，等待代理调和」

#### Scenario: 启动器可用时离线受理

- **GIVEN** 快照陈旧但启动器心跳新鲜且 `agent_ok=true`
- **WHEN** 确认写入 `desired_running=true`
- **THEN** 返回 `honor="launcher"` 与 `honor_eta_seconds=180`
- **AND** 不承诺立即拉起

#### Scenario: 无受理能力拒绝

- **GIVEN** 快照与启动器均不可用
- **WHEN** 确认写入
- **THEN** 返回 `control_unavailable`
- **AND** 不写控制文件、不谎称成功

### Requirement: 日批补跑白名单与后台执行

`run_ops_job` MUST 只接受 `JOB_IDS` 中的 job_id，MUST NOT 接受任意字符串。预览 MUST 包含任务标签与 job_id；执行 SHALL 在后台线程启动既有 `run_job`（单飞锁与运行历史不变），工具 MUST 立即返回回执（含 job_id 与「已触发」），MUST NOT 在快速通道内等待任务完成。未知 job_id MUST 拒绝；任务已在运行 MUST 返回明确的 `already_running` 语义。

#### Scenario: 未知任务拒绝

- **WHEN** 模型传入不在白名单的 job_id
- **THEN** 工具拒绝，不启动任何任务

#### Scenario: 白名单任务后台执行

- **GIVEN** 用户确认补跑 `factor_harvest_daily`
- **WHEN** 工具执行
- **THEN** 后台启动任务并立即返回回执
- **AND** 用户可用 `get_daily_ops_status` 查询运行结果

#### Scenario: 已在运行

- **GIVEN** 目标任务正在运行
- **WHEN** 用户确认补跑
- **THEN** 返回 `already_running` 语义
- **AND** MUST NOT 重复启动第二个实例

### Requirement: 记忆条款审批与精选池分析

`review_memory_clause` SHALL 支持 `confirm` 与 `reject` 两种动作，预览 MUST 包含条款文本；未知条款 ID MUST 返回明确错误，MUST NOT 静默成功。`start_pool_analysis` MUST 在预览中披露池内标数量；池为空时 MUST 返回明确错误，MUST NOT 静默返回空队列。两者均遵循统一确认协议。

#### Scenario: 确认记忆条款

- **GIVEN** 存在 pending 条款
- **WHEN** 用户确认 `action=confirm`
- **THEN** 条款状态变为 confirmed
- **AND** 审计日志记录条款 ID 与动作

#### Scenario: 未知条款

- **WHEN** 对不存在的条款执行确认/驳回
- **THEN** 返回明确错误
- **AND** 不产生任何状态变更

#### Scenario: 空池拒绝

- **GIVEN** 精选池为空
- **WHEN** 用户请求批量分析
- **THEN** 返回池空错误
- **AND** 不创建任何分析会话

### Requirement: 写操作审计

每次写操作（成功或失败）MUST 产生结构化审计日志 `operator_write`，字段至少包含 `session_id`、`action`、`params`、`result`、`timestamp`；MUST NOT 记录 API Key、token 明文或用户敏感信息。写操作 SHALL 经 `_trace_tool` 进入 Langfuse。

#### Scenario: 成功写操作留痕

- **WHEN** 任一写操作执行完成
- **THEN** 日志包含动作、参数摘要、结果与时间
- **AND** 可在 Langfuse 按工具名回溯

#### Scenario: 失败写操作留痕

- **WHEN** 写操作被拒绝或执行失败
- **THEN** 日志包含失败原因
- **AND** MUST NOT 只记录成功路径
