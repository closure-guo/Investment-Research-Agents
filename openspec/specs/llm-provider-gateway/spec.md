# llm-provider-gateway Specification

## Purpose

定义 LLM Provider 网关的统一契约：Provider 能力（`Capability`）显式化、唯一配置解析入口（`ProfileResolver`）、litellm 适配收口与运行时防护，以及生产 LLM 调用的唯一入口（`gateway.complete_*`）。所有 LLM 调用路径 SHALL 依据 capability 契约决策能力与预算，杜绝以模型名猜测能力、散落直连与半套配置漂移。
## Requirements
### Requirement: Provider 能力契约显式化

系统 SHALL NOT 以模型名字符串猜测 provider 能力（如 `_is_deepseek(model)` 分支）；所有 LLM 调用路径 SHALL 依据 `Capability` 契约字段决策，`Capability` 至少包含：`tools`（none/single/parallel）、`streaming`、`streaming_tool_calls`、`json_schema`（none/json_mode/strict_schema）、`reasoning_field`（下发字段名，不支持为 None）、`reasoning_must_echo_on_tool`（工具轮次是否必须回传思考字段）、`reasoning_forced`（思考是否强制开启不可关）、`max_context`、`max_output`。

#### Scenario: 方舟 GLM reasoning 字段不回传
- **WHEN** provider 的 `reasoning_must_echo_on_tool` 为 false（如方舟 GLM）且 ReAct 多轮工具调用构造回传消息
- **THEN** assistant 消息不得携带 `reasoning_content` 字段，请求被端点接受

#### Scenario: DeepSeek reasoning 字段必须回传
- **WHEN** provider 的 `reasoning_must_echo_on_tool` 为 true（如 DeepSeek 官方）且工具调用轮次回传
- **THEN** assistant 消息携带 `reasoning_content` 字段，缺失时由合同测试标记失败

#### Scenario: 强制 thinking 影响输出预算
- **WHEN** provider 的 `reasoning_forced` 为 true（如方舟 GLM，`thinking.type=disabled` 被端点 400 拒绝）
- **THEN** resolver 按 `reasoning_forced` 派生 max_tokens 预算策略（reasoning 与正文共享配额时预算必须覆盖 reasoning 峰值），并在 trace 中标记 `reasoning_forced=true`

### Requirement: 唯一配置解析入口（ProfileResolver）

model/baseUrl/apiKey/thinking/quick/deep 的优先级解析 SHALL 仅存在于 `ProfileResolver` 一处：请求级 `llm_config` → 激活 profile → 环境变量 → registry 默认。请求级配置存在时 MUST 组成完整 profile，缺关键字段显式报错或整体回退命名 profile，禁止半套配置漂移（如 judge 回退 LLM_* 打错网关）。

#### Scenario: 半套请求配置被拒绝
- **WHEN** 请求级 llm_config 只提供 model 不提供 base_url/api_key 且无对应命名 profile
- **THEN** 解析器返回显式配置错误，不得将请求 model 与环境变量 key/base_url 混搭

#### Scenario: 自定义端点模型前缀
- **WHEN** 配置了自定义 `base_url` 且 model 无 provider 前缀
- **THEN** 统一路由为 `openai/<model>`；model 已含前缀但 registry 未识别该 provider 时显式报错并提示可用前缀

#### Scenario: 同会话不串 provider
- **WHEN** 同一用户会话内发生 deep、quick、follow-up、tool follow-up 调用
- **THEN** trace 中这些调用的 provider/model 一致（除非显式 fallback 被记录），purpose 只决定档位不决定 provider

### Requirement: litellm 适配收口

`src/finance_agent/` 内仅 `llm/adapters/**` SHALL import litellm（CI grep 门禁，已收紧为仅 adapter，不再有兼容薄壳豁免）；所有 litellm 直接调用 SHALL 收口在 adapter 内（raw_completion / raw_stream / raw_acompletion），业务与 gateway 不直接 touch litellm，gateway complete 三入口（complete_text / complete_stream / complete_stream_async）SHALL 是生产代码唯一 LLM 入口。消息序列化、reasoning 字段读写、tool delta 合并、`finish_reason` 归一化（stop/tool_calls/length/content_filter/error/unknown）、错误归一化（AuthError/RateLimitError/TimeoutError/ModelNotFound/ContextOverflow/ContentFiltered/UnsupportedCapability/UnknownLLMError）SHALL 仅存在于 adapter。evals judge SHALL 经 resolver/gateway 调用 LLM（不再直连 litellm.completion），judge purpose 解析保持 JUDGE_* 独立映射。adapter SHALL 白名单化参数发送：移除全局 `litellm.drop_params=True` 静默丢弃，业务请求中 capability 不支持的关键参数（tools/tool_choice=required/response_format=json_schema/未登记 provider_options key）MUST 显式抛 UnsupportedCapabilityError；历史依赖 drop_params 吸收的参数组合 MUST 经合同测试确认后显式声明或移除。

#### Scenario: grep 门禁
- **WHEN** CI 执行 `grep -R "import litellm" src/finance_agent`
- **THEN** 命中范围仅限 `llm/adapters/**`，否则构建失败

#### Scenario: 模型下发 arguments 不合法时回传规范化
- **WHEN** 模型输出的 `tool_calls[].function.arguments` 为非合法 JSON 字符串（如单引号 Python 字面量）且需要回传
- **THEN** adapter 在回传前将其规范化为合法 JSON（可解析则重序列化，不可解析保留原值并记 trace warning），请求被严格端点接受

#### Scenario: finish_reason 分类处理
- **WHEN** 流式结束 `finish_reason` 为 length / content_filter / None 且无正文 delta
- **THEN** 分别归类为 OutputTruncated（触发 max_tokens 复核或 repair）/ ContentFiltered（不盲目重试）/ EmptyLLMOutput（触发重试路径），不再表现为下游偶发 JSONDecodeError

#### Scenario: judge 经统一入口
- **WHEN** 评估跑批发起 judge 调用
- **THEN** 请求经 resolver 解析 judge purpose（JUDGE_* 环境映射）并由 gateway 下发，trace 带独立 environment 审计

#### Scenario: 关键参数不再静默丢弃
- **WHEN** 请求携带 capability 不支持的 tool_choice=required
- **THEN** 显式抛 UnsupportedCapabilityError，而非被静默 drop 后行为漂移

#### Scenario: 非关键参数白名单
- **WHEN** 请求携带 provider 已知可忽略的次要参数
- **THEN** adapter 按白名单显式剔除并记 trace warning，不触发报错

#### Scenario: 全库无 legacy 引用
- **WHEN** 执行 `grep -rn "from finance_agent.llm import call_llm\|legacy" src/`
- **THEN** SHALL 仅命中 `__init__.py` 注释性提及或零命中
- **AND** 生产代码 SHALL 全部经 `gateway.complete_*` 调用

### Requirement: litellm 运行时防护

litellm 运行时开关（流式 logging 禁用、请求级 timeout）与库级平台 bug 防护 SHALL 统一在 adapter 初始化处设置，业务模块 MUST NOT 各自设置。adapter 测试套件 SHALL 包含线程/端口泄漏守护（incident 016 回归：流式调用后线程数与本地监听端口数不增长）。

#### Scenario: 死锁开关统一收口
- **WHEN** 任意入口（管线 llm 薄壳、harness、evals）首次触发 adapter 初始化
- **THEN** `litellm.disable_streaming_logging` 等运行时开关已生效，业务模块无需重复设置

#### Scenario: 泄漏守护测试
- **WHEN** 合同测试执行真实流式调用序列后检查进程状态
- **THEN** litellm 全局线程池工作线程数与进程本地监听端口数保持稳定，进程可正常退出

### Requirement: ark-glm 输出预算对齐官方默认

系统 SHALL 将 ark-glm（方舟 GLM-5.3）的默认输出预算对齐官方默认值：max_tokens 默认 65536（官方默认，最大 131072，建议 ≥1024）。deep 长 JSON 节点（analyst/debate/trader/fund_manager）在默认预算下 SHALL 避免因 reasoning 与正文共享配额而触发 `finish_reason=length` 空正文中断。模型输出完整后自然 `stop`，预算仅为上限非固定消耗。

#### Scenario: 默认预算生效
- **GIVEN** 经 env 或 preset 解析到 ark-glm profile
- **WHEN** derive_output_budget 派生预算且调用方未显式传 max_tokens
- **THEN** 预算 SHALL 为 65536（命令式：等于 registry max_output 与 default_params.max_tokens）

#### Scenario: 显式传 max_tokens 覆盖
- **WHEN** 调用方显式传 max_tokens（如 `_build_focus_summary` 的 400）
- **THEN** 显式值 SHALL 优先（derive_output_budget requested 分支）

### Requirement: ark-glm reasoning_effort 显式配置入口

系统 SHALL 为 ark-glm 提供 reasoning_effort 的配置入口并透传到请求：官方默认 `max`；支持档位 `max/high/low`（GLM-5.3 范围）；配置来源按优先级：请求级 llm_config → 环境变量 `LLM_REASONING_EFFORT` → registry 默认 `max`。透传方式 SHALL 为 `extra_body={"reasoning_effort": <值>}`（实证：litellm openai 路由拒收顶层 `reasoning_effort`，extra_body 才成功透传到方舟端点）。

#### Scenario: env 配置生效
- **GIVEN** 环境变量 `LLM_REASONING_EFFORT=high` 且模型为方舟 GLM（`glm` in model）
- **THEN** 解析出的 profile provider_options SHALL 含 `reasoning_effort: "high"`
- **AND** apply_provider_options SHALL 产出 `extra_body={"reasoning_effort": "high"}`

#### Scenario: 默认值生效
- **GIVEN** 未配置 LLM_REASONING_EFFORT 且模型为方舟 GLM
- **THEN** provider_options SHALL 含 `reasoning_effort: "max"`（官方默认）

#### Scenario: 请求级覆盖
- **GIVEN** llm_config 提供 `reasoning_effort: "low"`
- **THEN** 最终请求参数 SHALL 为 `reasoning_effort="low"`（请求级 > env > 默认）

#### Scenario: 非法值拒绝
- **WHEN** reasoning_effort 传入非 `max/high/low` 值
- **THEN** SHALL 显式抛错（pydantic ValidationError / 等价），不静默忽略

### Requirement: gateway 为唯一生产 LLM 入口（legacy 薄壳移除）

系统 SHALL 将 `finance_agent.llm.legacy`（call_llm / call_llm_stream / call_llm_with_tools / LLMConfig）从生产链路移除：所有 LLM 调用 SHALL 直接经 `gateway.complete_text` / `gateway.complete_stream` / `gateway.complete_stream_async`，不再存在第二个 deprecated 入口。移除前 SHALL 完成既有调用方迁移并逐条复刻 legacy 语义。

#### Scenario: 生产调用方迁到 gateway
- **GIVEN** 原 legacy call_llm 调用方（nlp.py / react_agent.py / web_fetcher.py / report.py）
- **WHEN** 迁移到 `gateway.complete_text`
- **THEN** SHALL 复刻以下 legacy 语义：`quick=True → purpose="quick"`、`temperature` 默认 0.3、`max_tokens` 默认 16384、`content 为空时回退 reasoning_content`
- **AND** `agent` / `session_id` / `stock_code` / `prompt_name` / `prompt_version` SHALL 经 `trace` dict（name/metadata）透传，Langfuse 命名与过滤字段不丢失

#### Scenario: deep 管线流式入口迁移
- **GIVEN** `_llm_utils.call_llm_streaming` 原经 legacy call_llm_stream 消费流式
- **WHEN** 迁移到 `gateway.complete_stream`
- **THEN** SHALL 保留 `(kind, text)` 迭代协议（thinking→"thinking"，answer→"answer"）与 error 事件 → typed error 还原（原 `_ERROR_CLASS_BY_NAME` 映射）
- **AND** 截断续写 delta 的 32768 翻倍重试逻辑 SHALL 行为不变（续写优先、翻倍兜底）

#### Scenario: legacy 移除与 re-export 清理
- **WHEN** 全部调用方迁移完成且回归通过
- **THEN** 删除 `legacy.py` 及 `finance_agent.llm.__init__` 中对它的 re-export
- **AND** 代码库 SHALL NOT 再出现 `from finance_agent.llm import call_llm` / `call_llm_stream` / `call_llm_with_tools`（全库 grep 零命中）

### Requirement: 请求级上下文长度配置

用户 SHALL 能经前端设置页配置上下文长度（tokens），随请求级 `llm_config.contextLength` 下发；留空时跟随 registry 静态 `capability.max_context` 默认。resolver 解析 profile 时，请求级 `contextLength`（正整数）SHALL 覆盖 `capability.max_context`；环境变量 `LLM_MAX_CONTEXT`（正整数）SHALL 在无请求级值时覆盖默认。非法值（非正整数）MUST 显式报配置错误，不得静默忽略。覆盖后的 capability SHALL 经既有 `ContextBudget.from_capability` 链路驱动 ReAct 上下文预算。

#### Scenario: 请求级覆盖
- **WHEN** 请求级 llm_config 携带 contextLength=200000 且解析成功
- **THEN** profile.capability.max_context 为 200000，其余能力字段不变

#### Scenario: 环境变量覆盖
- **WHEN** 无请求级 contextLength 且 env LLM_MAX_CONTEXT=200000
- **THEN** 解析出的 profile capability.max_context 为 200000

#### Scenario: 非法值拒绝
- **WHEN** contextLength 为 0、负数或非整数
- **THEN** 显式配置错误（HTTP 422 或 resolver 配置异常），不静默回退

#### Scenario: 留空跟随默认
- **WHEN** 请求与环境均未配置 contextLength
- **THEN** capability.max_context 保持 registry 静态声明值，行为与现状一致

### Requirement: kimi reasoning_effort 配置入口

系统 SHALL 为 Kimi 模型族（openai/ 前缀下 model 含 `kimi` 或 `k3` 前缀段）提供 reasoning_effort 的配置入口并透传到请求：支持档位 `none/low/high/max`（`none` SHALL 表示关闭思考，Kimi 官方语义为路由到无思考版）；请求参数 SHALL 经 `extra_body={"reasoning_effort": <值>}` 透传（litellm openai 路由拒收顶层 `reasoning_effort`，与 ark-glm 同机制）。系统 SHALL NOT 为 kimi 族设置隐式默认档位——未显式配置时不携带该参数，跟随端点模型默认。

#### Scenario: 请求级 effort=none 关思考
- **GIVEN** llm_config.model 为 `openai/kimi-for-coding` 且 provider_options 为 `{"reasoning_effort": "none"}`
- **THEN** 解析出的 profile provider_options SHALL 保留该值
- **AND** apply_provider_options SHALL 产出 `extra_body={"reasoning_effort": "none"}`

#### Scenario: 未配置时不携带
- **GIVEN** kimi 模型请求未提供 provider_options
- **THEN** 最终请求参数 SHALL 不含 reasoning_effort（不注入默认档）

#### Scenario: 非法值拒绝
- **WHEN** reasoning_effort 传入 `none/low/high/max` 之外的值（如 `medium`）
- **THEN** SHALL 显式抛错（pydantic ValidationError），不静默忽略

#### Scenario: k3 前缀模型同族识别
- **WHEN** model 为 `openai/k3` 或 `openai/k3-256k`
- **THEN** SHALL 识别为 kimi 族并走同一透传机制

#### Scenario: effort 显式配置时抑制 temperature
- **GIVEN** Kimi 端点采样温度锁死（实证：思考档仅接受 temperature=1，无思考档仅接受 0.6，其他值一律 400）
- **WHEN** kimi 请求携带显式 reasoning_effort
- **THEN** apply_provider_options SHALL 额外产出 `suppress_temperature=True`（adapter→gateway 内部契约，gateway 据此不发送 temperature，采样跟随端点固定值）
- **AND** 该抑制 SHALL NOT 外溢到 ark-glm（方舟 GLM 温度可调）

### Requirement: temperature 拒绝自动降级

adapter raw 入口（raw_completion / raw_stream / raw_acompletion）SHALL 支持温度拒绝自动降级：请求携带 `temperature` 且端点以 temperature 相关错误拒绝（如思考型模型的 `invalid temperature: only 1 is allowed for this model`）时，剔除 `temperature` 后重试一次（走端点默认值）。降级 SHALL 记录 logger warning 并经 `update_current_span` 写 trace（`degradation=temperature_dropped_endpoint_rejected`）；重试仍失败 SHALL 按原有错误归一上抛，MUST NOT 无限重试；未携带 temperature 的请求 MUST NOT 触发本降级。

#### Scenario: 思考型模型温度拒绝降级成功

- **GIVEN** 请求携带 `temperature=0.3` 且模型端点仅允许 temperature=1
- **WHEN** 端点返回 400 `invalid temperature: only 1 is allowed for this model`
- **THEN** adapter 剔除 `temperature` 后重发一次并正常返回结果
- **AND** trace 记录 `degradation=temperature_dropped_endpoint_rejected`，logger 记 warning

#### Scenario: 降级后仍失败按原错误上抛

- **WHEN** 剔除 temperature 重试后仍失败（如认证错误）
- **THEN** 按归一化错误上抛，不再重试

#### Scenario: 非 temperature 错误不触发降级

- **WHEN** 端点错误信息不含 temperature（如 auth / model_not_found）
- **THEN** 不剔除参数、不重试，走原有错误路径

#### Scenario: 未携带 temperature 的请求不受影响

- **WHEN** 请求未携带 `temperature`（如 capability probe、probe 探测调用）
- **THEN** 本降级逻辑零介入，行为与现状一致

### Requirement: daily_brief purpose 档位

`Purpose` 枚举 SHALL 包含 `daily_brief`，用于每日运营汇报；该 purpose 的模型解析 SHALL 遵守
「唯一配置解析入口」契约，不得在业务模块内直连 provider。

- 解析 SHALL 按字段覆盖：`DAILY_BRIEF_MODEL` / `DAILY_BRIEF_BASE_URL` / `DAILY_BRIEF_API_KEY`
  存在时优先，未设置时逐字段回退 `LLM_MODEL` / `LLM_BASE_URL` / `LLM_API_KEY`；
- 默认部署（仅配置 `LLM_*`）下 SHALL 解析为 opencode 端点
  `https://opencode.ai/zen/go/v1` + `openai/deepseek-v4.1-flash`；
- provider SHALL 由 model 前缀派生（`openai/...` → `openai`）；MUST NOT 使用 `opencode/` 前缀
  （resolver 已知前缀白名单不含该值）；
- 请求 SHALL 经 `gateway.complete_text` 发出，默认 `temperature=0.3`、`max_tokens=4000`；
  `response_format=json_object` MUST NOT 被强制使用（该端点不支持时会失败）。

#### Scenario: 默认继承 LLM_*

- **GIVEN** 只配置 `LLM_MODEL=openai/deepseek-v4.1-flash`、`LLM_BASE_URL=https://opencode.ai/zen/go/v1`、
  `LLM_API_KEY`，未设置 `DAILY_BRIEF_*`
- **WHEN** 调用 `resolve_profile(purpose="daily_brief")`
- **THEN** 返回 `provider=openai`、`model=openai/deepseek-v4.1-flash`、同一 base_url 与凭据

#### Scenario: 仅覆盖模型名

- **GIVEN** 配置 `DAILY_BRIEF_MODEL=openai/another-model`，未设置 base_url/api_key
- **WHEN** 调用 `resolve_profile(purpose="daily_brief")`
- **THEN** model 使用覆盖值，base_url/api_key 仍回退 `LLM_*`
- **AND** 不产生半套配置报错，也不混入其他 purpose 的端点

#### Scenario: 不得使用 opencode 前缀

- **GIVEN** 配置 `DAILY_BRIEF_MODEL=opencode/deepseek-v4.1-flash`
- **WHEN** 解析 profile
- **THEN** 显式报未知 provider 前缀错误，不得静默改写

