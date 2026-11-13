# Tasks: harden-sqlite-connection-discipline

- [x] delta 通过 `openspec validate --strict`
- [x] ① 16 模块 `_connect` 家族去 WAL pragma，`init_*` DDL 前设置（cache.py 单例例外不动）
- [x] ② `read_universe` / `latest_snapshot_date` 读路径去 `init_snapshot_db`，缺表按空语义（单测钉住）
- [x] 全量回归：snapshot/session/screening/factors/ops 相关面 + ruff + mypy 增量零新增
- [ ] ③ 面板区间查询 + `(as_of, lookback)` 进程内缓存（60 日 180 连接 / 500 日 1500 连接收敛）——另批实施
- [ ] ④ `session_store` 重试预算 ≥60–180s + `_flush_pending` 失败缓冲 + `terminal_pending` 终态标记——另批实施
- [ ] 部署后生产观察（连接数下降 / DB 探针全绿）回填后归档
