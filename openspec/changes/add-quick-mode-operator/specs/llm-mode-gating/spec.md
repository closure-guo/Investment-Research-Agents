## MODIFIED Requirements

### Requirement: 模式入口按 capability 门禁

前端 SHALL 按 capability 禁用不满足要求的模式入口并显示原因：`tool_call=false` 的 profile 禁用深度 ReAct（提示可切换 profile 或使用快速模式）；管线结构化入口在 `json_output=false` 时显示降级提示（JSON 输出不可用，结构化节点质量可能下降），不禁用入口。门禁 SHALL 消费 probe 事实（probe 优先于静态声明）。被禁用入口 MUST 显示禁用原因，不得静默隐藏。

快速模式入口 SHALL 在 `stream` 与 `non_stream` 均为 false 时禁用。`tool_call=false` 时快速模式 SHALL 仍允许纯对话入口，但 MUST 在入口或发送前显式披露「当前模型不支持工具调用，系统操作不可用」，且系统操作工具 MUST NOT 以可执行状态注册或 MUST 在执行前被拦截。系统 MUST NOT 静默失败或让模型误以为已完成派单。
(Previously: 快速模式仅在 `stream` 与 `non_stream` 均为 false 时禁用；`tool_call=false` 未对快速模式的系统操作能力作任何披露，因为快速模式当时只有 `web_search` 且不构成系统操作入口)

#### Scenario: 无工具能力禁用深度模式

- **GIVEN** 当前 profile probe 事实 tool_call=false
- **WHEN** 用户尝试进入深度 ReAct 模式
- **THEN** 入口呈禁用态并显示「该 provider 不支持工具调用」及可行动建议

#### Scenario: probe 修正静态声明

- **GIVEN** 静态 capability 声明 tools!=none 但 probe 事实 tool_call=false
- **WHEN** 渲染模式入口
- **THEN** 门禁按 probe 事实（false）判定，而非静态声明

#### Scenario: 无工具能力时快速模式仍可聊天但披露系统操作不可用

- **GIVEN** 当前 profile probe 事实 `tool_call=false`、`stream=true`
- **WHEN** 用户进入快速模式
- **THEN** 快速模式入口可用
- **AND** 入口或发送前可见「当前模型不支持工具调用，系统操作不可用」的披露
- **AND** 用户请求分析股票时不创建分析会话、不产生伪造派单回执
