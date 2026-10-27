# 因子方向反转监控 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把闸④入池定向符号持久化到因子行，衰减监控新增「方向反转」降级判据——负向因子 IC 翻正不再漏检。

**Architecture:** `factors` 表加 `orientation` 列（-1/0/1，0=不可定）；闸④已计算的训练段 IC 符号在 `admit_candidates` 入库路径写入（与 `set_pool_increment` 同点）；`monitor.py` 在滚动快照上新增反转分支（连续 20 个滚动点符号与定向相反 → `degraded`，reason=`DIRECTION_REVERSAL`，反转优先于衰减判据）；存量 12 条按全史 `factor_ic` 均值符号幂等回填。

**Tech Stack:** Python 3.14 / sqlite3 / pandas / pytest

## Global Constraints

- Python 3.14（`.python-version` 是真源）；`uv run pytest` / `uv run ruff check` / `uv run mypy` 全绿才算完成
- 测试零网络零共享状态：库落 `tmp_path`（`FACTOR_LIBRARY_DB_PATH` 或显式 `db_path` 参数）
- 事件 detail 经 `json.dumps` 落库——不得写 NaN/Inf（`_finite_or_none` 先例）
- 状态写唯一入口是 `set_status`；列写模仿 `set_pool_increment`（短连接 + UPDATE）
- 原衰减判据语义逐字不变（spec MODIFIED 的 Previously 部分逐字保留）
- 两判据同日命中只落一条事件，reason 取 `DIRECTION_REVERSAL`（反转优先）

---

### Task 1: `orientation` 列落库（schema 迁移 + set_orientation）

**Files:**
- Modify: `src/finance_agent/factors/library.py`（FACTOR_LIBRARY_DDL ~line 69-80、`init_library` ~line 147-156、`set_pool_increment` ~line 606 附近）
- Test: `tests/factors/test_library.py`

**Interfaces:**
- Consumes: 无（首任务）
- Produces: `set_orientation(factor_id: int, value: int, *, db_path: str | Path | None = None) -> None`（值限 {-1,0,1}，越界 `ValueError`）；`factors.orientation INTEGER` 列（新库 DDL 直含、旧库幂等 ALTER 迁移）；`init_library()` 对已存在旧 schema 库执行迁移

- [ ] **Step 1: Write the failing test**

```python
# 追加到 tests/factors/test_library.py
class TestOrientationColumn:
    def test_fresh_db_has_orientation_column(self, tmp_path):
        db = str(tmp_path / "factor_library.db")
        init_library(db)
        conn = sqlite3.connect(db)
        cols = {row[1] for row in conn.execute("PRAGMA table_info(factors)")}
        conn.close()
        assert "orientation" in cols

    def test_existing_db_migrated_idempotently(self, tmp_path):
        db = str(tmp_path / "factor_library.db")
        # 模拟旧库：先手工建旧 schema（无 orientation 列）
        old_ddl = FACTOR_LIBRARY_DDL.replace("  alignment_score REAL,\n", "  alignment_score REAL,\n")
        conn = sqlite3.connect(db)
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS factors (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              expression TEXT NOT NULL, hypothesis TEXT NOT NULL,
              category TEXT NOT NULL, status TEXT NOT NULL,
              ast_json TEXT, source TEXT NOT NULL DEFAULT 'proposal',
              alignment_score REAL, pool_increment REAL,
              created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            );
            """
        )
        conn.commit()
        conn.close()
        init_library(db)  # 迁移
        init_library(db)  # 重跑幂等（不抛 duplicate column）
        conn = sqlite3.connect(db)
        cols = {row[1] for row in conn.execute("PRAGMA table_info(factors)")}
        conn.close()
        assert "orientation" in cols

    def test_set_orientation_writes_and_validates(self, tmp_path):
        db = str(tmp_path / "factor_library.db")
        fid = add_factor(expression="ts_mean(close,5)", hypothesis="h", category="测试",
                         status="active", db_path=db)
        set_orientation(fid, -1, db_path=db)
        conn = sqlite3.connect(db)
        value = conn.execute("SELECT orientation FROM factors WHERE id=?", (fid,)).fetchone()[0]
        conn.close()
        assert value == -1
        with pytest.raises(ValueError):
            set_orientation(fid, 7, db_path=db)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/factors/test_library.py::TestOrientationColumn -v`
Expected: FAIL（`set_orientation` 未定义 / 列不存在）

- [ ] **Step 3: Write minimal implementation**

`FACTOR_LIBRARY_DDL` 的 `factors` 表在 `alignment_score REAL,` 行后插入一行：

```python
  orientation     INTEGER,
```

`init_library` 迁移（`executescript` 之后追加；幂等靠 pragma 检查）：

```python
def init_library(db_path: str | Path | None = None) -> Path:
    """幂等建 3 表 + 索引（含存量库 orientation 列迁移），返回实际使用的库路径。"""
    path = Path(db_path) if db_path else _default_db_path()
    conn = _connect(path)
    try:
        conn.executescript(FACTOR_LIBRARY_DDL)
        existing = {row[1] for row in conn.execute("PRAGMA table_info(factors)")}
        if "orientation" not in existing:
            conn.execute("ALTER TABLE factors ADD COLUMN orientation INTEGER")
        conn.commit()
    finally:
        conn.close()
    return path
```

`set_orientation`（放在 `set_pool_increment` 之后，同款纪律）：

```python
def set_orientation(factor_id: int, value: int, db_path: str | Path | None = None) -> None:
    """入池定向符号写入（-1/0/1；0=不可定）。越界 ValueError——列语义由监控判据消费。"""
    if value not in (-1, 0, 1):
        raise ValueError(f"orientation 限 {{-1, 0, 1}}，收到 {value!r}")
    conn = _connect(db_path)
    try:
        conn.execute("UPDATE factors SET orientation = ? WHERE id = ?", (value, factor_id))
        conn.commit()
    finally:
        conn.close()
```

文件头 import 区确认 `set_orientation` 可从本模块导入（新增顶层函数无需额外 import）。

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/factors/test_library.py -v`
Expected: 全 PASS（含既有用例）

- [ ] **Step 5: Commit**

```bash
git add src/finance_agent/factors/library.py tests/factors/test_library.py
git commit -m "feat(factors): orientation 列落库 + set_orientation（direction-reversal 1/4）"
```

---

### Task 2: `admit_candidates` 入库路径写入定向符号

**Files:**
- Modify: `src/finance_agent/factors/validate.py:972-978`（`set_pool_increment` 调用块）
- Test: `tests/factors/test_validate.py`

**Interfaces:**
- Consumes: Task 1 的 `set_orientation`；闸④ `gate4.detail["orientation"]`（已存在键，check_pool_increment 所有路径都写）
- Produces: 入库因子行 `orientation` = 闸④候选训练段定向符号（后续监控 Task 4 消费）

- [ ] **Step 1: Write the failing test**

测试复用 `tests/factors/test_validate.py` 现有合成面板 fixture（该文件已有「空池负向候选过闸④入库」用例可参照——找到现有 5 道闸全通过入库的用例，抄其面板/候选构造）。新增断言：

```python
def test_admitted_factor_persists_orientation(self, tmp_path, _admit_fixture):
    # _admit_fixture = 现有用例的面板+候选构造（空池、训练段 IC 为负、过五道闸入库）
    db, factor_id = _admit_fixture
    conn = sqlite3.connect(db)
    row = conn.execute("SELECT orientation FROM factors WHERE id=?", (factor_id,)).fetchone()
    conn.close()
    assert row is not None
    assert row[0] in (-1, 1)  # 过闸⑤者训练/验证段同号，定向必非 0
```

（实施时以文件内现有 fixture 真名替换 `_admit_fixture`；若无全通过用例，按 `test_validate.py` 既有的闸①②③构造模式补最小面板用例。）

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/factors/test_validate.py -k orientation -v`
Expected: FAIL（orientation 为 NULL）

- [ ] **Step 3: Write minimal implementation**

`validate.py` 入库路径（`set_pool_increment` 块之后）：

```python
        increment = gate4.detail.get("increment")
        if isinstance(increment, (int, float)) and not isinstance(increment, bool):
            set_pool_increment(factor_id, float(increment), db_path=db_path)
        orientation = gate4.detail.get("orientation")
        if orientation in (-1, 0, 1):
            set_orientation(factor_id, int(orientation), db_path=db_path)
```

import 区加 `set_orientation`（与 `set_pool_increment` 同一行 from import）。

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/factors/test_validate.py -v`
Expected: 全 PASS

- [ ] **Step 5: Commit**

```bash
git add src/finance_agent/factors/validate.py tests/factors/test_validate.py
git commit -m "feat(factors): admit_candidates 入库写入闸④定向符号（direction-reversal 2/4）"
```

---

### Task 3: 存量回填脚本

**Files:**
- Create: `scripts/backfill_factor_orientation.py`
- Test: `tests/factors/test_orientation_backfill.py`

**Interfaces:**
- Consumes: Task 1 的 `set_orientation`；`list_factors` / `list_ic_series`
- Produces: `backfill_orientation(db_path) -> {"backfilled": int, "skipped": int}`（幂等：仅 `orientation IS NULL` 行）

- [ ] **Step 1: Write the failing test**

```python
"""scripts/backfill_factor_orientation.py 单测：幂等回填。"""
from __future__ import annotations

import sqlite3

from finance_agent.factors.library import add_factor, record_ic
from scripts.backfill_factor_orientation import backfill_orientation


def test_backfill_sign_and_idempotent(tmp_path):
    db = str(tmp_path / "factor_library.db")
    fid_neg = add_factor(expression="ts_mean(close,5)", hypothesis="h", category="测试",
                         status="active", db_path=db)
    fid_zero = add_factor(expression="ts_mean(close,10)", hypothesis="h", category="测试",
                          status="active", db_path=db)
    for day, value in [("2026-01-05", -0.04), ("2026-01-06", -0.06), ("2026-01-07", -0.02)]:
        record_ic(fid_neg, day, value, db_path=db)
    # fid_zero 无 IC 样本
    result = backfill_orientation(db_path=db)
    assert result == {"backfilled": 2, "skipped": 0}
    conn = sqlite3.connect(db)
    rows = {r[0]: r[1] for r in conn.execute("SELECT id, orientation FROM factors")}
    conn.close()
    assert rows[fid_neg] == -1  # 全史均值 -0.04 → -1
    assert rows[fid_zero] == 0  # 无样本 → 0
    # 幂等：重跑零回填
    assert backfill_orientation(db_path=db) == {"backfilled": 0, "skipped": 0}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/factors/test_orientation_backfill.py -v`
Expected: FAIL（ModuleNotFoundError: scripts.backfill_factor_orientation）

- [ ] **Step 3: Write minimal implementation**

```python
"""存量因子 orientation 回填（add-factor-direction-reversal-monitor Task 3）。

对 ``orientation IS NULL`` 的因子行按全史已记录 ``factor_ic`` 均值符号回填：
|均值| < 1e-12 或无样本 → 0。幂等可重跑（仅回填 NULL 行）。
用法: uv run python scripts/backfill_factor_orientation.py [--db PATH]
"""
from __future__ import annotations

import argparse

from finance_agent.factors.library import list_factors, list_ic_series, set_orientation

_ZERO_TOL = 1e-12


def _sign(value: float) -> int:
    if value > _ZERO_TOL:
        return 1
    if value < -_ZERO_TOL:
        return -1
    return 0


def backfill_orientation(*, db_path: str | None = None) -> dict[str, int]:
    backfilled = 0
    for row in list_factors(db_path=db_path):
        if row.get("orientation") is not None:
            continue
        values = [float(v) for _d, v in list_ic_series(int(row["id"]), db_path=db_path)]
        mean = sum(values) / len(values) if values else 0.0
        set_orientation(int(row["id"]), _sign(mean), db_path=db_path)
        backfilled += 1
    return {"backfilled": backfilled, "skipped": 0}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default=None)
    args = parser.parse_args()
    print(backfill_orientation(db_path=args.db))


if __name__ == "__main__":
    main()
```

注意：`list_factors` 返回 dict 是否含 `orientation` 键取决于其 SELECT 列清单——若为显式列清单（`SELECT id, expression, ...`），须把 `orientation` 加进 SELECT（本任务一并改 `list_factors`，并在 `tests/factors/test_library.py` 加一条「list_factors 返回 orientation」断言）。

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/factors/test_orientation_backfill.py tests/factors/test_library.py -v`
Expected: 全 PASS

- [ ] **Step 5: Commit**

```bash
git add scripts/backfill_factor_orientation.py tests/factors/test_orientation_backfill.py src/finance_agent/factors/library.py
git commit -m "feat(factors): 存量 orientation 幂等回填脚本（direction-reversal 3/4）"
```

---

### Task 4: 监控反转判据分支

**Files:**
- Modify: `src/finance_agent/factors/monitor.py`（`_decay_snapshot` ~line 103-131、`run_factor_monitor` ~line 158-210）
- Test: `tests/factors/test_monitor.py`

**Interfaces:**
- Consumes: Task 1 的 factors 行 `orientation` 列（`list_active_factors` 返回 dict 含该键——其 SELECT 若为显式清单同 Task 3 一并加列）
- Produces: `run_factor_monitor` 返回 dict 新键 `reversed: list[int]`（本次反转降级 id）；`_decay_snapshot` 新增可选参 `orientation: int = 0`，返回 dict 新键 `"reversal": bool`

- [ ] **Step 1: Write the failing test**

沿用 `tests/factors/test_monitor.py` 现有合成面板构造（模块 docstring 有并列秩构造配方）。核心用例（直接构造落库 IC 序列 + 调 `_decay_snapshot` 层面的 `run_factor_monitor` 前置——沿用该文件对 `run_factor_monitor` 的既有调用模式，`patch` 掉面板求值路径的做法照抄现有用例）：

```python
class TestDirectionReversal:
    def test_reversal_degrades_negative_orientation_factor(self, tmp_path, _monitor_env):
        # _monitor_env = 现有 fixture（db + 面板 + as_of），落库 IC 序列构造为
        # 最近 _REQUIRED_IC_POINTS 点全部为正（负向因子翻正）、orientation=-1
        db, panel, as_of = _monitor_env
        fid = add_factor(expression="ts_mean(close,5)", hypothesis="h", category="测试",
                         status="active", db_path=db)
        set_orientation(fid, -1, db_path=db)
        for i in range(_REQUIRED_IC_POINTS):
            record_ic(fid, _day(i), 0.05, db_path=db)  # 全正 → 与 -1 定向相反
        result = run_factor_monitor(panel, as_of, db_path=db)
        assert fid in result["reversed"]
        states = {r["id"]: r["status"] for r in list_factors(db_path=db)}
        assert states[fid] == "degraded"
        events = list_events(fid, db_path=db)
        assert any("DIRECTION_REVERSAL" in str(e.get("detail")) for e in events)

    def test_short_positive_window_keeps_active(self, tmp_path, _monitor_env):
        db, panel, as_of = _monitor_env
        fid = add_factor(expression="ts_mean(close,5)", hypothesis="h", category="测试",
                         status="active", db_path=db)
        set_orientation(fid, -1, db_path=db)
        for i in range(_REQUIRED_IC_POINTS):
            value = 0.05 if i >= _REQUIRED_IC_POINTS - 5 else -0.05  # 仅末 5 点为正
            record_ic(fid, _day(i), value, db_path=db)
        result = run_factor_monitor(panel, as_of, db_path=db)
        assert fid not in result["reversed"]
        states = {r["id"]: r["status"] for r in list_factors(db_path=db)}
        assert states[fid] == "active"

    def test_orientation_zero_skips_reversal(self, tmp_path, _monitor_env):
        db, panel, as_of = _monitor_env
        fid = add_factor(expression="ts_mean(close,5)", hypothesis="h", category="测试",
                         status="active", db_path=db)
        set_orientation(fid, 0, db_path=db)
        for i in range(_REQUIRED_IC_POINTS):
            record_ic(fid, _day(i), 0.05, db_path=db)  # 全正但定向不可定
        result = run_factor_monitor(panel, as_of, db_path=db)
        assert fid not in result["reversed"]
        states = {r["id"]: r["status"] for r in list_factors(db_path=db)}
        assert states[fid] == "active"  # 原衰减判据（负且弱）也不命中全正序列
```

（`_monitor_env` 以文件内现有面板 fixture 真名替换；`_day` 抄该文件既有的日期辅助。）

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/factors/test_monitor.py::TestDirectionReversal -v`
Expected: FAIL（`reversed` 键不存在）

- [ ] **Step 3: Write minimal implementation**

`_decay_snapshot` 签名加 `orientation: int = 0`，`degraded` 行下加反转判定（反转优先——返回 dict 的 `reversal` 键承载，`degraded` 键改为「两判据任一命中」）：

```python
    tail_icirs = rolling_icir.reindex(tail_means.index)
    decay_hit = bool((tail_means < 0.0).all() and (tail_icirs.abs() < DECAY_ICIR_FLOOR).all())
    # 反转判据：滚动均值连续 20 点与入池定向相反（无 ICIR 幅度门槛——60 日平滑后
    # 连续 20 点反向的噪声穿越概率可忽略；定向 0 不判反转）。
    if orientation == -1:
        reversal_hit = bool((tail_means > 0.0).all())
    elif orientation == 1:
        reversal_hit = bool((tail_means < 0.0).all())
    else:
        reversal_hit = False
    degraded = decay_hit or reversal_hit
```

（实施时删除占位两行，只保留 if/elif 显式分支。）

（`_decay_snapshot` 返回 dict 加 `"reversal": reversal_hit` 键；`degraded` 键语义变为「两判据任一命中」。）

`run_factor_monitor` 判定块改为（`row` 即 `list_active_factors` 行，含 `orientation`）：

```python
        snapshot = _decay_snapshot(
            factor_id, as_of, db_path=db_path, orientation=int(row.get("orientation") or 0)
        )
        ...
        if not snapshot["degraded"]:
            continue
        reversal = bool(snapshot.get("reversal"))
        set_status(
            factor_id,
            "degraded",
            detail={
                "reason": "DIRECTION_REVERSAL" if reversal else "DECAY",
                "orientation": int(row.get("orientation") or 0),
                # DECAY 原有键原样保留（见现实现）；reversal 时同样携带滚动读数
                "as_of": as_of,
                ...
            },
            db_path=db_path,
        )
        degraded.append(factor_id)
        if reversal:
            reversed_ids.append(factor_id)
```

返回 dict 加 `"reversed": reversed_ids`（初始化 `reversed_ids: list[int] = []`）。`judged` 快照条目加 `"reversal": reversal`。

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/factors/test_monitor.py -v`
Expected: 全 PASS（含既有衰减用例——原判据行为不变）

- [ ] **Step 5: 全量回归 + lint + 类型**

Run: `uv run pytest tests/factors/ -v && uv run ruff check && uv run mypy`
Expected: 全绿

- [ ] **Step 6: Commit**

```bash
git add src/finance_agent/factors/monitor.py tests/factors/test_monitor.py
git commit -m "feat(factors): 衰减监控方向反转判据（direction-reversal 4/4）"
```

---

### Task 5: 生产库回填 + 实跑验证（运维任务）

**Files:**
- 无代码变更；生产操作 + tasks.md 回填

**Interfaces:**
- Consumes: Task 3 脚本；生产库 `data/factor_library.db`

- [ ] **Step 1: 生产库备份**

```bash
cp data/factor_library.db data/factor_library.db.bak-orientation-$(date +%Y%m%d-%H%M%S)
```

- [ ] **Step 2: 跑回填并核对读数**

```bash
uv run python scripts/backfill_factor_orientation.py
```

Expected: `backfilled=12`（当前 12 条存量因子），再跑一次 `backfilled=0`（幂等）。逐条核对：10 条负 IC 均值因子 → -1。

- [ ] **Step 3: 次日日批实跑验证**

次日 16:00 后查 `GET /api/v1/ops/jobs` 的 `factor_decay_monitor` 运行摘要：`reversed` 键存在且当期如实（无反转触发时为空数组——诚实留痕即可，不构造触发）。红线：日批窗口 15:30–17:10 不重启后端。

- [ ] **Step 4: tasks.md 回填 + 人工验证报告**

`openspec/changes/add-factor-direction-reversal-monitor/tasks.md` 全勾；报告落 `tests/validation/2026-10-XX-add-factor-direction-reversal-monitor-validation.md`。

- [ ] **Step 5: Commit**

```bash
git add openspec/changes/add-factor-direction-reversal-monitor/tasks.md tests/validation/
git commit -m "docs(factors): direction-reversal 验证报告与 tasks 回填"
```
