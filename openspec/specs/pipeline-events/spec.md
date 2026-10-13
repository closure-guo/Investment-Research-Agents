# Pipeline Events Specification

## Purpose

定义深度分析管线的后台执行、断点恢复与进度快照持久化能力域。管线执行与 SSE 订阅解耦：客户端断开 SSE 连接（如切换会话）不中断管线执行，管线进度快照持久化到会话，使客户端切回会话时可恢复管线 UI。该能力同时适用于 fast path 和 ReAct 路径。
## Requirements
### Requirement: 管线后台执行与断点恢复

系统 SHALL 使深度分析管线的执行与 SSE 订阅解耦：管线在独立后台任务中运行，客户端断开 SSE 连接（如切换会话）SHALL NOT 中断管线执行；管线进度快照 SHALL 持久化到会话，使客户端切回会话时可恢复管线 UI。该能力 SHALL 同时适用于 fast path（`/api/analyze` 带 `stock_code` 直传路径）和 ReAct 路径（自然语言输入，Agent 通过 `run_deep_analysis` 工具触发管线）。

#### Scenario: SSE 断开后管线后台继续执行

- **GIVEN** 某会话的深度分析管线正在运行（无论 fast path 还是 ReAct 路径）
- **WHEN** 客户端断开 SSE 连接（切换会话、关闭标签页）
- **THEN** 后端管线 SHALL 在后台任务中继续执行
- **AND** 管线完成后 SHALL 照常写入会话的报告与分析产物（report_markdown/agent_process/analyst_reports）
- **AND** 会话 status 在管线完成后 SHALL 更新为 completed

#### Scenario: ReAct 路径管线后台化

- **GIVEN** 用户通过自然语言输入触发深度分析，Agent 调用 `run_deep_analysis` 工具
- **WHEN** 客户端在管线执行期间断开 SSE 连接
- **THEN** `run_deep_analysis` 工具内的 `graph.stream` SHALL 在后台任务中继续执行
- **AND** 管线进度快照 SHALL 持续持久化到会话的 pipeline_snapshot
- **AND** 管线完成后 SHALL 将最终报告写入会话存储
- **AND** 会话 status SHALL 更新为 completed
- **AND** 客户端切回该会话时 SHALL 通过快照 + 轮询恢复管线 UI 并展示最终报告

#### Scenario: 管线进度快照持久化

- **GIVEN** 管线正在后台运行
- **WHEN** 任一图节点完成
- **THEN** 系统 SHALL 将当前管线状态快照（layerTree 各节点 status/startedAt/durationMs、currentNodeId、progress、updatedAt）持久化到会话的 pipeline_snapshot
- **AND** 快照 SHALL 可序列化为 JSON 并反序列化还原分层时间轴

#### Scenario: 会话详情返回管线快照与运行状态

- **GIVEN** 某会话有进行中的或已完成的管线
- **WHEN** 客户端请求 GET /api/sessions/{id}
- **THEN** 响应 SHALL 包含会话 status（running/completed/failed）与 pipeline_snapshot（若存在）
- **AND** 若 status 为 failed，响应 SHALL 包含 failure_reason 字段描述中断原因

#### Scenario: 重复启动幂等

- **GIVEN** 某会话的管线已在后台运行
- **WHEN** 因客户端重连/重试再次触发该会话的管线启动
- **THEN** 系统 SHALL NOT 重复启动管线，复用进行中的后台任务

#### Scenario: 后端重启后悬挂 running 会话处理

- **GIVEN** 后端进程重启前某会话 status=running（管线未持久化完成）
- **WHEN** 后端启动
- **THEN** 系统 SHALL 将无法恢复的 running 会话标记为 failed（MVP 策略）
- **AND** SHALL 在 failure_reason 中记录"后端重启，管线无法恢复"
- **AND** 客户端切回该会话时显示失败状态而非误认为仍在运行

### Requirement: 管线超时与中断检测

系统 SHALL 对深度分析管线实施全局超时机制：管线启动后超过最大执行时间未完成时，系统 SHALL 将会话标记为 failed 并记录超时原因。会话 status 更新为 failed 时 SHALL 持久化 `failure_reason` 字段，使客户端能展示具体中断原因而非笼统的"可能已中断"。默认最大执行时间从 10 分钟上调为 40 分钟，以匹配 LLM 端点的生成耗时方差（实测单节点 3.7~15.7 分钟，R1+R2 合法双轮最坏约 32 分钟）。
(Previously: 默认最大执行时间 10 分钟)

超时判定 SHALL 基于管线（图）的真实完成状态而非事件流的排空进度：预算耗尽时，若图已完成（交付物已生成或图流已结束），系统 SHALL 按完成路径处置（交付成果、正常终态），MUST NOT 判超时失败——事件排空延迟（明细洪峰滞后）不构成超时。仅当图自身在预算内确实未完成时才判超时。

#### Scenario: 管线全局超时

- GIVEN 某会话的深度分析管线已启动并在后台运行
- WHEN 管线执行时间超过配置的最大执行时间（默认 40 分钟）
- THEN 系统 SHALL 终止管线执行
- AND SHALL 将会话 status 更新为 failed
- AND SHALL 在 failure_reason 中记录"管线执行超时"

#### Scenario: 预算耗尽但管线已完成不得判超时

- GIVEN 深度分析管线在预算内已完成并生成报告，但事件流排空存在分钟级滞后（明细洪峰）
- WHEN 全局预算（如 2400 秒）在事件排空完成前耗尽
- THEN 系统 SHALL 按完成路径处置：交付已生成的报告、正常终态、会话状态流转 completed
- AND MUST NOT 判超时失败，MUST NOT 生成"管线执行超时"的失败语义
- AND 缓冲的明细事件 SHALL 允许压缩丢弃以使终态有界送达（见 session-streaming「终态事件优先送达」）

#### Scenario: 环境变量覆盖默认

- GIVEN 部署方设置了 PIPELINE_TIMEOUT_SECONDS 环境变量（如 "600"）
- WHEN 管线执行时间超过该环境变量配置的值
- THEN 超时判定 SHALL 以环境变量配置为准（默认值仅作未配置时兜底）

#### Scenario: 管线异常中断原因持久化

- GIVEN 管线执行过程中发生异常（数据拉取失败、LLM 调用失败、节点异常等）
- WHEN 异常导致管线中止
- THEN 系统 SHALL 将会话 status 更新为 failed
- AND SHALL 在 failure_reason 中记录异常类型与摘要信息
- AND 客户端切回该会话时 SHALL 通过 GET /api/sessions/{id} 获取 failure_reason 并展示

#### Scenario: 前端轮询展示中断原因

- **GIVEN** 客户端切回一个 status=failed 的会话
- **WHEN** 前端通过轮询获取到会话详情
- **THEN** 前端 SHALL 展示 failure_reason 中的具体中断原因
- **AND** SHALL NOT 仅显示笼统的"管线可能已中断"

### Requirement: LLM 调用错误传播

系统 SHALL 确保 LLM 调用失败时正确传播异常而非静默吞没：`chat_stream` 在重试耗尽后 SHALL raise 异常，使 Agent 主循环能捕获并产生 ERROR 事件，而非将错误文本作为正常回复 yield。

#### Scenario: LLM 重试耗尽后 raise 异常

- **GIVEN** LLM 调用经过最大重试次数后仍然失败
- **WHEN** `chat_stream` 的最后一次重试抛出异常
- **THEN** `chat_stream` SHALL raise 该异常（而非 yield 包含错误文本的 LLMResponse）
- **AND** Agent 主循环 SHALL 捕获该异常并 yield ERROR 事件
- **AND** 管线 SHALL 将会话 status 更新为 failed 并记录 failure_reason

#### Scenario: LLM 错误不产生静默失败

- **GIVEN** Agent 正在执行深度分析管线
- **WHEN** 某个节点的 LLM 调用失败且重试耗尽
- **THEN** 系统 SHALL NOT 将错误文本作为 LLM 的正常最终回复处理
- **AND** SHALL NOT 在未执行分析的情况下静默结束管线

### Requirement: SSE 流心跳保护

系统 SHALL 为所有 SSE 流（包括 fast path 和 ReAct 路径）发送心跳注释，防止代理/浏览器因空闲超时断开连接。心跳 SHALL 在管线长时间无事件输出时周期性发送。

#### Scenario: ReAct 路径 SSE 心跳

- **GIVEN** 用户通过自然语言触发深度分析，SSE 流已建立
- **WHEN** 管线执行期间连续 N 秒（默认 15 秒）无事件输出
- **THEN** 系统 SHALL 发送 SSE 心跳注释（`: heartbeat\n\n`）
- **AND** 心跳 SHALL NOT 干扰正常事件流的解析

#### Scenario: fast path SSE 心跳（保持现有行为）

- **GIVEN** 用户通过 fast path 触发深度分析
- **WHEN** 管线执行期间 SSE 事件队列空闲
- **THEN** 系统 SHALL 继续发送心跳注释（与当前行为一致）

### Requirement: 节点生命周期真实时间戳

系统 SHALL 在图节点的真实生命周期入口与出口发出带后端时间戳的事件，使前端节点计时反映真实执行耗时（而非 updates 流的 chunk 到达间隔）。

#### Scenario: 节点入口发出 node_start 生命周期事件

- **GIVEN** 深度分析管线执行某图节点
- **WHEN** 节点函数开始执行（入口）
- **THEN** 后端 SHALL 通过 custom 流发出 `{"type": "node_start", "node": <node_id>, "ts": <epoch_ms>}`
- **AND** ts 为节点入口的真实后端时间戳

#### Scenario: 节点出口发出 node_end 生命周期事件

- **GIVEN** 某图节点正在执行
- **WHEN** 节点函数执行完成（出口，返回状态更新前）
- **THEN** 后端 SHALL 通过 custom 流发出 `{"type": "node_end", "node": <node_id>, "ts": <epoch_ms>, "duration_ms": <ms>}`
- **AND** duration_ms 为该节点真实执行耗时（出口 ts - 入口 ts），>= 0

#### Scenario: 全部图节点统一包裹计时

- **GIVEN** 5 层管线图构建
- **THEN** 全部 22 个图节点 SHALL 通过统一的计时装饰器包裹（在 add_node 注册处应用）
- **AND** 装饰器 SHALL NOT 改变节点的签名、返回值与业务逻辑

#### Scenario: 并行分析师时间戳归属正确

- **GIVEN** Layer I 的 4 个分析师经 Send 扇出并行执行
- **WHEN** 各分析师发出 node_start/node_end 生命周期事件
- **THEN** 每个事件的 node 字段 SHALL 正确对应该分析师节点名
- **AND** 各分析师的 ts 互不串扰

#### Scenario: node_start 事件附加入口时间戳

- **GIVEN** 节点的 custom node_start（真实入口）已先于 updates chunk 到达
- **WHEN** 后端为该节点发出 node_start 事件（updates chunk 首次出现时）
- **THEN** node_start 事件 SHALL 附加 `server_start_ts`（后端真实入口时间戳）
- **AND** 该字段 SHALL 透传至 SSE 事件供前端用于"当前节点已运行时长"

#### Scenario: node_end 到达时下发 node_timing 事件

- **GIVEN** 某节点的 custom node_end（真实出口 + duration）到达
- **WHEN** 该节点的 node_complete 可能已先于 node_end 发出（updates chunk 驱动）
- **THEN** 后端 SHALL 额外下发 `node_timing` SSE 事件，携带 `node_id`、`server_start_ts`、`server_end_ts`、`server_duration_ms`
- **AND** 前端 SHALL 用 node_timing 的真实耗时更新该节点的 durationMs（覆盖 updates 到达时刻的近似值）

#### Scenario: 快速节点计时真实

- **GIVEN** 某快速纯函数节点（如 check_cache）真实执行耗时 <1s
- **WHEN** 该节点完成
- **THEN** 前端节点计时 SHALL 显示基于 server_duration_ms 的真实耗时
- **AND** 真实耗时 <1s 时显示 "0:00" 属正确行为（区别于"恒为 0 的假象"）

### Requirement: 管线阻断终态可见化

管线在未产出最终报告的情况下正常结束（图流自然耗尽、无异常抛出）时——无论阻断来源是决策价位交叉校验门禁、勾稽校验 FAIL 还是其他前置门禁——系统 SHALL 将会话 status 更新为 failed 并持久化 `failure_reason`（含可归因的阻断来源描述），MUST NOT 使会话停留于 running 等待超时兜底。失败通知 SHALL 按执行路径走既有通道：fast path（`_run_graph_streaming`）经既有 SSE error 终态事件；ReAct 慢路径（run_deep_analysis 工具）经既有 TOOL_RESULT 通知（带 `pipeline_blocked` 标记，形态同该路径超时/异常先例）。

#### Scenario: 决策价位门禁阻断的会话终态

- **GIVEN** risk_judge 的决策价位交叉校验打回重试后仍存在 anomaly（price-level-tooling 门禁语义）
- **WHEN** 管线路由跳过 fund_manager 与 generate_report 正常结束
- **THEN** 会话 status SHALL 更新为 failed，failure_reason SHALL 含「决策价位交叉校验」阻断描述
- **AND** fast path 的 SSE 流 SHALL 下发 error 类型终态事件；ReAct 慢路径 SHALL 下发带 `pipeline_blocked` 标记的 TOOL_RESULT 通知
- **AND** 会话 MUST NOT 写入报告产物，MUST NOT 落决策结算日志（final_trade_decision 未经审批）

#### Scenario: 勾稽校验 FAIL 的会话终态

- **GIVEN** validate_financials 判定硬等式不通过（validation_result=FAIL）路由至 END
- **WHEN** 管线在未产出报告的情况下正常结束
- **THEN** 会话 status SHALL 更新为 failed，failure_reason SHALL 含勾稽校验阻断描述
- **AND** 会话 MUST NOT 停留于 running 直至管线超时

#### Scenario: 正常完成路径不受影响

- **GIVEN** 管线正常产出最终报告
- **WHEN** 图流结束且报告已下发
- **THEN** 会话 status SHALL 照常更新为 completed，MUST NOT 被误标 failed

### Requirement: 批量分析进度复用

选股页触发的批量深度分析 SHALL 为每个标的创建独立 session 并复用现有 SSE 流式进度（终态事件优先送达等韧性纪律不变）；批量并发上限默认 2，超出部分排队。

#### Scenario: 并发上限

- **GIVEN** 精选池 5 只标的，并发上限 2
- **WHEN** 触发批量深度分析
- **THEN** 同时进行中的分析 ≤ 2，其余处于排队态并在前端展示排队标识

