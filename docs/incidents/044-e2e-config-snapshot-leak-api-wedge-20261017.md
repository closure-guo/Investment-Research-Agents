# 044：uvicorn --reload 优雅停机等待运行中分析 → 生产 API 整体卡死（同窗口附带暴露：E2E 配置缺快照隔离）

- 日期：2026-10-17
- 状态：**机制已定论**（reload 等待——纪律修复为主）；附带缺陷已修复（7 个 E2E 配置补快照隔离）
- 关联：incident 043 / issue #344（SQLite 跨挂载争用家族——本事件的**次发现**属该家族读侧变体）、
  incident 038（E2E 打穿生产的另一形态）

## 现象（2026-10-17 ~03:2x）

- 生产后端 API **全端点超时**：`/api/sessions` 60s+ 无响应（容器内 `urllib` 直连同样超时——
  非宿主网络路径问题）；`docker stats` CPU 36%（非打满）。
- 日志证据（`docker compose logs backend`，2618 行留档）：
  - 26 个 `sqlite3.OperationalError: unable to open database file`——全部来自
    `snapshot_status → init_snapshot_db → _connect → PRAGMA journal_mode=WAL`（快照库打不开；
    端点走降级路径返回 200 `status_unavailable`，**未 500**）；
  - 后台管线线程（cohort 分析 601058 的数据抓取，AKShare 重试）在 API 卡死后仍持续活动，
    其后亦静默；
- 恢复：重启后端（restart）→ API 立即恢复（200 / 1.4s）；`/api/snapshot/status` 恢复正常
  （latest_date=2026-10-16, stale=false）——**快照库文件本身无恙**（215MB，mtime 未变，无写入）。

## 时间线

- 02:00 cohort 补跑 run 109 启动，**正常推进 7/10**（02:00–03:2x，每只 ~12min）；
- ~02:40–03:0x E2E timeline 套件首跑（发现 pipeline-hierarchical-timeline rot 并修复）；
- ~03:1x–03:3x rot 修复后**全量复跑**（13.7min）；
- ~03:2x 起 API 不可用（首个 60s 超时样本）；
- 重启后端恢复；run 109 由悬挂清扫按 `stale_process_restart` 收尾（failed）；601058 会话
  `interrupted`；今日 cohort 计数 7 completed + 1 interrupted（重跑 110 按 `duplicate` 去重只补缺失票）。

## 根因（两度实证，机制定论）

**主机制（API 卡死）：`uvicorn --reload` 优雅停机无限等待运行中的分析后台任务。**

1. 后端以 `--reload` 启动且 `src/` 为 bind mount；宿主侧任何 src 文件写入会触发 watchfiles
   （**注意：与既有记忆「WSL2 bind mount 下 inotify 不传播、宿主机改码不触发 reload」相悖——
   watchfiles 实际检测到了，疑走轮询回退**）。
2. 本窗口内的触发源（日志逐条留档）：并发会话的 WIP 编辑（`litellm_adapter.py` / `report.py`）
   + **本会话在共享主检出的分支切换**（`akshare_client.py` 等随 checkout 重写）。
3. 触发后 uvicorn `Shutting down` → **`Waiting for background tasks to complete. (CTRL+C to
   force quit)`** ——此时 cohort 分析正在进程内运行（~10-20 分钟级），优雅停机等它完成；
   **停机期间 worker 不再接受/服务请求 → API 全端点停摆**。
4. 证据：首次卡死日志第 2486–2488 行（litellm_adapter 变更 → Shutting down → Waiting…）；
   本事件复盘时**实时复现同型**（第二次 reload 等待，`/api/sessions` 30s+ 超时）。
   分析完成后 reload 自愈（无需重启；本次重启属恢复处置）。

**次发现（独立缺陷，同窗口暴露，已修复）：7 个 E2E 配置缺 `SNAPSHOT_DB_PATH`。**

- `isolated`/`timeline`（三对后端）/`debug`/`manual`/`resilience`/`eval-ops`/`track-record` 未设
  → 未设时后端默认读 `data/market_snapshot.db`（生产快照，bind mount 共享）→ 本机跑这些套件
  时与容器跨挂载争用（#344 家族读侧）→ 26 处 `unable to open database file` traceback
  （快照状态端点降级返回 200，**未 500**）。
- CI 从未暴露：CI 工作区无生产快照（gitignore）→ 这些配置在 CI 行为=空快照。
- **与 API 卡死的关系**：tracebacks 是争用证据但非停摆机制（降级路径正常）；停摆由 reload
  等待解释。两事同窗口叠加，处置上分列。
## 修复与防护

- **纪律（主修复）**：
  1. **栈在跑分析时，共享主检出不得切分支/改 `src/` 文件**——实现改动一律走 worktree（并发会话
     记忆已有此条，本次升级为「reload 会真触发且卡死」实证）；
  2. 与并发会话并存时，任何宿主机 src 写入都视为「可能触发 reload 停机窗」——避开日批/cohort 时段。
- **已修复（附带缺陷）**：7 个配置补 `SNAPSHOT_DB_PATH: 'data/test-e2e-snapshot.db'`（与默认
  config 同口径）；修复后 timeline 套件全量复跑 31 passed / 0 failed 验证。
- **建议门禁（待落）**：
  1. 隔离清单校验（遍历 `playwright*.config.ts` 断言六库 env 覆盖，CI 执行）；
  2. reload 行为治理（部署形态评估：分析期禁 reload / `--reload` 换显式重启流程）；
  3. `cap_add: SYS_PTRACE`（下次复现取证用）。

## 影响与损失

- 生产 API 不可用窗口合计 ~40–60 分钟（两段 reload 等待；凌晨无用户在线影响）；
- cohort 10-16 批次 7/10 完成 + 1 interrupted + 2 未跑（重跑 110 去重补缺）；
- 快照库零损伤（只读争用，无写入）。
