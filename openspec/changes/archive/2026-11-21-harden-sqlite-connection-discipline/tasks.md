# Tasks: harden-sqlite-connection-discipline

- [x] delta 通过 `openspec validate --strict`
- [x] ① 16 模块 `_connect` 家族去 WAL pragma，`init_*` DDL 前设置（cache.py 单例例外不动）
- [x] ② `read_universe` / `latest_snapshot_date` 读路径去 `init_snapshot_db`，缺表按空语义（单测钉住）
- [x] 全量回归：snapshot/session/screening/factors/ops 相关面 + ruff + mypy 增量零新增
- [x] ③ 面板区间查询 + `(as_of, lookback)` 进程内缓存（60 日 180 连接 / 500 日 1500 连接收敛）——2026-11-20 实施：`read_universe_range` 单连接（latest 检查同连接折叠）+ `_load_panel` 单次构建 + `(dates, mtime_ns, size)` LRU(4) 缓存；语义与逐日逐值等价（缺日 fail-loud 保留）
- [x] ④ `session_store` 重试预算 ≥60–180s + `_flush_pending` 失败缓冲 + `terminal_pending` 终态标记——2026-11-20 实施：预算制（默认 120s env 可调、退避封顶 2s、硬顶次数）+ `_flush_pending`/单事件投递非 Timeout 异常整批回填队首（TimeoutError 保持 #490 跳过语义）+ `publish_terminal` 耗尽写 SQLite 旁路标记 + `scripts/repair_terminal_pending.py` 补落终态
- [x] 部署后生产观察（连接数下降 / DB 探针全绿）回填后归档——2026-11-21 00:0x 随 PR #607 重建部署（容器 23:56 新建，`terminal_pending` 标记进镜像）；生产读数：`/api/health` 8 库探针全绿、`terminal_pending/` 惰性目录不存在（部署后零终态写失败）、822 会话读取正常；连接计数无生产观测端点，③ 缓存收益（60 日 180→单连接）由单测与下批日批读数暴露。归档见 archive/2026-11-21-harden-sqlite-connection-discipline
