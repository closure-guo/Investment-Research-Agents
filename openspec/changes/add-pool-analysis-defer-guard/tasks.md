# Tasks: add-pool-analysis-defer-guard

- [x] delta 通过 `openspec validate --strict`
- [x] TDD：顺延重查（running→ok）/ 等待耗尽降级 / 前置无行零等待 / 前置已终态零等待 / 开关关闭 单元测试红→绿
- [x] 两档入口接线（`run_pool_analysis_daily` / `run_pool_analysis_microcap_daily`），摘要带 `defer` 字段
- [x] 全量回归：tests/screening 相关面 + ruff + mypy 增量零新增
- [ ] 部署后首日生产观察：16:22 主档批与小票档批 `defer` 读数、`stale_batch` 是否归零，回填本单后归档
