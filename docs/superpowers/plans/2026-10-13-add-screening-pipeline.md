# add-screening-pipeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 选股日批编排（快照确认→L1→L2→L3→精选池落库，失败不产残缺池 + 横幅告警）、信号时效性校验、`/screening` 前端页与一键批量深度分析（并发 2 + 排队）。

**Architecture:** `screening/pipeline.py`（编排 + 批次/精选池落库 + 时效性校验纯函数）；`screening/queue.py`（进程内批量分析队列，单 worker 前提同 StreamRegistry）；`api.py` 新增 `/api/screening/{pool,status,analyze}`（状态端点永不 500；analyze 为每个标的建独立 session 并复用 `PipelineRunner.start`，并发 2）；前端轻量 pathname 路由加 `/screening` 页（复用既有表格/横幅/SSE 观测模式）；TESTING seed 扩展 screening 造数。

**Tech Stack:** Python 3.14 / FastAPI / SQLite / React 18 + Vitest / Playwright。

## Global Constraints

- 测试：`uv run pytest tests/screening tests/test_api_screening.py -q`；`cd frontend && npx vitest run`；E2E `npx playwright test tests/screening-page.spec.ts`（默认 config）。
- TDD 红→绿；既有测试零修改（seed 扩展为纯追加；E2E 新增 spec）。
- 原因码（逐字）：时效性校验 `SUSPENDED` / `LIMIT_LOCKED` / `NEW_EVENT`；批次状态 `ok` / `failed`。
- 失败纪律：任一步失败 → 批次行 `status=failed` + `error` 落库，**当日精选池零行**；页面展示最近成功批次并标注其日期（spec 场景）。
- 并发上限默认 2（可配置 `SCREENING_ANALYZE_CONCURRENCY`）；排队态前端可见。
- 库：`SCREENING_DB_PATH`（复用 `screening/store.py` 连接纪律）；新表 `screening_batches(trade_date PK, status, error, started_at, finished_at, l1_passed, l2_top, l3_keep, pool_size)`、`selection_pool(trade_date, symbol, composite_score, rank, consecutive_days, l3_verdict, l3_reason, factor_contribs_json, PK(trade_date,symbol))`。
- 前端：testid 逐字 `screening-page` / `screening-pool-table` / `screening-contribs-tooltip` / `screening-batch-stale-banner` / `screening-analyze-btn` / `screening-queue-badge-{symbol}`。

## 关键设计决策

**S1 — 日批编排顺序**：`run_screening_batch(as_of=None, *, panel=None, events_by_symbol=None, triage_client=None, db_path=None, seed=20261013) -> dict`：
① 快照新鲜度：`latest_snapshot_date()` 必须 == `as_of`（as_of 缺省 = 最新快照日）否则 `SnapshotNotFreshError` → 批次 failed；
② L1：`read_universe(as_of)` → `build_candidate_record`（amounts=近 20 日、calendar=meta 日历）→ `filter_universe`；
③ L2：`list_active_factors` + `recent_ic_stats` → `rank_universe(top_n=100)` → `record_l2_candidates`（**全量 Top-N**，供稳定性查询）；
④ L3：`run_triage`（quick profile，事件注入）；
⑤ 精选池：`l3_verdict=="keep"` 且 `eligible_for_selection(symbol, as_of, min_days=3)` → `record_selection_pool`；
⑥ 批次行 ok + 计数。异常路径：`except` → 批次行 failed（error 文案）+ 当日池**不写任何行**（先写池后写批次 = 错误；必须批次成功才写池，或池写在批次行 ok 的同一事务后）。
- 面板/事件/triage client 全部可注入（测试零网络）；缺省从快照库/库构造。

**S2 — 时效性校验**：`check_signal_freshness(symbols, *, snapshot_rows: dict[str, dict], events_by_symbol: dict[str, list[dict]]) -> list[dict]`：`is_suspended` → `SUSPENDED`；`limit_up_sealed or limit_down_sealed` → `LIMIT_LOCKED`；当日/最近 1 个交易日内新事件（`events_by_symbol` 非空且事件日期 ≥ as_of-1 交易日）→ `NEW_EVENT`。返回 `[{"symbol","reason","detail"}]`（无问题者不返回）；一次可多因（按 S2 顺序取全部）。

**S3 — 批量分析队列**：`screening/queue.py::AnalyzeQueue(max_concurrency=2)`（进程内，单 worker 前提）：`enqueue(symbol, session_id) -> None`；后台 asyncio 任务轮询 `session_store.get_session(session_id)["status"]` 至终态后启动下一个；`snapshot() -> list[dict]`（`{symbol, session_id, state: queued|running|done|failed}`）；`api.py` 的 analyze 端点：对池内符号逐一 `create_session` + `append_chat`（与 `/api/analyze` 新会话路径同款）+ 交给队列；返回 `snapshot()`。终态判定沿用 session status 语义（`completed`/`failed`/`interrupted` 为终态）。
- 纪律：SSE 侧不改任何东西——每个 session 的进度经既有 journal/subscribe 通道（spec：复用既有 SSE，终态事件优先送达纪律不变）。

**S4 — API**：`GET /api/screening/pool` → `{batch_date, batch_status, stale: bool, pool: [...], queue: [...]}`（无成功批次 → `pool: []` + `batch_date: null`）；`GET /api/screening/status` → `{latest_success_date, failed_date, message, stale}`（失败横幅通道，文案「选股批次失败（最近成功: YYYY-MM-DD）」；永不 500）；`POST /api/screening/analyze`（body: `{}`）→ 触发队列，返回 `{started: [...], queued: [...]}`；池空 → 409 显式错误。

**S5 — 前端**：`/screening` 路由（App.tsx pathname 分支 + 侧边栏入口）；页面组件从 `/api/screening/pool` 取数（loading/error/empty 态）；综合分单元格 hover → 浮层（`factor_contribs` 按 |contribution| 降序，显示 factor_id + contribution）；批次 stale → 横幅；分析按钮 → POST analyze 后按 queue 状态轮询（2s）显示排队/运行标识。

**S6 — TESTING 造数**：`/api/test/seed` 扩展可选 `screening: {"batch": {...}, "pool": [...], "queue": [...]}` + `screening_reset: bool`（清三表）；仅 TESTING；供 E2E 确定性构造页面状态。

## 文件结构

| 文件 | 职责 |
|---|---|
| `src/finance_agent/screening/pipeline.py`（新） | 日批编排 + 时效性校验 + 池/批次落库读写 |
| `src/finance_agent/screening/store.py`（改，追加） | `screening_batches`/`selection_pool` 表与读写 |
| `src/finance_agent/screening/queue.py`（新） | 批量分析队列 |
| `src/finance_agent/api.py`（改） | 三端点 + seed 扩展 |
| `frontend/src/pages/screening/ScreeningPage.tsx`（新）+ `App.tsx`（改） | 页面 + 路由/侧边栏 |
| `frontend/src/test/screeningPage.test.tsx`（新） | Vitest |
| `tests/screening/test_pipeline.py`、`tests/test_api_screening.py`（新） | 后端单测 |
| `tests/e2e/playwright/tests/screening-page.spec.ts`（新） | E2E（默认 config） |
| `tests/validation/2026-10-13-add-screening-pipeline-validation.md`（新） | 验证报告（含 E2E 门禁证据 + 5 交易日核对受阻说明） |

---

### Task 1: 日批编排 + 落库

- [ ] **Step 1: 写失败测试**：正常日批（注入合成面板/事件/stub triage）→ 批次 ok、池行 = keep∩eligible、L2 全量落库；**中途失败不产残缺池**（stub triage 抛错 → 批次 failed、当日池零行）；快照不新鲜 → failed；幂等重跑（同日二次覆盖不翻倍）；`latest_successful_pool` 返回最近 ok 批次并带日期。
- [ ] **Step 2–4: 红 → 实现 → 绿**（S1）。
- [ ] **Step 5: Commit** `feat(screening): daily pipeline orchestration with fail-closed pool (T1)`。

### Task 2: 时效性校验

- [ ] **Step 1: 写失败测试**：停牌 → `SUSPENDED`（spec 场景：其余标的不受影响）；一字涨停/跌停 → `LIMIT_LOCKED`；新事件 → `NEW_EVENT`；多因并存；干净标的 → 不在返回列表。
- [ ] **Step 2–4: 红 → 实现 → 绿**（S2）。
- [ ] **Step 5: Commit** `feat(screening): pre-analysis signal freshness check (T2)`。

### Task 3: API 端点 + 队列

- [ ] **Step 1: 写失败测试**（TestClient）：pool 端点（有/无成功批次；含 factor_contribs 与 consecutive_days）；status 端点（失败 → stale + 文案；读库异常 → 降级 200 不 500）；analyze 端点（池空 409；**并发上限 2**——monkeypatch 队列/会话为可控桩，断言同时 running ≤2、其余 queued）；seed 扩展（screening 造数 + reset）。
- [ ] **Step 2–4: 红 → 实现 → 绿**（S3/S4/S6）。
- [ ] **Step 5: Commit** `feat(api): screening pool/status/analyze endpoints with concurrency-2 queue (T3)`。

### Task 4: 前端页面 + Vitest

- [ ] **Step 1: 写失败测试**（Vitest，fetch mock）：表格渲染（列齐 + 批次日期）；hover 综合分 → 浮层含 factor_contribs 按 |contribution| 降序（spec 场景）；stale 横幅；空态；analyze 按钮 → POST + 排队标识渲染。
- [ ] **Step 2–4: 红 → 实现 → 绿**（S5）；`npm run build` 通过。
- [ ] **Step 5: Commit** `feat(frontend): /screening page with contribution hover and batch analysis (T4)`。

### Task 5: E2E + 验证收口

- [ ] E2E spec（默认 config，端口预检后跑）：seed 造池 → 页面表格可见（含 L3 理由与在榜天数）→ hover 综合分断言浮层 → seed 失败批次 → 横幅可见且标注最近成功日期 → analyze 按钮存在（stub 后端不真跑深度分析时以 409/排队态断言之一，按实现钉）。`npx playwright test tests/screening-page.spec.ts` 全绿；默认全量套件记录（既有基线红归因）。
- [ ] 人工验证报告（交互类）：E2E 证据 + 主观项（页面可读性/浮层内容）+ **5 交易日连续日批核对受阻说明**（东财 IP 封禁 → issue #325 补验）。
- [ ] `uv run pytest tests/screening tests/test_api_screening.py -q` + 全量 `tests/ -m "not live"` + `cd frontend && npx vitest run`；ruff/mypy。
- [ ] 报告 + `tasks.md` 全勾 →（控制器 archive）。
