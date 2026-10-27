# fix-stability-gate-mode-axis Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 精选池资格判定的轴模式改为显式传入当日 `selection_mode`，消除「当日批次行未落库 → 混合轴降级」的结构性缺陷（delta: `fix-stability-gate-mode-axis`）。

**Architecture:** `days_in_top_recent` / `eligible_for_selection` 增加可选 `mode` 入参（提供时直接作 `as_of` 轴模式、跳过批次行查找；缺省保持既有降级路径）；`pipeline.run_screening_batch` 精选池计算处传入已知变量 `selection_mode`。零 schema 变更、零写入时序变更。

**Tech Stack:** Python 3.12 + sqlite3（store 层）；pytest（隔离 tmp_path 库，零网络）。

## Global Constraints

- 规范真相源：`openspec/changes/fix-stability-gate-mode-axis/specs/stock-screening/spec.md`（轴模式来源条款 + 6 场景）
- `SELECTION_MODES: tuple[str, ...] = ("factor", "fallback")`（store.py:127）是模式合法值唯一来源，参数校验必须复用它
- 缺省 `mode=None` 路径（批次行查找 → 缺失退全轴）语义**逐字节不变**（带外兼容，spec「无模式带外调用降级保留」场景）
- 测试零网络：pipeline 测试须注入 `triage_client` stub（`_ScriptedClient`），autouse fixture 已禁止默认 client
- pre-commit 本机缓存锁坏 → 手动跑 `uv run ruff check` + `uv run python scripts/check_secrets.py`（如存在）后 `git commit --no-verify`；禁 merge commit 进 origin/main（走 PR）
- 主检出被另一会话并发使用（frontend 有脏文件）：实施在 `.worktrees/fix-stability-gate-mode-axis`（从 `origin/main` 建分支 `fix/stability-gate-mode-axis`），不 `pull` 主检出
- 跑测试前确认 `GET /api/sessions` 无 running 会话（import 期 init_db 会把运行中分析翻成 interrupted）
- 不重建/重启后端（生产部署由用户另行决策；15:30–17:10 日批窗口与 17:55–20:45 禁重建）

---

### Task 1: store 层显式 mode 入参

**Files:**
- Modify: `src/finance_agent/screening/store.py:621-703`（`days_in_top_recent` / `eligible_for_selection`）
- Test: `tests/screening/test_screening_store.py`（P0-1 段落末尾，line ~1276 后追加 2 例）

**Interfaces:**
- Consumes: `SELECTION_MODES`（store.py:127，现有常量）
- Produces: `days_in_top_recent(symbol: str, as_of: str, *, window: int = 5, db_path=None, pool="main", mode: str | None = None) -> int`；`eligible_for_selection(symbol, as_of, *, min_days=3, db_path=None, pool="main", mode: str | None = None) -> bool`（Task 2 依赖此签名）

- [ ] **Step 1: Write the failing tests**（追加到 `tests/screening/test_screening_store.py` 末尾，紧随 `test_missing_batch_mode_falls_back_to_unfiltered_axis`）

```python
def test_explicit_mode_uses_same_mode_axis_when_as_of_row_absent(tmp_path) -> None:
    """日批形态（fix-stability-gate-mode-axis）：资格判定先于当日批次行落库，当日批次行
    必然不存在。显式传当日模式 → 轴只含同模式交易日，fallback 日不稀释。

    生产 10-27 形态：Y 因子口径命中 D1/D2/D6 = 3 → 入池；旧行为（当日无批次行 → 全轴
    混合 [D6..D2]）把 D1 命中推出窗外、只数出 2 → 挡在池外。缺省路径的降级语义保留
    （spec「无模式带外调用降级保留」）。
    """
    db = str(tmp_path / "screening.db")
    _D6 = "2026-10-13"
    _seed_mode(db, _D1, "factor")
    _seed_mode(db, _D2, "factor")
    _seed_mode(db, _D3, "fallback")
    _seed_mode(db, _D4, "fallback")
    _seed_mode(db, _D5, "factor")
    _in_top(db, _D1, "Y")
    _in_top(db, _D2, "Y")
    _in_top(db, _D3, "OTHER")
    _in_top(db, _D4, "OTHER")
    _in_top(db, _D5, "OTHER")  # 模式内单日跌出
    _in_top(db, _D6, "Y")  # as_of 在榜；**不落 D6 批次行**（= 生产判定时点）
    assert days_in_top_recent("Y", _D6, mode="factor", db_path=db) == 3
    assert eligible_for_selection("Y", _D6, mode="factor", db_path=db) is True
    # 对照：缺省路径（不传 mode）仍是「读当日批次行 → 缺失退全轴」，带外兼容不变
    assert days_in_top_recent("Y", _D6, db_path=db) == 2
    assert eligible_for_selection("Y", _D6, db_path=db) is False


def test_explicit_mode_rejects_invalid_values(tmp_path) -> None:
    """非法 mode 值 fail loud（与 ``pool`` 参数校验同风格，防静默 misuse）。"""
    db = str(tmp_path / "screening.db")
    _seed_mode(db, _D1, "factor")
    _in_top(db, _D1, "Y")
    with pytest.raises(ValueError, match="非法 selection_mode"):
        days_in_top_recent("Y", _D1, mode="bogus", db_path=db)
    with pytest.raises(ValueError, match="非法 selection_mode"):
        eligible_for_selection("Y", _D1, mode="bogus", db_path=db)
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /d/WorkSpace/finance_analysis_agent/.worktrees/fix-stability-gate-mode-axis && uv run pytest tests/screening/test_screening_store.py::test_explicit_mode_uses_same_mode_axis_when_as_of_row_absent tests/screening/test_screening_store.py::test_explicit_mode_rejects_invalid_values -v
```
Expected: FAIL（`TypeError: days_in_top_recent() got an unexpected keyword argument 'mode'`）

- [ ] **Step 3: Write minimal implementation**

`days_in_top_recent` 签名与模式推导段（store.py:621-651 区域）改为：

```python
def days_in_top_recent(
    symbol: str,
    as_of: str,
    *,
    window: int = STABILITY_WINDOW_DAYS,
    db_path: str | Path | None = None,
    pool: str = POOL_MAIN,
    mode: str | None = None,
) -> int:
```

docstring 追加一段（插在「``as_of`` 当日不在榜」句后）：

```
    显式 ``mode``（fix-stability-gate-mode-axis）：日批路径的资格判定先于当日批次行落库，
    当日批次行必然不存在——调用方（``run_screening_batch``）SHALL 传入批次自身已知的
    ``selection_mode`` 定轴，MUST NOT 依赖本函数回头读当日批次行（那会退化为跨口径混合轴）。
    ``mode=None``（缺省）保持既有「批次行查找 → 缺失退全轴」路径（带外查询兼容）；
    非 None 非法值 fail loud。
```

模式推导段（原 `mode_row = conn.execute(...)` 起，store.py:647-651）改为：

```python
        if mode is not None:
            if mode not in SELECTION_MODES:
                raise ValueError(f"非法 selection_mode {mode!r}；允许: {list(SELECTION_MODES)}")
        else:
            mode_row = conn.execute(
                "SELECT selection_mode FROM screening_batches WHERE trade_date = ? AND pool = ?",
                (normalized, pool),
            ).fetchone()
            mode = str(mode_row[0]) if mode_row and mode_row[0] is not None else None
```

（轴查询两分支不动——`mode` 非 None 走 LEFT JOIN 同模式轴、None 走全轴，逻辑已存在。）

`eligible_for_selection`（store.py:685-703）签名加 `mode: str | None = None`，透传：

```python
    return (
        days_in_top_recent(symbol, as_of, db_path=db_path, pool=pool, mode=mode) >= min_days
    )
```

docstring 追加一句：「``mode`` 透传 ``days_in_top_recent``（显式定轴；缺省 = 批次行查找）」。

- [ ] **Step 4: Run tests to verify they pass**（含全部既有滚动窗口用例不回归）

```bash
cd /d/WorkSpace/finance_analysis_agent/.worktrees/fix-stability-gate-mode-axis && uv run pytest tests/screening/test_screening_store.py -v
```
Expected: 全 PASS（既有 ~60 例 + 新 2 例）

- [ ] **Step 5: Commit**

```bash
cd /d/WorkSpace/finance_analysis_agent/.worktrees/fix-stability-gate-mode-axis && uv run ruff check src/finance_agent/screening/store.py tests/screening/test_screening_store.py && git add src/finance_agent/screening/store.py tests/screening/test_screening_store.py && git commit --no-verify -m "fix(screening): 资格判定支持显式 mode 定轴（fix-stability-gate-mode-axis T1）"
```

---

### Task 2: pipeline 调用点传当日模式 + 管线级回归钉

**Files:**
- Modify: `src/finance_agent/screening/pipeline.py:459-465`（`eligible_for_selection` 调用点）
- Test: `tests/screening/test_pipeline.py`（`test_batch_drops_excluded_from_pool` 后追加 1 例）

**Interfaces:**
- Consumes: Task 1 的 `eligible_for_selection(..., mode=...)` 签名；`run_screening_batch` 内局部变量 `selection_mode`（line 327 定义、line 412 可切 fallback，先于池计算）
- Produces: 无（链路终点；`consecutive_days_in_top` 信息列保持 mode 无关，不动）

- [ ] **Step 1: Write the failing test**（追加到 `tests/screening/test_pipeline.py` 的 `test_batch_drops_excluded_from_pool` 之后）

```python
def _seed_mode_row(ws: _Workspace, trade_date: str, mode: str) -> None:
    record_screening_batch(
        trade_date,
        status="ok",
        started_at=f"{trade_date}T15:40:00",
        finished_at=f"{trade_date}T16:09:00",
        l2_top=1,
        pool_rows=(),
        selection_mode=mode,
        db_path=ws.screening_db,
    )


def test_batch_pool_eligibility_uses_batch_mode_not_mixed_axis(ws: _Workspace) -> None:
    """日批资格判定以当日 selection_mode 定轴（fix-stability-gate-mode-axis）。

    生产 10-27 形态：600519 因子口径命中 [当日,-6,-5] = 3 → 入池；旧行为（当日批次行
    未落库 → 混合轴 [当日,-2,-3,-4,-5]）把它数成 2、反而让 fallback 日在榜的 000001
    混到 4 命中入池——两者在两种口径下恰好互换，判别力最强。
    """
    _cal = _CALENDAR
    _seed_l2(ws.screening_db, _cal[-6], ["600519"])
    _seed_l2(ws.screening_db, _cal[-5], ["600519"])
    _seed_l2(ws.screening_db, _cal[-4], ["000001"])  # 600519 模式内单日跌出
    _seed_l2(ws.screening_db, _cal[-3], ["000001"])
    _seed_l2(ws.screening_db, _cal[-2], ["000001"])
    _seed_mode_row(ws, _cal[-6], "factor")
    _seed_mode_row(ws, _cal[-5], "factor")
    _seed_mode_row(ws, _cal[-4], "factor")
    _seed_mode_row(ws, _cal[-3], "fallback")
    _seed_mode_row(ws, _cal[-2], "fallback")

    result = _run(ws)

    assert result["status"] == "ok"
    assert result["selection_mode"] == "factor"
    # 同模式轴 [当日,-6,-5,-4]：600519 命中 3 → 入池；000001 命中 2 → 出池
    assert [row["symbol"] for row in list_selection_pool(_AS_OF, db_path=ws.screening_db)] == [
        "600519"
    ]
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /d/WorkSpace/finance_analysis_agent/.worktrees/fix-stability-gate-mode-axis && uv run pytest tests/screening/test_pipeline.py::test_batch_pool_eligibility_uses_batch_mode_not_mixed_axis -v
```
Expected: FAIL（池为 `["000001"]`——混合轴旧行为）

- [ ] **Step 3: Write minimal implementation**（pipeline.py:459-465 调用点加 `mode=selection_mode`）

```python
            if not eligible_for_selection(
                decision.symbol,
                trade_date,
                min_days=POOL_MIN_DAYS,
                db_path=db_path,
                pool=pool,
                mode=selection_mode,
            ):
```

- [ ] **Step 4: Run tests to verify they pass**（全套 pipeline 用例不回归——既有用例预置日无批次行，LEFT JOIN 宽待下两口径同轴）

```bash
cd /d/WorkSpace/finance_analysis_agent/.worktrees/fix-stability-gate-mode-axis && uv run pytest tests/screening/test_pipeline.py tests/screening/test_microcap_lane.py -v
```
Expected: 全 PASS

- [ ] **Step 5: Commit**

```bash
cd /d/WorkSpace/finance_analysis_agent/.worktrees/fix-stability-gate-mode-axis && uv run ruff check src/finance_agent/screening/pipeline.py tests/screening/test_pipeline.py && git add src/finance_agent/screening/pipeline.py tests/screening/test_pipeline.py && git commit --no-verify -m "fix(screening): 日批池资格传入当日 selection_mode，消除混合轴降级（T2）"
```

---

### Task 3: 全量验证 + delta 工件入库

**Files:**
- Modify: `openspec/changes/fix-stability-gate-mode-axis/tasks.md`（勾选已完成项）
- Include: delta 四工件（proposal/spec/design/tasks，从主检出复制入 worktree）

- [ ] **Step 1: 全量 screening 测试 + lint + 类型**

```bash
cd /d/WorkSpace/finance_analysis_agent/.worktrees/fix-stability-gate-mode-axis && uv run pytest tests/screening/ -q && uv run ruff check && uv run ruff format --check src/finance_agent/screening/store.py src/finance_agent/screening/pipeline.py tests/screening/test_screening_store.py tests/screening/test_pipeline.py && uv run mypy src/finance_agent/screening/store.py src/finance_agent/screening/pipeline.py
```
Expected: 全 PASS；mypy 对改动文件零新增错误

- [ ] **Step 2: strict 校验 + delta 工件复制入 worktree + 勾选 tasks**

```bash
cd /d/WorkSpace/finance_analysis_agent/.worktrees/fix-stability-gate-mode-axis && cp -r /d/WorkSpace/finance_analysis_agent/openspec/changes/fix-stability-gate-mode-axis openspec/changes/ && openspec validate fix-stability-gate-mode-axis --strict
```
勾选 `openspec/changes/fix-stability-gate-mode-axis/tasks.md` 前 5 项（测试先红 / store 加参 / pipeline 传值 / 全套测试 / strict 校验）；后 2 项（部署后人工核验 / 归档顺序）保持未勾。

- [ ] **Step 3: Commit 工件 + 推分支开 PR**

```bash
cd /d/WorkSpace/finance_analysis_agent/.worktrees/fix-stability-gate-mode-axis && cp /d/WorkSpace/finance_analysis_agent/docs/superpowers/plans/2026-10-27-fix-stability-gate-mode-axis.md docs/superpowers/plans/ && git add openspec/changes/fix-stability-gate-mode-axis docs/superpowers/plans/2026-10-27-fix-stability-gate-mode-axis.md && git commit --no-verify -m "docs(openspec): fix-stability-gate-mode-axis delta 工件 + 实施计划" && git push -u origin fix/stability-gate-mode-axis && gh pr create --fill
```

- [ ] **Step 4: verification-before-completion**——新鲜证据：全套 screening pytest 输出（数失败数=0）、ruff 零输出、mypy 零新增、`openspec validate` 输出 valid；报告实际状态。
