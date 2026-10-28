# Add Rejected Factor Pool Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 五道闸拒收的候选（带 DSL 表达式）持久化为 `factors` 表 `rejected` 行，并被衰减监控日批纳入同口径 IC 缺口回填跟踪；提供存量收割状态文件回填脚本。

**Architecture:** `admit_candidates` 是月批提案 / builtin 注册 / 收割导入三入口共用的唯一准入单点——拒收持久化挂该单点；`list_factors` 加 `include_rejected` 缺省排除开关让"拒绝池对既有读路径不可见"一次到位；IC 跟踪复用 `_record_missing_ic` + `record_ic` 幂等 upsert，零新表。

**Tech Stack:** Python 3.12 / sqlite3 / pandas / pytest。

**Delta:** `openspec/changes/add-rejected-factor-pool/`（proposal / specs / design / tasks）

## Global Constraints

- 查重范围不变：`find_structural_duplicate` 只比对 `candidate/active/degraded`（`retired` 允许重提语义不动）；`rejected` 不参与查重。
- 账本口径不变：`factor_admission_ledger` 拒收行 `factor_id` 保持 NULL。
- 既有监控摘要字段语义不变：`recorded` / `days_recorded` / `checked` / `skipped` / `degraded` 只描述 active。
- `rejected` 行零状态迁移（终态）；无降级判定；不参与桶配额/池增量/打分。
- 回填脚本显式 `--state`/`--db` 必填，无默认路径。
- 测试全部走 tmp 库；先写失败测试再实现（TDD）。

---

### Task 1: 库层——rejected 状态机 + 拒绝池读写

**Files:**
- Modify: `src/finance_agent/factors/library.py`
- Test: `tests/factors/test_library.py`

**Interfaces (Produces):**

```python
# library.py 新增/修改
_INITIAL_STATES: frozenset[str]  # {"candidate", "active", "rejected"}
_TRANSITIONS["rejected"] = frozenset()          # 终态
EVENT_RE_REJECTED = "re_rejected"                # 新事件类型

def add_factor(*, expression, hypothesis, category, status="candidate",
               ast_json=None, source="proposal", alignment_score=None,
               event_detail: dict[str, Any] | None = None,
               db_path=None) -> int   # event_detail 并入 created 事件 detail

def list_factors(*, status=None, category=None, include_rejected=False,
                 db_path=None) -> list[dict[str, Any]]
# status 显式给定时精确返回（含 status="rejected"）；status=None 且
# include_rejected=False（缺省）→ SQL WHERE status != 'rejected'

def find_rejected_duplicate(ast_json: str, *, db_path=None) -> int | None
# 仅 status='rejected' 行内同构比对（与 find_structural_duplicate 同款快路径）

def record_rejected_factor(*, expression, hypothesis, category, ast_json,
                           source, gate: int | None, reason: str | None,
                           detail: Mapping | None, db_path=None) -> dict
# -> {"factor_id": int, "action": "created" | "updated"}
# created: add_factor(status="rejected", event_detail={"gate","reason",**detail})
# updated: UPDATE updated_at + re_rejected 事件
```

- [ ] **Step 1: 失败测试**（`tests/factors/test_library.py` 追加）

```python
class TestRejectedStateAndPool:
    def test_rejected_is_valid_initial_state_and_terminal(self, tmp_path):
        db = _db(tmp_path)
        fid = add_factor(expression="close", hypothesis="h", category="测试",
                         status="rejected", ast_json="close", db_path=db)
        row = _require_row(db, fid)
        assert row["status"] == "rejected"
        with pytest.raises(FactorStateError):
            set_status(fid, "active", db_path=db)   # rejected 终态，禁止迁出

    def test_record_rejected_factor_creates_row_and_event(self, tmp_path):
        db = _db(tmp_path)
        result = record_rejected_factor(
            expression="close", hypothesis="h", category="测试", ast_json="close",
            source="proposal", gate=2, reason="WEAK_ICIR", detail={"ic_mean": 0.01},
            db_path=db)
        assert result["action"] == "created"
        events = list_events(result["factor_id"], db_path=db)
        assert events[0]["event"] == "created"
        assert events[0]["detail"]["gate"] == 2
        assert events[0]["detail"]["reason"] == "WEAK_ICIR"

    def test_record_rejected_factor_upserts_on_structural_match(self, tmp_path):
        db = _db(tmp_path)
        first = record_rejected_factor(expression="close + close", hypothesis="a",
                                       category="测试", ast_json=None, source="s",
                                       gate=2, reason="WEAK_ICIR", detail={}, db_path=db)
        second = record_rejected_factor(expression="close + close", hypothesis="b",
                                        category="测试", ast_json=None, source="s",
                                        gate=3, reason="NOT_SIGNIFICANT", detail={}, db_path=db)
        assert second["factor_id"] == first["factor_id"]
        assert second["action"] == "updated"
        assert len(list_factors(status="rejected", db_path=db)) == 1
        events = list_events(first["factor_id"], db_path=db)
        assert [e["event"] for e in events] == ["created", "re_rejected"]

    def test_list_factors_excludes_rejected_by_default(self, tmp_path):
        db = _db(tmp_path)
        fid = add_factor(expression="close", hypothesis="h", category="测试",
                         status="active", db_path=db)
        record_rejected_factor(expression="volume", hypothesis="r", category="测试",
                               ast_json="volume", source="s", gate=2,
                               reason="WEAK_ICIR", detail={}, db_path=db)
        default_rows = list_factors(db_path=db)
        assert [int(r["id"]) for r in default_rows] == [fid]
        assert len(list_factors(include_rejected=True, db_path=db)) == 2
        assert len(list_factors(status="rejected", db_path=db)) == 1

    def test_find_rejected_duplicate_scopes_to_rejected_only(self, tmp_path):
        db = _db(tmp_path)
        add_factor(expression="close", hypothesis="h", category="测试",
                   status="active", ast_json="close", db_path=db)
        assert find_structural_duplicate("close", db_path=db) is not None
        assert find_rejected_duplicate("close", db_path=db) is None
        rid = record_rejected_factor(expression="close", hypothesis="r",
                                     category="测试", ast_json="close", source="s",
                                     gate=2, reason="WEAK_ICIR", detail={}, db_path=db)["factor_id"]
        assert find_rejected_duplicate("close", db_path=db) == rid
        assert find_structural_duplicate("close", db_path=db) is not None  # 范围仍含 active
```

- [ ] **Step 2: 跑测试确认失败**（ImportError/断言红）
- [ ] **Step 3: 最小实现**（按上述 Interfaces 改 `library.py`；`find_rejected_duplicate` 复制 `find_structural_duplicate` 结构改 `WHERE status = 'rejected'`；`record_rejected_factor` 内 `ast_json` 缺失时 `serialize(parse(expression, enforce_complexity=False))` 现算）
- [ ] **Step 4: 跑测试确认绿**；`uv run pytest tests/factors/test_library.py -q` 全绿
- [ ] **Step 5: Commit** `feat(factors): rejected 终态 + 拒绝池读写原语（add-rejected-factor-pool T1）`

### Task 2: 准入编排拒收持久化（单点）

**Files:**
- Modify: `src/finance_agent/factors/validate.py`（`admit_candidates` 七个拒收点）
- Test: `tests/factors/test_validate.py`

**Interfaces (Consumes):** Task 1 的 `record_rejected_factor`。

- [ ] **Step 1: 失败测试**

```python
def test_admit_candidates_persists_gate_rejected_row(tmp_path):
    db, panel = _weak_icir_setup(tmp_path)   # 复用既有 _weak_icir 弱因子造数
    results = admit_candidates([_cand("volume")], panel, seed=1, db_path=db)
    assert results[0].factor_id is None and results[0].rejected_gate == GATE_SINGLE_FACTOR
    rejected = list_factors(status="rejected", db_path=db)
    assert len(rejected) == 1
    assert rejected[0]["expression"] == "volume"
    events = list_events(int(rejected[0]["id"]), db_path=db)
    assert events[0]["detail"]["gate"] == GATE_SINGLE_FACTOR

def test_admit_candidates_same_batch_duplicates_upsert(tmp_path):
    db, panel = _weak_icir_setup(tmp_path)
    admit_candidates([_cand("volume"), _cand("volume")], panel, seed=1, db_path=db)
    assert len(list_factors(status="rejected", db_path=db)) == 1
    fid = int(list_factors(status="rejected", db_path=db)[0]["id"])
    assert [e["event"] for e in list_events(fid, db_path=db)] == ["created", "re_rejected"]

def test_admit_candidates_gate0_duplicate_not_persisted(tmp_path):
    db, panel = _weak_icir_setup(tmp_path)
    add_factor(expression="volume", hypothesis="已入库", category="测试",
               status="active", ast_json="volume", db_path=db)
    admit_candidates([_cand("volume")], panel, seed=1, db_path=db)
    assert list_factors(status="rejected", db_path=db) == []

def test_admit_candidates_ledger_rejection_factor_id_unchanged(tmp_path):
    # 既有账本断言照抄 + 新增：拒收行 ledger.factor_id 仍为 NULL
```

- [ ] **Step 2: 红** → **Step 3: 实现**——`admit_candidates` 加模块级 `_persist_rejection(expression, hypothesis, category, ast_json, source, gate_result, db_path)` 薄封装，在闸①②（阶段 A 三个分支，dedup 除外）、闸③（阶段 B）、闸④⑤与 `dedup_again`（阶段 C）各 `outcomes[...] = AdmissionResult(...)` 前/后调用；`gate=gate_result.gate`、`reason=str(gate_result.reason) if gate_result.reason else None`、`detail=gate_result.detail`
- [ ] **Step 4: 绿**（`uv run pytest tests/factors/test_validate.py -q`）
- [ ] **Step 5: Commit** `feat(factors): admit_candidates 拒收持久化单点（T2）`

### Task 3: builtin 注册排除拒绝池

**Files:**
- Modify: `src/finance_agent/factors/builtin.py:138`（仅 docstring——行为由 Task 1 的 `list_factors` 缺省排除自动生效）
- Test: `tests/factors/test_builtin.py`

- [ ] **Step 1: 失败测试**

```python
def test_missing_builtins_treats_rejected_row_as_missing(tmp_path):
    db = str(tmp_path / "factors.db")
    item = BUILTIN_FACTORS[0]
    record_rejected_factor(expression=item.expression, hypothesis="曾拒收",
                           category=BUILTIN_CATEGORY, ast_json=None, source="builtin",
                           gate=2, reason="WEAK_ICIR", detail={}, db_path=db)
    assert item in missing_builtins(db_path=db)   # 被拒 builtin 次日仍可重试
```

- [ ] **Step 2: 红（当前 missing_builtins 把 rejected 视为已存在）** → **Step 3:** 该测试经 Task 1 的 `list_factors` 缺省排除即绿；若仍红则 `missing_builtins` 显式传 `include_rejected=False` 并更新 docstring「任意状态含 retired/degraded，**rejected 视为缺失（可重试）**」→ **Step 4: 绿** → **Step 5: Commit** `fix(factors): builtin 注册不把拒绝池行视为已存在（T3）`

### Task 4: 监控日批拒绝池 IC 跟踪

**Files:**
- Modify: `src/finance_agent/factors/monitor.py`（`run_factor_monitor`）
- Test: `tests/factors/test_monitor.py`

**Interfaces (Consumes):** `list_factors(status="rejected")`、`_record_missing_ic`。

- [ ] **Step 1: 失败测试**

```python
def test_run_factor_monitor_records_ic_for_rejected_rows(tmp_path):
    db = _db(tmp_path); panel = _monitor_panel(); as_of = _day(0)
    rid = record_rejected_factor(expression="volume", hypothesis="r", category="测试",
                                 ast_json="volume", source="s", gate=2,
                                 reason="WEAK_ICIR", detail={}, db_path=db)["factor_id"]
    summary = run_factor_monitor(panel, as_of, db_path=db)
    assert summary["rejected_checked"] == 1
    assert summary["rejected_days_recorded"] >= 1
    assert len(list_ic_series(rid, db_path=db)) >= 1

def test_rejected_rows_never_degrade_or_transition(tmp_path):
    db = _db(tmp_path); panel = _monitor_panel(); as_of = _day(0)
    rid = record_rejected_factor(...)  # 同上
    _craft_history(db, rid, [-0.5] * 40)   # 滚动全负、ICIR 低
    summary = run_factor_monitor(panel, as_of, db_path=db)
    assert _require_row(db, rid)["status"] == "rejected"   # 无状态迁移
    assert summary["degraded"] == []                        # 不混入 degraded

def test_monitor_summary_rejected_fields_present(tmp_path):
    summary = run_factor_monitor(panel, as_of, db_path=db)   # 无 rejected 行
    assert summary["rejected_checked"] == 0
    assert summary["rejected_recorded"] == 0
    assert summary["rejected_days_recorded"] == 0
```

- [ ] **Step 2: 红** → **Step 3: 实现**——active 循环后追加：`for row in list_factors(status="rejected", db_path=db):` 计数 `rejected_checked`、复用 `_record_missing_ic`（`None` → 跳过），累计 `rejected_recorded` / `rejected_days_recorded`；return dict 增三键 → **Step 4: 绿**（`uv run pytest tests/factors/test_monitor.py -q`；既有字段回归断言不动）→ **Step 5: Commit** `feat(factors): 衰减监控日批扩扫拒绝池 IC（T4）`

### Task 5: 存量回填

**Files:**
- Create: `src/finance_agent/factors/reject_backfill.py`
- Create: `scripts/backfill_rejected_factors.py`
- Test: `tests/factors/test_reject_backfill.py`

**Interfaces (Consumes):** Task 1 `record_rejected_factor`；harvest 状态文件结构 `{"processed": {name: verdict}}`，verdict 含 `status/expression/ast_json/gate/reason/detail`（`untranslatable`/`transcription_error` 无 `expression` DSL）。

- [ ] **Step 1: 失败测试**

```python
def test_backfill_imports_gate_rejected_and_is_idempotent(tmp_path):
    state = tmp_path / "state.json"
    state.write_text(json.dumps({"processed": {
        "f1": {"status": "rejected", "expression": "volume", "gate": 4,
               "reason": "REDUNDANT_WITH_POOL", "detail": {"duplicate_of": 3}, "at": "t"},
        "f2": {"status": "rejected", "expression": "", "gate": None,
               "reason": None, "detail": "不可翻译（LLM）", "at": "t"},
        "f3": {"status": "untranslatable", "stage": "translator"},
        "f4": {"status": "admitted"},
    }}), encoding="utf-8")
    db = str(tmp_path / "f.db")
    summary = backfill_rejected_from_state(state, db_path=db)
    assert summary["imported"] == 1 and summary["skipped_no_expression"] >= 1
    rows = list_factors(status="rejected", db_path=db)
    assert rows[0]["expression"] == "volume"
    events = list_events(int(rows[0]["id"]), db_path=db)
    assert events[0]["detail"]["gate"] == 4
    summary2 = backfill_rejected_from_state(state, db_path=db)
    assert summary2["imported"] == 0 and summary2["updated"] == 1   # 幂等零新增

def test_backfill_derives_ast_json_when_missing(tmp_path):
    # verdict 无 ast_json → record_rejected_factor 现算；同构后续命中 upsert
```

- [ ] **Step 2: 红** → **Step 3: 实现**——`reject_backfill.py`：

```python
def backfill_rejected_from_state(state_path, *, db_path, candidates_path=None) -> dict[str, int]:
    # processed 遍历：status != "rejected" → skipped_other；无 expression → skipped_no_expression
    # hypothesis 重建：candidates_path 命中 name → f"[RD-Agent/{name}] {description}"，缺省 f"[backfill/{name}]"
    # category 固定 DEFAULT_CATEGORY（harvest 语义）；source="backfill"
    # DSLParseError → skipped_unparseable；其余走 record_rejected_factor（原 gate/reason/detail 原样）
```

`scripts/backfill_rejected_factors.py`：argparse `--state`/`--db` 必填、`--candidates` 可选，`print(json.dumps(summary))` → **Step 4: 绿** → **Step 5: Commit** `feat(factors): 拒绝池存量回填（T5）`

### Task 6: 读路径收口 + 回归

**Files:**
- Modify: `src/finance_agent/api.py:4387`（detail 传 `include_rejected=True`）、`src/finance_agent/outcome/daily_brief.py:251`（SQL 加 `WHERE status != 'rejected'`）
- Test: 既有回归（api/daily_brief 相关测试）+ `tests/factors/test_external_import.py` 补一条经 `run_factor_external_import` 的持久化集成断言

- [ ] **Step 1:** 补集成断言（external import 拒收 → rejected 行存在）；跑 `uv run pytest tests/ -q -k "factor and not e2e"` 定向回归
- [ ] **Step 2:** 修 Task 1 缺省排除暴露的红（如有）
- [ ] **Step 3:** `uv run ruff check && uv run ruff format --check && uv run mypy`（存量 mypy 项之外零新增）
- [ ] **Step 4:** Commit `chore(factors): 拒绝池读路径收口（T6）`

### Task 7: 收尾

- [ ] **Step 1:** `openspec validate add-rejected-factor-pool --strict`（worktree 根执行）
- [ ] **Step 2:** tasks.md 勾选；全量 `uv run pytest -q`（记录读数；外网/Langfuse 阻塞项如实记录）
- [ ] **Step 3:** push + `gh pr create`（body：delta 摘要 + 验证读数 + 部署后动作清单：跑回填脚本、次日监控摘要人工核对）
