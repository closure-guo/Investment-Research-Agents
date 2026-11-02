# 小市值精选池 UI（add-microcap-pool-ui）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 小市值（microcap）精选池经读侧端点与选股页档位切换可见（门禁/约束披露，v1 只展示）。

**Architecture:** store 层已全量档位参数化，本计划只做三层接线：API 端点暴露 `pool: Literal["main","microcap"]` 参数并透传批次记录字段 → seed 端点补档位/门禁造数 → ScreeningPage 加档位切换与小市值披露区块。

**Tech Stack:** FastAPI（Literal 查询参数）、pytest、React 18 + vitest + testing-library、Playwright（tests/e2e/playwright）。

**delta:** `openspec/changes/add-microcap-pool-ui/`（proposal / design D1–D5 / specs×2 / tasks）

## Global Constraints

- **main 档缺省路径零漂移**：不带 `pool` 参数的池/批次响应，既有字段语义逐字不变；既有 E2E 断言（`screening-row-*`、`screening-queue-badge-*`、`screening-stock-name-*`、`screening-score-*`）不得改名。
- 非法档位 → 422（FastAPI Literal 自动），不静默回退。
- 代码注释中文；前端变量 camelCase；后端常量从 `pipeline.py` 复用口径、不复制语义（前端仅复制 `observation_only:missing=` 前缀字面量，由 E2E 门禁用例钉住漂移）。
- E2E 红线：禁止 route.fulfill/MSW mock 业务接口；种子经 `POST /api/test/seed`（TESTING=1）真实写 `data/test-e2e-screening.db`。
- 本机跑 E2E 前：8000/5173 被生产容器占用时停容器或用隔离 config（incident 038 纪律）；停容器属重启红线——先查 `GET /api/sessions` 无 running 会话、`GET /api/v1/ops/jobs` 无 running 作业。
- 每任务完成即 commit；分支 `feature/microcap-pool-ui`（origin/main 须走 PR，禁 merge commit）。
- 执行时按 `superpowers:using-git-worktrees` 建 worktree（共享检出纪律）。

---

### Task 1: 后端池/批次端点档位参数化

**Files:**
- Modify: `src/finance_agent/screening/pipeline.py:729-738`（`latest_successful_pool`）
- Modify: `src/finance_agent/api.py`（`_screening_pool_payload` ~3786-3805、`screening_pool` 端点 ~3827-3835、`screening_batches` 端点 ~3871-3882、typing 导入、store 导入块）
- Test: `tests/test_api_screening.py`（文件末尾追加）

**Interfaces:**
- Consumes: `store.latest_successful_batch(db_path, *, pool)`、`store.list_selection_pool(trade_date, db_path, pool)`、`store.latest_screening_batch(db_path, *, pool)`、`store.list_batches(limit, db_path, *, pool)`（均已支持 pool，零改动）。
- Produces: `pipeline.latest_successful_pool(*, db_path=None, pool='main') -> dict | None`；`api._screening_pool_payload(queue_snapshot, pool='main')`；`GET /api/screening/pool?pool=` 与 `GET /api/screening/batches?pool=`；池响应新增顶层键 `frontier_gate: str | null`、`exec_constraints: dict | null`（main 档恒 null）。

- [ ] **Step 1: Write the failing test**

追加到 `tests/test_api_screening.py` 末尾（复用文件既有 `_pool_rows` / `pool_seed` / `_AS_OF`；测试客户端模式沿用文件内 `TestClient(app)` 直构）：

```python
# ── 池档位（add-microcap-pool-ui） ──────────────────────────────────────────

_MICROCAP_DATE = "2026-10-15"
_MICROCAP_GATE = "observation_only:missing=regime_data"
_MICROCAP_CONSTRAINTS = {"suggested_single_cap": 0.05, "slippage_tier_bump": 1}


def _seed_microcap_batch(db_path, trade_date: str = _MICROCAP_DATE) -> None:
    """microcap 档成功批次 + 2 池行（复用 _pool_rows；门禁/约束为批次记录字段）。"""
    record_screening_batch(
        trade_date,
        status="ok",
        started_at=f"{trade_date}T16:45:00",
        finished_at=f"{trade_date}T16:47:00",
        l1_passed=30,
        l2_top=12,
        l3_keep=2,
        pool_rows=_pool_rows(2),
        selection_mode="factor",
        pool="microcap",
        frontier_gate=_MICROCAP_GATE,
        exec_constraints=dict(_MICROCAP_CONSTRAINTS),
        db_path=db_path,
    )


def test_pool_default_main_unchanged(pool_seed, _screening_db) -> None:
    """缺省（不带 pool 参数）= main 档：既有字段语义零漂移，microcap 行不混入。"""
    _seed_microcap_batch(_screening_db)
    body = TestClient(app).get("/api/screening/pool").json()

    assert body["batch_date"] == _AS_OF
    assert body["frontier_gate"] is None
    assert body["exec_constraints"] is None
    assert all(row["symbol"].startswith("60000") for row in body["pool"])


def test_pool_microcap_tier(pool_seed, _screening_db) -> None:
    _seed_microcap_batch(_screening_db)
    body = TestClient(app).get("/api/screening/pool", params={"pool": "microcap"}).json()

    assert body["batch_date"] == _MICROCAP_DATE
    assert body["frontier_gate"] == _MICROCAP_GATE
    assert body["exec_constraints"] == _MICROCAP_CONSTRAINTS
    assert len(body["pool"]) == 2
    assert all(row["symbol"].startswith("60000") for row in body["pool"])


def test_pool_microcap_empty(_screening_db) -> None:
    """microcap 尚无成功批次 → 200 空态，不报错（池与批次历史两接口）。"""
    client = TestClient(app)
    body = client.get("/api/screening/pool", params={"pool": "microcap"}).json()

    assert body["batch_date"] is None
    assert body["pool"] == []
    assert body["frontier_gate"] is None
    assert body["exec_constraints"] is None

    assert client.get("/api/screening/batches", params={"pool": "microcap"}).json() == []


def test_pool_invalid_tier_422() -> None:
    client = TestClient(app)

    assert client.get("/api/screening/pool", params={"pool": "foo"}).status_code == 422
    assert client.get("/api/screening/batches", params={"pool": "foo"}).status_code == 422


def test_batches_default_excludes_microcap(_screening_db) -> None:
    _seed_microcap_batch(_screening_db)
    rows = TestClient(app).get("/api/screening/batches").json()

    assert rows and all(row["pool"] == "main" for row in rows)


def test_batches_microcap_tier(_screening_db) -> None:
    _seed_microcap_batch(_screening_db)
    rows = TestClient(app).get("/api/screening/batches", params={"pool": "microcap"}).json()

    assert [row["trade_date"] for row in rows] == [_MICROCAP_DATE]
    assert rows[0]["frontier_gate"] == _MICROCAP_GATE
    assert rows[0]["exec_constraints"] == _MICROCAP_CONSTRAINTS
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_api_screening.py -k "microcap or invalid_tier or default_main or default_excludes" -v`
Expected: FAIL —— `test_pool_default_main_unchanged` 断言 `frontier_gate` 键不存在（KeyError）；`test_pool_invalid_tier_422` 返回 200（FastAPI 无此参数，忽略未知 query）；其余按缺省 main 返回错档数据。

- [ ] **Step 3: Write minimal implementation**

3a. `src/finance_agent/screening/pipeline.py` —— `latest_successful_pool` 加 `pool` kwarg（确认 `POOL_MAIN` 已在导入块，pipeline.py:604 区域已用 `POOL_MICROCAP`）：

```python
def latest_successful_pool(
    *, db_path: str | Path | None = None, pool: str = POOL_MAIN
) -> dict[str, Any] | None:
    """最近成功批次 + 其当日精选池；无成功批次 → None（前端回退空态/旧日期标注）。

    返回 ``{"batch": {...}, "pool": [池行...]}``；池行含解析后的 ``factor_contribs``。
    ``pool`` 缺省 ``'main'``（既有页面/接口不变）；小票通道读侧显式传
    ``pool='microcap'``（add-microcap-pool-ui）。
    """
    batch = latest_successful_batch(db_path, pool=pool)
    if batch is None:
        return None
    return {
        "batch": batch,
        "pool": list_selection_pool(batch["trade_date"], db_path=db_path, pool=pool),
    }
```

3b. `src/finance_agent/api.py` —— ① `typing` 导入补 `Literal`（若无）；② `finance_agent.screening.store` 导入块补 `POOL_MAIN`；③ 端点附近模块级定义档位别名；④ `_screening_pool_payload` 与两个端点改造：

```python
# 池档位（add-microcap-pool-ui）：Literal 违例由 FastAPI 自动 422（非法值不静默回退）
ScreeningPoolTier = Literal["main", "microcap"]
```

```python
def _screening_pool_payload(
    queue_snapshot: list[dict], pool: str = POOL_MAIN
) -> dict[str, Any]:
    """池端点载荷（同步体，供 to_thread 包裹）：最近成功池 + 最近尝试状态 + 队列。

    披露（add-screening-empty-library-fallback）：``selection_mode`` 取自最近成功
    批次；``fallback`` 时附 ``notice``（明示非因子信号），``factor`` 时为 ``None``。
    add-microcap-pool-ui：``pool`` 缺省 ``'main'``（既有契约零漂移）；批次记录字段
    ``frontier_gate`` / ``exec_constraints`` 随最近成功批次透传（main 档批次该两
    字段落库即 ``None``）；microcap 档不挂分析队列（v1 不触发池分析，队列属主档）。
    """
    latest = latest_screening_batch(pool=pool)
    successful = latest_successful_pool(pool=pool)
    batch = successful["batch"] if successful else None
    selection_mode = batch.get("selection_mode") if batch else None
    return {
        "batch_date": batch["trade_date"] if batch else None,
        "batch_status": latest["status"] if latest else None,
        # 最近一次尝试非 ok（含失败）→ stale：页面横幅可见，池仍展示最近成功批次
        "stale": bool(latest is not None and latest["status"] != "ok"),
        "selection_mode": selection_mode,
        "notice": _SCREENING_FALLBACK_NOTICE if selection_mode == "fallback" else None,
        "factor_signal": _factor_signal_block(selection_mode),
        "frontier_gate": batch.get("frontier_gate") if batch else None,
        "exec_constraints": batch.get("exec_constraints") if batch else None,
        "pool": successful["pool"] if successful else [],
        "queue": queue_snapshot if pool == POOL_MAIN else [],
    }
```

```python
@app.get("/api/screening/pool")
async def screening_pool(pool: ScreeningPoolTier = "main") -> dict[str, Any]:
    """选股页取数（S4）：最近成功批次池（含贡献分解）+ 批次状态 + 队列快照。

    无成功批次 → ``batch_date=None`` + 空池；最近一次尝试失败 → ``stale=True``，
    池仍为最近成功批次（spec：页面标注其日期）。``pool`` 缺省 ``'main'``——既有
    消费者零变化；``pool=microcap`` 返回小票通道池（add-microcap-pool-ui）。
    """
    queue_snapshot = _get_analyze_queue().snapshot()
    return await asyncio.to_thread(_screening_pool_payload, queue_snapshot, pool)
```

```python
@app.get("/api/screening/batches")
async def screening_batches(
    limit: int = DEFAULT_BATCH_HISTORY_LIMIT, pool: ScreeningPoolTier = "main"
) -> list[dict[str, Any]]:
    """选股批次历史（delta add-daily-ops-page T1；spec「批次历史查询」）。

    按交易日期倒序返回近 ``limit`` 批次（默认 20），字段 = 日期 / 状态（ok/failed）/
    错误 / 起止时间 / 各层计数（l1_passed/l2_top/l3_keep）/ 池数（pool_size）。
    空库 → 200 + 空列表（不以错误冒充「无历史」）；``limit`` 钳制到
    ``[1, MAX_BATCH_HISTORY_LIMIT]``（越界不 422——越界值只影响窗口大小，钳制后再
    交给 store 的严格校验）。``pool`` 缺省 ``'main'``，``pool=microcap`` 返回小票
    通道批次历史（add-microcap-pool-ui）。只读端点：无任何写操作暴露。
    """
    clamped = max(1, min(limit, MAX_BATCH_HISTORY_LIMIT))
    return await asyncio.to_thread(list_batches, clamped, None, pool=pool)
```

注意 `latest_screening_batch` 签名为 `(db_path=None, *, pool=POOL_MAIN)`，`list_batches` 为 `(limit, db_path=None, *, pool)`——`to_thread` 传参按上例。

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_api_screening.py -v`
Expected: 全 PASS（既有用例一并回归——缺省路径零漂移由它们钉住）。

- [ ] **Step 5: Lint + 类型 + Commit**

Run: `uv run ruff check && uv run mypy`
Expected: 0 error。

```bash
git add src/finance_agent/screening/pipeline.py src/finance_agent/api.py tests/test_api_screening.py
git commit -m "feat(screening): 池与批次历史端点按档位查询（add-microcap-pool-ui T1）"
```

---

### Task 2: seed 端点档位/门禁造数透传

**Files:**
- Modify: `src/finance_agent/api.py`（seed 端点 `screening` 造数块 ~1122-1150 + 该块 docstring）
- Test: `tests/test_api_screening.py`（追加）

**Interfaces:**
- Consumes: Task 1 的端点（断言用）；`record_screening_batch(..., pool=, frontier_gate=, exec_constraints=)`（store 已支持）。
- Produces: seed 载荷 `screening.batches[]` 与 `screening.batch` 行新增可选键 `tier`（档位，缺省 `"main"`）、`frontier_gate`（str | null）、`exec_constraints`（dict | null）；既有键 `pool`（池行列表）语义不变。E2E（Task 4）依赖本接口造两档并存数据。

- [ ] **Step 1: Write the failing test**

追加（`testing_client` 为文件既有 fixture，~line 482，TESTING=1 reload 模式）：

```python
def test_seed_screening_microcap_tier(testing_client) -> None:
    """seed 透传档位/门禁/约束（add-microcap-pool-ui T2）：两档并存、缺省只见 main。"""
    resp = testing_client.post(
        "/api/test/seed",
        json={
            "screening": {
                "batches": [
                    {
                        "trade_date": _AS_OF,
                        "status": "ok",
                        "started_at": f"{_AS_OF}T15:30:00",
                        "finished_at": f"{_AS_OF}T15:33:00",
                        "l1_passed": 20,
                        "l2_top": 9,
                        "l3_keep": 2,
                        "pool": _pool_rows(1),
                        "tier": "main",
                    },
                    {
                        "trade_date": _MICROCAP_DATE,
                        "status": "ok",
                        "started_at": f"{_MICROCAP_DATE}T16:45:00",
                        "finished_at": f"{_MICROCAP_DATE}T16:47:00",
                        "l1_passed": 30,
                        "l2_top": 12,
                        "l3_keep": 1,
                        "pool": _pool_rows(2),
                        "tier": "microcap",
                        "frontier_gate": _MICROCAP_GATE,
                        "exec_constraints": _MICROCAP_CONSTRAINTS,
                    },
                ]
            }
        },
    )
    assert resp.status_code == 200

    main_rows = testing_client.get("/api/screening/batches").json()
    assert [row["trade_date"] for row in main_rows] == [_AS_OF]

    micro_rows = testing_client.get(
        "/api/screening/batches", params={"pool": "microcap"}
    ).json()
    assert [row["trade_date"] for row in micro_rows] == [_MICROCAP_DATE]
    assert micro_rows[0]["frontier_gate"] == _MICROCAP_GATE
    assert micro_rows[0]["exec_constraints"] == _MICROCAP_CONSTRAINTS

    micro_pool = testing_client.get(
        "/api/screening/pool", params={"pool": "microcap"}
    ).json()
    assert len(micro_pool["pool"]) == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_api_screening.py::test_seed_screening_microcap_tier -v`
Expected: FAIL —— `micro_rows` 为空列表（seed 把 `tier:"microcap"` 行落成了 main 档，microcap 档无批次）。

- [ ] **Step 3: Write minimal implementation**

`src/finance_agent/api.py` seed 端点 `screening` 造数块（两处 `_seed_batch` 调用各加三参；注释键名说明）。`batches` 历史循环：

```python
batches_seed = screening_seed.get("batches")
if isinstance(batches_seed, list):
    for history_row in batches_seed:
        trade_date = history_row["trade_date"]
        _seed_batch(
            trade_date,
            status=history_row.get("status", "ok"),
            error=history_row.get("error"),
            started_at=history_row.get("started_at", _now()),
            finished_at=history_row.get("finished_at", _now()),
            l1_passed=history_row.get("l1_passed", 0),
            l2_top=history_row.get("l2_top", 0),
            l3_keep=history_row.get("l3_keep", 0),
            pool_rows=history_row.get("pool") or [],
            selection_mode=history_row.get("selection_mode", "factor"),
            # add-microcap-pool-ui：档位/门禁/约束透传。既有 ``pool`` 键是池行列表
            # （历史语义不动），档位走独立 ``tier`` 键（缺省 main，缺省路径零漂移）
            pool=history_row.get("tier", "main"),
            frontier_gate=history_row.get("frontier_gate"),
            exec_constraints=history_row.get("exec_constraints"),
        )
```

单数 `batch` 块同样加：

```python
batch_seed = screening_seed.get("batch")
if isinstance(batch_seed, dict):
    trade_date = batch_seed["trade_date"]
    _seed_batch(
        trade_date,
        status=batch_seed.get("status", "ok"),
        error=batch_seed.get("error"),
        started_at=batch_seed.get("started_at", _now()),
        finished_at=batch_seed.get("finished_at", _now()),
        l1_passed=batch_seed.get("l1_passed", 0),
        l2_top=batch_seed.get("l2_top", 0),
        l3_keep=batch_seed.get("l3_keep", 0),
        pool_rows=screening_seed.get("pool") or [],
        selection_mode=batch_seed.get("selection_mode", "factor"),
        pool=batch_seed.get("tier", "main"),
        frontier_gate=batch_seed.get("frontier_gate"),
        exec_constraints=batch_seed.get("exec_constraints"),
    )
```

同函数 docstring 的 S6 段补一句（键名契约）：``batches[]/batch 行可选 tier（档位，缺省 main）、frontier_gate、exec_constraints（add-microcap-pool-ui）``。

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_api_screening.py -v`
Expected: 全 PASS。

- [ ] **Step 5: Lint + Commit**

Run: `uv run ruff check && uv run mypy`

```bash
git add src/finance_agent/api.py tests/test_api_screening.py
git commit -m "feat(screening): seed 造数透传档位/门禁/约束（add-microcap-pool-ui T2）"
```

---

### Task 3: 前端档位切换与小市值披露

**Files:**
- Modify: `frontend/src/types.ts:874-893`（`ScreeningPoolResponse` 加两字段）
- Modify: `frontend/src/pages/screening/ScreeningPage.tsx`（全页改造，完整代码见下）
- Test: `frontend/src/test/screeningPage.test.tsx`（追加 describe；`mockFetch` 扩展 pool 参数分发）

**Interfaces:**
- Consumes: Task 1 的响应契约（`frontier_gate` / `exec_constraints` / `pool` 参数）。
- Produces: testid 契约——档位切换 `screening-tier-main` / `screening-tier-microcap`；microcap 行 `screening-microcap-row-{symbol}`（同构列 + `screening-microcap-queue-badge-*` / `screening-microcap-stock-name-*` / `screening-microcap-score-*`）；门禁 `screening-microcap-gate-ready` / `screening-microcap-gate-observation`；约束 `screening-microcap-constraints`；声明 `screening-microcap-observation-note`；空态 `screening-microcap-empty`。main 档全部既有 testid 逐字不变。

- [ ] **Step 1: Write the failing test**

1a. `frontend/src/types.ts` 先补类型（否则测试编译不过）——`ScreeningPoolResponse` 的 `queue: ScreeningQueueEntry[]` 行之前插入：

```ts
  // 小票通道门禁与建议执行约束（add-microcap-pool-ui）：批次级记录字段透传，
  // main 档批次与无成功批次时为 null
  frontier_gate: string | null
  exec_constraints: Record<string, number> | null
```

1b. `frontend/src/test/screeningPage.test.tsx` —— `mockFetch` 的池分发改为带参数解析（替换既有 `if (url === '/api/screening/pool' && ...)` 分支；其余分支不动）：

```ts
  const poolBodies = opts.pool ?? [poolBody()]
  const microPoolBodies = opts.microPool ?? []
  let poolCalls = 0
  let microCalls = 0
  const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === 'string' ? input : input.toString()
    if (url.startsWith('/api/screening/pool') && (!init || !init.method || init.method === 'GET')) {
      if (url.includes('pool=microcap')) {
        const body = microPoolBodies[Math.min(microCalls, Math.max(microPoolBodies.length - 1, 0))]
        microCalls += 1
        return respond(body ?? { ...poolBody(), batch_date: null, pool: [], frontier_gate: null, exec_constraints: null })
      }
      const body = poolBodies[Math.min(poolCalls, poolBodies.length - 1)]
      poolCalls += 1
      return respond(body)
    }
    // …其余分支原样保留（analyze / sessions / snapshot）
```

`mockFetch` opts 类型加 `microPool?: ScreeningPoolResponse[]`。既有 `poolBody()` 工厂补两个新字段（`frontier_gate: null, exec_constraints: null`），并新增工厂：

```ts
function microcapBody(overrides: Partial<ScreeningPoolResponse> = {}): ScreeningPoolResponse {
  return {
    ...poolBody(),
    batch_date: '2026-10-16',
    pool: [
      {
        symbol: '300001',
        stock_name: '特锐德',
        composite_score: 0.98,
        rank: 1,
        consecutive_days: 4,
        l3_verdict: 'keep',
        l3_reason: '无风险事件',
        factor_contribs: [],
      },
    ],
    frontier_gate: 'observation_only:missing=regime_data',
    exec_constraints: { suggested_single_cap: 0.05, slippage_tier_bump: 1 },
    queue: [],
    ...overrides,
  }
}
```

追加 describe（沿用文件既有 describe 的 beforeEach/afterEach 形态）：

```tsx
describe('池档位切换与小市值披露（add-microcap-pool-ui）', () => {
  beforeEach(() => {
    localStorage.clear()
    vi.spyOn(window, 'scrollTo').mockImplementation(() => {})
  })
  afterEach(() => {
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
    window.history.pushState({}, '', '/')
  })

  it('缺省主档；切到小市值 → 观察横幅（缺失要点映射）+ 约束 + 声明 + 无分析按钮', async () => {
    const fetchMock = mockFetch({ microPool: [microcapBody()] })
    window.history.pushState({}, '', '/screening')
    render(<ScreeningPage onBack={() => {}} />)

    // 缺省主档：既有行 testid 与分析按钮在
    await waitFor(() => expect(screen.getByTestId('screening-row-600519')).toBeInTheDocument())
    expect(screen.getByTestId('screening-analyze-btn')).toBeInTheDocument()
    expect(fetchMock).toHaveBeenCalledWith('/api/screening/pool', undefined)

    fireEvent.click(screen.getByTestId('screening-tier-microcap'))

    const row = await waitFor(() => screen.findByTestId('screening-microcap-row-300001'))
    expect(row).toHaveTextContent('300001')
    expect(row).toHaveTextContent('特锐德')
    // 门禁观察横幅：缺失要点映射可读文案（regime_data → 拥挤度数据），两态不混淆
    expect(screen.getByTestId('screening-microcap-gate-observation')).toHaveTextContent('拥挤度数据')
    expect(screen.queryByTestId('screening-microcap-gate-ready')).not.toBeInTheDocument()
    // 约束（建议口径）+ 观察声明
    expect(screen.getByTestId('screening-microcap-constraints')).toHaveTextContent('单票上限 5%')
    expect(screen.getByTestId('screening-microcap-constraints')).toHaveTextContent('滑点档位提升 1 档')
    expect(screen.getByTestId('screening-microcap-observation-note')).toHaveTextContent('不向执行链产生订单')
    // v1 不提供深度分析触发
    expect(screen.queryByTestId('screening-analyze-btn')).not.toBeInTheDocument()
    // 切档请求带档位参数
    expect(fetchMock).toHaveBeenCalledWith('/api/screening/pool?pool=microcap', undefined)
  })

  it('门禁 ready → 「可执行候选」横幅，非观察态', async () => {
    mockFetch({
      microPool: [microcapBody({ frontier_gate: 'ready' })],
    })
    window.history.pushState({}, '', '/screening')
    render(<ScreeningPage onBack={() => {}} />)
    fireEvent.click(await screen.findByTestId('screening-tier-microcap'))

    const banner = await screen.findByTestId('screening-microcap-gate-ready')
    expect(banner).toHaveTextContent('可执行候选')
    expect(banner).toHaveTextContent('不自动产生订单')
    expect(screen.queryByTestId('screening-microcap-gate-observation')).not.toBeInTheDocument()
  })

  it('小市值空态（batch_date null）→ 专用空态，非报错', async () => {
    mockFetch({ microPool: [] })
    window.history.pushState({}, '', '/screening')
    render(<ScreeningPage onBack={() => {}} />)
    fireEvent.click(await screen.findByTestId('screening-tier-microcap'))

    const empty = await screen.findByTestId('screening-microcap-empty')
    expect(empty).toHaveTextContent('尚无小市值批次')
    expect(screen.queryByTestId('screening-error')).not.toBeInTheDocument()
  })

  it('切回主档：既有行 testid 与内容恢复', async () => {
    mockFetch({ microPool: [microcapBody()] })
    window.history.pushState({}, '', '/screening')
    render(<ScreeningPage onBack={() => {}} />)

    fireEvent.click(await screen.findByTestId('screening-tier-microcap'))
    await screen.findByTestId('screening-microcap-row-300001')
    fireEvent.click(screen.getByTestId('screening-tier-main'))

    const row = await screen.findByTestId('screening-row-600519')
    expect(row).toHaveTextContent('600519')
    expect(screen.getByTestId('screening-analyze-btn')).toBeInTheDocument()
    expect(screen.queryByTestId('screening-microcap-gate-observation')).not.toBeInTheDocument()
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx vitest run src/test/screeningPage.test.tsx`
Expected: FAIL —— `screening-tier-microcap` 等元素不存在（页面未实现）；既有用例应仍绿（类型补齐后）。

- [ ] **Step 3: Write minimal implementation**

`frontend/src/pages/screening/ScreeningPage.tsx` 改造（改动点全量给出，未提及处原样保留）：

3a. 模块级新增（`ACTIVE_QUEUE_STATES` 之后）：

```tsx
// 池档位（add-microcap-pool-ui）：主档缺省；小市值档 v1 只展示（不触发深度分析）
type PoolTier = 'main' | 'microcap'

// 门禁缺失要点码 → 可读文案（口径 pipeline.FRONTIER_MISSING_*；未知码原样列出）
const FRONTIER_MISSING_LABEL: Record<string, string> = {
  delist_pool: '退市股池未就绪',
  regime_data: '拥挤度数据未就绪',
}
// pipeline.FRONTIER_GATE_OBSERVATION_PREFIX 逐字前缀（记录字段自由串，只做前缀分流；
// 漂移由 E2E 门禁用例钉住）
const FRONTIER_GATE_OBSERVATION_PREFIX = 'observation_only:missing='

function parseFrontierGate(gate: string | null): { ready: boolean; missing: string[] } | null {
  if (!gate) return null
  if (gate === 'ready') return { ready: true, missing: [] }
  if (gate.startsWith(FRONTIER_GATE_OBSERVATION_PREFIX)) {
    const missing = gate
      .slice(FRONTIER_GATE_OBSERVATION_PREFIX.length)
      .split(',')
      .map(code => code.trim())
      .filter(Boolean)
    return { ready: false, missing }
  }
  // 未知取值：保守按观察态处理并原样列出（spec：未知要点码原样列出）
  return { ready: false, missing: [gate] }
}
```

3b. 组件内 state 与取数（替换既有 `data`/`load`/首屏 effect；`loadSeqRef` 防切档竞态）：

```tsx
  const [poolTier, setPoolTier] = useState<PoolTier>('main')
  const [data, setData] = useState<ScreeningPoolResponse | null>(null)
  const [error, setError] = useState(false)
  const [hoverSymbol, setHoverSymbol] = useState<string | null>(null)
  const [queue, setQueue] = useState<ScreeningQueueEntry[]>([])
  const [removed, setRemoved] = useState<ScreeningRemoval[]>([])
  const [analyzing, setAnalyzing] = useState(false)
  const [analyzeMessage, setAnalyzeMessage] = useState<string | null>(null)
  const timerRef = useRef<number | null>(null)
  const loadSeqRef = useRef(0)
```

```tsx
  const load = useCallback(async (tier: PoolTier) => {
    const seq = ++loadSeqRef.current
    setError(false)
    setData(null)
    try {
      const resp = await fetch(`/api/screening/pool?pool=${tier}`)
      if (!resp.ok) throw new Error(String(resp.status))
      const body = (await resp.json()) as ScreeningPoolResponse
      if (seq !== loadSeqRef.current) return // 迟到的旧档响应丢弃
      setData(body)
      setQueue(body.queue ?? [])
    } catch {
      if (seq !== loadSeqRef.current) return
      setError(true)
      toast.error('选股池加载失败')
    }
  }, [])

  // 档位切换即重取；切回重取保证数据与对应档位严格对应
  useEffect(() => {
    void load(poolTier)
  }, [load, poolTier])
```

切档回调（放在 `startPolling` 之后）：

```tsx
  const switchTier = useCallback(
    (tier: PoolTier) => {
      if (tier === poolTier) return
      stopPolling() // 队列徽标轮询属主档分析流，切档即停
      setPoolTier(tier)
    },
    [poolTier, stopPolling]
  )
```

`poll` 的 fetch 同步带档位（轮询只在主档触发，参数恒 'main' 亦正确；直接用当前档位防切档窗口误刷）：

```tsx
      const resp = await fetch(`/api/screening/pool?pool=${poolTier}`)
```
（`poll` 的 useCallback 依赖补 `poolTier`。）

3c. 派生值（`fallback` 声明之后）：

```tsx
  const isMicrocap = poolTier === 'microcap'
  // testid 档位中缀：main 保持既有逐字（既有 E2E 零漂移），microcap 加前缀防选择器歧义
  const tid = (base: string) => (isMicrocap ? `screening-microcap-${base}` : base)
  const gate = parseFrontierGate(data?.frontier_gate ?? null)
  // 档位口径文案
  const caliberText = isMicrocap
    ? '口径：小票通道阈值（流动性下限 1000 万）· L3 keep'
    : '口径：L3 粗筛 keep ∩ 连续在榜门槛达标'
```

3d. 标题栏副标题与按钮（`<p className="text-xs text-muted-foreground mt-1">…` 替换为按档位文案；分析按钮整块包进 `poolTier === 'main'` 条件）：

```tsx
        <p className="text-xs text-muted-foreground mt-1">
          {isMicrocap
            ? '小市值通道观察池（同 L1→L2→L3 链；仅观察，不产生订单）'
            : '最近成功选股批次的候选池（L3 粗筛 keep ∩ 连续在榜门槛达标）'}
        </p>
        {poolTier === 'main' && (
          <div className="flex items-center gap-3 mt-3">
            {/* …既有分析按钮与 analyzeMessage 原样… */}
          </div>
        )}
```

3e. 池卡头部加档位切换（`<div className="mb-2 flex flex-wrap items-baseline justify-between gap-2">` 块之前插入独立一行），并把既有「口径」span 换成 `{caliberText}`：

```tsx
          <div className="mb-2 flex items-center gap-1" role="tablist" aria-label="池档位">
            {(['main', 'microcap'] as const).map(tier => (
              <button
                key={tier}
                type="button"
                role="tab"
                aria-selected={poolTier === tier}
                data-testid={`screening-tier-${tier}`}
                onClick={() => switchTier(tier)}
                className="rounded-full px-2.5 py-1 text-xs"
                style={
                  poolTier === tier
                    ? { background: 'var(--primary)', color: '#fff' }
                    : { background: 'var(--bg-overlay-l2)', color: 'var(--text-secondary)' }
                }
              >
                {tier === 'main' ? '主档池' : '小市值池'}
              </button>
            ))}
          </div>
```

3f. microcap 披露区块（插入在既有 stale 横幅之后、removedGroups 之前；三块互斥与并存关系见代码）：

```tsx
        {isMicrocap && gate?.ready && (
          <div
            data-testid="screening-microcap-gate-ready"
            className="rounded-lg border px-3 py-2 text-xs flex items-center gap-2"
            style={{ borderColor: 'var(--status-success-default)', color: 'var(--status-success-default)' }}
          >
            <i className="fas fa-circle-check text-xs"></i>
            前置齐备，可执行候选（仍不自动产生订单）
          </div>
        )}

        {isMicrocap && gate && !gate.ready && (
          <div
            data-testid="screening-microcap-gate-observation"
            className="rounded-lg border px-3 py-2 text-xs flex items-center gap-2 flex-wrap"
            style={{ borderColor: 'var(--status-warning-default)', color: 'var(--status-warning-default)' }}
          >
            <i className="fas fa-triangle-exclamation text-xs"></i>
            <span>观察模式：以下前置未就绪 ——</span>
            <span className="font-medium text-foreground">
              {gate.missing.map(code => FRONTIER_MISSING_LABEL[code] ?? code).join('、')}
            </span>
            <span>（仍不产生订单）</span>
          </div>
        )}

        {isMicrocap && data?.exec_constraints && (
          <div
            data-testid="screening-microcap-constraints"
            className="rounded-lg border px-3 py-2 text-xs"
            style={{ borderColor: 'var(--border)', color: 'var(--text-secondary)' }}
          >
            建议执行约束（记录字段，非生效约束）：
            {typeof data.exec_constraints.suggested_single_cap === 'number' &&
              ` 单票上限 ${Math.round(data.exec_constraints.suggested_single_cap * 100)}%`}
            {typeof data.exec_constraints.slippage_tier_bump === 'number' &&
              ` · 滑点档位提升 ${data.exec_constraints.slippage_tier_bump} 档`}
          </div>
        )}

        {isMicrocap && (
          <div
            data-testid="screening-microcap-observation-note"
            className="text-xs"
            style={{ color: 'var(--text-tertiary)' }}
          >
            小市值通道 v1 仅观察：批次与约束仅为记录字段，不向执行链产生订单
          </div>
        )}
```

3g. 空态与表格 testid 中缀化：

```tsx
        ) : poolEmpty ? (
          <div
            data-testid={isMicrocap ? 'screening-microcap-empty' : 'screening-empty'}
            className="text-sm"
            style={{ color: 'var(--text-tertiary)' }}
          >
            {isMicrocap
              ? '尚无小市值批次：等待小票通道日批落库'
              : '暂无可分析的精选池：等待下一批选股日批（或批次失败后重跑）'}
          </div>
```

表格内四个动态 testid 改为 `tid(...)`（main 输出与既有逐字一致）：
- 行：`` data-testid={`${tid('row')}-${row.symbol}`} `` → main=`screening-row-600519`、microcap=`screening-microcap-row-300001`
- 徽标：`` `${tid('queue-badge')}-${row.symbol}` ``
- 名称：`` `${tid('stock-name')}-${row.symbol}` ``
- 分数：`` `${tid('score')}-${row.symbol}` ``
- 浮层：`` data-testid={tid('contribs-tooltip')} ``

3h. 上下文卡按档位过滤（主档因子信号不误导小市值视图；温度计/事件清单与档位无关保留）：

```tsx
        <MarketRegimeCard />
        {poolTier === 'main' && data?.factor_signal && <FactorSignalCard signal={data.factor_signal} />}
        <EventWatchlistCard queue={queue} onAnalysisStarted={startPolling} />
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend && npx vitest run src/test/screeningPage.test.tsx`
Expected: 全 PASS（含既有用例——main 档 testid 与行为零漂移由它们钉住）。

- [ ] **Step 5: 类型 + Commit**

Run: `cd frontend && npx tsc --noEmit && npm test`
Expected: 0 error；全量前端测试 PASS。

```bash
git add frontend/src/types.ts frontend/src/pages/screening/ScreeningPage.tsx frontend/src/test/screeningPage.test.tsx
git commit -m "feat(screening): 选股页池档位切换 + 小市值门禁/约束披露（add-microcap-pool-ui T3）"
```

---

### Task 4: E2E spec（screening-page.spec.ts 扩展）

**Files:**
- Modify: `tests/e2e/playwright/tests/screening-page.spec.ts`（追加 4 用例 + seedMicrocap 辅助；沿用文件 serial/共享库纪律）

**Interfaces:**
- Consumes: Task 1 端点、Task 2 seed `tier` 键；既有文件常量 `BATCH_DATE`/`POOL` 与 `resetScreening`/`seedPool`/`apiBase`。
- Produces: E2E 用例——档位切换、门禁两态、空态、切回主档。

- [ ] **Step 1: 写用例（真种子、真端点，禁 route mock）**

文件末尾追加（串行 describe 内；`beforeEach/afterEach` 复用文件顶层的 reset——新用例纳入同一 describe.configure serial 即文件级 reset 已覆盖）：

```ts
const MICROCAP_DATE = '2026-10-16'
const MICROCAP_POOL = [
  {
    symbol: '300001',
    stock_name: '特锐德',
    composite_score: 0.98,
    rank: 1,
    consecutive_days: 4,
    l3_verdict: 'keep',
    l3_reason: '无风险事件',
    factor_contribs: [],
  },
]

// 两档并存种子（Task 2 的 tier/frontier_gate/exec_constraints 透传；既有 pool 键=池行列表）
async function seedTwoTiers(request: APIRequestContext, frontierGate: string) {
  const resp = await request.post(`${apiBase()}/api/test/seed`, {
    data: {
      screening: {
        batches: [
          {
            trade_date: BATCH_DATE,
            status: 'ok',
            started_at: `${BATCH_DATE}T15:30:00`,
            finished_at: `${BATCH_DATE}T15:33:00`,
            l1_passed: 20,
            l2_top: 9,
            l3_keep: 2,
            pool: POOL,
            tier: 'main',
          },
          {
            trade_date: MICROCAP_DATE,
            status: 'ok',
            started_at: `${MICROCAP_DATE}T16:45:00`,
            finished_at: `${MICROCAP_DATE}T16:47:00`,
            l1_passed: 30,
            l2_top: 12,
            l3_keep: 1,
            pool: MICROCAP_POOL,
            tier: 'microcap',
            frontier_gate: frontierGate,
            exec_constraints: { suggested_single_cap: 0.05, slippage_tier_bump: 1 },
          },
        ],
      },
    },
  })
  expect(resp.ok()).toBeTruthy()
}

test('种子两档 → 切小市值：行 + 观察横幅（缺失要点映射）+ 约束 + 声明 + 无分析按钮', async ({ page, request }) => {
  await seedTwoTiers(request, 'observation_only:missing=delist_pool,regime_data')

  await page.goto('/screening')
  await expect(page.getByTestId('screening-row-600519')).toBeVisible({ timeout: 15_000 })

  await page.getByTestId('screening-tier-microcap').click()
  const row = page.getByTestId('screening-microcap-row-300001')
  await expect(row).toBeVisible()
  await expect(row).toContainText('特锐德')
  const gate = page.getByTestId('screening-microcap-gate-observation')
  await expect(gate).toContainText('退市股池')
  await expect(gate).toContainText('拥挤度数据')
  await expect(gate).toContainText('不产生订单')
  await expect(page.getByTestId('screening-microcap-constraints')).toContainText('单票上限 5%')
  await expect(page.getByTestId('screening-microcap-constraints')).toContainText('滑点档位提升 1 档')
  await expect(page.getByTestId('screening-microcap-observation-note')).toContainText('不向执行链产生订单')
  await expect(page.getByTestId('screening-analyze-btn')).toHaveCount(0)
})

test('小市值门禁 ready → 「可执行候选」横幅（与观察态可区分）', async ({ page, request }) => {
  await seedTwoTiers(request, 'ready')

  await page.goto('/screening')
  await expect(page.getByTestId('screening-row-600519')).toBeVisible({ timeout: 15_000 })
  await page.getByTestId('screening-tier-microcap').click()

  const banner = page.getByTestId('screening-microcap-gate-ready')
  await expect(banner).toContainText('可执行候选')
  await expect(banner).toContainText('不自动产生订单')
  await expect(page.getByTestId('screening-microcap-gate-observation')).toHaveCount(0)
})

test('小市值无批次 → 专用空态（非报错、非主档数据）', async ({ page, request }) => {
  await seedPool(request) // 仅主档

  await page.goto('/screening')
  await expect(page.getByTestId('screening-row-600519')).toBeVisible({ timeout: 15_000 })
  await page.getByTestId('screening-tier-microcap').click()

  await expect(page.getByTestId('screening-microcap-empty')).toContainText('尚无小市值批次')
  await expect(page.getByTestId('screening-error')).toHaveCount(0)
  await expect(page.getByTestId('screening-row-600519')).toHaveCount(0)
})

test('切回主档：既有行与批次内容恢复', async ({ page, request }) => {
  await seedTwoTiers(request, 'observation_only:missing=regime_data')

  await page.goto('/screening')
  await expect(page.getByTestId('screening-row-600519')).toBeVisible({ timeout: 15_000 })
  await page.getByTestId('screening-tier-microcap').click()
  await expect(page.getByTestId('screening-microcap-row-300001')).toBeVisible()
  await page.getByTestId('screening-tier-main').click()

  await expect(page.getByTestId('screening-row-600519')).toBeVisible()
  await expect(page.getByTestId('screening-analyze-btn')).toBeVisible()
  await expect(page.getByTestId('screening-microcap-gate-observation')).toHaveCount(0)
})
```

- [ ] **Step 2: 本机跑 E2E 门禁**

前置（红线）：`curl -s http://127.0.0.1:8000/api/sessions` 无 `status=running` 会话、`curl -s http://127.0.0.1:8000/api/v1/ops/jobs` 无 running 作业；8000/5173 被生产占用时停容器（或用隔离 config）。不在日批窗口（15:30–17:10、17:55–20:45）动容器。

Run: `cd tests/e2e/playwright && npx playwright test screening-page.spec.ts`
Expected: 全 PASS（含既有 5 用例——main 档零漂移由它们钉住）。

- [ ] **Step 3: Commit**

```bash
git add tests/e2e/playwright/tests/screening-page.spec.ts
git commit -m "test(e2e): 小市值池档位切换与门禁披露 E2E（add-microcap-pool-ui T4）"
```

---

### Task 5: 全量验证与 E2E 门禁

**Files:**
- 无新文件（验证任务）

- [ ] **Step 1: 后端全量**：`uv run pytest` → 0 failed（全量需 Langfuse 在线，Docker 未起时先起栈）
- [ ] **Step 2: 前端全量**：`cd frontend && npm test` → 0 failed；`npx tsc --noEmit` 0 error
- [ ] **Step 3: Lint/类型**：`uv run ruff check && uv run ruff format --check && uv run mypy` → 0 error
- [ ] **Step 4: E2E 全套件**：`cd tests/e2e/playwright && npx playwright test` → 全绿；report 路径记入人工验证报告
- [ ] **Step 5: Commit 收尾**（如有 lint 修正）

```bash
git add -A && git commit -m "chore: add-microcap-pool-ui 全量验证修正"
```
