# Delta for llm-config

## ADDED Requirements

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
