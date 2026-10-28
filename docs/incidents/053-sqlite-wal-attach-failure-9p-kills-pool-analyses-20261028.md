# incident 053：9p 挂载上 SQLite 连接打不开（WAL attach 失败）→ 10 个池分析全部中断（2026-10-28）

> 同族：[043](043-snapshot-parallel-backfill-index-corruption-20261016.md)（回填 + 跨挂载读）、
> [044](044-e2e-config-snapshot-leak-api-wedge-20261017.md)（E2E 读生产快照库争用）、
> [046](046-sessions-db-wal-handle-index-corruption-20261020.md)（sessions.db WAL 句柄悬挂 → 同一错误串 `unable to open database file`）。
> **复发型**：046 已把「`data/` 是 Windows bind mount（9p/virtiofs），SQLite WAL 跨挂载并发写不可靠」写进根因，并把「迁 named volume」列为挂账项；本次是该挂账项未落地后的第三发。
> 与 052 的关系：052 的「详情接口请求内建 500 日面板」是本次的**引信**——#502 已修但未部署，容器仍在跑旧码。

## 现象

2026-10-28 16:58:02–17:00:18（北京时间）约 **136 秒**内，后端容器内**所有** SQLite 连接创建失败，四种错误混合出现：

| 错误串 | 含义 |
|---|---|
| `unable to open database file` | SQLITE_CANTOPEN：连不上/建不了 WAL 文件名或 `-shm` |
| `disk I/O error` | 9p 层读写失败 |
| `database is locked` | busy timeout 耗尽 |
| `PRAGMA journal_mode=WAL` 处抛出 | 每个新连接的第一条语句 |

窗口内共 **95–96 条** DB 错误（16:58 时段 41 条、17:00 前后 54 条）；当天此前与 10-27 全天均为 0。

### 时间线（北京时间）

| 时间 | 事件 |
|---|---|
| 16:24:50 | 池分析派发 10 个会话（16:22 作业），10 个先后进入 running |
| 16:45:00 | microcap 选股日批开始（硬编码 16:45） |
| **16:58:02.886** | tqdm `0/6 → 6/6`（当时正在建面板；6 块） |
| **16:58:07.680** | **首条错误**：`因子库工具读路径失败（降级为空因）: unable to open database file` |
| 16:58:12.622 | `研报触发分析批次失败（投喂结果不受影响）: unable to open database file` |
| 16:58:15.936 | `GET /api/factors/6` → **500**（详见证取 §2） |
| 16:58:17.160 | `选股日批失败（2026-10-28）: unable to open database file` |
| 16:58:17.267 | `microcap_screening_daily job 失败`，5s 后重试 |
| 16:59:22.055 | `database is locked`（首个 BUSY） |
| 16:59:45–17:00:16 | 10 个会话逐个「终态落库重试 attempt=1..5」全部失败 |
| **17:00:18.505** | **最后一条错误**；此后自愈，无人工干预 |

## 影响

1. **10 个池分析全部中断，0 份报告落库**。10 个会话均抛「后台管线执行异常」：9 个根异常 `disk I/O error`、1 个 `database is locked`（600744）。
   - 9 个会话残留 `status=running`，但 `report_markdown` 为空、`tokens_total=0`、`duration_ms=0`——**管线被打断，报告未生成**（不是「生成了没存下来」）。
   - 这 9 行僵尸 running 会挡后端的重建前置检查（AGENTS.md 红线）。
2. **microcap 选股日批失败**（16:58），当日小票通道无产出。
3. **研报触发分析批次失败**（投喂结果不受影响）。
4. **因子工具降级**：窗口内任何分析师调用 `query_factor_library` 都拿到「空因子」——若该查询落在报告推理路径上，会静默影响分析内容（比 #511 的停牌股污染更直接）。
5. `GET /api/factors/6` 500（详情页加载失败）。
6. 附带观察：17:50 起 Docker 引擎对 `container inspect` 返回 500、`docker compose ps/logs` 空输出（046 记录过同款「引擎 500 + exec 超时」伴生现象）。

## 取证

### 1. 错误窗口与数量

```bash
docker compose logs backend --timestamps --since 2026-10-28T16:20:00 --until 2026-10-28T17:12:00 2>&1 \
  | grep -E "unable to open database file|database is locked|disk I/O error" \
  | sed 's/backend-1  | //' | cut -c1-160 | head -40
# 首条 2026-10-28T08:58:07.680Z（UTC）= 16:58:07 北京；末条 09:00:18.505Z = 17:00:18
```

注：`docker compose logs --since/--until` 在 Git Bash 下按**本地时区**解释参数，而输出时间戳是 UTC，两者相差 8 小时，取证时勿混淆。

### 2. 请求内建 500 日面板（052 未部署的实证）

```
File "/app/src/finance_agent/api.py", line 4510, in _factor_detail_diagnostics
    panel = _panel_from_snapshot(None)
File "/app/src/finance_agent/factors/monthly.py", line 227, in _panel_from_snapshot
    return _load_panel(snapshot_db_path, dates=dates)
File "/app/src/finance_agent/factors/monitor.py", line 279, in _load_panel
    build_panel_from_rows(pd.DataFrame(read_universe(trade_date, db_path=snapshot_db_path)))
File "/app/src/finance_agent/data/snapshot.py", line 435, in read_universe
    latest = latest_snapshot_date(db_path)
File ".../snapshot.py", line 408, in latest_snapshot_date
    init_snapshot_db(db_path)
File ".../snapshot.py", line 164, in init_snapshot_db
    conn = _connect(path)
File ".../snapshot.py", line 150, in _connect
    conn.execute("PRAGMA journal_mode=WAL")
sqlite3.OperationalError: unable to open database file
```

容器代码核对（**均为「未部署 #502」的实证**）：

```bash
docker compose exec -T backend sh -c "grep -n '_panel_from_snapshot(None)' /app/src/finance_agent/api.py"
# 4510:        panel = _panel_from_snapshot(None)        ← 宿主 main 已无此行
docker compose exec -T backend sh -c "grep -rn 'factor_diagnostics_daily' /app/src/finance_agent/outcome/ops/jobs.py"
# （无输出：容器无该日批作业）
findmnt -T /app/src
# /  overlay ...   ← /app/src 不是挂载，吃镜像代码
```

### 3. 会话管线中断点

```
File "/app/src/finance_agent/pipeline_runner.py", line 478, in _run
    _flush_pending()
File ".../pipeline_runner.py", line 401, in _flush_pending
    ).result(timeout=5)
File "/app/src/finance_agent/stream_registry.py", line 213, in publish_many
    seqs = await asyncio.to_thread(session_store.append_session_events, session_id, passed)
File "/app/src/finance_agent/session_store.py", line 87, in _get_db
    raise last_exc
File ".../session_store.py", line 76, in _get_db
    conn.execute("PRAGMA journal_mode=WAL")
sqlite3.OperationalError: unable to open database file
```

### 4. 挂载类型（平台层根因）

```bash
docker compose exec -T backend sh -c 'mount | grep "/app/data"'
# D:\ on /app/data type 9p (rw,noatime,aname=drvfs;path=D:\;uid=0;gid=0;metadata;
#   symlinkroot=/mnt/host/,cache=0x5,access=client,msize=65536,trans=fd,rfd=5,wfd=5)
```

### 5. 受影响会话与产物缺失

```bash
python - <<'PY'
import sqlite3
s = sqlite3.connect("file:data/sessions.db?mode=ro", uri=True); s.row_factory = sqlite3.Row
for r in s.execute("""SELECT substr(session_id,1,8) sid, stock_code, status,
   length(report_markdown) rlen, tokens_total, duration_ms
FROM sessions WHERE created_at>='2026-10-28T16:20' ORDER BY created_at"""):
    print(dict(r))
PY
# 10 行：9 running / 1 failed；报告长度全 0、tokens 全 0、duration 全 0
```

## 根因

### 平台层（决定性）

`data/` 是 **9p/drvfs bind mount**。SQLite 的 WAL 模式要求所有连接在同一主机上共享一段 `-shm` 共享内存；9p 的 `msize=65536` + drvfs 语义在**并发连接创建**下会间歇失败（EIO / ENOENT），表现即 `disk I/O error` 与 `unable to open database file`。这不是权限、磁盘或业务 bug——同机 `data/` 换成本地文件系统即不复现（043/046 的判别实验同结论）。

### 代码层（放大器）

| 放大点 | 位置 | 量级 |
|---|---|---|
| 每个连接都跑 `PRAGMA journal_mode=WAL` | `data/snapshot.py:150`、`session_store.py:76`、`screening/store.py:155`、`factors/library.py:153` | 每次连接都要 attach 一次 `-shm` |
| `read_universe` 读路径跑 DDL | `read_universe` → `latest_snapshot_date` → `init_snapshot_db`（`executescript(SNAPSHOT_DDL)`） | **3 个连接/天**（init + latest + query） |
| 面板逐日读 | `monitor._load_panel` / `pipeline._build_default_panel` | 60 日面板 **180** 次连接；500 日准入面板 **1,500** 次 |
| 面板不缓存 | `agent_factory._factor_library_payload`（每次工具调用重建） | 每个分析师每次调用都重建 |
| 详情接口请求内建面板 | `api.py:4510`（**容器为 #502 之前版本**） | 单次 HTTP 请求 **1,500** 次连接 |

当日叠加：10 个池分析（16:24 派发，运行中）+ microcap 日批（16:45）+ 因子详情请求（16:58:15）+ 宿主侧对同一 508MB 快照库的连续重读（16:50–17:10，本次诊断脚本，走同一块 D: 盘；时间窗重叠，**无法排除为加剧因素**）。

### 重试预算不足（为什么「2 分钟抖动 = 10 份报告全丢」）

`session_store._get_db` 的重试是 6 次指数退避（0.1s 基数，总覆盖 ≈ 3.1 秒），注释写明设计目标是「覆盖 spec 假设的 ~2s bind mount 自愈窗口」。实际停摆 **136 秒**，远超预算 → 重试耗尽抛错 → `_flush_pending` 失败 → 整个管线 abort，而非「暂存后补写」。

## 修复建议（按性价比）

1. **把 DB 迁出 9p**（046 挂账项，结构性修复）：`data/*.db` 改挂 Docker **named volume**（WSL ext4），仓库源码继续 bind mount。迁移需停栈拷贝 + `PRAGMA integrity_check` 验收。
2. **每连接不再跑 `PRAGMA journal_mode=WAL`**：只在建库/init 时设一次（WAL 是持久化属性）；每连接只设 `busy_timeout`；读路径用 `?mode=ro` URI。
3. **读路径不跑 DDL**：`latest_snapshot_date` / `read_universe` 不再调用 `init_snapshot_db`；建表只在写侧/启动时做一次。
4. **面板构建合并为一次区间查询**（`WHERE trade_date BETWEEN ? AND ?`），按 `(as_of, lookback)` 缓存进程内复用。
5. **部署当前 main**（#502：详情接口零面板构建 + `factor_diagnostics_daily` 17:15），拆掉最大的单请求引信。
6. **重试预算对齐已知故障时长**：连接类重试覆盖 ≥ 60–180s；`_flush_pending` 失败不应直接终止管线（事件缓冲 + 收尾补写），终态落库失败要落「终态待补」标记而不是留 running。

## 防复发要点

1. **WAL 不该跑在 9p/virtiofs 上**——这是 043/044/046 的共同根因，挂账三年不发（043→046→053）说明「换 volume」必须当成 P0 而不是优化项。
2. **PRAGMA 不应该在每连接路径上**：它需要 `-shm` attach，等于把「打开一个文件」升级成「打开三个文件 + 共享内存协商」，故障面乘以三。
3. **读路径跑 DDL 是隐性写**：`CREATE TABLE IF NOT EXISTS` + 迁移分支出现在 `latest_snapshot_date` 的调用链上，让纯读也承担写锁风险。
4. **成本型计算不进请求路径**（052 教训）+ **修了不等于部署了**：052 的修复在 main，容器跑旧码，同一天复发。
5. **终态落库不能是 all-or-nothing**：一次 2 分钟抖动不该等于「10 份分析白跑」。至少落一个 `terminal_pending` 标记，恢复后补写。
6. **观测**：本次事件在 access log 里几乎不可见（失败发生在事件泵，不是 HTTP 请求），只有 `终态落库重试` 与管线异常可查；建议加「DB 连接失败率」与「running 超 30 分钟会话数」两个读数。
