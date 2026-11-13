# Proposal: harden-sqlite-connection-discipline

## Why

incident 053（issue #515）的根因（9p 挂载）已由 named volume 迁移根治，但连接层放大器仍在：①每个 `_connect` 都跑 `PRAGMA journal_mode=WAL`（每次连接 attach 一次 `-shm`，9p 时代正是这一步失败；卷迁后仍是无谓开销）；②`read_universe`/`latest_snapshot_date` 读路径调 `init_snapshot_db` 执行 DDL（每日 3 连发连接 + 写锁）；③面板逐日读无缓存（60 日面板 180 次连接、500 日 1500 次）；④`session_store` 重试预算 3.1 秒 << 实际停摆窗口，`_flush_pending` 失败直接 abort 整条管线、终态落库失败留僵尸 `running`。本 delta 承接 harden-sqlite-availability 归档后遗留的四项连接纪律加固。

## What Changes

- **① WAL 收敛到 init**：16 个 store 模块的每连接 helper 去掉 `PRAGMA journal_mode=WAL`，由各自 `init_*` 在 DDL 前设置一次（cache.py 例外：单例 `__init__` 天然一次，不动）；每连接只设 `busy_timeout`
- **② 读路径去 DDL**：`read_universe` / `latest_snapshot_date` 不再调 `init_snapshot_db`；缺表按既有空语义处理（None / `SnapshotEmptyError`），不静默建表
- **③ 面板区间查询+进程内缓存**（本轮不实施，挂账）：面板构建合并为 `WHERE trade_date BETWEEN` 一次查询 + 按 `(as_of, lookback)` 缓存
- **④ flush 失败缓冲与终态标记**（本轮不实施，挂账）：`session_store` 重试预算 ≥60–180s；`_flush_pending` 失败缓冲不终止管线；终态落库失败写 `terminal_pending`

## Capabilities

- **Modified Capabilities**: `session-persistence`（ADDED requirement：SQLite 连接纪律）

## Impact

- 16 个 store 模块 `_connect` 家族 + `init_*`（机械同构改动）
- `data/snapshot.py` 两个读函数签名/返回不变，缺表行为从「建表后空结果」改为「空语义直达」
- 生产库文件头已是 WAL（持久属性），去掉每连接 pragma 不改变存量库的 journal 模式
