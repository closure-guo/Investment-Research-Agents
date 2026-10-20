# Design: add-auto-pool-analysis-daily-brief

## Approach

### 1. 自动选股分析：在 API 进程内复用队列，而不是另起执行器

`POST /api/screening/analyze` 的现有实现已经把「建会话 → 入 `AnalyzeQueue` → `PipelineRunner`
写 SSE/journal」串好了。自动化的最小风险路径是复用同一条链，而不是照 cohort 的同步路径再写一套
（后者不写 `session_events`，前端无进度、无恢复语义）。

新增 `outcome/pool_analysis.py`：

- `prepare_pool_analysis_batch(as_of, max_symbols, enqueue, db_path, screening_db_path)`：同步纯编排。
  读 `latest_successful_pool()`；只接受 `batch.trade_date == as_of`；按 `rank` 截断；跑
  `screening_freshness_removals`；对每个 `(trade_date, symbol)` 查 `pool_analysis_runs` 幂等表；
  建会话（与人工端点同序列）、写 run 行、调用 `enqueue(symbol, session_id)`。
- `run_pool_analysis_daily()`：日批入口。读 `AUTO_POOL_ANALYSIS_ENABLED`（默认 1）与
  `AUTO_POOL_ANALYSIS_MAX_SYMBOLS`（默认 10，钳制 1–100），惰性导入 `api` 并调用
  `api.submit_pool_analysis_batch()`。

`api.py` 新增：

- lifespan 捕获 `_MAIN_LOOP = asyncio.get_running_loop()`，shutdown 清空；
- `submit_pool_analysis_batch()`：`asyncio.run_coroutine_threadsafe` 把一个
  `asyncio.to_thread(prepare_pool_analysis_batch, ...)` 提交到主循环，`future.result(timeout=120)`。
  它只等「建会话 + 入队」完成，不等分析本身；
- `enqueue` 回调经 `loop.call_soon_threadsafe(queue.enqueue, ...)` 回到主循环，保证
  `AnalyzeQueue._promote` / `_watch` / `PipelineRunner.start(loop=...)` 都在同一个事件循环线程。

这样：自动批次与人工批次共用 `SCREENING_ANALYZE_CONCURRENCY`、共用选股页队列徽标、共用 SSE。

幂等表放 `screening.db`：

```sql
CREATE TABLE IF NOT EXISTS pool_analysis_runs (
  trade_date TEXT NOT NULL,
  symbol TEXT NOT NULL,
  session_id TEXT NOT NULL,
  status TEXT NOT NULL,          -- queued / dispatch_failed
  error TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  PRIMARY KEY (trade_date, symbol)
);
```

状态只记录「已入队/入队失败」；会话终态由 `sessions.status` 派生，避免再维护一套状态机。

### 2. 每日汇报：事实收集与 LLM 生成分离

新增 `outcome/daily_brief.py`：

- `collect_daily_facts(as_of)`：只读汇总。每个来源失败时记 `warnings`，不阻断整份汇报；
- `render_fallback(facts, error)`：确定性 Markdown，顶部显式标注「LLM 生成失败」，事实照录；
- `run_daily_brief()`：读 prompt（`load_prompt_with_meta("daily_brief")`）→
  `complete_text(purpose="daily_brief", max_tokens=4000, temperature=0.3)` → 写
  `daily_briefs`（`brief_date` 主键 upsert）→ 写 `reports/daily-brief-YYYY-MM-DD.md`；
- 失败路径：先写降级摘要（status=failed），再抛 `RuntimeError`，让 `run_job` 记录 failed 并走
  既有 3 次重试。

`daily_briefs` 放 `sessions.db`（与 `job_runs` 同库，已有隔离 fixture）：

```sql
CREATE TABLE IF NOT EXISTS daily_briefs (
  brief_date TEXT PRIMARY KEY,
  status TEXT NOT NULL,          -- ok / failed
  markdown TEXT NOT NULL,
  model TEXT,
  prompt_version TEXT,
  error TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
```

API 新增在 `ops_api.py`：`GET /api/v1/ops/daily-brief?date=` 与 `.../latest`；无行 404 +
`daily_brief_not_found`。前端 `/ops` 顶部新增卡片，ReactMarkdown 渲染，404 为空态、failed 为横幅。

### 3. 调度与任务注册

| job_id | 时间 | 位置 |
|---|---|---|
| `auto_pool_analysis_daily` | 工作日 16:22 | 选股 15:40 完成、paper 结算 16:20 之后；只入队，不阻塞 |
| `daily_brief_daily` | 工作日 20:30 | cohort 18:00 之后，给自动分析留出完成时间 |

两个 job 都进 `JOB_IDS/JOB_LABELS/JOB_FUNCS/SCHEDULES`，并在 `start_scheduler` 用 `_with_retry`
注册；注册表数量 24 → 26。`/ops` 与评估运维页数据驱动，自动出现；E2E 的硬编码顺序/数量同步更新。

### 4. LLM purpose

`Purpose` 加 `daily_brief`。resolver 增加逐字段覆盖：

```python
_DAILY_BRIEF_ENV = {
    "model": "DAILY_BRIEF_MODEL" if env.get("DAILY_BRIEF_MODEL") else "LLM_MODEL",
    "base_url": "DAILY_BRIEF_BASE_URL" if env.get("DAILY_BRIEF_BASE_URL") else "LLM_BASE_URL",
    "api_key": "DAILY_BRIEF_API_KEY" if env.get("DAILY_BRIEF_API_KEY") else "LLM_API_KEY",
}
```

不使用 `opencode/` 前缀；模型用 `openai/deepseek-v4.1-flash` + opencode base URL，adapter 会按
base_url 注入 `x-opencode-session` 头。

## Alternatives Considered

- **照 cohort 在调度线程同步跑 `_run_graph_streaming`**：不需要事件循环，能等分析完成，但不写
  `session_events`，选股页看不到进度，且绕开 `AnalyzeQueue` 的并发治理。否决。
- **单独起一个 asyncio loop 跑队列**：`PipelineRunner` 会把 SSE 事件发布到那个 loop，而订阅者在
  uvicorn 主循环上，容易出现跨循环投递问题。否决。
- **watch 触发位命中即机械转 buy**：绕过模型的审批与风控，等于用系统自己的「如果…就…」句子
  直接下单，风险不可控。不做；改为每日重新分析（池内标的次日会再次进入分析），并在日报里
  披露「连续 N 日 0 买入」。
- **把自动分析做成 100 只全量**：单日 LLM 调用与运行时长不可控。默认 10 只、可配置上限。

## Risks

| 风险 | 对策 |
|---|---|
| 选股批次 16:22 仍未完成 | 只接受 `trade_date==当日` 的成功批次；否则 `skipped_stale_pool`，不分析旧池 |
| 进程重启导致队列中断 | `PipelineRunner.mark_swept_failed()` 会把会话标 failed；日报披露；次日新日期重新分析 |
| 分析跑过 20:30 | 日报按当前状态如实写 `running`，不等待、不编造完成 |
| LLM 失败 | 确定性降级摘要 + `status=failed` + 任务失败重试；不允许空报告冒充成功 |
| 自动买入过于激进 | 不强制 buy；执行链只做多；`watch/hold/sell` 全部 flat；日报披露 0 买入原因 |
| 与 cohort 18:00 争额度 | `AUTO_POOL_ANALYSIS_MAX_SYMBOLS` 默认 10；额度已升级；两者并发由既有 LLM 网关治理 |
| 真实资金误解 | 执行仍为 paper broker；文档与日报明确「纸面」 |

## Open Questions

无。默认值均可经 env 覆盖，后续如需界面开关再另立 delta。
