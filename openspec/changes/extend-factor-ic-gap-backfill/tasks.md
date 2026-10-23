# Tasks: extend-factor-ic-gap-backfill

- [x] 缺口回填场景测试（多日中断补齐 / 幂等不重写 / 无可算日跳过）红 → 绿
- [x] `run_factor_monitor` 摘要新增 `days_recorded`，`recorded` 语义不变（存量测试全绿）
- [x] `openspec validate extend-factor-ic-gap-backfill --strict` 通过
- [x] tests/factors 全套 + ruff + mypy 通过
