# 入库后业绩复核 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 月批尾部对 active 因子做入库后全样本复核——|ICIR| 跌破入池水位 0.3 或账本全局 BH 反事实 p_adj > 0.1 → 降级。

**Architecture:** 新函数 `run_post_admission_review(db_path)` 复用既有件：IC 序列走 `list_ic_series`（入库后才开始记 IC，序列天然 = 入库后样本）、全局反事实走 `benjamini_hochberg_adjust` + `list_ledger_entries`（单一真相源，零新增校正实现）、降级走 `set_status` 既有事件路径；挂载点 `run_factor_proposal_monthly` 深度批之后，摘要加 `{reviewed, degraded, skipped}`。

**Tech Stack:** Python 3.14 / sqlite3 / pandas / pytest

## Global Constraints

- 复核不触碰 `factor_admission_ledger` 任何行（只读消费）；闸③批内校正语义逐字不变
- 已 degraded/retired 因子天然不在 `list_active_factors` 集合（MUST NOT 复核恢复）
- 复核异常上抛（fail loud，job 记 failed），不静默跳过
- 样本门槛 60 = `MIN_IC_SAMPLES` 之外的独立常量 `POST_ADMISSION_MIN_SAMPLES = 60`（放 `constraints.py`，与闸②水位 `MIN_ABS_ICIR=0.3`、账本 `BH_FDR_Q=0.1` 同源引用）
- 测试落 tmp_path，零网络

---

### Task 1: 复核函数 + 常量

**Files:**
- Modify: `src/finance_agent/factors/constraints.py`（末尾加常量）
- Create: `src/finance_agent/factors/review.py`
- Test: `tests/factors/test_review.py`

**Interfaces:**
- Consumes: `list_active_factors` / `list_ic_series` / `list_ledger_entries`（library.py）；`benjamini_hochberg_adjust` / `BH_FDR_Q` / `MIN_ABS_ICIR`（validate.py / constraints.py）；`set_status`（library.py）
- Produces: `run_post_admission_review(*, db_path=None) -> dict[str, Any]`，返回 `{"reviewed": int, "degraded": [int], "skipped": [int]}`；常量 `POST_ADMISSION_MIN_SAMPLES: int = 60`

- [ ] **Step 1: Write the failing test**

```python
"""factors/review.py 单测（add-factor-post-admission-review）。

四用例：恒定序列水位失守降级 / 真波动序列+账本显著通过 / 全局反事实降级 /
样本不足跳过。库全落 tmp_path。
"""
from __future__ import annotations

import math
import sqlite3

from finance_agent.factors.library import add_factor, record_admission_ledger, record_ic
from finance_agent.factors.review import run_post_admission_review


def _seed_factor(db, *, name_hint: str, n_samples: int, ic: float) -> int:
    fid = add_factor(expression=f"ts_mean(close,5)+{name_hint}", hypothesis="h",
                     category="测试", status="active", db_path=db)
    for i in range(n_samples):
        record_ic(fid, f"2026-01-{i + 1:02d}", ic, db_path=db)
    return fid


def _seed_ledger(db, *, fid: int | None, p_value: float) -> None:
    record_admission_ledger(
        [{"batch_id": "b1", "source": "test", "expression": "ts_mean(close,5)",
          "p_value": p_value, "p_adj": None, "q": None, "n_hypotheses": 1,
          "admitted": 1, "factor_id": fid, "rejected_gate": None}],
        db_path=db,
    )


class TestPostAdmissionReview:
    def test_watermark_degrades_constant_series(self, tmp_path):
        # 恒定 IC → std=0 → ICIR=0（与闸②同口径）→ 水位失守降级
        db = str(tmp_path / "factor_library.db")
        f_weak = _seed_factor(db, name_hint="1", n_samples=80, ic=0.04)
        result = run_post_admission_review(db_path=db)
        assert result["reviewed"] == 1
        assert f_weak in result["degraded"]
        conn = sqlite3.connect(db)
        status = conn.execute("SELECT status FROM factors WHERE id=?", (f_weak,)).fetchone()[0]
        conn.close()
        assert status == "degraded"

    def test_pass_with_real_series_and_ledger_significant(self, tmp_path):
        db = str(tmp_path / "factor_library.db")
        fid = add_factor(expression="ts_mean(close,5)", hypothesis="h", category="测试",
                         status="active", db_path=db)
        # 80 点波动序列：均值 ~0.4 / std ~0.1 → |ICIR| ≥ 0.3（过水位）
        for i in range(80):
            record_ic(fid, f"2026-01-{i + 1:02d}", 0.4 + 0.1 * math.sin(i), db_path=db)
        _seed_ledger(db, fid=fid, p_value=0.001)  # 强 p → 全局 BH 亦显著
        result = run_post_admission_review(db_path=db)
        assert result["reviewed"] == 1
        assert result["degraded"] == []
        conn = sqlite3.connect(db)
        status = conn.execute("SELECT status FROM factors WHERE id=?", (fid,)).fetchone()[0]
        conn.close()
        assert status == "active"

    def test_ledger_counterfactual_degrades(self, tmp_path):
        db = str(tmp_path / "factor_library.db")
        fid = add_factor(expression="ts_mean(close,5)", hypothesis="h", category="测试",
                         status="active", db_path=db)
        for i in range(80):
            record_ic(fid, f"2026-01-{i + 1:02d}", 0.4 + 0.1 * math.sin(i), db_path=db)
        # 30 条家族 p=0.5 的陪跑 + 本因子 p=0.06（批内显著、全局 BH 后 >0.1）
        _seed_ledger(db, fid=fid, p_value=0.06)
        _seed_ledger(db, fid=None, p_value=0.5)  # 无 factor_id 的陪跑行
        result = run_post_admission_review(db_path=db)
        assert fid in result["degraded"]

    def test_insufficient_samples_skipped(self, tmp_path):
        db = str(tmp_path / "factor_library.db")
        fid = _seed_factor(db, name_hint="3", n_samples=30, ic=0.04)
        result = run_post_admission_review(db_path=db)
        assert result["reviewed"] == 0
        assert fid in result["skipped"]
        assert result["degraded"] == []
```

（测试以「恒定序列 → std=0 → ICIR=0 < 0.3 → 降级」覆盖水位判据与闸②同口径；通过路径用 sin 波动序列覆盖；反事实用陪跑家族撑 BH 分母。）

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/factors/test_review.py -v`
Expected: FAIL（ModuleNotFoundError: finance_agent.factors.review）

- [ ] **Step 3: Write minimal implementation**

`constraints.py` 末尾：

```python
# 入库后复核样本门槛（add-factor-post-admission-review）：入库后 IC 样本 ≥ 60 才判定
POST_ADMISSION_MIN_SAMPLES: int = 60
```

`review.py`：

```python
"""入库后业绩复核（add-factor-post-admission-review）：月批尾部运行期处置。

对 active 且入库后 IC 样本 ≥ ``POST_ADMISSION_MIN_SAMPLES`` 的因子复核两项
（detail 同记）：
1. 水位复核：入库后全样本 |ICIR|（mean/std，std=0 → 0，与闸②同口径）
   < ``MIN_ABS_ICIR`` → 命中；
2. 全局多重检验复核：账本全部非空 p 值作单一家族 BH 校正（与
   ``cumulative_fdr_preview`` 同一算法单一真相源），该因子 ledger 行
   ``p_adj > BH_FDR_Q`` → 命中。

任一命中 → ``set_status(active → degraded, reason=POST_ADMISSION_REVIEW)``。
账本只读消费（MUST NOT 写 ledger）；异常上抛（fail loud）。
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

from finance_agent.factors.constraints import BH_FDR_Q, MIN_ABS_ICIR, POST_ADMISSION_MIN_SAMPLES
from finance_agent.factors.library import (
    list_active_factors,
    list_ic_series,
    list_ledger_entries,
    set_status,
)
from finance_agent.factors.validate import benjamini_hochberg_adjust


def _icir(values: list[float]) -> float:
    """mean/std（ddof=1）；样本 < 2 或 std=0 → 0.0（与闸② ``_ic_stats`` 同口径）。"""
    n = len(values)
    if n < 2:
        return 0.0
    mean = sum(values) / n
    var = sum((v - mean) ** 2 for v in values) / (n - 1)
    std = math.sqrt(var)
    if std == 0.0:
        return 0.0
    return mean / std


def _global_p_adj_by_factor(db_path: str | Path | None) -> dict[int, float]:
    """账本全局 BH：全部非空 p 值作单一家族 → {factor_id: p_adj}（同 factor 多行取最小）。"""
    entries = list_ledger_entries(db_path=db_path)
    tested = {str(e["id"]): float(e["p_value"]) for e in entries if e.get("p_value") is not None}
    if not tested:
        return {}
    adjusted = benjamini_hochberg_adjust(tested)
    best: dict[int, float] = {}
    for e in entries:
        if e.get("p_value") is None or e.get("factor_id") is None:
            continue
        p_adj = adjusted.get(str(e["id"]))
        fid = int(e["factor_id"])
        if p_adj is not None and (fid not in best or p_adj < best[fid]):
            best[fid] = float(p_adj)
    return best


def run_post_admission_review(*, db_path: str | Path | None = None) -> dict[str, Any]:
    reviewed = 0
    degraded: list[int] = []
    skipped: list[int] = []
    global_p_adj = _global_p_adj_by_factor(db_path)
    for row in list_active_factors(db_path=db_path):
        fid = int(row["id"])
        values = [float(v) for _d, v in list_ic_series(fid, db_path=db_path)]
        if len(values) < POST_ADMISSION_MIN_SAMPLES:
            skipped.append(fid)
            continue
        reviewed += 1
        icir = _icir(values)
        p_adj = global_p_adj.get(fid)
        watermark_hit = abs(icir) < MIN_ABS_ICIR
        counterfactual_hit = p_adj is not None and p_adj > BH_FDR_Q
        if not (watermark_hit or counterfactual_hit):
            continue
        set_status(
            fid,
            "degraded",
            detail={
                "reason": "POST_ADMISSION_REVIEW",
                "post_admission": {
                    "icir": icir,
                    "global_p_adj": p_adj,
                    "q": BH_FDR_Q,
                    "watermark_hit": watermark_hit,
                    "counterfactual_hit": counterfactual_hit,
                },
            },
            db_path=db_path,
        )
        degraded.append(fid)
    return {"reviewed": reviewed, "degraded": degraded, "skipped": skipped}
```

（`benjamini_hochberg_adjust` 的真实签名以 `validate.py` 现文件为准——实施者先 grep 该函数定义对齐键类型；`record_admission_ledger` 的 entries 合同以 `library.py:483` 现签名为准。）

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/factors/test_review.py -v`
Expected: 全 PASS

- [ ] **Step 5: Commit**

```bash
git add src/finance_agent/factors/constraints.py src/finance_agent/factors/review.py tests/factors/test_review.py
git commit -m "feat(factors): 入库后业绩复核函数（post-admission-review 1/2）"
```

---

### Task 2: 月批挂载 + 摘要读数

**Files:**
- Modify: `src/finance_agent/factors/monthly.py:388-405`（summary 构造与 `run_factor_proposal_monthly` 尾部）
- Test: `tests/factors/test_monthly.py`

**Interfaces:**
- Consumes: Task 1 的 `run_post_admission_review`
- Produces: `run_factor_proposal_monthly` 摘要新增 `{"review": {"reviewed": int, "degraded": [int], "skipped": [int]}}`

- [ ] **Step 1: Write the failing test**

```python
# 追加到 tests/factors/test_monthly.py（复用该文件现有 stub client / 面板 fixture）
def test_monthly_summary_includes_review_block(self, tmp_path, _monthly_fixture):
    db, summary = _monthly_fixture  # 现有月批测试的 db + run 返回值构造
    assert "review" in summary
    assert set(summary["review"].keys()) == {"reviewed", "degraded", "skipped"}
```

（以文件内现有月批用例的真 fixture 替换；若现用例返回结构断言过严，扩展其断言而非放松。）

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/factors/test_monthly.py -k review -v`
Expected: FAIL（摘要无 `review` 键）

- [ ] **Step 3: Write minimal implementation**

`monthly.py` summary 构造前插入（深度批之后）：

```python
    from finance_agent.factors.review import run_post_admission_review  # 函数内 import 防成环（FM 审批面先例）

    review = run_post_admission_review(db_path=db_path)
```

summary dict 加：

```python
        "review": review,
```

模块顶部 import 区不加（防循环 import——review 无需反向依赖 monthly）。

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/factors/test_monthly.py -v && uv run pytest tests/factors/ -v`
Expected: 全 PASS

- [ ] **Step 5: 全量回归 + lint + 类型**

Run: `uv run pytest && uv run ruff check && uv run mypy`
Expected: 全绿

- [ ] **Step 6: Commit**

```bash
git add src/finance_agent/factors/monthly.py tests/factors/test_monthly.py
git commit -m "feat(factors): 月批尾部挂载入库后复核 + 摘要读数（post-admission-review 2/2）"
```

---

### Task 3: 生产首月实跑验证（运维任务）

**Files:**
- 无代码变更

- [ ] **Step 1: 下一次月批（每月 1 日 10:00）后查 job 摘要**

`GET /api/v1/ops/jobs` → `factor_proposal_monthly` 最新 run：`summary.review` 键齐全；degraded 非空时**逐条人工归因**（incident 026 纪律：聚合指标进入处置前先分桶归因、逐条终裁——确认降级因子入库后 IC 确实走弱，排除数据缺口导致的假 ICIR）。

- [ ] **Step 2: 降级因子次日退出打分核验**

`list_active_factors` 不含被降级 id；当日选股打分 L2 权重表无该因子。

- [ ] **Step 3: 报告落档**

`tests/validation/2026-11-XX-add-factor-post-admission-review-validation.md` + tasks.md 全勾。

- [ ] **Step 4: Commit**

```bash
git add openspec/changes/add-factor-post-admission-review/tasks.md tests/validation/
git commit -m "docs(factors): post-admission-review 验证报告与 tasks 回填"
```
