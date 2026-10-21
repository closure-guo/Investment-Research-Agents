# Design: harden-sqlite-availability

## Approach

### 1. 热库迁 named volume

- compose 新增 `hot_db_data` 卷，挂到 `/app/hot-db`；`data/` bind mount 保留给 reports/charts/
  factor_research/research_report_inbox 等冷文件。
- 环境变量显式指向新路径：`SESSIONS_DB_PATH=/app/hot-db/sessions.db`，其余热库同理。
- 迁移步骤（一次性，需停后端）：
  1. `GET /api/sessions` 确认无 running 会话；
  2. 停后端 → 对每个库执行 `PRAGMA wal_checkpoint(TRUNCATE)` 后校验 `integrity_check=ok`；
  3. 复制到卷目录（保留 `sessions.db` 与两个备份的命名规则）；
  4. 改 compose + env，起后端，验证 `/api/health` 与 `/api/sessions`；
  5. 宿主机 `data/*.db` 改名 `.migrated-<date>` 保留一个观察窗口，不立即删。
- 回滚：停后端 → 从卷复制回 `data/` → 还原 compose/env → 起后端。

### 2. DB 可用性探针

- 在 `session_store`（或独立 `db_health` 模块）提供 `probe_databases(timeout_s)`：对每个热库打开连接、
  执行 `SELECT 1` 与 `PRAGMA journal_mode`，捕获异常并返回 `{name, status, error_class}`。
- `/api/health` 调用探针；全部 ok → 200 `{status: ok, databases: {...}}`；任一失败 → 503
  `{status: degraded, databases: {...}}`，并在响应中给出失败库名。
- 探针整体超时（默认 2s）由 `asyncio.wait_for` 或线程 join 控制，避免 I/O 卡死拖住健康检查。

### 3. 页级完整性校验

- `outcome/track_record/model.integrity_check` 增加热库循环：`PRAGMA quick_check`（比 integrity_check
  便宜一个量级）优先，失败时再跑完整 `integrity_check` 取详情；结果写入 summary 的 `databases` 字段。
- 任一库非 ok → 抛错让 `run_job` 记 failed（与现有失败语义一致）。
- 保留现有 predictions 快照哈希校验与 `integrity_mismatch` 审计行为。

### 4. 派发终态回写

- 在 `screening/pool_analysis.py` 增加
  `update_pool_analysis_status_by_session(session_id, status, error=None)`：按 `session_id` 定位行，
  更新 status/error/updated_at；无匹配行时静默返回 0。
- 在会话终态落库的单一入口（`session_store` 的终态更新函数或 `PipelineRunner` 完成/失败分支）调用；
  用函数内 import 避免 `screening ↔ session_store` 环。
- 状态映射：session `completed` → `completed`；`failed` → `failed`；`interrupted` → `interrupted`。
  `queued` 仅在派发阶段写入，不再作为长期状态。

## Alternatives Considered

- **只把 `sessions.db` 迁卷**：范围小，但 screening/execution 等热库仍在 bind mount，046 同类风险仍在。
- **整个 `data/` 迁卷**：冷文件（factor_research、reports）需要宿主机/rdagent 直接访问，会破坏现有工作流。
- **关闭 WAL 改用 DELETE 模式**：牺牲并发写吞吐，且不解决跨挂载损坏的根因。
- **只在应用层加重试**：本次是连接无法建立（WAL 文件名不可用），重试无效。

## Risks

- 迁移期间的停机窗口（预计 5–10 分钟），必须避开 15:30 日批与运行中分析。
- 宿主机直接读热库的脚本（backfill/track_record_rebuild）需改为 `docker exec`；迁移前用
  `grep -rn "data/sessions.db" scripts/` 列清单逐项改造。
- named volume 不在宿主机可见，备份策略需改为 `docker run --rm -v ...` 或导出端点。
