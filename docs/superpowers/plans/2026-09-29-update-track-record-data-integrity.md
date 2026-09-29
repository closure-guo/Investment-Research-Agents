# Update Track Record Data Integrity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复历史战绩组合指标失真（neutral 幻影空头、坏参考价、年化口径放大），落地 delta `update-track-record-data-integrity`。

**Architecture:** marking/metrics 纯函数层加 neutral 排除与交易日历骨架；ingest 与 marking 加参考价护栏（新模块 reference_price.py 单点阈值）；API as_of 改数据日期；derived 三表 wipe+rebuild 脚本。

**Tech Stack:** Python 3.12 / pytest / ruff / mypy / uv

## Global Constraints

- neutral 盯市符号 = long 口径（与 `judgment` 回避判定同号）；组合聚合 SHALL 排除 neutral
- 阈值 env `TRACK_ENTRY_PRICE_MAX_DEVIATION` 默认 0.30，调用期读取（测试可 patch）
- 净值首盯市日贡献 0；年化 n = 交易日数（calendar 传入时）
- 传参向后兼容：`daily_portfolio_returns(marks)` / `compute_metrics_from_marks(marks, risk_free_rate=…)` 现有调用不破坏
- 每任务 TDD：先写失败测试 → 红 → 最小实现 → 绿 → commit
- 分支 `fix/track-record-data-integrity`（worktree `.worktrees/track-data-integrity`），commit 格式 `fix: [track-record] …`

---

### Task 1: neutral 盯市符号 + 组合聚合排除

**Files:**
- Modify: `src/finance_agent/outcome/track_record/marking.py:115`
- Modify: `src/finance_agent/outcome/track_record/metrics.py`
- Modify: `src/finance_agent/outcome/track_record/model.py`（新增 prediction_ids_by_direction）
- Test: `tests/outcome/test_track_record_metrics.py`、`tests/outcome/test_track_record_model.py`

**Interfaces:**
- Produces: `model.prediction_ids_by_direction(direction: str, db_path=None) -> set[str]`；`daily_portfolio_returns(marks, exclude_prediction_ids=None)`；`compute_metrics_from_marks(marks, risk_free_rate=…, exclude_prediction_ids=None)`

- [ ] **Step 1: Write the failing test**

```python
class TestNeutralExclusion:
    def test_neutral_marks_excluded_from_portfolio(self):
        """incident 032 根因 A：neutral（hold/watch）盯市不得进组合净值/指标。"""
        marks = [
            {"prediction_id": "n1", "mark_date": "2026-06-02", "cum_return": 0.05, "benchmark_price": 3000.0},
            {"prediction_id": "n1", "mark_date": "2026-06-03", "cum_return": 0.10, "benchmark_price": 3010.0},
        ]
        pm = compute_metrics_from_marks(marks, risk_free_rate=0.02, exclude_prediction_ids={"n1"})
        assert pm.nav_points == []
        assert pm.annual_return is None

    def test_neutral_diff_not_in_portfolio_mean(self):
        marks = [
            {"prediction_id": "n1", "mark_date": "2026-06-02", "cum_return": 0.50, "benchmark_price": 3000.0},
            {"prediction_id": "n1", "mark_date": "2026-06-03", "cum_return": 0.40, "benchmark_price": 3010.0},
            {"prediction_id": "p1", "mark_date": "2026-06-02", "cum_return": 0.01, "benchmark_price": 3000.0},
            {"prediction_id": "p1", "mark_date": "2026-06-03", "cum_return": 0.02, "benchmark_price": 3010.0},
        ]
        rets = daily_portfolio_returns(marks, exclude_prediction_ids={"n1"})
        # 06-03 只含 p1 的 +0.01；n1 的 -0.10 幻影空头损益被排除
        assert rets["2026-06-03"] == pytest.approx(0.01)
```

（加到 `tests/outcome/test_track_record_metrics.py`；`test_track_record_model.py` 加 `prediction_ids_by_direction` 往返测试；`TestMarking` 加：）

```python
    def test_neutral_marked_long_caliber(self, db, fake_client):
        """incident 032 根因 A：neutral 盯市按多头口径（与回避判定同号），不取反。"""
        pid = _insert(db, direction="neutral", created_at="2026-06-01T10:00:00")
        mark_open_predictions(client=fake_client, db_path=db)
        marks = list_daily_marks(prediction_id=pid, db_path=db)
        assert marks[0]["cum_return"] == pytest.approx(0.01)  # 101/100-1，而非 -0.01
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/outcome/test_track_record_metrics.py -v -k "Neutral or neutral_marked"`
Expected: FAIL（TypeError: unexpected keyword / cum_return == -0.01）

- [ ] **Step 3: Write minimal implementation**

marking.py sign 行替换：

```python
        # neutral 按 long 口径记录（与 judgment 回避判定同号；incident 032 根因 A：
        # 旧代码 else -1.0 把 hold/watch 当空头，产生幻影空头损益）
        sign = -1.0 if p["direction"] == "short" else 1.0
```

metrics.py：`daily_portfolio_returns` 加 `exclude_prediction_ids: set[str] | None = None`（循环内 `if m["prediction_id"] in excl: continue`）；`compute_metrics_from_marks` 加同名参数并在函数入口过滤 marks。model.py 追加：

```python
def prediction_ids_by_direction(direction: str, db_path: str | Path | None = None) -> set[str]:
    """按方向取 prediction_id 集合（组合聚合排除 neutral 用，incident 032 根因 A）。"""
    conn = _connect(db_path)
    try:
        rows = conn.execute(
            "SELECT prediction_id FROM predictions WHERE direction=?", (direction,)
        ).fetchall()
        return {r["prediction_id"] for r in rows}
    finally:
        conn.close()
```

`build_equity_curve_points` 与 `compute_metrics_snapshot` 内取 `excl = prediction_ids_by_direction("neutral", db_path=db_path)` 传入。

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/outcome/test_track_record_metrics.py tests/outcome/test_track_record_model.py -q`
Expected: PASS（存量断言不受影响——首日语义在 Task 4 才变）

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "fix: [track-record] neutral 盯市改多头口径并排除出组合聚合 (incident 032 根因 A)"
```

---

### Task 2: reference_price 模块 + ingest 参考价交叉校验

**Files:**
- Create: `src/finance_agent/outcome/track_record/reference_price.py`
- Modify: `src/finance_agent/outcome/track_record/ingest.py:47-58`
- Test: `tests/outcome/test_track_record_reference_price.py`（新建）、`tests/outcome/test_track_record_ingest.py`

**Interfaces:**
- Produces: `reference_price.max_reference_deviation() -> float`；`reference_price_ok(price: float, reference: float) -> bool`

- [ ] **Step 1: Write the failing test**

```python
# tests/outcome/test_track_record_reference_price.py
"""参考价护栏单测（delta update-track-record-data-integrity）。"""
import pytest
from finance_agent.outcome.track_record.reference_price import (
    max_reference_deviation,
    reference_price_ok,
)


def test_default_threshold_30pct(monkeypatch):
    monkeypatch.delenv("TRACK_ENTRY_PRICE_MAX_DEVIATION", raising=False)
    assert max_reference_deviation() == pytest.approx(0.30)
    assert reference_price_ok(129.0, 100.0) is True   # +29% 通过
    assert reference_price_ok(133.0, 100.0) is False  # +33% 拒绝
    assert reference_price_ok(69.0, 100.0) is False   # -31% 拒绝


def test_env_override(monkeypatch):
    monkeypatch.setenv("TRACK_ENTRY_PRICE_MAX_DEVIATION", "0.05")
    assert reference_price_ok(104.0, 100.0) is False


def test_nonpositive_reference_passes():
    """reference 非正视为不可校验（放行，由调用方另行兜底）。"""
    assert reference_price_ok(100.0, 0.0) is True
```

（`test_track_record_ingest.py` 追加三个用例：quote 1800 + kline 收盘 1330 → entry=1330 + WARN「交叉校验」；quote 100.5 + kline 100.0 → entry=100.5；quote 有 kline 无 → entry=quote + WARN「无法交叉校验」。kline 形态用 `accumulated["kline"] = [{"收盘": 1330.0}]`。）

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/outcome/test_track_record_reference_price.py tests/outcome/test_track_record_ingest.py -q`
Expected: FAIL（ModuleNotFoundError / entry==1800）

- [ ] **Step 3: Write minimal implementation**

新建 reference_price.py：

```python
"""参考价护栏（delta update-track-record-data-integrity / incident 032 根因 B）。

quote 实时价与 K 线收盘可能因数据源异常大幅偏离（生产事故：茅台 quote 1800 vs
实际收盘 1330，偏离 35%）。阈值与校验在此单点定义，ingest（落库）与 marking
（盯市）共用，禁止各自再写拷贝。
"""

from __future__ import annotations

import os


def max_reference_deviation() -> float:
    """参考价偏离阈值；env TRACK_ENTRY_PRICE_MAX_DEVIATION 可配（默认 0.30）。"""
    try:
        return float(os.getenv("TRACK_ENTRY_PRICE_MAX_DEVIATION", "0.30"))
    except ValueError:
        return 0.30


def reference_price_ok(price: float, reference: float) -> bool:
    """|price/reference - 1| 未超阈值 → True。任一价非正视為不可校验（True）。"""
    if reference <= 0 or price <= 0:
        return True
    return abs(price / reference - 1.0) <= max_reference_deviation()
```

ingest.py 取价段替换：

```python
        quote_price = (accumulated.get("stock_quote") or {}).get("price")
        kline_close = _kline_last_close(accumulated.get("kline"))
        entry_price: float | None = None
        if quote_price is not None and kline_close is not None:
            if reference_price_ok(float(quote_price), float(kline_close)):
                entry_price = float(quote_price)
            else:
                logger.warning(
                    "参考价交叉校验未过（quote=%s vs kline收盘=%s 偏离超阈值）→ 降级采用 K 线收盘",
                    quote_price,
                    kline_close,
                )
                entry_price = float(kline_close)
        elif quote_price is not None:
            logger.warning("参考价保留 quote=%s：K 线缺失无法交叉校验", quote_price)
            entry_price = float(quote_price)
        elif kline_close is not None:
            entry_price = float(kline_close)
```

`_kline_last_close`：

```python
def _kline_last_close(kline: Any) -> float | None:
    if kline is None or len(kline) == 0:
        return None
    last = kline.iloc[-1] if hasattr(kline, "iloc") else kline[-1]
    try:
        return float(last["收盘"])
    except (KeyError, TypeError, ValueError):
        return None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/outcome/test_track_record_reference_price.py tests/outcome/test_track_record_ingest.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "fix: [track-record] ingest 参考价 quote↔K 线交叉校验护栏 (incident 032 根因 B)"
```

---

### Task 3: 盯市侧参考价失效防护

**Files:**
- Modify: `src/finance_agent/outcome/track_record/marking.py`（mark_open_predictions 循环内）
- Test: `tests/outcome/test_track_record_metrics.py::TestMarking`

**Interfaces:**
- Consumes: `reference_price_ok`（Task 2）

- [ ] **Step 1: Write the failing test**

```python
    def test_stale_reference_price_skipped(self, db):
        """incident 032 根因 B 存量防护：entry 与首盯市收盘偏离 >30% → skipped 不写 marks。"""
        klines = {"600519": _df(["2026-06-02", "2026-06-03"], [1316.01, 1309.3])}
        bench = _df(["2026-06-01", "2026-06-02"], [3000.0, 3100.0])
        client = FakeClient(klines, bench)
        pid = _insert(db, entry_price=1800.0, created_at="2026-06-01T10:00:00")
        result = mark_open_predictions(client=client, db_path=db)
        assert result["skipped"] == 1
        assert result["marked"] == 0
        assert list_daily_marks(prediction_id=pid, db_path=db) == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/outcome/test_track_record_metrics.py::TestMarking::test_stale_reference_price_skipped -v`
Expected: FAIL（marked==3，marks 非空）

- [ ] **Step 3: Write minimal implementation**

mark_open_predictions 在 `if rows.empty: … continue` 之后、for 循环之前插入：

```python
        first_close = float(rows.iloc[0]["收盘"])
        if not reference_price_ok(float(entry), first_close):
            logger.warning(
                "参考价失效（entry=%s vs 首盯市收盘=%s 偏离超阈值），跳过 %s 供人工甄别",
                entry,
                first_close,
                p["prediction_id"],
            )
            result["skipped"] += 1
            continue
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/outcome/test_track_record_metrics.py -q`
Expected: PASS（既有用例 entry=100 vs 收盘 101/9.0 偏离均在阈值内）

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "fix: [track-record] 盯市侧参考价失效跳过防护 (incident 032 根因 B)"
```

---

### Task 4: 净值/指标交易日历口径（首日 0、空仓补 0、年化 n）

**Files:**
- Modify: `src/finance_agent/outcome/track_record/metrics.py`
- Modify: `src/finance_agent/outcome/track_record/marking.py`（run_daily_marking / persist_metrics_snapshot 透传日历）
- Test: `tests/outcome/test_track_record_metrics.py`

**Interfaces:**
- Produces: `daily_portfolio_returns(marks, calendar_dates=None, exclude_prediction_ids=None)`；`compute_metrics_from_marks(marks, risk_free_rate=…, calendar_dates=None, benchmark_by_date=None, exclude_prediction_ids=None)`；`build_equity_curve_points(db_path=None, calendar_dates=None, benchmark_by_date=None, exclude_prediction_ids=None)`；`compute_metrics_snapshot(db_path=None, calendar_dates=None, benchmark_by_date=None)`；`marking.persist_metrics_snapshot(db_path=None, client=None, kline_days=280, benchmark=None)`

- [ ] **Step 1: Write the failing test**

```python
class TestCalendarCaliber:
    """incident 032 根因 C：首盯市日 0 收益 + 交易日历覆盖空仓日 + 年化 n=交易日数。"""

    def test_first_mark_day_contributes_zero(self):
        marks = [
            {"prediction_id": "p1", "mark_date": "2026-06-02", "cum_return": 0.05, "benchmark_price": 3000.0},
            {"prediction_id": "p1", "mark_date": "2026-06-03", "cum_return": 0.08, "benchmark_price": 3010.0},
        ]
        rets = daily_portfolio_returns(marks)
        assert rets["2026-06-02"] == pytest.approx(0.0)
        assert rets["2026-06-03"] == pytest.approx(0.03)

    def test_calendar_fills_empty_days_with_zero(self):
        marks = [
            {"prediction_id": "p1", "mark_date": "2026-06-02", "cum_return": 0.01, "benchmark_price": 3000.0},
            {"prediction_id": "p1", "mark_date": "2026-06-05", "cum_return": 0.02, "benchmark_price": 3010.0},
        ]
        cal = ["2026-06-02", "2026-06-03", "2026-06-04", "2026-06-05"]
        rets = daily_portfolio_returns(marks, calendar_dates=cal)
        assert set(rets) == set(cal)
        assert rets["2026-06-03"] == pytest.approx(0.0)
        assert rets["2026-06-04"] == pytest.approx(0.0)

    def test_annual_uses_calendar_n_not_marked_n(self):
        marks = [
            {"prediction_id": "p1", "mark_date": "2026-06-02", "cum_return": 0.00, "benchmark_price": 3000.0},
            {"prediction_id": "p1", "mark_date": "2026-06-05", "cum_return": 0.01, "benchmark_price": 3010.0},
        ]
        cal = ["2026-06-02", "2026-06-03", "2026-06-04", "2026-06-05"]
        pm = compute_metrics_from_marks(marks, risk_free_rate=0.02, calendar_dates=cal)
        expected = 1.01 ** (252 / 4) - 1  # n=4，SHALL NOT 按 2 个盯市日外推
        assert pm.annual_return == pytest.approx(expected, abs=1e-6)

    def test_benchmark_nav_advances_on_empty_days(self):
        marks = [
            {"prediction_id": "p1", "mark_date": "2026-06-02", "cum_return": 0.0, "benchmark_price": 3000.0},
            {"prediction_id": "p1", "mark_date": "2026-06-05", "cum_return": 0.01, "benchmark_price": 3100.0},
        ]
        bench = {"2026-06-02": 3000.0, "2026-06-03": 3030.0, "2026-06-04": 3060.0, "2026-06-05": 3090.0}
        pm = compute_metrics_from_marks(
            marks,
            calendar_dates=["2026-06-02", "2026-06-03", "2026-06-04", "2026-06-05"],
            benchmark_by_date=bench,
        )
        navs = {p["date"]: p["benchmark_nav"] for p in pm.nav_points}
        assert navs["2026-06-02"] == pytest.approx(1.0)
        assert navs["2026-06-03"] == pytest.approx(3030.0 / 3000.0)
        assert navs["2026-06-05"] == pytest.approx(3090.0 / 3000.0)
```

（同时把既有 `test_equal_weight_mean_and_missing_excluded` 期望改为新口径：`06-02 → 0.0`、`06-03 → 0.01`、`06-04 → 0.015`——这是 spec 行为变更点，diff 可解释。）

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/outcome/test_track_record_metrics.py -q -k "Calendar or equal_weight"`
Expected: FAIL

- [ ] **Step 3: Write minimal implementation**

`daily_portfolio_returns` 重写（见 delta design D2/D3）：首盯市日 `daily[d] = 0.0`；`calendar_dates` 传入时 `dates = sorted({d for d in calendar_dates if d >= first_mark})`，无盯市日记 0（原死分支激活）；`compute_metrics_from_marks` 加 `calendar_dates` / `benchmark_by_date` 参数——基准净值改由 `benchmark_by_date` 按日历推进（基日 = 序列首日收盘，缺失日沿用前值持平），缺省退化为现行 `_benchmark_returns(marks)`；年化/波动/回撤公式不变（n 自然变为日历长度）。marking.py：抽 `_fetch_benchmark(client, kline_days)`；`mark_open_predictions` 加 `benchmark=None` 透传参数；`run_daily_marking` 拉一次基准 → 日历+收盘表透传 equity/snapshot；`persist_metrics_snapshot` 加 `benchmark=None` 参数（同 batch 复用）。

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/outcome/ tests/test_api_track_record.py -q`
Expected: PASS（含既有 TestEquityCurve/TestMetricsSnapshotCaliber 全部）

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "fix: [track-record] 净值首盯市日 0 收益 + 交易日历骨架补空仓日 + 年化 n=交易日数 (incident 032 根因 C)"
```

---

### Task 5: portfolio.as_of 数据日期诚实性

**Files:**
- Modify: `src/finance_agent/outcome/track_record/model.py`（新增 latest_equity_date）
- Modify: `src/finance_agent/api.py`（track_record_overview portfolio 块）
- Test: `tests/test_api_track_record.py`

**Interfaces:**
- Produces: `model.latest_equity_date(db_path=None) -> str | None`

- [ ] **Step 1: Write the failing test**

更新 `test_overview_portfolio_block_with_snapshot`：落一行 equity_curve（`upsert_equity_point("2026-09-04", 1.0, 1.0)`）后断言 `p["as_of"] == "2026-09-04"`；新增 `test_overview_portfolio_as_of_is_data_date`：metrics 行 metric_date=2026-09-28、curve 最新 2026-09-24 → `as_of == "2026-09-24"`（incident 032 伴生发现）。

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_api_track_record.py -q -k as_of`
Expected: FAIL（现返回 metric_date）

- [ ] **Step 3: Write minimal implementation**

api.py portfolio 组装改为：

```python
    metrics = await asyncio.to_thread(get_latest_metrics)
    latest_curve_date = await asyncio.to_thread(latest_equity_date)
    portfolio = {
        "available": metrics is not None and latest_curve_date is not None,
        "annual_return": metrics.get("annual_return") if metrics else None,
        "volatility": metrics.get("volatility") if metrics else None,
        "sharpe": metrics.get("sharpe") if metrics else None,
        "max_drawdown": metrics.get("max_drawdown") if metrics else None,
        "risk_score": metrics.get("risk_score") if metrics else None,
        "risk_label": metrics.get("risk_label") if metrics else None,
        # as_of = 指标所依据净值数据日期，SHALL NOT 用快照写入日期冒充（incident 032）
        "as_of": latest_curve_date,
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_api_track_record.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "fix: [track-record] portfolio.as_of 改为净值数据日期（诚实性）"
```

---

### Task 6: derived 表重建脚本 + 生产库副本核对

**Files:**
- Create: `scripts/track_record_rebuild.py`

**Interfaces:**
- Consumes: `marking.run_daily_marking`、`model.init_track_record_tables / get_latest_metrics / list_equity_curve`

- [ ] **Step 1: 实现**（运维脚本，deploy 类不经 pytest；ruff/mypy 干净）

```python
"""wipe + 重建 daily_marks / equity_curve / agent_metrics_daily（delta update-track-record-data-integrity）。

三张 derived 表可随时从 predictions + 行情重算；predictions / audit_log 不动。
用法（先拷贝生产库演练）：
  uv run python scripts/track_record_rebuild.py --db-path data/sessions.db --yes
"""

from __future__ import annotations

import argparse
import os
import shutil
import sqlite3
import sys

from finance_agent.outcome.track_record.marking import run_daily_marking
from finance_agent.outcome.track_record.model import (
    get_latest_metrics,
    init_track_record_tables,
    list_equity_curve,
)


def _resolve_db(args_db: str | None) -> str:
    if args_db:
        return args_db
    return os.getenv("SESSIONS_DB_PATH", "data/sessions.db")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db-path", default=None, help="目标 SQLite 路径（缺省 SESSIONS_DB_PATH 或 data/sessions.db）")
    ap.add_argument("--kline-days", type=int, default=280)
    ap.add_argument("--yes", action="store_true", help="确认清空三张 derived 表并重建")
    args = ap.parse_args()
    if not args.yes:
        sys.exit("拒绝执行：将清空 daily_marks/equity_curve/agent_metrics_daily，需 --yes 确认（建议先拷贝库演练）")
    db = _resolve_db(args.db_path)
    shutil.copy2(db, db + ".bak-rebuild")  # 重算前自动备份
    init_track_record_tables(db)
    conn = sqlite3.connect(db)
    try:
        for table in ("daily_marks", "equity_curve", "agent_metrics_daily"):
            conn.execute(f"DELETE FROM {table}")  # noqa: S608 — 表名来自固定白名单
        conn.commit()
    finally:
        conn.close()
    result = run_daily_marking(db_path=db, kline_days=args.kline_days)
    curve = list_equity_curve(db_path=db)
    latest = get_latest_metrics(db_path=db)
    print("rebuild:", result)
    print("equity points:", len(curve), "first:", curve[0] if curve else None, "last:", curve[-1] if curve else None)
    print("latest metrics:", latest)
    if latest:
        annual, vol, sharpe = latest.get("annual_return"), latest.get("volatility"), latest.get("sharpe")
        if (annual is not None and abs(annual) > 2.0) or (vol is not None and vol > 2.0) or (sharpe is not None and abs(sharpe) > 5.0):
            print("⚠️ 指标量级仍异常（年化/波动/夏普超常识范围）——按 incident 032 纪律先归因再处置")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: 生产库副本核对**

```bash
cp data/sessions.db /tmp/sessions-rebuild-check.db
uv run python scripts/track_record_rebuild.py --db-path /tmp/sessions-rebuild-check.db --yes
```

Expected: `marked/skipped` 合理（4 条茅台坏参考价 skipped）、equity points 覆盖 09-07 起、最新快照年化/波动回到常态量级、neutral 不再影响净值。核对记录写入本变更 tasks.md 勾选项。

- [ ] **Step 3: Commit**

```bash
git add -A && git commit -m "feat: [track-record] derived 三表 wipe+rebuild 运维脚本（含量级合理性核对输出）"
```

---

### Task 7: 全量验证与收口

- [ ] `uv run pytest`（全量；若 Langfuse 未在线按 memory 只跑非依赖子集并如实记录）
- [ ] `uv run ruff check` + `uv run mypy`
- [ ] openspec/changes/update-track-record-data-integrity/tasks.md 回填勾选
- [ ] 派发 Code-Quality-Review-Agent 审查全分支 diff
