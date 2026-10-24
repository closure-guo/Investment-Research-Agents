## MODIFIED Requirements

### Requirement: 前端设置面板支持自定义 LLM 配置

系统 SHALL 在前端提供设置面板（LLM 配置），按「连接 / 模型 / 高级」三段组织字段：连接段含 Provider 预设、API Base URL、API Key 与连通性测试；模型段含模型名称与自动发现、上下文窗口、最大输出、思考模式；高级段含 API 形式与多 profile 管理。API Key 输入框 SHALL 为只写语义：已保存密钥时输入框保持为空并提示「已保存（不回显）」，输入新值即替换，且 SHALL 提供显示/隐藏切换。API Base URL 非空时 MUST 匹配 `http(s)://`；上下文长度非空时 MUST 为正整数；非法值 SHALL 在对应字段下方以红字提示并阻止保存。配置持久化到浏览器 localStorage（`fa_llm_profiles` / 激活 profile），既有旧 key 迁移、占位符默认值与多 profile 管理语义保持不变。

(Previously: 系统 SHALL 在前端提供设置面板（LLM 配置），允许用户配置 model、base_url、api_key、thinking 等参数；面板展示 API Key（密码输入框）、模型名称、API Base URL、思考模式四项；已保存的 API Key 会回填进密码输入框。)

#### Scenario: 设置面板展示三段字段

- **GIVEN** 用户进入设置中心页「LLM 配置」分区
- **THEN** 连接段展示 Provider 预设、API Base URL、API Key、测试连接
- **AND** 模型段展示模型名称、上下文窗口、最大输出、思考模式（仅 DeepSeek 模型）
- **AND** 高级段折叠展示 API 形式与配置管理

#### Scenario: 已保存密钥不回显

- **GIVEN** 激活 profile 已保存 API Key
- **WHEN** 用户进入 LLM 配置分区
- **THEN** API Key 输入框的值为空
- **AND** 输入框提示「已保存（不回显）」
- **AND** 用户不修改密钥直接保存时，原密钥保持不变

#### Scenario: 密钥显示切换

- **GIVEN** 用户在 API Key 输入框输入了新密钥
- **WHEN** 点击眼睛按钮
- **THEN** 输入框在明文与掩码之间切换

#### Scenario: 非法字段就地报错

- **GIVEN** 上下文长度输入 `0` 或 `abc`，或 API Base URL 输入 `ftp://x`
- **WHEN** 用户点击保存
- **THEN** 对应字段下方显示红字错误
- **AND** 不写入 localStorage、不触发保存回调

#### Scenario: 配置持久化到 localStorage

- **WHEN** 用户修改合法配置并保存
- **THEN** 系统 SHALL 将配置序列化存入 `fa_llm_profiles` 的激活 profile
- **AND** 页面刷新后重新进入「LLM 配置」分区时，SHALL 恢复上次保存的配置

#### Scenario: 设置面板占位符显示后端默认值

- **WHEN** 设置面板首次加载（无已保存配置）
- **THEN** 模型名称输入框的 placeholder SHALL 显示后端默认模型
- **AND** API Base URL 输入框的 placeholder SHALL 显示后端默认 base_url
- **AND** 思考模式开关 SHALL 默认设为后端默认值

#### Scenario: 设置面板向后兼容现有 API Key

- **WHEN** localStorage 中存在旧 key `fa_api_key` 且无已保存 profile
- **THEN** 系统 SHALL 自动迁移：将 `fa_api_key` 的值读入首个 profile 的 api_key，并清除旧 key

#### Scenario: 无 profile 强制配置跳转设置页

- **WHEN** 用户尝试发起分析/聊天但无任何 LLM profile
- **THEN** 系统 SHALL 阻止发送并跳转 `/settings` 设置中心页，定位到「LLM 配置」分区
- **AND** 用户保存配置创建首个 profile 后方可发起分析
