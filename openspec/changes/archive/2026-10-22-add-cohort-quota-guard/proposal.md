# Proposal: add-cohort-quota-guard

## Why

2026-10-08/09/16 的 cohort 批（18:00）连续三次在 18:00:00→18:04 顺序撞上 provider 的「5 小时使用上限」；2026-10-20/21 则在同窗口出现 5xx。单轮预算（`COHORT_MAX_TOKENS_PER_RUN`，默认 2M）只看**本批**消耗，不看当天已用量，也不感知「今天额度已经撞过」——于是撞限后仍然逐只尝试，把 4 次失败摊在同一窗口里。

## What Changes

- 新增**当日额度守卫**（批次开始前 + 每只标的启动前各判一次）：
  1. **当日预算余量**：`COHORT_DAILY_TOKEN_BUDGET`（默认 3,000,000）− 当日 `cohort_runs.tokens_total` 合计 < 单只预估成本 → 跳过剩余标的，原因 `budget_daily`；
  2. **额度已耗尽信号**：当日已有任何 `quota` 失败（错误信息命中「使用上限/速率限制/RateLimit/429/额度」）→ 整批预检直接跳过，原因 `quota`。
- runtime 失败归因新增稳定码 `quota`：`no_report_ready` / `exception` 路径先做额度特征匹配，命中即记 `quota`（异常原文仍只进日志）。
- 批次内一旦出现 `quota` 失败，剩余标的立即 `skipped`，不再逐只重试。
- 不改变：单轮预算熔断、未知 usage 熔断、幂等/重复跳过语义、`enabled` 开关。

## Capabilities

- **Modified Capabilities**: `paper-trading-cohort`（Requirement「成本预算与运维开关」扩展当日守卫）

## Impact

- `src/finance_agent/outcome/cohort/runner.py`（守卫 + 失败分类）
- 观测：`cohort_runs.failure_reason` 新增 `budget_daily` / `quota`（稳定码全集扩展）；`/api/v1/ops/cohort` 读数可聚合
- 测试：`tests/outcome/test_cohort_runner.py`（预检跳过、批内熔断、分类、估算回退）
- 已知边界：当日口径只统计 **cohort 自身** 的 token（screening/自动池/用户会话的消耗不在 `cohort_runs`），所以「余量」是保守下界；撞限信号（`quota`）才是硬证据。若后续要把全站消耗纳入，需要单独的 usage 台账（不在本 delta）。
