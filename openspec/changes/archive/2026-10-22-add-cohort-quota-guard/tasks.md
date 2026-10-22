# Tasks: add-cohort-quota-guard

- [x] 先红后绿：批内撞额度 → 该只记 `quota`、剩余立即 skipped（5 条新用例先红）
- [x] 先红后绿：当日已有 `quota` 失败 → 整批预检跳过（零 LLM 调用）
- [x] 先红后绿：当日余量不足（`COHORT_DAILY_TOKEN_BUDGET`）→ 整批/批中跳过（`budget_daily`）
- [x] 幂等 duplicate 优先于当日跳过，不被改写
- [x] `FAILURE_CODES` 增 `budget_daily` / `quota`；估算用历史成功 run 的 p75（避免低估）
- [x] `uv run pytest tests/outcome -q` 931 passed；ruff / mypy 通过
- [x] `openspec validate add-cohort-quota-guard --strict` 通过
- [x] 容器内真实数据核验（当日消耗 0 / p75 估算 / 三种预检分支）
- [x] 验证报告 `tests/validation/2026-10-22-add-cohort-quota-guard-validation.md`
- [ ] sync + archive
