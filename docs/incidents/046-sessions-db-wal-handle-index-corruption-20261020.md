# incident 046：sessions.db WAL 句柄悬挂 + 索引损坏 → 会话/战绩/日报 API 全断（2026-10-20）

## 现象

1. 2026-10-20 20:01 之后，`/api/sessions`、`/api/v1/track-record/*`、`/api/decision*`、`/api/scorecards`、
   `/api/v1/ops/daily-brief/latest`、`/api/v1/ops/cohort` 全部返回 500；`/api/health`、行情/事件/执行
   等其余端点正常。后端容器因此 `unhealthy`（健康检查就是 `/api/sessions`），连续失败 948 次。
2. 20:30 `daily_brief_daily` 定时任务连续 3 次失败：`OperationalError: unable to open database file`。
3. 19:16 启动的 601066 会话在 `verify_citations` 冻结 2 小时 20 分，`langfuse_trace_id` 为空，无后续日志
   ——典型「进程已死、行仍 running」。
4. 10-20 日批被后续的进程重启/引擎抖动连累：15:40 选股在 16:10 被标 stale、16:00 判定与 16:45 小票通道
   同样 stale、15:55 因子收割 3 次 LLM `Connection error`、cohort 只完成 2/10。

## 根因

**直接原因：worker 进程持有已删除 `sessions.db-wal` 的文件句柄。**

- 容器内检查 `/proc/<worker>/fd`：worker 同时持有 `sessions.db-wal` 的多个 fd（20/22/84/90/92），
  但 `ls /app/data/sessions.db-wal` 报不存在——Windows 侧删除后进入 delete-pending，句柄未释放。
- 任何新建 SQLite 连接都会执行 `PRAGMA journal_mode=WAL`，此时无法创建 WAL 文件名 →
  `sqlite3.OperationalError: unable to open database file`。
- 判别实验：同目录 `sessions2.db-wal`、`abc-wal` 可正常创建；WSL 内 `touch sessions.db-wal` 也 ENOENT，
  而 Windows 侧 Python 可以创建——**文件名级别的挂载/句柄问题，不是权限、磁盘或应用 bug**。
- 停后端容器释放句柄后，同名文件立刻可创建，证明无需重启 WSL/Docker 即可解除该阻塞。

**独立问题：`sessions.db` 存在真实 b-tree/索引损坏。**

- `PRAGMA integrity_check` 报 freelist 异常（`freelist leaf count too big`、`invalid page number 168128000`）
  与大量 `2nd reference to page`；受影响的是索引树 `idx_session_events_session`（root page 7）。
- 表扫描可读（`session_events` 1,100,824 行），但按 `session_id` 查询走索引即
  `database disk image is malformed`。
- `REINDEX`、`DROP INDEX` 均被 malformed 拒绝，只能按 `sqlite_master` 导出-重建。

**环境放大因素：Docker Desktop/WSL VM 在高负载下不稳定。**

- 期间 Docker 引擎多次对 `/containers/json` 返回 500，`docker exec` 超时；`factor_builtin_register`
  报 `OperationalError: disk I/O error`；AKShare 三路行情与 LLM 在并发拉取时出现间歇
  `ConnectionError` / `EAI_AGAIN`（空闲时探测 500/500 次解析全部成功）。
- C: 盘一度仅剩 12GB（97%），后续回升到 28–39GB；VM 数据卷虽已重定向到 D:，Windows 临时区受限
  仍会拖垮引擎。
- 本项目 `data/` 是 Windows bind mount（9p/virtiofs），SQLite WAL 并发写跨挂载是 043/044 同族问题的
  共同根因；本次是「句柄悬挂 + 索引损坏」组合形态。

## 影响

- 会话/战绩/评分卡/日报/cohort 全部 500，持续约 2 小时（20:01 最后一次 200 → 23:40 修复后恢复）。
- 10-20 日批：选股、判定、因子注册/衰减、小票通道、cohort 被重启或引擎抖动打断；已全部补跑（见下），
  仅 cohort 留待后续按 10-20 收盘数据补齐。
- **数据损失**：重建时 `session_events` 抢救回 1,072,797 / 1,100,824 行（97.5%），损失 28,027 行，
  集中在卡死会话尾部（id > 1,400,000 的坏页区间）。355 条会话记录、predictions/decision_log/
  daily_briefs/job_runs 等业务表零丢失。

## 处置与修复

1. **释放句柄**：停后端 → 验证 `sessions.db-wal` 可创建（无需 `wsl --shutdown`）。
2. **备份**：`data/sessions.db.bak-pre-repair-20261020-2142`（281MB，已命中 `**/sessions.db.bak*` 忽略规则）。
3. **重建**：按 `sqlite_master` 建新库，逐表搬迁；`session_events` 用「可读前缀 + 区间探测 + 1k 子窗降级」
   抢救，跳过坏页窗口；重建索引后 `integrity_check = ok`。
4. **替换并重启**：新库 276MB 覆盖；启动后旧 running 行被清扫为 interrupted；全部接口恢复 200。
5. **补跑 10-20 日批**：选股（L1 3,988 / pool_size=1，600519）、自动池分析（600519 完成，447s、
   17.5k 字报告）、纸面执行（5 条信号）、预测结算（superseded 5 / duplicates_closed 2 / unresolvable 0）、
   因子衰减/注册/收割（均 ok，0 入库）、小票通道（observation_only，pool_size=0）、日报（按 10-20 重生成）。
6. **环境**：两次 Docker Desktop 重启（第二次含 `wsl --shutdown`）；引擎恢复后栈稳定运行 2 小时以上。

## 防再发（挂账）

1. **热 SQLite 迁 Docker named volume**（最高优先）：`sessions.db` / `screening.db` / `execution.db` 等
   热库移出 Windows bind mount，冷文件保留在 `data/`。这是 043/044/046 的共同根因类别。
2. **健康检查区分 DB 可用性**：当前 `/api/sessions` 单点 500 会让整个容器 unhealthy 一个多小时；
   应增加轻量 DB 可用性探针并区分「服务活着但库不可用」。
3. **`integrity_check` 增页级校验**：现有日批只校验 predictions 快照哈希，本次 16:40 报 ok 而库已损坏。
4. **`pool_analysis_runs` 终态回写**：自动池分析会话失败/完成时未回写派发表，本次靠手工把
   `(2026-10-20, 600519)` 指向成功会话 `f3dbed77-537`。
5. **C: 盘空间与 bind mount I/O 纳入巡检**；部署命令统一 `-p finance-agent`（改名后 compose 已固定
   `name: finance-agent` 且 Langfuse 卷名钉死，陷阱已消除）。

## 关联

- [043](043-snapshot-parallel-backfill-index-corruption-20261016.md)：同一挂载上的索引层损坏（表重建恢复）
- [044](044-e2e-config-snapshot-leak-api-wedge-20261017.md)：跨挂载 SQLite 争用导致生产 API 卡死
- [014](014-refresh-clears-session-list-20260716.md) / [021](021-deep-analysis-session-stuck.md)：
  SQLite 同步写冻结事件循环、会话假卡死家族
