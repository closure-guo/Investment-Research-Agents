# Incident 043：快照库并行回填致索引损坏（2026-10-16）

**状态**：已恢复（表数据零丢失，索引重建）；防护措施待落（回填运维纪律 + 检测）

## 现象

2026-10-16 02:19，宿主机 16 进程并行跑 `scripts/backfill_market_snapshot.py`（全量重取补 amount，unify-quant-research-architecture P0-1c）期间，宿主侧只读查询报 `sqlite3.DatabaseError: database disk image is malformed`。

## 诊断

`PRAGMA integrity_check` 输出（节选）：

```
wrong # of entries in index idx_market_snapshot_date
wrong # of entries in index idx_market_snapshot_code_date
wrong # of entries in index sqlite_autoindex_market_snapshot_1
row 5571 missing from index idx_market_snapshot_date
...
```

——**全部为索引层不一致（计数错/缺项），无表页级损坏**。后续 REINDEX 报 `UNIQUE constraint failed: market_snapshot.trade_date, market_snapshot.code`：唯一索引已失效 → 此前 `INSERT OR REPLACE` 的唯一性查找落空 → 表内积累重复行（全扫描实测 17 个重复键）→ 唯一索引无法重建（自相矛盾状态）。

## 根因（多因叠加，按嫌疑排序）

1. **硬杀进程**：本夜对 16 进程并行回填做过两次 `Stop-Process -Force`（一次为让容器读库、一次为切换 bulk 写入修复）。SQLite WAL 对单进程崩溃安全，但 16 进程同时被强杀 + 后续立即重启读写，属未设计工况。
2. **跨挂载并发访问（issue #344 同源）**：容器经 bind mount 读同一 DB 与宿主机 16 写者并发；`-wal/-shm` 的共享内存语义跨 mount 边界不可靠，容器侧打开/恢复动作可能基于陈旧视图重置 WAL。
3. **提交放大后的大事务**：bulk 修复后每票一次 ~190 行大事务，与 16 写者的锁竞争窗口更宽。

## 恢复过程（表数据零丢失）

1. **备份**：`data/market_snapshot.db.corrupt-20261016-0310`（128MB，保留）。
2. 尝试 `REINDEX` → 因重复行失败（唯一索引重建冲突）。
3. **表重建**（避开损坏索引）：
   - 全扫描 `SELECT rowid, trade_date, code ORDER BY rowid` 收集每键最大 rowid（756,605 行 → 756,588 键，17 个重复）；
   - `CREATE TABLE market_snapshot_rebuilt (<原 DDL>)`；`INSERT ... SELECT * FROM market_snapshot WHERE rowid NOT IN (<17 个非最大 rowid>)`（rowid 列表精确去重，保留最新写入=含 amount 行）；
   - `DROP TABLE market_snapshot` → `ALTER TABLE ... RENAME TO market_snapshot` → 按 `sqlite_master.sql` 原样重建两个命名索引。
4. `PRAGMA integrity_check` → **ok**；行数 756,588 精确吻合；日期 187 个（全为交易日）。
5. 附带清理：写入性能探针误写生产库的 `T99999` 测试行 190 行（含 54 个周末日期）已删除。

## 防护（待落，随 unify-quant-research-architecture 收口）

- **回填运维纪律**：回填运行期间不硬杀进程（要停就等其自然结束或逐进程 SIGTERM 后等待）；回填时段避免容器读快照（issue #344 已有独立治理项）。
- **回填后置校验**：回填收尾自动 `PRAGMA quick_check` + 重复键扫描（脚本侧新增），异常即 fail loud。
- **检测**：`integrity_check` 纳入快照日批健康检查或运维巡检（当前无任何自动检测——本次是人工查询偶然发现）。

## 教训

- 「数据面运维操作」与「生产并发读」的交叉面（跨挂载 WAL、硬杀、大事务并行）是本库最脆弱处；批量运维应默认低并发 + 不中断 + 事后校验。
- 写入性能探针**必须用隔离库**（本次 T99999 污染生产库即反例，已清理）。
