# Tasks: harden-sqlite-availability

- [ ] 列出宿主机直读热库的脚本清单（`grep -rn "data/sessions.db\|data/screening.db\|data/execution.db" scripts/ src/`），逐项改为容器内通道或环境变量
- [x] compose 新增热库 named volume（finance-agent_data，PR #549）；写迁移脚本 scripts/migrate_hot_db_to_volume.sh（stop → copy → quick_check+全表行数对账 → up → 端点验证；2026-11-01 执行，八库对账 ALL-OK，验证报告 tests/validation/2026-11-01-hot-db-named-volume-migration.md）
- [x] 停后端执行迁移并验证（health/sessions/factors/snapshot 端点 + 最新会话时间戳 2026-10-31 新鲜度判定；首个真实写负载观察点=11-02 日批+池分析批）
- [ ] 写回滚脚本并空跑演练（复制回 bind mount → 还原配置 → 起后端）——脚本已写（scripts/rollback_hot_db_to_volume.sh，挂载/对账与迁移脚本同修），空跑演练未做
- [ ] `/api/health` 增加 DB 探针（含超时），补单测：全 ok / 单库不可用 / 探针超时
- [ ] `integrity_check` 增加热库页级校验，补单测：全 ok / 单库损坏 → job failed
- [ ] `pool_analysis_runs` 终态回写，补单测：completed / failed / interrupted / 无匹配行
- [ ] E2E 或集成验证：库不可用时健康端点返回 503 且给出库名；恢复后回到 200
- [ ] 人工验证报告落 `tests/validation/`（含迁移前后数据行数对照）
- [ ] `openspec validate harden-sqlite-availability --strict` 通过
