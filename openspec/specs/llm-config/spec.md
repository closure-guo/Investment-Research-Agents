# llm-config Specification

## Purpose

定义请求级 LLM 配置注入契约：前端设置面板（自定义 model / base_url / api_key / thinking / apiForm）、`/api/llm-config` 默认配置端点、Provider 预设快捷填充、模型自动发现、连通性测试与多 profile 管理。后端 SHALL 以请求级配置覆盖环境变量默认值，并据此映射 litellm 调用的 `api` 参数与模型前缀。
## Requirements
### Requirement: 前端设置面板支持自定义 LLM 配置

系统 SHALL 在前端提供设置面板（LLM 配置），允许用户配置 model（模型名称）、base_url（API 端点）、api_key（API 密钥）、thinking（思考模式开关）等 LLM 参数，配置持久化到浏览器 localStorage。设置面板 SHALL 位于设置中心页 `/settings` 的「LLM 配置」分区内（由 SettingsModal 弹窗整体迁移而来），通过左侧垂直导航访问；配置字段、Provider 预设、模型自动发现、连通性测试与多 profile 管理行为语义不变。

#### Scenario: 设置面板展示四项配置字段

- **GIVEN** 用户进入设置中心页「LLM 配置」分区（原 SettingsModal 弹窗整体迁移至此，header 齿轮/侧栏入口改为跳转设置页）
- **THEN** 面板 SHALL 展示四个配置项：API Key（密码输入框）、模型名称（文本输入框）、API Base URL（文本输入框）、思考模式（Toggle 开关，enabled/disabled）

#### Scenario: 配置持久化到 localStorage

- **WHEN** 用户在设置面板修改任意配置项并保存
- **THEN** 系统 SHALL 将配置序列化为 JSON 存入浏览器 localStorage（`fa_llm_profiles` / 激活 profile）
- **AND** 页面刷新后重新进入「LLM 配置」分区时，SHALL 恢复上次保存的配置

#### Scenario: 设置面板占位符显示后端默认值

- **WHEN** 设置面板首次加载（无已保存配置）
- **THEN** 模型名称输入框的 placeholder SHALL 显示后端默认模型（从 `GET /api/llm-config` 获取）
- **AND** API Base URL 输入框的 placeholder SHALL 显示后端默认 base_url
- **AND** 思考模式开关 SHALL 默认设为后端默认值

#### Scenario: 设置面板向后兼容现有 API Key

- **WHEN** localStorage 中存在旧 key `fa_api_key` 且无已保存 profile
- **THEN** 系统 SHALL 自动迁移：将 `fa_api_key` 的值读入首个 profile 的 api_key，并清除旧 key

#### Scenario: 无 profile 强制配置跳转设置页

- **WHEN** 用户尝试发起分析/聊天但无任何 LLM profile
- **THEN** 系统 SHALL 阻止发送并跳转 `/settings` 设置中心页，定位到「LLM 配置」分区
- **AND** 用户保存配置创建首个 profile 后方可发起分析

### Requirement: 请求携带 LLM 配置

前端向 `/api/chat`、`/api/analyze` 发送请求时 SHALL 携带 `llm_config` 字段（包含 model / base_url / api_key / thinking），后端 SHALL 以请求级配置覆盖环境变量默认值。

#### Scenario: 请求携带用户自定义配置

- **WHEN** 用户在设置面板配置了 model、base_url、api_key、thinking 并提交分析/聊天请求
- **THEN** 请求体 SHALL 包含 `llm_config` 字段（JSON 对象），包含用户配置的非空字段
- **AND** 后端 SHALL 使用 `llm_config` 中的值覆盖对应环境变量默认值

#### Scenario: 请求未携带 llm_config 时使用环境变量默认值

- **WHEN** 请求体不包含 `llm_config` 字段或该字段为 null
- **THEN** 后端 SHALL 使用环境变量（`LLM_MODEL`、`LLM_BASE_URL`、`LLM_API_KEY`、`LLM_THINKING`）的值作为 LLM 配置
- **AND** 系统行为 SHALL 与未引入此功能前完全一致

#### Scenario: llm_config 部分字段为空时回退环境变量

- **WHEN** 请求体 `llm_config` 中 model 字段有值但 base_url 为空
- **THEN** 后端 SHALL 使用 `llm_config.model` 的值作为模型名，base_url 回退到环境变量 `LLM_BASE_URL`

### Requirement: 后端提供默认配置端点

系统 SHALL 提供 `GET /api/llm-config` 端点，返回当前后端环境变量的 LLM 默认配置，供前端展示占位符和默认值。

#### Scenario: 获取默认配置

- **WHEN** 前端调用 `GET /api/llm-config`
- **THEN** 系统 SHALL 返回 JSON 对象，包含 `model`（环境变量 `LLM_MODEL` 的值或内置默认值）、`base_url`（环境变量 `LLM_BASE_URL` 的值或空字符串）、`thinking`（环境变量 `LLM_THINKING` 的值或 `"enabled"`）
- **AND** 响应 SHALL NOT 包含 api_key（安全考虑，不暴露密钥）

### Requirement: 管线链路支持请求级 LLM 配置

管线节点 LLM 调用（`call_llm` / `call_llm_stream` / `call_llm_with_tools`）SHALL 接受请求级 LLM 配置参数，覆盖环境变量中的 model / base_url / api_key / thinking。

#### Scenario: 管线节点使用请求级模型配置

- **WHEN** 分析请求携带 `llm_config.model = "openai/gpt-4o"` 并触发深度分析
- **THEN** 5 层分析流水线所有节点的 LLM 调用 SHALL 使用模型 `openai/gpt-4o`
- **AND** Langfuse trace 中的 generation span SHALL 记录实际使用的模型名

#### Scenario: 管线节点使用请求级 base_url

- **WHEN** 分析请求携带 `llm_config.base_url = "https://api.example.com/v1"`
- **THEN** 管线 LLM 调用 SHALL 通过 litellm 的 `api_base` 参数使用该端点

#### Scenario: 管线节点使用请求级思考模式

- **WHEN** 分析请求携带 `llm_config.thinking = "disabled"` 且模型为 DeepSeek 系列
- **THEN** 管线 LLM 调用 SHALL 在 `extra_body` 中设置 `{"thinking": {"type": "disabled"}}`
- **AND** 非 DeepSeek 模型 SHALL 忽略 thinking 配置（使用 temperature 参数）

#### Scenario: 管线节点使用请求级 api_key

- **WHEN** 分析请求携带 `llm_config.api_key = "sk-xxx"`
- **THEN** 管线 LLM 调用 SHALL 使用该 api_key 进行认证
- **AND** 若 `llm_config.api_key` 为空，SHALL 回退到环境变量 `LLM_API_KEY` 再回退 `DEEPSEEK_API_KEY`

### Requirement: ReAct Agent 链路支持请求级 LLM 配置

ReAct Agent（`build_agent` → `LiteLLMClient`）SHALL 接受请求级 LLM 配置参数，覆盖环境变量中的 model / base_url / api_key / thinking。

#### Scenario: ReAct Agent 使用请求级模型配置

- **WHEN** 聊天请求携带 `llm_config.model = "anthropic/claude-sonnet-4-20250514"` 并触发快速模式
- **THEN** ReAct Agent 的 LLM 调用 SHALL 使用模型 `anthropic/claude-sonnet-4-20250514`

#### Scenario: ReAct Agent 使用请求级 base_url

- **WHEN** 聊天请求携带 `llm_config.base_url = "http://localhost:11434/v1"`（本地 Ollama）
- **THEN** `LiteLLMClient` SHALL 通过 `api_base` 参数使用该端点

#### Scenario: ReAct Agent 使用请求级思考模式

- **WHEN** 聊天请求携带 `llm_config.thinking = "disabled"` 且模型为 DeepSeek 系列
- **THEN** `LiteLLMClient._build_kwargs` SHALL 在 `extra_body` 中设置 `{"thinking": {"type": "disabled"}}`
- **AND** 非 DeepSeek 模型 SHALL 忽略 thinking 配置（使用 temperature 参数）

### Requirement: 配置变更对 Langfuse trace 透明

LLM 配置的注入 SHALL 不改变 Langfuse trace 的结构——trace 中仅 model 字段反映实际使用的模型，其余 span 结构不变。

#### Scenario: trace 记录实际模型名

- **WHEN** 用户使用自定义模型 `openai/gpt-4o` 发起分析
- **THEN** Langfuse trace 的 generation span SHALL 记录 `model: "openai/gpt-4o"`
- **AND** span 的父子层级结构 SHALL 与使用默认模型时一致

### Requirement: Provider 预设快捷填充

设置面板 SHALL 提供 Provider 预设选择器，用户选择预设后自动填充 model 和 base_url 输入框，降低手动输入出错率。预设数据为前端静态常量，不需后端端点。

#### Scenario: 选择 DeepSeek 预设自动填充

- **WHEN** 用户在设置面板选择 "DeepSeek 官方" 预设
- **THEN** model 输入框 SHALL 自动填充为 `deepseek/deepseek-chat`
- **AND** base_url 输入框 SHALL 自动填充为 `https://api.deepseek.com/v1`
- **AND** 思考模式开关 SHALL 默认设为 enabled

#### Scenario: 选择 OpenAI 预设自动填充

- **WHEN** 用户在设置面板选择 "OpenAI" 预设
- **THEN** model 输入框 SHALL 自动填充为 `openai/gpt-4o`
- **AND** base_url 输入框 SHALL 留空（litellm 自动路由）
- **AND** 思考模式开关 SHALL 隐藏（非 DeepSeek 模型）

#### Scenario: 选择本地 Ollama 预设自动填充

- **WHEN** 用户在设置面板选择 "本地 Ollama" 预设
- **THEN** model 输入框 SHALL 自动填充为 `openai/llama3`
- **AND** base_url 输入框 SHALL 自动填充为 `http://localhost:11434/v1`

#### Scenario: 选择预设后用户仍可手动修改

- **WHEN** 用户选择预设后手动修改了 model 或 base_url 输入框中的值
- **THEN** 系统 SHALL 接受用户修改后的值，不强制覆盖
- **AND** 预设选择器 SHALL 切换为 "自定义" 状态

### Requirement: 模型自动发现

系统 SHALL 提供模型自动发现功能，用户填写 base_url 后可拉取该端点支持的模型列表。模型发现请求 SHALL 由后端代理调用（非前端直连），规避 CORS 和密钥暴露问题。用户从模型列表选择模型后自动拼接的前缀 SHALL 与后端 resolver 白名单一致：域名推导出的前缀不在白名单内时 MUST 回退 `openai/`（OpenAI 兼容端点语义），不得拼出后端必然拒绝的前缀。

#### Scenario: 成功拉取模型列表

- **WHEN** 用户填写了 base_url（如 `https://api.deepseek.com/v1`）和 api_key，点击"刷新模型列表"按钮
- **THEN** 前端 SHALL 调用 `POST /api/llm-config/models` 发送 base_url 和 api_key 到后端
- **AND** 后端 SHALL 调用 `GET {base_url}/models` 拉取可用模型列表
- **AND** 前端 SHALL 将返回的模型列表渲染为下拉选择

#### Scenario: 用户从模型列表选择模型后自动拼接前缀

- **WHEN** 用户从自动发现的模型下拉中选择 `deepseek-chat`（base_url 为 `https://api.deepseek.com/v1`）
- **THEN** model 输入框 SHALL 自动填充为 `deepseek/deepseek-chat`（litellm 前缀格式）

#### Scenario: 域名推导前缀不在白名单时回退 openai

- **WHEN** 用户从自动发现的模型下拉中选择 `kimi-k2-0905-preview`（base_url 为 `https://api.kimi.ai/v1`，域名主体推导出的前缀 `kimi` 不在后端 resolver 白名单）
- **THEN** model 输入框 SHALL 自动填充为 `openai/kimi-k2-0905-preview`（回退 OpenAI 兼容前缀，避免测试连接与解析必然失败）

#### Scenario: base_url 为空时不回退环境变量（决策 A）

- **WHEN** 用户点击"刷新模型列表"但 base_url 输入框为空（如选择 OpenAI/Anthropic/自定义预设时）
- **THEN** 后端 SHALL **NOT** 回退到环境变量 `LLM_BASE_URL` 拉取模型列表
- **AND** 系统 SHALL 返回空模型列表，并提示用户"请先配置 API Base URL 再刷新模型列表"
- **AND** 分析链路 `call_llm` 的回退行为 SHALL 不受影响（仍为 请求配置 → 环境变量 → 默认值）

#### Scenario: base_url 不支持模型列表端点时优雅降级

- **WHEN** 用户点击"刷新模型列表"但 base_url 不支持 `/models` 端点（如返回 404 或非标准格式）
- **THEN** 系统 SHALL 返回空模型列表并展示提示信息（"该端点不支持模型自动发现，请手动输入"）
- **AND** 用户 SHALL 仍能手动在 model 输入框中输入模型名

#### Scenario: 模型发现请求超时或网络错误

- **WHEN** 模型发现请求超时或网络不通
- **THEN** 系统 SHALL 返回错误状态并展示错误信息（如"连接超时，请检查 Base URL"）
- **AND** 错误 SHALL 不阻塞用户继续手动配置

### Requirement: 连通性测试

系统 SHALL 提供连通性测试功能，用户在保存配置前可验证 LLM 配置是否有效。测试请求 SHALL 由后端发送探测 LLM 请求验证，返回成功/失败结果及错误分类。配置类错误（如模型名携带未知 provider 前缀）SHALL 返回结构化失败响应（`success=false` + `errorType` + 人话错误信息），MUST NOT 以未捕获异常 500 裸栈响应。

#### Scenario: 测试成功

- **WHEN** 用户填写了完整 LLM 配置并点击"测试连接"按钮
- **THEN** 后端 SHALL 使用该配置发送探测 LLM 请求
- **AND** 若请求成功，前端 SHALL 展示成功状态（绿色）和响应延迟（毫秒）

#### Scenario: 测试失败返回错误分类

- **WHEN** 用户点击"测试连接"但配置无效（如 API key 错误、base_url 不通、模型名不存在）
- **THEN** 前端 SHALL 展示失败状态（红色）和错误信息
- **AND** 系统 SHALL 返回错误分类（`auth` / `network` / `model_not_found` / `unknown`）便于前端展示针对性提示

#### Scenario: 认证失败时提示检查 API Key

- **WHEN** 连通性测试返回 `error_type: "auth"`
- **THEN** 前端 SHALL 展示提示 "API Key 无效，请检查密钥配置"

#### Scenario: 连接不通时提示检查 Base URL

- **WHEN** 连通性测试返回 `error_type: "network"`
- **THEN** 前端 SHALL 展示提示 "无法连接到 API 端点，请检查 Base URL"

#### Scenario: 未知 provider 前缀返回结构化错误

- **WHEN** 用户配置的模型名携带后端白名单外的 provider 前缀（如 `kimi/kimi-k2-0905-preview`）并点击"测试连接"
- **THEN** 后端 SHALL 返回结构化失败响应：`success=false`、`errorType="model_prefix_invalid"`、错误信息含已知前缀列表与「OpenAI 兼容端点请使用 openai/<model>」指引
- **AND** 响应 MUST NOT 为 HTTP 500 未捕获异常

### Requirement: 多配置管理（profiles）

系统 SHALL 支持保存多套命名 LLM 配置（profile），持久化到浏览器 localStorage（key `fa_llm_profiles`），同一时刻恰好一个 profile 为激活状态；请求携带的 `llm_config` 字段 SHALL 来自激活 profile。后端 SHALL NOT 存储 profile。

#### Scenario: 保存当前配置为命名 profile

- **WHEN** 用户在设置面板配置好 model / base_url / api_key / thinking 后，在「配置管理」区输入名称（如「DeepSeek 办公」）并点击「另存为」
- **THEN** 系统 SHALL 将当前表单配置保存为一个新 profile（含唯一 id、名称、config）
- **AND** 新保存的 profile SHALL 成为激活 profile
- **AND** 名称 SHALL NOT 为空（为空时提示用户输入名称，不保存）

#### Scenario: 已有 profile 列表展示

- **WHEN** 用户打开设置面板且已保存 ≥1 个 profile
- **THEN** 「配置管理」区 SHALL 展示 profile 列表，每项包含名称、激活标记（如对勾）、删除按钮

#### Scenario: 从 LLM 切换下拉框切换 profile

- **WHEN** 用户在模式选择器右侧的 LLM 切换下拉框中选择某个 profile
- **THEN** 该 profile SHALL 立即成为激活 profile 并持久化到 localStorage
- **AND** 后续 `/api/chat`、`/api/analyze` 请求 SHALL 携带该 profile 的 config（`llm_config` 字段）
- **AND** 下拉框 SHALL 显示当前激活 profile 名

#### Scenario: 切换下拉框在 EmptyState 与聊天输入区均可见

- **WHEN** 用户处于空闲首页（EmptyState）
- **THEN** LLM 切换下拉框 SHALL 显示在模式选择器（"模式："下拉）右侧
- **WHEN** 用户处于聊天界面
- **THEN** LLM 切换下拉框 SHALL 显示在 ChatInputBar 模式切换旁

#### Scenario: 删除 profile 时激活项回退

- **WHEN** 用户在设置面板删除当前激活的 profile，且仍有其他 profile
- **THEN** 系统 SHALL 自动激活剩余第一个 profile
- **WHEN** 用户删除最后一个 profile
- **THEN** 系统 SHALL 将无可用配置，前端 SHALL 阻止发起分析/聊天并引导打开设置面板（后端环境变量回退仅作为兜底，不再提供前端隐式入口）

#### Scenario: 旧配置自动迁移为第一个 profile

- **WHEN** localStorage 存在旧 key `fa_llm_config`（升级前保存的配置）且 `fa_llm_profiles` 不存在或为空
- **THEN** 系统 SHALL 将 `fa_llm_config` 迁移为第一个 profile（名称「旧配置」），并激活之
- **AND** 迁移后 SHALL 清除旧 key `fa_llm_config`，此后以 `fa_llm_profiles` 为唯一配置来源

#### Scenario: 无任何 profile 时必须先配置（强制配置）

- **WHEN** `fa_llm_profiles` 不存在/为空且旧 key `fa_llm_config` 也不存在
- **THEN** LLM 切换下拉框 SHALL 显示"未配置"，点击 SHALL 引导打开设置面板
- **AND** 用户尝试发送分析/聊天（回车或点击发送按钮）时，系统 SHALL 阻止发送并自动打开设置面板
- **AND** 用户通过设置面板保存配置后 SHALL 创建第一个 profile 并自动激活，方可发起分析
- **AND** 后端环境变量回退保留（供后端直连测试与 CI 使用），但前端不提供任何隐式默认配置入口

### Requirement: API 形式配置

设置面板 SHALL 提供「API 形式」下拉框，可选项为 OpenAI Chat Completion、Anthropic Messages、OpenAI Responses；所选值 SHALL 持久化到 profile 配置（`apiForm` 字段）并随请求级 `llm_config.apiForm` 下发，后端 SHALL 据此映射 litellm 调用的 `api` 参数。未显式配置时 SHALL 默认 OpenAI Chat Completion（`chat_completion`）。

#### Scenario: 设置面板展示 API 形式下拉框
- **WHEN** 用户打开设置面板
- **THEN** 面板 SHALL 展示「API 形式」下拉框
- **AND** 可选项 SHALL 包含 `chat_completion`（OpenAI Chat Completion）、`messages`（Anthropic Messages）、`responses`（OpenAI Responses）三者
- **AND** 未选择过时 SHALL 默认选中 OpenAI Chat Completion（无「跟随默认」空态）

#### Scenario: 选择 OpenAI Chat Completion
- **WHEN** 用户在「API 形式」下拉框选择 `chat_completion` 并保存
- **THEN** profile 配置 SHALL 持久化 `apiForm = "chat_completion"`
- **AND** 后端 litellm 调用 SHALL 使用 `api = "chat"`（`/chat/completions` 协议）

#### Scenario: 选择 Anthropic Messages
- **WHEN** 用户在「API 形式」下拉框选择 `messages` 并保存
- **THEN** profile 配置 SHALL 持久化 `apiForm = "messages"`
- **AND** 后端 litellm 调用 SHALL 使用 `api = "messages"`（`/v1/messages` 协议）

#### Scenario: 选择 OpenAI Responses
- **WHEN** 用户在「API 形式」下拉框选择 `responses` 并保存
- **THEN** profile 配置 SHALL 持久化 `apiForm = "responses"`
- **AND** 后端 litellm 调用 SHALL 使用 `api = "responses"`（Responses 协议）

#### Scenario: 缺省默认 OpenAI Chat Completion
- **WHEN** 配置未显式设置 `apiForm`（前端缺省值落定为 `chat_completion`）
- **THEN** 请求级 `llm_config.apiForm` SHALL 为 `"chat_completion"`
- **AND** 后端 litellm 调用 SHALL 使用 `api = "chat"`

#### Scenario: 请求携带 API 形式
- **WHEN** 请求级 `llm_config.apiForm` 已设置
- **THEN** 请求体 `llm_config.apiForm` SHALL 等于该 profile 保存的值

#### Scenario: 直接 API 客户端缺省 apiForm（兼容）
- **WHEN** 直连客户端（不经前端）请求级 `llm_config.apiForm` 为 null 或缺失
- **THEN** 系统 SHALL 不显式设置 litellm `api` 参数
- **AND** 协议选择 SHALL 交由 litellm 按 model 前缀自动路由（仅供无前端直连场景的向后兼容）

#### Scenario: API 形式随 profile 切换
- **WHEN** 用户从 LLM 切换下拉框切换到一个携带不同 `apiForm` 的 profile
- **THEN** 后续请求 SHALL 使用该 profile 的 `apiForm` 值
- **AND** 设置面板「API 形式」下拉框 SHALL 显示该 profile 已保存的 `apiForm`

#### Scenario: 非法 API 形式值拒绝
- **WHEN** 请求级 `llm_config.apiForm` 既非 `chat_completion` 亦非 `messages` 亦非 `responses`
- **THEN** 系统 SHALL 返回配置错误（HTTP 422），不得静默忽略或误映射

### Requirement: 模型名称前缀推导

模型名称输入框 SHALL 允许用户填写不带 provider 前缀的裸模型名（如 `gpt-4o`、`claude-sonnet-4-20250514`）；系统调度 LLM 时 SHALL 依据所选「API 形式」推导并内部补全 litellm 前缀。若模型名已含 `/` 前缀（如 `deepseek/deepseek-chat`），系统 SHALL 原样使用，不做推导。

#### Scenario: 裸模型名按 API 形式补全前缀
- **WHEN** 用户在设置面板选择 API 形式 `chat_completion` 并填写裸模型名 `gpt-4o`
- **THEN** 生效的模型 SHALL 为 `openai/gpt-4o`（补全 `openai` 前缀）
- **WHEN** 用户在设置面板选择 API 形式 `messages` 并填写裸模型名 `claude-sonnet-4-20250514`
- **THEN** 生效的模型 SHALL 为 `anthropic/claude-sonnet-4-20250514`（补全 `anthropic` 前缀）
- **WHEN** 用户在设置面板选择 API 形式 `responses` 并填写裸模型名 `gpt-4o-mini`
- **THEN** 生效的模型 SHALL 为 `openai/gpt-4o-mini`（补全 `openai` 前缀）

#### Scenario: 已含前缀的模型名原样使用
- **WHEN** 用户在设置面板填写模型名 `deepseek/deepseek-chat`（已含 `/` 前缀）
- **THEN** 生效的模型 SHALL 保持 `deepseek/deepseek-chat`，不做任何前缀推导

#### Scenario: 未设置 API 形式时不推导
- **WHEN** 用户未设置「API 形式」下拉框（apiForm 为空）
- **THEN** 系统 SHALL 不对模型名做前缀推导，原样下发
- **AND** 行为与引入本功能前完全一致

### Requirement: LLM 切换下拉框提供「LLM 配置…」分隔项

系统 SHALL 在 LLM 切换下拉框（profile 列表）底部以分隔线隔开一个「LLM 配置…」项，点击后跳转 `/settings` 设置中心页并定位「LLM 配置」分区；该分隔项 SHALL NOT 改变当前激活 profile。

#### Scenario: 下拉框显示分隔项

- **GIVEN** 用户展开 LLM 切换下拉框且存在若干 profile
- **THEN** 下拉框 SHALL 在 profile 列表底部以分隔线展示「LLM 配置…」项

#### Scenario: 点击分隔项跳转设置页

- **WHEN** 用户点击「LLM 配置…」项
- **THEN** 系统 SHALL 跳转到 `/settings` 设置中心页并定位「LLM 配置」分区

#### Scenario: 分隔项不改变激活 profile

- **GIVEN** 用户展开 LLM 切换下拉框时已激活 profile P
- **WHEN** 用户点击「LLM 配置…」分隔项
- **THEN** 激活 profile 仍为 P
- **AND** 后续请求 SHALL 仍携带 profile P 的配置

### Requirement: Kimi 族请求级 provider_options 白名单与 judge effort 配置

resolver 请求级分支 SHALL 为 Kimi 模型族登记 provider_options 白名单 `REQUEST_OVERRIDABLE["kimi"] = {"reasoning_effort"}`，校验经 registry `KimiOptions` schema（`none/low/high/max`，extra=forbid）；白名单外的 key SHALL 显式报错。评估裁判 SHALL 支持环境变量 `JUDGE_REASONING_EFFORT`：非空时并入请求级 llm_config.provider_options 透传，未设置时 SHALL NOT 携带该键（跟随端点默认档）。

#### Scenario: 请求级白名单校验
- **GIVEN** llm_config.model 为 `openai/kimi-for-coding` 且 provider_options 为 `{"reasoning_effort": "none"}`
- **THEN** resolve_profile SHALL 返回携带该 provider_options 的 profile

#### Scenario: 白名单外 key 显式报错
- **WHEN** kimi 模型请求级 provider_options 含 `{"thinking": "enabled"}`
- **THEN** SHALL 抛 IncompleteLLMConfigError（键不在白名单）

#### Scenario: judge effort 环境变量生效
- **GIVEN** 环境变量 `JUDGE_REASONING_EFFORT=none`
- **WHEN** judges 构建裁判请求
- **THEN** llm_config.provider_options SHALL 为 `{"reasoning_effort": "none"}`

#### Scenario: judge effort 未设置不带键
- **GIVEN** 环境变量 `JUDGE_REASONING_EFFORT` 未设置
- **THEN** judges 构建的 llm_config SHALL 不含 provider_options 键

### Requirement: 环境变量注入 fallback 成员

系统 SHALL 支持通过 `LLM_FALLBACK_MODEL` / `LLM_FALLBACK_BASE_URL` / `LLM_FALLBACK_API_KEY`
为主管线环境变量解析出的 profile 注入一个 fallback 链成员：

- 仅当 `LLM_FALLBACK_MODEL` 与 `LLM_FALLBACK_BASE_URL` **同时**配置时生效；缺任一项 MUST
  视为未配置且 MUST NOT 注册半套成员（fallback 打错网关比没有 fallback 更危险）；
- 成员 SHALL 注册为运行时 preset `env-fallback:<model>`，并挂到主 profile 的 `fallback`
  元组上，使既有链执行器（`fallback_attempt_plan` / `complete_text_with_fallback`）可直接消费；
- 成员能力档 SHALL 按模型名推断（与请求级、环境变量主分支同一实现，MUST NOT 另行硬编码）；
- `purpose=judge` MUST NOT 挂载该 fallback（裁判配置必须显式且独立于主管线）；
- 链装配 MUST 继续受 PolicyRouter 的能力偏序校验与链长上限约束（成员能力低于 primary 时
  仍会被过滤，本变更不放宽该门禁）。

#### Scenario: 完整配置注入链成员

- **GIVEN** `LLM_MODEL` / `LLM_BASE_URL` / `LLM_API_KEY` 已配置，且 `LLM_FALLBACK_MODEL` 与
  `LLM_FALLBACK_BASE_URL` 也已配置
- **WHEN** 解析 deep purpose 的 profile
- **THEN** 主 profile 的 `fallback` 含 `env-fallback:<model>`
- **AND** `fallback_attempt_plan` 的尝试序列为 [primary, env-fallback 成员]

#### Scenario: 半套配置不生效

- **GIVEN** 只配置了 `LLM_FALLBACK_MODEL`，未配置 `LLM_FALLBACK_BASE_URL`
- **WHEN** 解析 deep purpose 的 profile
- **THEN** 主 profile 的 `fallback` 为空元组，MUST NOT 注册任何成员

#### Scenario: judge 不受影响

- **GIVEN** `JUDGE_*` 与 `LLM_FALLBACK_*` 同时配置
- **WHEN** 解析 judge purpose 的 profile
- **THEN** 其 `fallback` 为空元组，MUST NOT 指向 env fallback 成员

