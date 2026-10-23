# Tasks: add-env-fallback-profile

- [x] 先红后绿：`LLM_FALLBACK_*` 完整配置 → 主 profile.fallback 含动态成员且 preset 可解析
- [x] 先红后绿：半套配置（只有 model）→ 不注册成员
- [x] 先红后绿：judge purpose 不受 fallback 注入影响
- [x] 先红后绿：`fallback_attempt_plan` 尝试序列为 [primary, env-fallback]
- [x] `uv run pytest tests/llm -q` 全绿（375 passed）；ruff / mypy 通过
- [x] `openspec validate add-env-fallback-profile --strict` 通过
- [x] 真实环境验证：容器内链装配 + 备用端点小请求成功
- [x] 验证报告落 `tests/validation/2026-10-22-add-env-fallback-profile-validation.md`
- [ ] sync + archive
