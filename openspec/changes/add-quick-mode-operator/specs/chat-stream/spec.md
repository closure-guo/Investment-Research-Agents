## ADDED Requirements

### Requirement: 快速通道系统操作工具事件

AG-UI 快速通道 SHALL 支持系统操作工具的 `TOOL_CALL_START` / `TOOL_CALL_ARGS` / `TOOL_CALL_END` / `TOOL_CALL_RESULT` 事件序列，且这些事件 MUST 由模型真实发起、由真实工具执行产生。工具结果 SHALL 为紧凑摘要（含必要的会话标识与状态），MUST NOT 内含管线时间线、报告全文或未裁剪的大对象。系统操作工具 MUST NOT 改变「双轨隔离」：深度分析管线事件仍 MUST NOT 经 AG-UI 通道传输。

#### Scenario: 派单工具事件序列

- **GIVEN** 快速模式已注册 `start_stock_analysis`
- **WHEN** 用户请求分析一只已确认的股票
- **THEN** AG-UI 流中出现该工具的 `TOOL_CALL_START` → `TOOL_CALL_ARGS` → `TOOL_CALL_END` → `TOOL_CALL_RESULT`
- **AND** `TOOL_CALL_RESULT` 含 `session_id`、`display_name`、`status`，不含管线节点或报告内容
- **AND** 流以 `RUN_FINISHED` 正常终止

#### Scenario: 管线事件双轨隔离

- **GIVEN** 派单创建了独立 analysis 会话
- **WHEN** 该会话的管线开始运行
- **THEN** 快速会话的 AG-UI 流中不出现 `node_start` / `node_complete` / 报告事件
- **AND** analysis 会话继续使用既有 journal/SSE 通道

#### Scenario: 工具失败不伪造结果

- **GIVEN** 系统操作工具执行失败
- **WHEN** 工具结果返回
- **THEN** 结果显式标注错误原因
- **AND** 不出现成功回执字段（如伪造的 `session_id`）

### Requirement: 快速通道只读查询与跨轮写确认

AG-UI 快速通道 SHALL 支持只读系统查询工具的调用与结果事件，结果 MUST 为紧凑摘要。写操作的两步确认 SHALL 跨两个 run 完成：首个 run 返回预览与 token（不产生副作用），用户回复确认词后的下一个 run 携带同一 token 执行；token SHALL 由服务端按 session 保存与校验，MUST NOT 仅依赖对话文本。写操作仍 MUST NOT 把管线事件、报告或节点时间线经 AG-UI 传输。

#### Scenario: 只读查询事件

- **GIVEN** 快速模式已注册只读工具
- **WHEN** 用户询问系统状态
- **THEN** 事件流包含该工具的 `TOOL_CALL_*` 与 `TOOL_CALL_RESULT`
- **AND** 结果只含紧凑摘要

#### Scenario: 跨轮确认

- **GIVEN** 用户请求一个写操作
- **WHEN** 第一个 run 结束
- **THEN** 只返回预览与确认提示，无副作用
- **AND** 用户回复确认词后的下一个 run 可凭 token 执行
- **AND** 未经确认的第二个 run MUST 被拒绝

#### Scenario: 写操作仍不夹带管线事件

- **GIVEN** 写操作触发后台日批或分析
- **WHEN** 快速 run 继续流式输出
- **THEN** 快速通道只出现对话与工具事件
- **AND** 后台任务进度经各自既有通道呈现
