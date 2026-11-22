# Session Streaming Specification

> 来源：change `resume-stream-on-session-switch`（会话切换时流式恢复）
> 基线构建日期：2026-08-01
> 说明：本 spec 定义 Agent 生成任务生命周期与 HTTP/SSE 连接解耦后的会话流式行为契约，包括事件日志、断线重放、中断持久化与单飞控制。

## Purpose

定义后端会话流式推送的行为契约：将 Agent 生成任务的生命周期与 HTTP/SSE 连接解耦，确保客户端断开连接不终止生成；通过事件日志支持断线重放；提供恢复端点从指定序号续传事件流；在任务取消或异常时兜底持久化部分输出；保证同一会话同一时间至多一个活跃生成任务。
## Requirements

---

### Requirement: Decoupled Generation Lifecycle

系统 SHALL 将 Agent 生成任务的生命周期与 HTTP/SSE 连接解耦：`POST /api/analyze` 与 `POST /api/chat` 收到请求后以后台任务（`asyncio.create_task`）运行生成逻辑，任务持有者为 session 而非连接；SSE 响应仅作为 per-session 事件流的订阅转发者。客户端断开连接 SHALL NOT 终止生成任务。

#### Scenario: 客户端断开后生成继续

- **GIVEN** 某会话的生成任务正在运行，SSE 连接已建立
- **WHEN** 客户端断开该 SSE 连接（切换会话、关闭面板等）
- **THEN** 生成任务继续运行直至完成、出错或被显式取消
- **AND** 断开期间产生的事件继续写入事件日志
- **AND** 任务完成后 session 状态正常流转（completed / clarifying），不残留 running

#### Scenario: 断开不丢失已生成内容

- **GIVEN** 生成任务已输出部分 assistant 回复
- **WHEN** 客户端断开且任务随后正常完成
- **THEN** assistant 回复完整落库 chat_history
- **AND** 重新加载会话可看到完整回复

### Requirement: Stream Event Journal

系统 SHALL 为每个会话维护事件日志表 `session_events`，每个 SSE 事件在投递给订阅者之前以 session 内单调递增的 `seq` 落库。事件日志是断线重放的事实源，不依赖进程内存。删除会话时 SHALL 级联删除其事件。

#### Scenario: 事件按序落库

- **GIVEN** 生成任务运行中
- **WHEN** 产生任意 SSE 事件（chat_token / report_chunk / node_start 等）
- **THEN** 事件以 `(session_id, seq)` 唯一约束写入 session_events，seq 在该会话内从 1 单调递增
- **AND** 写入成功后才 fan-out 给实时订阅者

#### Scenario: 重启后历史事件可重放

- **GIVEN** 服务进程重启，内存中的订阅关系丢失
- **WHEN** 客户端经恢复端点请求某会话的事件流
- **THEN** 系统从 session_events 重放历史事件，不依赖内存状态

### Requirement: Resumable Stream Endpoint

系统 SHALL 提供 `GET /api/sessions/{id}/stream`，支持经 `after_seq` 查询参数或 `Last-Event-ID` 请求头从指定序号恢复事件流：先按 seq 升序重放历史事件，再接续实时事件。会话无活跃任务时，重放完成后 SHALL 下发终态事件（`interrupted` 或 `done`）并关闭流。

#### Scenario: 从断点重放并续传

- **GIVEN** 会话有活跃生成任务，事件日志已含 seq 1..N
- **WHEN** 客户端请求 `GET /api/sessions/{id}/stream?after_seq=K`（K < N）
- **THEN** 先按 seq 升序下发 seq > K 的历史事件
- **AND** 随后无缝接续实时事件，不重复、不遗漏

#### Scenario: 恢复已中断的会话

- **GIVEN** 会话 status 为 interrupted，无活跃任务
- **WHEN** 客户端请求恢复端点
- **THEN** 重放全部历史事件后下发 `{"type":"interrupted"}` 终态事件并关闭流
- **AND** 前端据此展示"输出已中断，可追问继续"

#### Scenario: 慢订阅者不阻塞生成

- **GIVEN** 某订阅者的消费速度低于事件产生速度
- **WHEN** 其订阅队列达到容量上限
- **THEN** 系统断开该订阅者连接
- **AND** 生成任务与其他订阅者不受影响
- **AND** 被断开的订阅者可经 after_seq 重连追平

### Requirement: Graceful Interruption Persistence

系统 SHALL 在生成任务被取消或异常终止时兜底持久化：collector 已收集的部分 assistant 回复落库（内容标注输出中断，保留 thinking 与 tool_calls），session status 置为 `interrupted`，事件日志追加 interrupted 终态事件。chat_history 中 SHALL NOT 出现无 assistant 回复的悬空 user 消息。

#### Scenario: 显式取消后部分输出可见

- **GIVEN** 生成任务运行中，已输出部分回复
- **WHEN** 收到取消请求或任务被取消
- **THEN** 部分回复作为 assistant 消息落库并标注中断
- **AND** session status 变为 interrupted
- **AND** 重新加载会话可见该半截回复及中断标记

#### Scenario: 服务启动时状态 reconcile

- **GIVEN** 数据库中存在 status 为 running 的会话（上次进程退出残留）
- **WHEN** 服务启动
- **THEN** 这些会话的 status 被置为 interrupted
- **AND** 列表接口不再将已死任务展示为运行中

### Requirement: Session Single-Flight and Explicit Cancel

系统 SHALL 保证同一会话同一时间至多一个活跃生成任务：会话存在活跃任务时，`POST /api/analyze` 与 `POST /api/chat` 返回 409 `session_busy`。系统 SHALL 提供 `POST /api/sessions/{id}/cancel` 显式取消该会话的活跃任务，取消走 Graceful Interruption Persistence 路径。任务结束时（完成、出错、取消）SHALL 从 registry 注销，不残留泄漏任务。

#### Scenario: 运行中拒绝新消息

- **GIVEN** 会话 A 有活跃生成任务
- **WHEN** 对会话 A 再次发起 analyze 或 chat 请求
- **THEN** 返回 HTTP 409 及 `{"error":"session_busy"}`
- **AND** 不创建第二个任务，chat_history 不追加新 user 消息

#### Scenario: 显式取消运行中的任务

- **GIVEN** 会话 A 有活跃生成任务
- **WHEN** 调用 `POST /api/sessions/A/cancel`
- **THEN** 任务被取消，走中断兜底持久化
- **AND** 订阅者收到 interrupted 终态事件
- **AND** 之后可对该会话正常发起新消息

#### Scenario: 任务结束必然注销

- **GIVEN** 生成任务完成、异常或被取消
- **WHEN** 任务退出
- **THEN** registry 中该会话的任务句柄被移除
- **AND** 后续请求不受 single-flight 限制

### Requirement: Terminal Event Per-Run Deduplication

系统 SHALL 对终态事件（done/interrupted/error）实施 per-run 去重：同一轮运行（单次 `StreamRegistry.start()` 创建的 `SessionStream` 生命周期）内，首个终态事件正常写入 journal 并 fan-out 给订阅者；同轮内后续终态事件被丢弃（返回 0）。去重作用域 SHALL 为单次运行的内存标志，SHALL NOT 跨越同一会话的多轮追问--每轮运行的终态事件独立发送。

#### Scenario: 同一轮运行内重复终态事件去重

- **GIVEN** 某会话的生成任务正在运行，生成逻辑已显式发布 `done` 事件
- **WHEN** `_run_task` 正常完成后自动发布 `done` 事件
- **THEN** 第二个 `done` 被去重丢弃（返回 0），不写入 journal、不 fan-out
- **AND** 订阅者仅收到一个 `done` 事件

#### Scenario: 多轮追问每轮独立发送终态事件

- **GIVEN** 会话第一轮生成已完成，journal 中存在第一轮的 `done` 事件
- **WHEN** 用户追问发起第二轮生成，第二轮正常完成并发布 `done` 事件
- **THEN** 第二轮的 `done` 事件 SHALL 正常写入 journal 并 fan-out 给订阅者
- **AND** 前端收到第二轮的 `done` 事件并据此清除流式游标

#### Scenario: 第二轮中断事件正常下发

- **GIVEN** 会话第一轮生成已完成，journal 中存在第一轮的 `done` 事件
- **WHEN** 用户追问发起第二轮生成，用户在第二轮运行中点击停止按钮
- **THEN** 第二轮的 `interrupted` 事件 SHALL 正常写入 journal 并 fan-out 给订阅者
- **AND** 前端收到 `interrupted` 事件并据此清除流式状态、回到可追问态

#### Scenario: 第二轮 error 事件正常下发

- **GIVEN** 会话第一轮生成已完成，journal 中存在第一轮的 `done` 事件
- **WHEN** 用户追问发起第二轮生成，第二轮生成过程中发生异常
- **THEN** 第二轮的 `error` 事件 SHALL 正常写入 journal 并 fan-out 给订阅者
- **AND** 前端收到 `error` 事件并据此展示错误信息、清除流式状态

### Requirement: 终态事件优先送达

系统 SHALL 为终态事件（`done` / `report_ready` / `error` / `interrupted`）提供独立于明细事件（thinking_token 等）批量队列的发布路径。终态落库 SHALL 带瞬态重试；终态发布前 SHALL 仅冲刷有界的 pending 明细缓冲（单事务）以保持 `(session_id, seq)` 单调，SHALL NOT 等待未落库的历史明细洪峰排空；会话状态（`sessions.status`）的终态流转 SHALL 与终态事件发布同窗完成且带重试。终态事件的落库与发布 MUST NOT 因明细事件积压被无限期推迟。既有「先落库后 fan-out」与终态 per-run 去重语义 SHALL 保持不变。

#### Scenario: 明细洪峰下终态仍及时落库发布

- **GIVEN** 单会话存在数万 thinking_token 明细的积压（journal 写入或 SSE 消费滞后分钟级）
- **WHEN** 管线完成并发布 `done`
- **THEN** `done` 在 pending 明细缓冲冲刷（单事务）的有界时间内完成落库并获得 seq
- **AND** 订阅者立即可收到 `done`，不依赖积压明细排空
- **AND** `sessions.status` 在同一窗口内流转为 completed（带重试）

#### Scenario: 重连客户端不依赖积压排空即见终态

- **GIVEN** 终态已落库而部分明细事件永久丢失（incident 037 场景）
- **WHEN** 新客户端经 `GET /api/sessions/{id}/stream?after_seq` 重连
- **THEN** 重放后 SHALL 下发终态事件并关闭流
- **AND** 不存在「会话实际完成但任何界面都无法看到完成」的中间态

#### Scenario: 终态去重不因优先路径失效

- **GIVEN** 某轮分析的终态已发布
- **WHEN** 同轮再次发布终态事件
- **THEN** per-run 终态去重（CAS）仍然生效，不产生重复终态

#### Scenario: 图完成后工具消费端压缩缓冲明细

- **GIVEN** ReAct 路径 `run_deep_analysis` 的消费循环中，图流已完成但事件队列仍积压大量 thinking_token 明细
- **WHEN** 图完成信号到达消费循环
- **THEN** 队列中缓冲的 thinking_token 明细 SHALL 允许压缩丢弃（节点边界/工具/终态事件保留），使终态 TOOL_RESULT 在图完成后的有界时间内产生
- **AND** 超时看门狗据此获得图的真实完成状态，不得将排空延迟误判为超时

### Requirement: 发布积压可观测与背压

(Previously: 事件队列满时按事件类别分级处置——thinking 明细可丢弃并计数，节点边界/工具/终态事件 MUST NOT 因队列满被丢弃、以有界等待保证落库与 fan-out；积压信号含队列深度、pending 缓冲深度与丢弃计数，可经日志或指标查询。)

事件发布链路 SHALL 暴露积压可观测信号：事件队列深度、pending 明细缓冲深度、明细丢弃计数（日志 + 可查询指标）。事件队列满时 SHALL 按事件类别分级处置：thinking_token 明细 SHALL 允许丢弃且每次丢弃 MUST 计数并记录日志（含 session_id）；节点边界（`node_start` / `node_complete`）、工具与搜索（`tool_call` / `tool_result` / `search`）、终态（`done` / `report_ready` / `error` / `interrupted`）事件 MUST NOT 因队列满被丢弃，MUST 保证其落库与 fan-out（必要时阻塞生产端而非丢弃）。无计数、无日志的静默丢弃 MUST NOT 存在。

发布链路 SHALL 具备连续投递失败熔断降级：同一会话连续 N 次（默认 3）undeliverable（有界等待超时）后，非终态事件 SHALL 降级为立即丢弃并计数（计入 `breaker_degraded` 桶），MUST NOT 再逐个执行有界等待；终态事件与流终结哨兵 SHALL 保留有界等待入队路径，MUST NOT 被熔断丢弃；任一事件成功入队 SHALL 重置连续失败计数。被熔断丢弃的每次事件 MUST 计数并记录日志，MUST NOT 静默丢弃。

积压状态 SHALL 提供运维查询出口：经只读运维端点（`GET /api/v1/ops/stream-backlog`）返回全部活跃会话的订阅者队列深度、订阅者数、丢弃计数与最后 seq，使队列深度与丢弃规模可事后审计。

#### Scenario: 队列满时明细被丢弃且可观测

- **GIVEN** 事件队列已满（消费速率低于生产速率）
- **WHEN** thinking_token 明细事件到达
- **THEN** 该事件被丢弃，丢弃计数递增并输出含 session_id 的日志
- **AND** 明细稀疏不产生前端 seq 空洞卡死（既有 resync 语义覆盖）

#### Scenario: 队列满时边界与终态事件不被丢弃

- **GIVEN** 事件队列已满
- **WHEN** `node_complete` 或终态事件到达
- **THEN** 事件 MUST NOT 被静默丢弃：生产端以有界等待入队，事件最终落库并 fan-out
- **AND** `(session_id, seq)` 单调性保持

#### Scenario: 连续 undeliverable 触发熔断降级

- **GIVEN** 消费端长期停滞，同一会话已连续 3 次事件入队超时（undeliverable）
- **WHEN** 第 4 个非终态事件到达
- **THEN** 该事件立即丢弃并计入 `breaker_degraded` 桶（含日志），MUST NOT 再等待有界超时
- **AND** 终态事件或流终结哨兵到达时仍保留有界等待入队路径

#### Scenario: 成功入队重置熔断计数

- **GIVEN** 连续 2 次 undeliverable 后，消费者恢复、下一事件成功入队
- **WHEN** 此后再发生 1 次 undeliverable
- **THEN** 熔断不触发（连续计数已重置，未达阈值）

#### Scenario: 积压状态可查询

- **GIVEN** 单会话事件队列或 pending 缓冲深度超过阈值
- **THEN** 队列深度与明细丢弃计数可经日志或指标查询，丢弃规模可事后审计
- **AND** `GET /api/v1/ops/stream-backlog` 返回活跃会话的订阅者队列深度、丢弃计数与 last_seq

### Requirement: 会话状态流转单一 owner

会话状态流转（clarifying→running→completed/failed/interrupted）SHALL 经统一生命周期模块（`sessions/lifecycle`）执行；api/pipeline 层 SHALL NOT 直接调用 session_store 的状态 setter 手写 transition（读取状态做订阅恢复语义的 stream_registry 除外）。流转产生的事件（session_created / 终态事件）与既有发布语义 SHALL 保持不变；终态事件优先送达契约（本文件既有 Requirement）不变。前端消费的事件流 SHALL 零变化。

#### Scenario: 分析启动一段式

- **GIVEN** 任一分析入口（/api/analyze、screening_analyze、events_watchlist_analyze）
- **WHEN** 发起新分析
- **THEN** 会话创建、chat 落库、pipeline anchor、clarify→running 与 session_created 事件 SHALL 由 `begin_analysis` 一次调用完成
- **AND** 三个入口的启动序列 SHALL 无逐行重复实现

#### Scenario: 终态流转统一入口

- **GIVEN** 分析管线到达任一终态（completed/failed/interrupted）
- **WHEN** 状态流转
- **THEN** SHALL 经 `mark_complete / mark_failed / mark_interrupted` 执行
- **AND** 落库、终态事件写入与既有重试语义不变

