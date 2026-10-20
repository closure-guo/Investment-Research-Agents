# Auto Pool Analysis & Daily Brief Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 选股池自动进入深度分析、分析观点自动进入次日纸面执行，并在每天 20:30 用 opencode `deepseek-v4.1-flash` 生成一份基于当日真实数据的运营日报。

**Architecture:** 新增两个日批任务（16:22 自动池分析、20:30 日报）。自动池分析复用 API 进程内的 `AnalyzeQueue` 与 SSE/journal，经主事件循环 `call_soon_threadsafe` 入队；幂等表 `pool_analysis_runs` 放 `screening.db`。日报事实汇总与 LLM 生成分离，`daily_briefs` 放 `sessions.db`，失败写确定性降级摘要并让任务失败。执行链不改口径，仍由 `paper_execute_daily` 次日开盘消费。

**Tech Stack:** Python 3.14 / FastAPI / APScheduler / SQLite / litellm gateway / React 18 + Vitest + Playwright.

## Global Constraints

- OpenSpec delta `add-auto-pool-analysis-daily-brief` 已 `validate --strict` 通过；实现不得偏离其 Scenario。
- 所有新日批任务必须同时登记 `JOB_IDS/JOB_LABELS/JOB_FUNCS/SCHEDULES` 与 `start_scheduler`，否则 `TestSchedulerRegistryMirror` 红。
- 时间统一 `Asia/Shanghai`；`misfire_grace_time=3600`、`coalesce=True` 沿用 `_JOB_MISFIRE_KW`。
- 自动分析标的数默认 10，env `AUTO_POOL_ANALYSIS_MAX_SYMBOLS`，钳制 1–100。
- 自动分析开关 env `AUTO_POOL_ANALYSIS_ENABLED`，默认 `1`；`0` 时零 LLM、零会话。
- 日报模型：`purpose="daily_brief"`，`DAILY_BRIEF_MODEL/BASE_URL/API_KEY` 逐字段覆盖、否则回退 `LLM_*`；不得使用 `opencode/` 前缀。
- 日报 `max_tokens=4000`、`temperature=0.3`；禁止强制 `response_format=json_object`。
- 日报失败必须 `daily_briefs.status=failed` + 确定性降级摘要 + 任务失败；不得空报告冒充成功。
- 执行链仍为 paper broker；不得引入真实券商调用；`watch/hold/sell` 不得转成买入。
- 所有 SQLite 读取使用参数化 SQL；测试必须走 conftest 的临时库隔离。

---

### Task 1: `daily_brief` purpose 与 resolver 逐字段覆盖

**Files:**
- Modify: `src/finance_agent/llm/types.py:20-32`
- Modify: `src/finance_agent/llm/resolver.py:36-42, 323-328`
- Test: `tests/llm/test_resolver.py`
- Test: `tests/llm/test_router.py`（仅当需要 REQUIRED_CAPS；本任务不需要）

**Interfaces:**
- Produces: `Purpose` 包含 `"daily_brief"`；`resolve_profile(purpose="daily_brief")` 按字段覆盖。

- [ ] **Step 1: Write the failing test**

```python
def test_daily_brief_inherits_llm_env(monkeypatch):
    monkeypatch.setenv("LLM_MODEL", "openai/deepseek-v4.1-flash")
    monkeypatch.setenv("LLM_BASE_URL", "https://opencode.ai/zen/go/v1")
    monkeypatch.setenv("LLM_API_KEY", "k")
    for key in ("DAILY_BRIEF_MODEL", "DAILY_BRIEF_BASE_URL", "DAILY_BRIEF_API_KEY"):
        monkeypatch.delenv(key, raising=False)
    profile = resolve_profile(purpose="daily_brief")
    assert profile.provider == "openai"
    assert profile.model == "openai/deepseek-v4.1-flash"
    assert profile.base_url == "https://opencode.ai/zen/go/v1"
    assert profile.api_key == "k"

def test_daily_brief_model_override_keeps_llm_endpoint(monkeypatch):
    monkeypatch.setenv("LLM_MODEL", "openai/deepseek-v4.1-flash")
    monkeypatch.setenv("LLM_BASE_URL", "https://opencode.ai/zen/go/v1")
    monkeypatch.setenv("LLM_API_KEY", "k")
    monkeypatch.setenv("DAILY_BRIEF_MODEL", "openai/another-model")
    profile = resolve_profile(purpose="daily_brief")
    assert profile.model == "openai/another-model"
    assert profile.base_url == "https://opencode.ai/zen/go/v1"
    assert profile.api_key == "k"
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/llm/test_resolver.py -k daily_brief -v`
Expected: FAIL with `UnknownProviderPrefixError` / unknown purpose or model mismatch.

- [ ] **Step 3: Minimal implementation**

`types.py` 的 `Purpose` 增加 `"daily_brief"`。`resolver.py` 增加：

```python
_DAILY_BRIEF_ENV = {
    "model": "DAILY_BRIEF_MODEL",
    "base_url": "DAILY_BRIEF_BASE_URL",
    "api_key": "DAILY_BRIEF_API_KEY",
}
```

在分支中：

```python
if purpose == "judge":
    mapping = _JUDGE_ENV
elif purpose == "daily_brief":
    mapping = {
        key: (value if env.get(value) else fallback)
        for key, value, fallback in (
            ("model", "DAILY_BRIEF_MODEL", "LLM_MODEL"),
            ("base_url", "DAILY_BRIEF_BASE_URL", "LLM_BASE_URL"),
            ("api_key", "DAILY_BRIEF_API_KEY", "LLM_API_KEY"),
        )
    }
elif purpose == "quick" and env.get("LLM_QUICK_MODEL"):
    mapping = _LLM_QUICK_ENV
else:
    mapping = _LLM_ENV
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/llm/test_resolver.py -k daily_brief -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/finance_agent/llm/types.py src/finance_agent/llm/resolver.py tests/llm/test_resolver.py
git commit -m "feat(llm): add daily_brief purpose with per-field env overlay"
```

---

### Task 2: 池分析编排与幂等表（纯同步、可测）

**Files:**
- Create: `src/finance_agent/screening/freshness.py`（从 `api.py` 迁出 `_screening_freshness_removals`）
- Create: `src/finance_agent/screening/pool_analysis.py`
- Modify: `src/finance_agent/api.py:3516-3563`（改为 import 共享函数）
- Test: `tests/screening/test_pool_analysis.py`

**Interfaces:**
- Produces:
  - `screening_freshness_removals(pool_rows: list[dict], batch_date: str | None) -> list[dict]`
  - `init_pool_analysis_runs(db_path=None) -> None`
  - `record_pool_analysis_run(trade_date, symbol, session_id, status, error=None, db_path=None) -> None`
  - `list_pool_analysis_runs(trade_date, db_path=None) -> list[dict]`
  - `prepare_pool_analysis_batch(*, as_of=None, max_symbols=None, enqueue, db_path=None, screening_db_path=None) -> dict`
  - `run_pool_analysis_daily() -> dict`

- [ ] **Step 1: Write failing tests**

```python
def test_empty_pool_skips(monkeypatch):
    monkeypatch.setenv("SCREENING_DB_PATH", str(tmp_path / "s.db"))
    # seed latest successful batch with pool_size=0
    out = prepare_pool_analysis_batch(enqueue=lambda s, sid: True, as_of="2026-10-20")
    assert out["status"] == "skipped_empty_pool"
    assert out["started"] == 0

def test_stale_pool_skips(monkeypatch):
    # latest successful batch trade_date=2026-10-19
    out = prepare_pool_analysis_batch(enqueue=lambda s, sid: True, as_of="2026-10-20")
    assert out["status"] == "skipped_stale_pool"
    assert out["batch_date"] == "2026-10-19"

def test_top_n_and_idempotent(monkeypatch):
    started = []
    out1 = prepare_pool_analysis_batch(enqueue=lambda s, sid: started.append(s) or True,
                                       as_of="2026-10-20", max_symbols=2)
    assert out1["started"] == 2
    assert started == ["600519", "000001"]  # rank 1,2
    out2 = prepare_pool_analysis_batch(enqueue=lambda s, sid: started.append(s) or True,
                                       as_of="2026-10-20", max_symbols=2)
    assert out2["started"] == 0
    assert out2["skipped_existing"] == 2

def test_freshness_removes_suspended(monkeypatch):
    # patch screening_freshness_removals -> [{"symbol": "600519", "reason": "SUSPENDED"}]
    out = prepare_pool_analysis_batch(enqueue=lambda s, sid: True, as_of="2026-10-20")
    assert "600519" in [r["symbol"] for r in out["removed"]]
    assert "600519" not in out["started_symbols"]
```

Seed helper: use `record_screening_batch(trade_date, status="ok", ..., pool_rows=[...])` from `screening.store`.

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/screening/test_pool_analysis.py -v`
Expected: FAIL `ModuleNotFoundError: finance_agent.screening.pool_analysis`.

- [ ] **Step 3: Implement**

- 迁移 freshness：复制 `_screening_freshness_removals` 到 `screening/freshness.py`，改名
  `screening_freshness_removals`；`api.py` 改为 `from finance_agent.screening.freshness import screening_freshness_removals`，
  保留 `_screening_freshness_removals = screening_freshness_removals` 别名以防测试引用。
- `pool_analysis.py` 建表与读写：

```python
_SCHEMA = """
CREATE TABLE IF NOT EXISTS pool_analysis_runs (
  trade_date TEXT NOT NULL, symbol TEXT NOT NULL, session_id TEXT NOT NULL,
  status TEXT NOT NULL, error TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
  PRIMARY KEY (trade_date, symbol)
)"""
```

- `prepare_pool_analysis_batch` 逻辑：读 `latest_successful_pool()`；日期校验；`pool[:max_symbols]`；
  freshness；逐标的 `already_analyzed`；`create_analysis_session(symbol, display_name=f"{symbol} 自动选股分析", source="auto_pool")`；
  `record_pool_analysis_run(..., status="queued")`；`enqueue(symbol, session_id)`；失败记
  `dispatch_failed` 并计数。返回摘要。
- `create_analysis_session` 放同模块，序列与 api 端点一致（create_session → append_chat →
  set_pipeline_anchor → update_session_for_clarify → append_session_event）。

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/screening/test_pool_analysis.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/finance_agent/screening/freshness.py src/finance_agent/screening/pool_analysis.py src/finance_agent/api.py tests/screening/test_pool_analysis.py
git commit -m "feat(screening): add idempotent auto pool analysis preparation"
```

---

### Task 3: 主循环桥接 + `auto_pool_analysis_daily` 注册

**Files:**
- Modify: `src/finance_agent/api.py`（lifespan `_MAIN_LOOP`、`submit_pool_analysis_batch`、`_enqueue_pool_analysis`）
- Modify: `src/finance_agent/outcome/ops/jobs.py`（四注册表）
- Modify: `src/finance_agent/outcome/scheduler.py`（`_auto_pool_analysis_job` + `add_job`）
- Test: `tests/screening/test_pool_analysis_job.py`
- Test: `tests/outcome/test_ops_jobs.py`, `tests/outcome/test_scheduler.py`（计数 24→26 的一部分）

**Interfaces:**
- Consumes: Task 2 `prepare_pool_analysis_batch`。
- Produces: `run_pool_analysis_daily() -> dict`；job id `auto_pool_analysis_daily`，16:22 mon-fri。

- [ ] **Step 1: Write failing test**

```python
def test_job_disabled_skips(monkeypatch):
    monkeypatch.setenv("AUTO_POOL_ANALYSIS_ENABLED", "0")
    assert run_pool_analysis_daily()["status"] == "skipped_disabled"

def test_job_uses_submitter(monkeypatch):
    monkeypatch.setenv("AUTO_POOL_ANALYSIS_ENABLED", "1")
    monkeypatch.setenv("AUTO_POOL_ANALYSIS_MAX_SYMBOLS", "3")
    monkeypatch.setattr(pool_analysis, "_submit", lambda **kw: {"status": "ok", "max_symbols": kw["max_symbols"]})
    assert run_pool_analysis_daily() == {"status": "ok", "max_symbols": 3}
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/screening/test_pool_analysis_job.py -v`
Expected: FAIL (missing `run_pool_analysis_daily` or env handling).

- [ ] **Step 3: Implement**

`api.py`:

```python
_MAIN_LOOP: asyncio.AbstractEventLoop | None = None
# lifespan startup:
_MAIN_LOOP = asyncio.get_running_loop()
# lifespan shutdown:
_MAIN_LOOP = None

def _enqueue_pool_analysis(symbol: str, session_id: str) -> bool:
    loop = _MAIN_LOOP
    if loop is None or loop.is_closed():
        return False
    loop.call_soon_threadsafe(_get_analyze_queue().enqueue, symbol, session_id)
    return True

def submit_pool_analysis_batch(*, max_symbols: int | None = None, timeout: float = 120.0) -> dict:
    loop = _MAIN_LOOP
    if loop is None or loop.is_closed():
        raise RuntimeError("主事件循环不可用")
    async def _run() -> dict:
        return await asyncio.to_thread(
            prepare_pool_analysis_batch,
            max_symbols=max_symbols,
            enqueue=_enqueue_pool_analysis,
        )
    return asyncio.run_coroutine_threadsafe(_run(), loop).result(timeout=timeout)
```

`pool_analysis.py`:

```python
def _submit(**kwargs):
    from finance_agent import api
    return api.submit_pool_analysis_batch(**kwargs)

def run_pool_analysis_daily() -> dict:
    if os.getenv("AUTO_POOL_ANALYSIS_ENABLED", "1").strip() == "0":
        return {"status": "skipped_disabled"}
    raw = os.getenv("AUTO_POOL_ANALYSIS_MAX_SYMBOLS", "10")
    try:
        max_symbols = max(1, min(100, int(raw)))
    except ValueError:
        max_symbols = 10
    return _submit(max_symbols=max_symbols)
```

`ops/jobs.py` 四表加 `auto_pool_analysis_daily`；`SCHEDULES` 16:22。`scheduler.py` 加
`_auto_pool_analysis_job = lambda: _with_retry("auto_pool_analysis_daily", ..., run_pool_analysis_daily)`
与 `add_job(..., id="auto_pool_analysis_daily", ...)`。

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/screening/test_pool_analysis_job.py tests/outcome/test_ops_jobs.py::TestRegistry -v`
Expected: job tests PASS；registry count 仍可能 24→25，Task 3 结束时改为 25（Task 4 再加日报任务到 26）。

- [ ] **Step 5: Commit**

```bash
git add src/finance_agent/api.py src/finance_agent/screening/pool_analysis.py src/finance_agent/outcome/ops/jobs.py src/finance_agent/outcome/scheduler.py tests/screening/test_pool_analysis_job.py tests/outcome/test_ops_jobs.py tests/outcome/test_scheduler.py
git commit -m "feat(ops): schedule automatic screening-pool deep analysis at 16:22"
```

---

### Task 4: 每日汇报模块 + prompt + 存储

**Files:**
- Create: `src/finance_agent/prompts/daily_brief.md`
- Create: `src/finance_agent/outcome/daily_brief.py`
- Test: `tests/outcome/test_daily_brief.py`

**Interfaces:**
- Produces:
  - `collect_daily_facts(as_of: str, *, sessions_db_path=None, screening_db_path=None, execution_db_path=None, factor_db_path=None) -> dict`
  - `render_fallback(facts: dict, error: str) -> str`
  - `run_daily_brief() -> dict`
  - `init_daily_briefs(db_path=None)`, `upsert_daily_brief(...)`, `get_daily_brief(date=None, ...)`

- [ ] **Step 1: Write failing tests**

```python
def test_collect_facts_includes_sections(monkeypatch):
    facts = collect_daily_facts("2026-10-20")
    for key in ("screening", "pool", "analyses", "predictions", "execution", "factors", "failures"):
        assert key in facts

def test_llm_success_persists(monkeypatch):
    monkeypatch.setattr(daily_brief, "complete_text", lambda *a, **k: ("# 日报\n内容", {"model": "m"}))
    monkeypatch.setattr(daily_brief, "load_prompt_with_meta", lambda name: SimpleNamespace(template="{{daily_facts}}", prompt_version=3))
    monkeypatch.setenv("REPORTS_DIR", str(tmp_path / "reports"))
    out = run_daily_brief(as_of="2026-10-20")
    assert out["status"] == "ok"
    row = get_daily_brief("2026-10-20")
    assert row["markdown"].startswith("# 日报")
    assert (tmp_path / "reports" / "daily-brief-2026-10-20.md").exists()

def test_llm_failure_writes_fallback_and_raises(monkeypatch):
    monkeypatch.setattr(daily_brief, "complete_text", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    with pytest.raises(RuntimeError):
        run_daily_brief(as_of="2026-10-20")
    row = get_daily_brief("2026-10-20")
    assert row["status"] == "failed"
    assert "LLM 生成失败" in row["markdown"]

def test_same_date_rerun_upserts(monkeypatch):
    # first ok, then ok with different markdown
    run_daily_brief(as_of="2026-10-20")
    run_daily_brief(as_of="2026-10-20")
    assert len(list_daily_briefs()) == 1
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/outcome/test_daily_brief.py -v`
Expected: FAIL `ModuleNotFoundError`.

- [ ] **Step 3: Implement**

- `prompts/daily_brief.md`：

```markdown
你是量化交易系统的运营日报编辑。仅根据 <facts> 中的事实写一份中文 Markdown 日报。
不得编造任何数字、标的、收益或事件；缺失数据写「无数据」。
必须包含：## 今日结论 / ## 选股与精选池 / ## 分析与决策 / ## 交易与持仓 / ## 风险与异常 / ## 明日关注。
全文不超过 1200 字。

<facts>
{{daily_facts}}
</facts>
```

- `daily_brief.py`：`collect_daily_facts` 用参数化 SQL 汇总：
  - screening：`screening_batches` 当日行（`trade_date,status,error,l1_passed,l2_top,l3_keep,pool_size,selection_mode`）+ `selection_pool` 前 10。
  - analyses：`pool_analysis_runs` 当日行 + `sessions.status`。
  - predictions：`predictions` 当日 created/resolved 计数、`open` 总数、已结算 `raw_return`。
  - execution：`orders` 当日行、`positions`、`cash`、`paper_equity` 最近两行。
  - factors：`factors` active 数、`factor_ic` 最近 20 个交易日按 factor 聚合均值/样本数。
  - failures：`job_runs` 当日 `status in ('failed','skipped-disabled')` 的 `job_id/error`。
  - 每个来源 try/except → `warnings`，不阻断。
- `run_daily_brief(as_of=None)`：日期默认 `datetime.now().strftime("%Y-%m-%d")`；成功/失败两条路径；
  文件写 `Path(os.getenv("REPORTS_DIR","reports")) / f"daily-brief-{as_of}.md"`。
- 表 `daily_briefs` 懒建；`upsert_daily_brief` 用 `ON CONFLICT(brief_date) DO UPDATE`。

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/outcome/test_daily_brief.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/finance_agent/prompts/daily_brief.md src/finance_agent/outcome/daily_brief.py tests/outcome/test_daily_brief.py
git commit -m "feat(ops): add daily LLM operations brief with deterministic fallback"
```

---

### Task 5: 日报任务注册 + API + `/ops` 前端

**Files:**
- Modify: `src/finance_agent/outcome/ops/jobs.py`, `src/finance_agent/outcome/scheduler.py`（`daily_brief_daily`, 20:30）
- Modify: `src/finance_agent/ops_api.py`（`GET /api/v1/ops/daily-brief` + `/latest`）
- Modify: `src/finance_agent/api.py`（`/api/test/seed` 支持 `daily_brief`）
- Modify: `frontend/src/pages/ops/DailyOpsPage.tsx`（每日汇报卡片）
- Test: `tests/test_ops_api.py`, `frontend/src/test/dailyOpsPage.test.tsx`

**Interfaces:**
- Consumes: Task 4 `run_daily_brief`, `get_daily_brief`, `list_daily_briefs`.
- Produces: `GET /api/v1/ops/daily-brief?date=`, `GET /api/v1/ops/daily-brief/latest`; `data-testid="daily-brief"`.

- [ ] **Step 1: Write failing tests**

Backend:

```python
def test_daily_brief_latest_returns_row(client, monkeypatch):
    upsert_daily_brief("2026-10-20", "ok", "# 日报", "m", "1", None)
    resp = client.get("/api/v1/ops/daily-brief/latest")
    assert resp.status_code == 200
    assert resp.json()["markdown"] == "# 日报"

def test_daily_brief_404(client):
    resp = client.get("/api/v1/ops/daily-brief?date=1999-01-01")
    assert resp.status_code == 404
    assert resp.json()["error"] == "daily_brief_not_found"
```

Frontend (mock fetch for `/api/v1/ops/daily-brief/latest`):

```tsx
it('展示最新日报', async () => {
  render(<App />) // pushState('/ops')
  expect(await screen.findByTestId('daily-brief')).toBeInTheDocument()
  expect(screen.getByTestId('daily-brief-markdown')).toHaveTextContent('今日结论')
})
it('无日报时为空态', async () => { /* 404 -> 空态文案 */ })
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_ops_api.py -k daily_brief -v` and `cd frontend && npm test -- dailyOpsPage`
Expected: FAIL 404 / missing testid.

- [ ] **Step 3: Implement**

- 注册 `daily_brief_daily` 20:30 mon-fri；`JOB_IDS` 到 26。
- `ops_api.py`：

```python
@app.get("/api/v1/ops/daily-brief")
def get_brief(date: str | None = None):
    row = get_daily_brief(date)
    if row is None:
        return JSONResponse(status_code=404, content={"error": "daily_brief_not_found"})
    return row

@app.get("/api/v1/ops/daily-brief/latest")
def get_latest_brief():
    row = get_daily_brief(None)
    ...
```

- `api.py` seed：`req.get("daily_brief")` → `upsert_daily_brief(**payload)`。
- `DailyOpsPage.tsx`：新增 state `brief`；`fetch('/api/v1/ops/daily-brief/latest')`；404 → null；
  `status` badge；`ReactMarkdown` + `remarkGfm` 渲染；`data-testid="daily-brief"`、
  `daily-brief-status`、`daily-brief-error`、`daily-brief-markdown`。
- 更新 `dailyOpsPage.test.tsx` 的 fetch mock：未知 URL 返回 404，新增日报成功/空态/失败三例。

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_ops_api.py -k daily_brief -v` and `cd frontend && npm test -- dailyOpsPage`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/finance_agent/outcome/ops/jobs.py src/finance_agent/outcome/scheduler.py src/finance_agent/ops_api.py src/finance_agent/api.py frontend/src/pages/ops/DailyOpsPage.tsx frontend/src/test/dailyOpsPage.test.tsx tests/test_ops_api.py
git commit -m "feat(ops): expose daily brief API and render it on /ops"
```

---

### Task 6: 注册表/E2E 同步与全量验证

**Files:**
- Modify: `tests/outcome/test_ops_jobs.py`（24→26、顺序）
- Modify: `tests/outcome/test_scheduler.py`（24→26、ID 集合、misfire）
- Modify: `tests/factors/test_monitor.py`（顺序断言）
- Modify: `tests/e2e/playwright/tests/daily-ops.spec.ts`（26、索引、日报区）
- Modify: `tests/e2e/playwright/tests/eval-ops-console.spec.ts`（仅测试名/数量若硬编码）
- Test: 上述文件

- [ ] **Step 1: Update assertions**

  - `JOB_IDS` 顺序：`... paper_settle_daily, auto_pool_analysis_daily, event_settle_daily, ... daily_brief_daily, cohort_batch`（按时刻）。
  - `len == 26`；`TestSchedulerRegistryMirror` 自动跟随。
  - E2E：`toHaveCount(26)`；在 16:20 后插入 16:22 自动选股分析；18:00 后加 20:30 每日汇报；
    循环上限 26。

- [ ] **Step 2: Run**

```bash
uv run pytest tests/outcome/test_ops_jobs.py tests/outcome/test_scheduler.py tests/factors/test_monitor.py tests/test_ops_api.py -v
cd frontend && npm test
cd tests/e2e/playwright && npx playwright test daily-ops.spec.ts
```

Expected: 全绿；E2E 若因端口/栈不可用，按 docs 的专用配置跑并记录。

- [ ] **Step 3: Commit**

```bash
git add tests/outcome/test_ops_jobs.py tests/outcome/test_scheduler.py tests/factors/test_monitor.py tests/e2e/playwright/tests/daily-ops.spec.ts tests/e2e/playwright/tests/eval-ops-console.spec.ts
git commit -m "test(ops): sync registry and E2E expectations for 26 jobs and daily brief"
```

---

### Task 7: 全量验证与人工验证

**Files:**
- Create: `tests/validation/2026-10-20-add-auto-pool-analysis-daily-brief-validation.md`

- [ ] **Step 1: Run backend verification**

```bash
uv run pytest -q
uv run ruff check
uv run mypy
```

Expected: 0 failures；记录输出。

- [ ] **Step 2: Run frontend verification**

```bash
cd frontend && npm test
cd frontend && npx tsc --noEmit
```

- [ ] **Step 3: E2E gate**

```bash
cd tests/e2e/playwright && npx playwright test daily-ops.spec.ts
```

- [ ] **Step 4: Manual validation report**

记录：自动分析任务在 `/ops` 可见（16:22）；日报区空态/失败态/正常态；日报内容与库内数字逐项对照；
纸面执行仍为 0 或按模型 buy 正常成交。落 `tests/validation/2026-10-20-...md`。

- [ ] **Step 5: Commit**

```bash
git add tests/validation/2026-10-20-add-auto-pool-analysis-daily-brief-validation.md
git commit -m "docs(validation): record auto pool analysis and daily brief verification"
```
