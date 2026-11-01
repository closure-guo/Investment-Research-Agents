# Delta for llm-provider-gateway

## ADDED Requirements

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
