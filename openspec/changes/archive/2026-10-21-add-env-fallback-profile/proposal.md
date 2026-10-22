# Proposal: add-env-fallback-profile

## Why

2026-10-20 晚间主 provider（opencode zen）连续 1 小时 5xx/连接错误，4 个分析会话在同一端点重试 3 次后全部 failed；近 7 天 24 次会话失败中 9 次属该桶。registry 的 fallback 链只服务**命名 preset**（`deepseek-official` 等），而生产走 env 配置（`LLM_*`），主 profile 的 `fallback` 恒为空——断供时没有第二个端点可切。

## What Changes

- 新增 `LLM_FALLBACK_MODEL` / `LLM_FALLBACK_BASE_URL` / `LLM_FALLBACK_API_KEY`：
  model + base_url 同时配置时，解析期动态注册 preset `env-fallback:<model>` 并挂到主 profile 的 `fallback` 元组。
- 能力档按模型名推断（与请求级/环境变量分支同一实现）；judge 不挂 fallback（裁判配置必须显式独立）。
- 半套配置（只有 model 或只有端点）视为未配置，MUST NOT 注册——fallback 打错网关比没有 fallback 更危险。

## Capabilities

- **Modified Capabilities**: `llm-config`（新增一条 env 契约 Requirement）

## Impact

- `src/finance_agent/llm/resolver.py`（`_env_fallback_profile` / `_with_env_fallback`）
- `src/finance_agent/llm/registry.py`（`register_preset`）
- `.env` / `.env.example`（生产配 Kimi k3-256k 作为备用端点）
- 测试：`tests/llm/test_env_fallback_profile.py`
- 行为：仅当主端点抛 fallback 触发类错误（5xx/连接/限流等）时按既有链执行器切换；正常路径零变化。
