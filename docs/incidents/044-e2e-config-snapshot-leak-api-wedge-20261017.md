# 044：E2E 隔离配置缺 SNAPSHOT_DB_PATH → 跨挂载 SQLite 争用 → 生产后端 API 整体卡死

- 日期：2026-10-17
- 状态：**已修复**（7 个 E2E 配置补快照隔离 + 套件复跑验证）；遗留：隔离清单门禁（建议项）
- 关联：incident 043 / issue #344（同一「bind mount 上 SQLite 跨进程争用」家族的**读侧变体**）、
  incident 038（E2E 打穿生产的另一形态：默认套件经 vite 代理命中生产后端）

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

## 根因（证据链）

**E2E 隔离配置缺 `SNAPSHOT_DB_PATH` → 其 webServer 后端默认读生产快照库 → 与生产容器
跨挂载并发打开同一 SQLite 文件 → 生产容器读侧失败并拖垮 API。**

1. **配置缺口**：`playwright.isolated.config.ts` 与 `playwright.timeline.config.ts`（三对后端
   8001/8002/8003）以及 `debug/manual/resilience/eval-ops/track-record` 共 7 个配置**未设**
   `SNAPSHOT_DB_PATH`（`playwright.config.ts` / `ui-baseline` 有）。未设时后端按代码默认读
   `data/market_snapshot.db`——bind mount 与生产容器共享的**生产快照库**。
2. **CI 从未暴露**：CI 工作区无 `data/market_snapshot.db`（gitignore）→ 这些配置在 CI 的
   实际行为=空快照，从不接触生产库；**本机（Docker 栈在线）跑这些套件即命中**。
3. **争用形态**：与 incident 043 / #344 同族——宿主侧进程与容器进程跨 bind mount 打开同一
   SQLite 文件，WAL/锁语义在 9p/virtiofs 上不可靠；#344 记录的是**写侧**（回填使容器读全不可用），
   本次为**读侧**（E2E 后端读生产库使容器读失败）。
4. **卡死机制（推断，未取到线程栈）**：容器缺少 ptrace 能力（py-spy attach 失败）未获栈；
   按证据（API 全挂 + 后台线程仍活 + 挂载 I/O 失败）最可能为**执行器线程在挂载 I/O 上阻塞 →
   `asyncio.to_thread` 队列耗尽 → 所有走线程池的端点排队死等**。精确机制待下次复发时以
   `cap_add: SYS_PTRACE` + py-spy 复现取证。

## 修复与防护

- **已修复**：7 个配置全部补 `SNAPSHOT_DB_PATH: 'data/test-e2e-snapshot.db'`（与默认 config
  同口径；debug/manual 不 spawn 后端无需）；修复后 timeline 套件全量复跑验证（读数随 PR）。
- **建议门禁（待落）**：脚本级「隔离清单」校验——遍历 `playwright*.config.ts` 断言每个
  spawn 后端的 env 覆盖六库（SESSIONS/SNAPSHOT/SCREENING/FACTOR_LIBRARY/EXECUTION/REPORTS），
  CI 执行；防同类新配置再漏。
- **操作纪律（本次强化）**：本机跑任何 E2E 配置前，核对其 `SNAPSHOT_DB_PATH` 已隔离
  （与 incident 038 的「先查端口占用」并列）。

## 影响与损失

- 生产 API 不可用窗口 ~40–60 分钟（凌晨，无用户在线影响；前端轮询降级自愈）；
- cohort 10-16 批次 7/10 完成 + 1 interrupted + 2 未跑（重跑 110 去重补缺）；
- 快照库零损伤（只读争用，无写入）。
