# Tasks: add-factor-direction-reversal-monitor

- [ ] `factors.orientation` 列落库：`admit_candidates` 入库路径写入闸④训练段定向符号（四入口统一生效），含失败测试先行
- [ ] 存量 12 条因子定向回填脚本（全史 IC 均值符号，幂等可重跑）
- [ ] 衰减监控反转判据分支：连续 20 个滚动点符号与定向相反 → `degraded`（`reason=DIRECTION_REVERSAL`），与原判据同时命中只落一条事件、反转优先
- [ ] 单测覆盖六个 spec 场景（衰减/反转/短窗不触发/定向 0 跳过/入库落库/回填幂等）
- [ ] 生产库回填执行 + 次日日批实跑验证：`factor_events` 出现反转判据路径（或如实确认当期无触发）+ 衰减判据行为不回归
