# Tasks: update-market-snapshot-first-run-resilience

- [x] delta 通过 `openspec validate --strict`
- [x] `MARKET_SNAPSHOT_SCHEDULE` 15:30 → 15:31（错峰；级联约束：最坏完成 < regime_daily 15:35，注释写明裁定理由）
- [x] spot 双源失败后同 run 65s 冷却重试一轮（env `MARKET_SNAPSHOT_SPOT_RETRY_DELAY_S` 可覆盖、每 run 有界一轮），命中时成功摘要含 `spot_retry=true`、首轮成功 `spot_retry=false` 且零额外等待
- [x] 真失败时异常消息含源级失败明细（源名/失败形态/尝试次数）：`_call_ak` 可选 failure_sink + `fetch_market_spot` 留痕 `last_spot_failure`，流入 `job_runs.error` 与快照 `meta.last_error`
- [ ] stock-screening 主规范「晚于行情快照任务 15:30」括注同步 15:31（随 archive sync）
- [x] 回归：tests/data 快照面 pytest 全绿 + ruff / mypy 基线不新增
- [ ] 部署后生产观察：连续 ≥3 交易日排程首跑零 failed（挂账，回填本单后归档）
