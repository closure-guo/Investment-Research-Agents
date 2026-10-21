# Proposal: harden-sqlite-availability

## Why

2026-10-20 的 incident 046 暴露了同一类故障的第三次发作：生产 `data/` 位于 Windows bind mount（9p/virtiofs），
SQLite 在跨挂载 WAL 并发下先后出现索引损坏（043）、跨挂载争用卡死 API（044）、worker 持有已删除
`-wal` 句柄导致新连接无法进入 WAL 模式（046）。同时现有健康检查把「单个业务接口 500」等同于「容器不健康」，
`integrity_check` 日批只校验 predictions 快照哈希、不校验 SQLite 页完整性，本次故障期间均未报警。

## What Changes

- **热 SQLite 库迁至 Docker named volume**：`sessions.db` / `screening.db` / `execution.db` / `events.db` /
  `regime.db` / `research_reports.db` / `factor_library.db` 从 `/app/data` bind mount 迁到 named volume，
  冷文件（reports、charts、factor_research、research_report_inbox）保留在 bind mount。
  **BREAKING（部署路径）**：宿主机不再能直接打开热库；备份/排障改走 `docker exec` 或导出端点。
- **DB 可用性探针**：`/api/health` 增加轻量 DB 探测，区分「服务进程存活」与「库不可用」；容器健康检查
  改用该探针，避免单个业务接口 500 把整机标成 unhealthy。
- **页级完整性校验**：`integrity_check` 日批在快照哈希校验之外，对热库执行 `PRAGMA integrity_check` 并
  记录结果，失败时任务记 failed。
- **自动池分析派发终态回写**：`pool_analysis_runs` 在会话进入 completed/failed/interrupted 时回写终态
  （现状只在派发时写 `queued`，本次事故中需手工修正指向成功会话）。

## Capabilities

- **Modified Capabilities**: `deploy`, `session-persistence`, `eval-ops-console`, `stock-screening`

## Impact

- `docker-compose.yml`（新增 named volume 与热库路径环境变量）、部署/备份脚本、E2E 隔离配置
- `/api/health` 契约（新增字段）、`integrity_check` 运行摘要、`pool_analysis_runs` 状态语义
- 数据迁移一次性步骤与回滚方案（见 design.md）；不改 API 业务契约与数据库 schema
