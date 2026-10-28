# add-pool-stock-name-display Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 精选池行携带股票名：`selection_pool` 加 `stock_name` 列（幂等迁移），选股日批从同日快照透传名称，池接口透传，选股页表格新增「股票名称」列（缺名渲染 `—`）。

**Architecture:** 名字只在池行落库一处丢失（上游 `market_snapshot.name` → `CandidateRecord.name` 已全程携带）。改动三段：store 加列+迁移+读写列集合；pipeline L1 记录就地建 `symbol→name` 映射注入池行 dict；前端类型+列渲染。API 与 e2e seed 均为透传，零接线改动。

**Tech Stack:** Python 3.12 / FastAPI / sqlite3（手写迁移）/ React 18 + TS + Vitest / Playwright E2E。

**关联 delta:** `openspec/changes/add-pool-stock-name-display/`（proposal / design / specs / tasks）

## Global Constraints

- **工作区纪律**：实施在 worktree `.worktrees/add-pool-stock-name-display`、分支
  `feat/add-pool-stock-name-display`；主检出并发使用，禁止直接改主检出 src/。
- **origin/main 禁 merge commit，须 PR**；commit 不带 `Generated with` 尾注。
- **本机 pre-commit 缓存锁坏**：提交用 `--no-verify`，提交前手动跑
  `uv run ruff check src tests && uv run ruff format --check src tests`（CI 有独立
  format 门禁）+ `uv run mypy src`。
- **缺名语义（spec 契约）**：`stock_name` 是纯展示字段——缺失/空 → 落空串 + pipeline
  warning，批次 MUST NOT 失败；迁移前历史行为 NULL（读取返回 null）；写路径一律
  normalize 成 str。
- **列迁移单点**：新列只声明在 `_POOL_COLUMN_MIGRATIONS`；`_POOL_TABLE_REBUILD_SQL`
  的 CREATE/INSERT SELECT 必须同步含新列（其注释承诺与 SCREENING_DDL 逐字一致）；
  init 顺序固定「恢复 → 列补齐 → 主键重建 → 列兜底」。
- **E2E 红线**：不 mock 业务接口（seed 走 TESTING=1 `/api/test/seed` 真实写入）；
  断言稳定终态、web-first assertion。
- **测试口径**：池行 upsert 按 `(trade_date, pool)` 原子替换；同日补跑自动补名，无回填。

---

### Task 1: store 层 —— selection_pool 加列 + 幂等迁移 + 读写往返

**Files:**
- Modify: `src/finance_agent/screening/store.py`（DDL `selection_pool` 块 ~L100-111、
  `_POOL_TABLE_REBUILD_SQL` ~L194-204、`init_screening_db` ~L392-400、
  `_BATCH_COLUMN_MIGRATIONS`/`_ensure_batch_columns` 邻域 ~L334-348、
  `_pool_row_payload` ~L873-918、`record_screening_batch` INSERT ~L1022-1030、
  `list_selection_pool` SELECT ~L1054-1057）
- Test: `tests/screening/test_screening_store.py`

**Interfaces:**
- Consumes: 既有 `record_screening_batch(trade_date, *, status, started_at, finished_at,
  error, l1_passed, l2_top, l3_keep, pool_rows, selection_mode, pool, frontier_gate,
  exec_constraints, db_path) -> int`；`list_selection_pool(trade_date, db_path, *, pool) -> list[dict]`。
- Produces: 池行 dict 新增键 `stock_name: str | None`（写路径 normalize 为 str，历史行为
  None）；`_ensure_pool_columns(conn)` 私有函数；pool_rows 入参 dict 支持可选
  `stock_name`（str 或 None）。

- [ ] **Step 1: 写失败测试**

在 `tests/screening/test_screening_store.py` 追加（紧邻 `test_record_and_read_back_pool_and_frontier_gate` 之后；文件已 `import sqlite3`、`import pytest`，`_D1` 常量已存在）：

```python
def test_pool_row_roundtrip_carries_stock_name(tmp_path) -> None:
    """池行 stock_name 写读往返；主档与小票通道同一落库契约（add-pool-stock-name-display）。"""
    from finance_agent.screening.store import list_selection_pool, record_screening_batch

    db = tmp_path / "screening.db"
    row = {
        "symbol": "600519",
        "stock_name": "贵州茅台",
        "composite_score": 1.5,
        "rank": 1,
        "consecutive_days": 3,
        "l3_verdict": "keep",
        "l3_reason": "无风险事件",
        "factor_contribs": [],
    }
    record_screening_batch(
        _D1, status="ok", started_at="t0", finished_at="t1", pool_rows=[row], db_path=db
    )
    record_screening_batch(
        _D1,
        status="ok",
        started_at="t0",
        finished_at="t1",
        pool="microcap",
        pool_rows=[
            {
                "symbol": "600150",
                "stock_name": "中国船舶",
                "composite_score": 0.9,
                "rank": 1,
                "consecutive_days": 3,
                "l3_verdict": "keep",
                "l3_reason": "无风险事件",
                "factor_contribs": [],
            }
        ],
        db_path=db,
    )

    main = list_selection_pool(_D1, db_path=db)
    assert main[0]["stock_name"] == "贵州茅台"
    micro = list_selection_pool(_D1, db_path=db, pool="microcap")
    assert micro[0]["stock_name"] == "中国船舶"

    # 幂等补跑自动补名：同 (trade_date, pool) 重跑 upsert 整体替换（spec 场景）
    record_screening_batch(
        _D1,
        status="ok",
        started_at="t2",
        finished_at="t3",
        pool_rows=[{**row, "stock_name": "贵州茅台（补跑）"}],
        db_path=db,
    )
    assert list_selection_pool(_D1, db_path=db)[0]["stock_name"] == "贵州茅台（补跑）"


def test_pool_row_stock_name_missing_normalizes_to_empty(tmp_path) -> None:
    """池行缺 stock_name 键 → 落空串（写路径 normalize 为 str；批次不失败）。"""
    from finance_agent.screening.store import list_selection_pool, record_screening_batch

    db = tmp_path / "screening.db"
    record_screening_batch(
        _D1,
        status="ok",
        started_at="t0",
        finished_at="t1",
        pool_rows=[
            {
                "symbol": "000001",
                "composite_score": 1.0,
                "rank": 1,
                "consecutive_days": 3,
                "l3_verdict": "keep",
                "l3_reason": "无风险事件",
                "factor_contribs": [],
            }
        ],
        db_path=db,
    )
    assert list_selection_pool(_D1, db_path=db)[0]["stock_name"] == ""


def test_pool_row_stock_name_rejects_non_string(tmp_path) -> None:
    """stock_name 非 str（且非 None）→ fail loud（决策字段纪律，坏值不入库）。"""
    from finance_agent.screening.store import record_screening_batch

    with pytest.raises(ValueError, match="stock_name"):
        record_screening_batch(
            _D1,
            status="ok",
            started_at="t0",
            finished_at="t1",
            pool_rows=[
                {
                    "symbol": "000001",
                    "stock_name": 123,
                    "composite_score": 1.0,
                    "rank": 1,
                    "consecutive_days": 3,
                    "l3_verdict": "keep",
                    "l3_reason": "无风险事件",
                    "factor_contribs": [],
                }
            ],
            db_path=tmp_path / "screening.db",
        )


def test_legacy_pool_schema_gets_stock_name_column(tmp_path) -> None:
    """旧 schema 库 init → 幂等 ALTER 补列；历史行保留且 stock_name 为 NULL。"""
    from finance_agent.screening.store import init_screening_db, list_selection_pool

    db = tmp_path / "screening.db"
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE selection_pool ("
        "trade_date TEXT NOT NULL, pool TEXT NOT NULL DEFAULT 'main', symbol TEXT NOT NULL, "
        "composite_score REAL NOT NULL, rank INTEGER NOT NULL, consecutive_days INTEGER NOT NULL, "
        "l3_verdict TEXT NOT NULL, l3_reason TEXT NOT NULL, factor_contribs_json TEXT NOT NULL, "
        "PRIMARY KEY (trade_date, pool, symbol))"
    )
    conn.execute(
        "INSERT INTO selection_pool VALUES "
        "('2026-10-27','main','600519',1.5,1,3,'keep','无风险事件','[]')"
    )
    conn.commit()
    conn.close()

    init_screening_db(db)

    rows = list_selection_pool("2026-10-27", db_path=db)
    assert len(rows) == 1
    assert rows[0]["symbol"] == "600519"
    assert rows[0]["stock_name"] is None
```

并在既有 `test_pool_rows_are_namespaced_by_pool`（~L616）断言区末尾补两行（其池行 dict
无 `stock_name` 键，此断言同时钉住 normalize 空串语义与两档同契约）：

```python
    # 池行缺 stock_name 键 → normalize 空串；主档/小票通道同一落库契约
    # （add-pool-stock-name-display）
    assert list_selection_pool(_D1, db_path=db)[0]["stock_name"] == ""
    assert list_selection_pool(_D1, db_path=db, pool="microcap")[0]["stock_name"] == ""
```

- [ ] **Step 2: 跑测试确认失败**

```bash
cd .worktrees/add-pool-stock-name-display && uv run pytest tests/screening/test_screening_store.py -k "stock_name or legacy_pool_schema" -v
```

Expected: 4 个新用例 FAIL（`stock_name` KeyError / 列不存在 `sqlite3.OperationalError`）。

- [ ] **Step 3: 最小实现**

store.py 五处改动（逐处给全量代码）：

(a) `SCREENING_DDL` 的 `selection_pool` 块——`factor_contribs_json` 行后加一列：

```sql
  factor_contribs_json TEXT NOT NULL,
  stock_name TEXT,
  PRIMARY KEY (trade_date, pool, symbol)
```

(b) `_POOL_TABLE_REBUILD_SQL`（重建序列，CREATE 与 INSERT SELECT 同步加列）：

```python
_POOL_TABLE_REBUILD_SQL: tuple[str, ...] = (
    "CREATE TABLE selection_pool__migrate ("
    "trade_date TEXT NOT NULL, pool TEXT NOT NULL DEFAULT 'main', symbol TEXT NOT NULL, "
    "composite_score REAL NOT NULL, rank INTEGER NOT NULL, consecutive_days INTEGER NOT NULL, "
    "l3_verdict TEXT NOT NULL, l3_reason TEXT NOT NULL, factor_contribs_json TEXT NOT NULL, "
    "stock_name TEXT, PRIMARY KEY (trade_date, pool, symbol))",
    "INSERT INTO selection_pool__migrate (trade_date, pool, symbol, composite_score, rank, "
    "consecutive_days, l3_verdict, l3_reason, factor_contribs_json, stock_name) "
    "SELECT trade_date, 'main', symbol, composite_score, rank, consecutive_days, l3_verdict, "
    "l3_reason, factor_contribs_json, stock_name FROM selection_pool",
    "DROP TABLE selection_pool",
    "ALTER TABLE selection_pool__migrate RENAME TO selection_pool",
    # DROP TABLE 连带删掉旧索引：重建后恢复（与 SCREENING_DDL 同名同形）
    _POOL_INDEX_SQL,
)
```

（注：重建仅在主键缺 `pool` 的最老库上触发；init 顺序保证此时 `stock_name` 列已由
(c) 的列补齐加上，SELECT 不会落空。）

(c) 紧邻 `_ensure_batch_columns` 后新增（迁移单点 + init 接线）：

```python
_POOL_COLUMN_MIGRATIONS: tuple[tuple[str, str], ...] = (
    ("stock_name", "ALTER TABLE selection_pool ADD COLUMN stock_name TEXT"),
)


def _ensure_pool_columns(conn: sqlite3.Connection) -> None:
    """旧库缺失的池表列 → 幂等 ALTER 补齐（新增列只在此声明，迁移单点）。"""
    columns = {row[1] for row in conn.execute("PRAGMA table_info(selection_pool)")}
    for name, ddl in _POOL_COLUMN_MIGRATIONS:
        if name not in columns:
            conn.execute(ddl)
```

`init_screening_db` 体内（`_ensure_batch_columns(conn)` 行后、
`_rebuild_pool_pk_if_needed(conn)` 行前后各一次——后者为重建兜底）：

```python
        _ensure_batch_columns(conn)  # ② 列补齐（重建 SQL 引用这些列）
        _ensure_pool_columns(conn)  # ② 池表列补齐（重建 SQL 引用这些列）
        _rebuild_pool_pk_if_needed(conn)  # ③ 主键重建（幂等）
        _ensure_pool_columns(conn)  # ④ 列兜底：重建语句序列与新 DDL 漂移时兜住
```

并把 docstring 迁移清单 ② 一行改为：
`② 批次表/池表缺列 → 幂等 ALTER 补齐（批次：selection_mode / frontier_gate / exec_constraints；池：stock_name）`。

(d) `_pool_row_payload`——`reason` 校验块后加校验，返回元组末尾加元素：

```python
    name = row.get("stock_name")
    if name is None:
        name = ""
    if not isinstance(name, str):
        raise ValueError(f"池行 stock_name 必须是字符串或 None: {name!r}（symbol={symbol}）")
    name = name.strip()
```

返回 tuple 的 `contribs_json` 之后追加 `name`；docstring 输出列序注释补 `stock_name`
（历史行为 NULL、写路径 normalize 空串、缺名不阻断批次）。

(e) `record_screening_batch` 的池行 INSERT 与 `list_selection_pool` 的 SELECT：

```python
            conn.executemany(
                "INSERT INTO selection_pool "
                "(trade_date, pool, symbol, composite_score, rank, consecutive_days, "
                "l3_verdict, l3_reason, factor_contribs_json, stock_name) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                payload,
            )
```

```python
        rows = conn.execute(
            "SELECT trade_date, pool, symbol, composite_score, rank, consecutive_days, "
            "l3_verdict, l3_reason, factor_contribs_json, stock_name FROM selection_pool "
            "WHERE trade_date = ? AND pool = ? ORDER BY rank, symbol",
            (normalized, pool),
        ).fetchall()
```

- [ ] **Step 4: 跑测试确认通过**

```bash
uv run pytest tests/screening/test_screening_store.py -v
```

Expected: 全部 PASS（含既有 30+ 用例零回归——重点盯
`test_pool_migration_rebuilds_pk_and_preserves_rows`、`test_init_recovers_interrupted_pool_rebuild`、
`test_successful_rebuild_leaves_no_migrate_tables`）。

- [ ] **Step 5: 提交**

```bash
git add src/finance_agent/screening/store.py tests/screening/test_screening_store.py
git commit --no-verify -m "feat(screening): selection_pool 加 stock_name 列（幂等迁移+读写往返）"
```

---

### Task 2: pipeline 层 —— 池行透传同日快照名称 + 缺名 warning

**Files:**
- Modify: `src/finance_agent/screening/pipeline.py`（`run_screening_batch` ⑤ 段
  pool_rows 组装 ~L458-479）
- Test: `tests/screening/test_pipeline.py`

**Interfaces:**
- Consumes: Task 1 的池行 `stock_name` 键；既有 `build_candidate_record` 产出的
  `CandidateRecord(code, name)`（`records` 列表，pipeline ② 段已在作用域内）。
- Produces: 池行 dict 含 `stock_name`（str，缺名空串）；pipeline 模块 logger
  `finance_agent.screening.pipeline` 输出缺名 warning（含 trade_date 与 symbol）。

- [ ] **Step 1: 写失败测试**

`tests/screening/test_pipeline.py` 文件头补 `import logging`（若无）；在
`test_batch_ok_writes_batch_l2_full_list_and_eligible_pool` 之后追加：

```python
def test_pool_rows_carry_snapshot_stock_name(ws: _Workspace) -> None:
    """池行 stock_name = 同日快照 name（合成 fixture 每标的「合成{code}」）。"""
    _seed_pool_days(ws)

    result = _run(ws)

    assert result["status"] == "ok"
    pool = {row["symbol"]: row for row in list_selection_pool(_AS_OF, db_path=ws.screening_db)}
    assert pool["600519"]["stock_name"] == "合成600519"
    assert pool["000001"]["stock_name"] == "合成000001"


def test_pool_row_missing_stock_name_degrades_with_warning(
    ws: _Workspace, caplog: pytest.LogCaptureFixture
) -> None:
    """快照缺名 → 池行落空串 + warning，批次不失败（展示字段不阻断决策字段）。"""
    conn = sqlite3.connect(ws.snapshot_db)
    try:
        conn.execute("UPDATE market_snapshot SET name = '' WHERE code = '600519'")
        conn.commit()
    finally:
        conn.close()
    _seed_pool_days(ws)

    with caplog.at_level(logging.WARNING, logger="finance_agent.screening.pipeline"):
        result = _run(ws)

    assert result["status"] == "ok"
    pool = {row["symbol"]: row for row in list_selection_pool(_AS_OF, db_path=ws.screening_db)}
    assert pool["600519"]["stock_name"] == ""
    assert pool["000001"]["stock_name"] == "合成000001"
    assert "600519" in caplog.text
```

- [ ] **Step 2: 跑测试确认失败**

```bash
uv run pytest tests/screening/test_pipeline.py -k "stock_name" -v
```

Expected: 2 个新用例 FAIL（KeyError: 'stock_name'）。

- [ ] **Step 3: 最小实现**

`run_screening_batch` ② 段（`records = [...]` 列表推导之后、`passed = [...]` 之前）加映射：

```python
        names_by_symbol = {record.code: record.name for record in records}
```

⑤ 段 `pool_rows.append({...})` 的 dict 加一键（`factor_contribs` 之后），并在 append 前
缺名记 warning：

```python
            candidate = by_symbol[decision.symbol]
            stock_name = names_by_symbol.get(candidate.symbol, "")
            if not stock_name:
                _logger.warning(
                    "选股日批（%s）池行缺股票名（快照 name 为空）：%s", trade_date, candidate.symbol
                )
            pool_rows.append(
                {
                    "symbol": candidate.symbol,
                    "composite_score": candidate.composite_score,
                    "rank": candidate.rank,
                    "consecutive_days": consecutive_days_in_top(
                        candidate.symbol, trade_date, db_path=db_path, pool=pool
                    ),
                    "l3_verdict": decision.verdict,
                    "l3_reason": decision.reason,
                    "factor_contribs": _candidate_contribs(candidate),
                    "stock_name": stock_name,
                }
            )
```

（小票通道 `run_microcap_lane_daily` 复用同一 `run_screening_batch`，自动同享，零改动。）

- [ ] **Step 4: 跑测试确认通过**

```bash
uv run pytest tests/screening/test_pipeline.py -v
```

Expected: 全部 PASS（含既有 ok-batch / microcap / fallback 用例零回归）。

- [ ] **Step 5: 提交**

```bash
git add src/finance_agent/screening/pipeline.py tests/screening/test_pipeline.py
git commit --no-verify -m "feat(screening): 池行透传同日快照股票名（缺名空串+warning 不阻断批次）"
```

---

### Task 3: API 层 —— 池端点透传 stock_name 用例

**Files:**
- Test: `tests/test_api_screening.py`（`_pool_rows()` fixture ~L80-99 与 pool 端点用例区 ~L130）
- Modify: 无（`/api/screening/pool` 池行 dict 透传 store 读结果，Task 1 后新键自动出现）

**Interfaces:**
- Consumes: Task 1 store 池行 `stock_name`；既有 `pool_seed` fixture。
- Produces: 端点契约读数（历史行 null / 新行 str）各一条断言，供 e2e 与前端对照。

- [ ] **Step 1: 写失败测试**

`_pool_rows()` 返回的第一行 dict 加 `"stock_name": "贵州茅台",`（其余行不加，钉住
normalize 空串口径）。用例区追加（文件已 `import sqlite3` 则省略局部导入）：

```python
def test_pool_endpoint_pool_rows_carry_stock_name(pool_seed) -> None:
    body = TestClient(app).get("/api/screening/pool").json()
    by_symbol = {row["symbol"]: row for row in body["pool"]}
    assert by_symbol["600519"]["stock_name"] == "贵州茅台"


def test_pool_endpoint_legacy_row_returns_null_stock_name(pool_seed) -> None:
    """迁移前历史行（stock_name 为 NULL）→ 接口返回 null（前端退化 — 展示）。"""
    import sqlite3

    conn = sqlite3.connect(pool_seed)
    try:
        conn.execute("UPDATE selection_pool SET stock_name = NULL WHERE symbol = '600519'")
        conn.commit()
    finally:
        conn.close()

    body = TestClient(app).get("/api/screening/pool").json()
    by_symbol = {row["symbol"]: row for row in body["pool"]}
    assert by_symbol["600519"]["stock_name"] is None
```

- [ ] **Step 2: 跑测试确认失败**

```bash
uv run pytest tests/test_api_screening.py -k stock_name -v
```

Expected: `carry_stock_name` FAIL（KeyError: 'stock_name'）；`legacy_row` FAIL
（UPDATE 报列不存在 `sqlite3.OperationalError`——Task 1 合并后此用例单独跑才绿，
两用例同分支共验收）。

- [ ] **Step 3: 实现**——无代码改动（透传）。若 `carry_stock_name` 在 Task 1 已合入的
  分支上仍红，回到 Task 1 检查 SELECT 列集合。

- [ ] **Step 4: 跑测试确认通过**

```bash
uv run pytest tests/test_api_screening.py -v
```

Expected: 全部 PASS。

- [ ] **Step 5: 提交**

```bash
git add tests/test_api_screening.py
git commit --no-verify -m "test(api): 池端点 stock_name 透传与历史行 null 契约用例"
```

---

### Task 4: frontend —— 池行类型 + 「股票名称」列 + 组件测试

**Files:**
- Modify: `frontend/src/types.ts`（`ScreeningPoolRow` ~L854）、
  `frontend/src/pages/screening/ScreeningPage.tsx`（表头 ~L304-311、行渲染 ~L324-335）
- Test: `frontend/src/test/screeningPage.test.tsx`（`poolBody` ~L22-70 与断言区）

**Interfaces:**
- Consumes: 池接口池行 `stock_name: string | null`（Task 3 契约）。
- Produces: 表格新单元格 testid `screening-stock-name-{symbol}`（e2e Task 5 断言依赖）。

- [ ] **Step 1: 写失败测试**

`poolBody` 的 `600519` 行加 `stock_name: '贵州茅台',`、`000001` 行加
`stock_name: null,`（同时给该文件内其余行字面量——fallback 用例 ~L129 的池行——补
`stock_name: null`，满足新必填类型的编译）；既有「种子池」断言用例追加：

```tsx
    const row = page.getByTestId('screening-row-600519') // 既有行定位之后
```

```tsx
    await waitFor(() => {
      expect(screen.getByTestId('screening-stock-name-600519')).toHaveTextContent('贵州茅台')
    })
    await waitFor(() => {
      expect(screen.getByTestId('screening-stock-name-000001')).toHaveTextContent('—')
    })
```

（该文件既有断言风格以 `await waitFor(...)` / `screen.getByTestId` 为准；若既有用例用
`await expect(...).toHaveTextContent` 直写的同款，照抄同款即可——以文件现状为准，不要
混两套。另在表头断言用例（若有「表格列齐」用例）中加 `expect(screen.getByText('股票名称')).toBeInTheDocument()`。）

- [ ] **Step 2: 跑测试确认失败**

```bash
cd frontend && npx vitest run src/test/screeningPage.test.tsx
```

Expected: 新断言 FAIL（元素不存在）；TS 编译报 `stock_name` 缺失（fixture 未补全处）。

- [ ] **Step 3: 最小实现**

`frontend/src/types.ts`：

```ts
export interface ScreeningPoolRow {
  symbol: string
  /** 股票名称（add-pool-stock-name-display）：历史行为 null，快照缺名落空串 */
  stock_name: string | null
  composite_score: number
  rank: number
  consecutive_days: number
  l3_verdict: string
  l3_reason: string
  factor_contribs: ScreeningFactorContrib[]
}
```

`ScreeningPage.tsx` 表头（「标的」th 之后插一列）：

```tsx
                <th className="py-2 pr-3 font-medium">标的</th>
                <th className="py-2 pr-3 font-medium">股票名称</th>
```

行渲染（「标的」td 之后插一td）：

```tsx
                    <td
                      data-testid={`screening-stock-name-${row.symbol}`}
                      className="py-2 pr-3 align-top"
                      style={{ color: 'var(--text-secondary)' }}
                    >
                      {row.stock_name || '—'}
                    </td>
```

- [ ] **Step 4: 跑测试确认通过**

```bash
cd frontend && npx vitest run src/test/screeningPage.test.tsx && npx tsc --noEmit
```

Expected: 全部 PASS + 类型零错误。

- [ ] **Step 5: 提交**

```bash
git add frontend/src/types.ts frontend/src/pages/screening/ScreeningPage.tsx frontend/src/test/screeningPage.test.tsx
git commit --no-verify -m "feat(ui): 选股页精选池表格新增股票名称列（缺名退化 —）"
```

---

### Task 5: E2E —— seed 补名 + 名称列断言

**Files:**
- Modify: `tests/e2e/playwright/tests/screening-page.spec.ts`（`POOL` 常量 ~L40-68、
  首个表格用例 ~L103-123、fallback 用例 ~L198-210）

**Interfaces:**
- Consumes: `/api/test/seed` 的 `screening.pool`（经 `record_screening_batch` 落库，
  Task 1 后支持 `stock_name` 键）；前端 testid `screening-stock-name-{symbol}`（Task 4）。
- Produces: E2E 门禁证据（选股页名称列 + 缺名退化两场景全绿）。

- [ ] **Step 1: 改 seed 与断言**

`POOL` 常量：`600519` 行加 `stock_name: '贵州茅台',`；`000001` 行加
`stock_name: '平安银行',`。首个表格用例（`row` 定位后）追加：

```ts
  await expect(page.getByTestId('screening-stock-name-600519')).toContainText('贵州茅台')
```

fallback 池 seed 的行**不补** `stock_name`（经 store normalize 落空串 → 接口返回空串 →
页面渲染 `—`），在该用例表格可见断言后追加缺名退化断言（symbol 以该用例 seed 的实际
行为准）：

```ts
  await expect(page.getByTestId('screening-stock-name-600519')).toContainText('—')
```

- [ ] **Step 2: 跑选股页 spec 确认通过**

```bash
cd tests/e2e/playwright && npx playwright test screening-page.spec.ts
```

Expected: 全绿（种子池名称展示 + fallback 缺名 `—`）。

- [ ] **Step 3: 提交**

```bash
git add tests/e2e/playwright/tests/screening-page.spec.ts
git commit --no-verify -m "test(e2e): 选股页股票名称列与缺名退化断言"
```

---

### Task 6: 全量验证 + 人工验证报告

**Files:**
- Create: `tests/validation/2026-10-28-add-pool-stock-name-display-validation.md`

- [ ] **Step 1: 后端定向 + lint + 类型**

```bash
uv run pytest tests/screening tests/test_api_screening.py -q
uv run ruff check src tests && uv run ruff format --check src tests
uv run mypy src
```

Expected: 0 failed；ruff/mypy 既有存量之外零新增。

- [ ] **Step 2: 前端测试**

```bash
cd frontend && npm test
```

Expected: 全绿。

- [ ] **Step 3: E2E 门禁（stub 套件全量）**

```bash
cd tests/e2e/playwright && npx playwright test
```

Expected: 全绿（交互类变更门禁；playwright-report 路径记入验证报告）。红 →
playwright-debugger 诊断 → 回对应 Task 修复，禁止改弱断言。

- [ ] **Step 4: 人工验证报告**

落 `tests/validation/2026-10-28-add-pool-stock-name-display-validation.md`（模板见
project-workflow §5）：逐 Scenario 对照（E2E 已覆盖的抽查确认；缺名退化展示、名称列
不替代代码列两场景为主观确认项），异常记录与结论区块齐全。

- [ ] **Step 5: 回填 tasks.md + 提 PR**

勾选 `openspec/changes/add-pool-stock-name-display/tasks.md` 全部验收项；推分支开 PR
（base main，标题 `feat(screening): 精选池行携带股票名+选股页名称列 (add-pool-stock-name-display)`）。
PR 描述附：delta validate --strict 输出、E2E 门禁路径、验证报告路径。

> **Post-merge 提醒**（不阻塞 PR）：生产生效须 `docker compose up -d --build backend`
> 且避开日批窗口（15:30–17:10 / 17:55–20:45）与运行中分析；次批（下一交易日 15:40）
> 池行自动带名，存量历史行展示 `—`。
