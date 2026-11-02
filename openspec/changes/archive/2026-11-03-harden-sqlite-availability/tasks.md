# Tasks: harden-sqlite-availability

- [x] 列出宿主机直读热库的脚本清单（`grep -rn "data/sessions.db\|data/screening.db\|data/execution.db" scripts/ src/`），逐项改为容器内通道或环境变量
      （2026-11-01 完成：scripts/ 8 个直读脚本全部接共享护栏 `finance_agent/data/hotdb_guard.py`——
      #515 迁卷后语义升级为「宿主进程解析到主检出 data/ 下即拒绝，与容器状态无关」；
      `--allow-host-write` 语义改为「确知在操作过期回滚副本」。src/ 侧 ~24 处为 env 可覆写的
      容器内默认路径，形态正确不动。改造清单：backfill_market_snapshot（本地实现删除改 import）、
      backfill_delisted（调用点补 repo_root/db 解析）、backfill_predictions_from_reports、
      backfill_rejected_factors、backfill_risk_free_rates、backfill_index_closes、
      import_external_factors（双库）、track_record_rebuild、cohort_eval_readout（双库只读）；
      单测 tests/scripts/test_hotdb_guard.py 7 例 + 快照/delisted 护栏测试翻新为迁卷语义）
- [x] compose 新增热库 named volume（finance-agent_data，PR #549）；写迁移脚本 scripts/migrate_hot_db_to_volume.sh（stop → copy → quick_check+全表行数对账 → up → 端点验证；2026-11-01 执行，八库对账 ALL-OK，验证报告 tests/validation/2026-11-01-hot-db-named-volume-migration.md）
- [x] 停后端执行迁移并验证（health/sessions/factors/snapshot 端点 + 最新会话时间戳 2026-10-31 新鲜度判定；首个真实写负载观察点=11-02 日批+池分析批）
- [x] 写回滚脚本并空跑演练（复制回 bind mount → 还原配置 → 起后端）——脚本已写
      （scripts/rollback_hot_db_to_volume.sh）；**2026-11-01 空跑演练通过**（八库行数对账
      全 OK），演练抓出并修复两个静默失败：①`docker run` 缺 `-i` 时对账 heredoc 空转
      exit 0 放行（#550 同款坑漏修本文件）；②只读挂载直开带 WAL 的库必炸
      `unable to open database file`——对账侧先把源拷进容器可写 /tmp 再开（与迁移
      脚本 verify_pair 同配方）
- [x] `/api/health` 增加 DB 探针（含超时），补单测：全 ok / 单库不可用 / 探针超时
      （2026-11-01：`finance_agent/data/db_health.py::probe_databases`——读 sqlite_master
      页 1 而非 SELECT 1（连接级语句不触库文件，目录/垃圾文件假绿，design §2 已记修订）；
      整体超时闸 2s = 线程 monotonic deadline + 端点 asyncio.wait_for 双层；
      tests/test_db_health.py 10 例）
- [x] `integrity_check` 增加热库页级校验，补单测：全 ok / 单库损坏 → job failed
      （`_hotdb_page_integrity`：quick_check 先行、失败跑完整 integrity_check 取截断
      detail；任一库非 ok 抛 RuntimeError → run_job 落 failed；tests/outcome/
      test_integrity_check_hotdbs.py 3 例含哈希行为保留）
- [x] `pool_analysis_runs` 终态回写，补单测：completed / failed / interrupted / 无匹配行
      （#456 已实现；补 interrupted→failed 与会话消失行保持两例）
- [x] E2E 或集成验证：库不可用时健康端点返回 503 且给出库名；恢复后回到 200
      （tests/test_health_endpoint.py 集成层 TestClient 直连真实应用，env 注入 tmp 库
      路径不 mock 被测系统：200 全 ok / 503 库名+错误类 / 修复后回 200）
- [x] 人工验证报告落 `tests/validation/`（含迁移前后数据行数对照）
      （2026-11-01-db-health-probe-validation.md；行数对照由迁移环节承担——
      2026-11-01-hot-db-named-volume-migration.md 八库对账 ALL-OK，报告引用不重复）
- [x] `openspec validate harden-sqlite-availability --strict` 通过（2026-11-01）
