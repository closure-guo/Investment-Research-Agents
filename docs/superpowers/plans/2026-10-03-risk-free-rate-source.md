# Risk-Free Rate Real Data Source Implementation Plan

> **For agentic workers:** REQUIRED SUB-KILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** track-record 夏普/Jensen α 的无风险利率从常数 2% 切换为逐日中债国债收益率曲线 1 年期真实序列（落库缓存 + 回退链）。

**Architecture:** 复用 index_closes 范式——`risk_free_rates` 表存日频年化利率（小数），`AKShareClient.fetch_bond_yield_curve()` 封装 `ak.bond_china_yield`（中债官方源），`risk_free.py` 提供同步与解析（carry-forward → 常数回退），`metrics.py` 夏普改标准逐日超额定义、α 改逐日 rf_t 残差，日批在指标快照前同步 rf（失败隔离），回填脚本 + as-of 历史重算（只改 rf 相关列）。

**Tech Stack:** Python 3.12 / SQLite / AKShare（已依赖，1.18.94 已验证 `bond_china_yield` 可用）/ pytest。

**Worktree:** `.worktrees/rf-source`，分支 `feat/risk-free-rate-source`，**堆叠于 `fix/track-record-data-integrity`（#213，未合 main）**——所有路径以此 worktree 为根。

## Global Constraints

- 单位约定：`risk_free_rates.rate` 与 `RISK_FREE_RATE` 同口径 = **年化小数**（0.012317）；AKShare 返回百分数（1.2317），sync 层 ÷100 转换
- 回退链顺序：库内 carry-forward（含序列起点前的 backward-fill 到最早记录）→ 全库无数据回退 `TRACK_RISK_FREE_RATE` 常数（默认 0.02，env 语义不变）
- 失败隔离铁律：rf 同步失败不得使盯市/快照失败，不计入 marked/skipped/errors，仅 WARNING
- rf 源只取「中债国债收益率曲线」的「1年」列；日期规整 `str(d)[:10]`
- as-of 重算只 UPDATE `agent_metrics_daily` 的 `sharpe`/`beta`/`jensen_alpha` 三列，其余列（年化/波动/回撤/风险分——口径未变）保持原值
- 夏普定义：`mean(r_t − rf_t/252) / std_pop(r_t − rf_t) × √252`（总体标准差，与现行 vol 的 ÷n 约定一致）；rf_series 缺省时全窗口用常数 rf（定义仍为新口径——这是口径变更，不是可选项）
- α 定义：`mean(ra_t − rf_t/252 − β×(rb_t − rf_t/252))×252`，β 不变（OLS 斜率，不含 rf）
- commit 格式对齐仓库惯例：`feat(track-record): …` / `test: [track-record] …`；TDD 每任务一提交

---

### Task 1: model 层——risk_free_rates 表与读写函数

**Files:**
- Modify: `src/finance_agent/outcome/track_record/model.py`（TRACK_RECORD_EXTRA_DDL 末尾 + 读写函数区）
- Test: `tests/outcome/test_risk_free_rate.py`（新建）

**Interfaces:**
- Produces:
  - `upsert_risk_free_rates(rows: list[tuple[str, float, str]], db_path: str | Path | None = None) -> int`（rows=(rate_date, rate, source)，INSERT OR REPLACE，返回写入行数）
  - `list_risk_free_rates(db_path: str | Path | None = None) -> list[dict[str, Any]]`（按 rate_date 升序，元素 {rate_date, rate, source}）
  - `earliest_mark_date(db_path: str | Path | None = None) -> str | None`（daily_marks 最小 mark_date；空表 None）

- [ ] **Step 1: Write the failing test**

```python
# tests/outcome/test_risk_free_rate.py 头部 + Task1 用例
"""update-risk-free-rate-source：无风险利率序列（中债 1Y 国债）测试。"""

import sqlite3

import pytest

from finance_agent.outcome.track_record.model import (
    init_track_record_tables,
    insert_daily_mark,
)


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "track.db"
    init_track_record_tables(path)
    return path


class TestRiskFreeTable:
    def test_table_created(self, db):
        conn = sqlite3.connect(db)
        names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        conn.close()
        assert "risk_free_rates" in names

    def test_upsert_idempotent_and_list_asc(self, db):
        from finance_agent.outcome.track_record.model import (
            list_risk_free_rates,
            upsert_risk_free_rates,
        )

        n1 = upsert_risk_free_rates(
            [("2026-09-28", 0.012257, "chinabond-cgb-1y"), ("2026-09-29", 0.012197, "chinabond-cgb-1y")],
            db_path=db,
        )
        assert n1 == 2
        # 同日覆盖重写，幂等
        n2 = upsert_risk_free_rates([("2026-09-29", 0.012300, "chinabond-cgb-1y")], db_path=db)
        assert n2 == 1
        rows = list_risk_free_rates(db_path=db)
        assert [r["rate_date"] for r in rows] == ["2026-09-28", "2026-09-29"]
        assert rows[1]["rate"] == pytest.approx(0.0123)
        assert rows[0]["source"] == "chinabond-cgb-1y"

    def test_earliest_mark_date(self, db):
        from finance_agent.outcome.track_record.model import earliest_mark_date

        assert earliest_mark_date(db_path=db) is None
        insert_daily_mark("p1", "2026-09-10", 101.0, 0.01, None, 3100.0, db_path=db)
        insert_daily_mark("p1", "2026-09-11", 102.0, 0.02, None, 3110.0, db_path=db)
        assert earliest_mark_date(db_path=db) == "2026-09-10"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/outcome/test_risk_free_rate.py -v`
Expected: FAIL（ImportError: cannot import name 'upsert_risk_free_rates' / 表不存在）

- [ ] **Step 3: Write minimal implementation**

```python
# model.py — TRACK_RECORD_EXTRA_DDL 的 index_closes 段之后追加：
CREATE TABLE IF NOT EXISTS risk_free_rates (
  rate_date TEXT PRIMARY KEY,
  rate      REAL NOT NULL,
  source    TEXT NOT NULL
);

# model.py — index_closes 读写函数附近追加（对齐 house 风格）：
def upsert_risk_free_rates(
    rows: list[tuple[str, float, str]],
    db_path: str | Path | None = None,
) -> int:
    """幂等写入日频无风险利率（rate_date, rate 年化小数, source）；同日覆盖。"""
    conn = _connect(db_path)
    try:
        conn.executemany(
            "INSERT OR REPLACE INTO risk_free_rates (rate_date, rate, source) VALUES (?, ?, ?)",
            rows,
        )
        conn.commit()
        return len(rows)
    finally:
        conn.close()


def list_risk_free_rates(db_path: str | Path | None = None) -> list[dict[str, Any]]:
    """全部无风险利率记录，按 rate_date 升序。"""
    conn = _connect(db_path)
    try:
        cur = conn.execute("SELECT rate_date, rate, source FROM risk_free_rates ORDER BY rate_date")
        return [
            {"rate_date": str(r[0]), "rate": float(r[1]), "source": str(r[2])} for r in cur.fetchall()
        ]
    finally:
        conn.close()


def earliest_mark_date(db_path: str | Path | None = None) -> str | None:
    """daily_marks 最早 mark_date；空表返回 None（sync 回填区间起点用）。"""
    conn = _connect(db_path)
    try:
        row = conn.execute("SELECT MIN(mark_date) FROM daily_marks").fetchone()
        return str(row[0]) if row and row[0] else None
    finally:
        conn.close()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/outcome/test_risk_free_rate.py -v`
Expected: PASS（4 用例）

- [ ] **Step 5: Commit**

```bash
git add src/finance_agent/outcome/track_record/model.py tests/outcome/test_risk_free_rate.py
git commit -m "feat(track-record): risk_free_rates 表与读写函数——中债1Y利率序列存储"
```

---

### Task 2: AKShareClient.fetch_bond_yield_curve

**Files:**
- Modify: `src/finance_agent/data/akshare_client.py`（fetch_benchmark_kline 之后）
- Test: `tests/outcome/test_risk_free_rate.py`（追加类）

**Interfaces:**
- Produces: `AKShareClient.fetch_bond_yield_curve(self, start_date: str, end_date: str) -> pd.DataFrame`——列 `日期`（str[:10]）/`1年`（float，**百分数原样**，÷100 在 sync 层）；仅「中债国债收益率曲线」行；`_call_ak` 全失败返回 None

- [ ] **Step 1: Write the failing test**

```python
class TestFetchBondYieldCurve:
    def test_filters_gov_curve_and_normalizes(self, monkeypatch):
        import pandas as pd

        from finance_agent.data import akshare_client

        def fake_bond_china_yield(start_date="20200204", end_date="20210124", **kw):
            return pd.DataFrame(
                {
                    "曲线名称": [
                        "中债中短期票据收益率曲线(AAA)",
                        "中债国债收益率曲线",
                        "中债国债收益率曲线",
                    ],
                    "日期": ["2026-09-28", "2026-09-28", "2026-09-29"],
                    "1年": [1.5, 1.2257, 1.2197],
                }
            )

        monkeypatch.setattr(akshare_client.ak, "bond_china_yield", fake_bond_china_yield)
        client = akshare_client.AKShareClient()
        df = client.fetch_bond_yield_curve("2026-09-28", "2026-09-29")
        assert list(df["日期"]) == ["2026-09-28", "2026-09-29"]
        assert df["1年"].tolist() == [1.2257, 1.2197]

    def test_returns_none_when_source_fails(self, monkeypatch):
        from finance_agent.data import akshare_client

        def boom(*a, **kw):
            raise RuntimeError("chinabond down")

        monkeypatch.setattr(akshare_client.ak, "bond_china_yield", boom)
        client = akshare_client.AKShareClient()
        assert client.fetch_bond_yield_curve("2026-09-28", "2026-09-29") is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/outcome/test_risk_free_rate.py::TestFetchBondYieldCurve -v`
Expected: FAIL（AttributeError: no attribute 'fetch_bond_yield_curve'）

- [ ] **Step 3: Write minimal implementation**

```python
# akshare_client.py — AKShareClient 内，fetch_benchmark_kline 之后：
def fetch_bond_yield_curve(self, start_date: str, end_date: str) -> pd.DataFrame | None:
    """中债国债收益率曲线日频读数（update-risk-free-rate-source）。

    源：中债估值中心（yield.chinabond.com.cn，经 ak.bond_china_yield），
    非东财域。返回列 日期(str[:10])/1年(float, 百分数——年化小数转换由
    track_record.risk_free 层负责，与本文件其余 fetch_* 返回原单位的约定一致）。
    仅取「中债国债收益率曲线」；空结果/调用失败返回 None。
    """
    df = _call_ak(ak.bond_china_yield, start_date=start_date, end_date=end_date)
    if df is None or df.empty:
        return None
    df = df[df["曲线名称"] == "中债国债收益率曲线"]
    if df.empty:
        return None
    out = pd.DataFrame(
        {
            "日期": df["日期"].astype(str).str[:10],
            "1年": df["1年"].astype(float),
        }
    )
    return out.dropna(subset=["1年"]).sort_values("日期").reset_index(drop=True)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/outcome/test_risk_free_rate.py::TestFetchBondYieldCurve -v`
Expected: PASS（2 用例；boom 用例经 _call_ak 重试后返回 None）

- [ ] **Step 5: Commit**

```bash
git add src/finance_agent/data/akshare_client.py tests/outcome/test_risk_free_rate.py
git commit -m "feat(data): AKShareClient.fetch_bond_yield_curve——中债国债1Y收益率曲线封装"
```

---

### Task 3: risk_free.py——sync 与序列解析（回退链）

**Files:**
- Create: `src/finance_agent/outcome/track_record/risk_free.py`
- Test: `tests/outcome/test_risk_free_rate.py`（追加类）

**Interfaces:**
- Consumes: Task 1 的 `upsert_risk_free_rates`/`list_risk_free_rates`/`earliest_mark_date`；Task 2 的 `fetch_bond_yield_curve`
- Produces:
  - `RF_SOURCE = "chinabond-cgb-1y"`（模块常量）
  - `sync_risk_free_rates(*, client: Any = None, db_path: str | Path | None = None) -> dict[str, Any]`——区间 `[max(earliest_mark_date−7d, today−400d), today]`，取数→÷100→落库；返回 `{"stored": int, "start": str, "end": str}`；取数失败**抛异常**（由调用方隔离）
  - `risk_free_series(dates: list[str], db_path: str | Path | None = None) -> dict[str, float]`——目标日 → 年化小数；最近历史日优先（carry-forward），序列前向后向取最早记录，空表全常数 `RISK_FREE_RATE`

- [ ] **Step 1: Write the failing test**

```python
class TestRiskFreeSeries:
    def test_carry_forward_and_backward_fill(self, db):
        from finance_agent.outcome.track_record.risk_free import risk_free_series
        from finance_agent.outcome.track_record.model import upsert_risk_free_rates

        upsert_risk_free_rates(
            [("2026-09-28", 0.012257, "chinabond-cgb-1y"), ("2026-09-30", 0.012197, "chinabond-cgb-1y")],
            db_path=db,
        )
        got = risk_free_series(
            ["2026-09-25", "2026-09-28", "2026-09-29", "2026-09-30", "2026-10-08"], db_path=db
        )
        assert got["2026-09-25"] == pytest.approx(0.012257)  # 序列前：backward-fill 最早记录
        assert got["2026-09-28"] == pytest.approx(0.012257)
        assert got["2026-09-29"] == pytest.approx(0.012257)  # 缺日 carry-forward
        assert got["2026-09-30"] == pytest.approx(0.012197)
        assert got["2026-10-08"] == pytest.approx(0.012197)  # 越过最新记录仍沿用

    def test_empty_table_falls_back_to_constant(self, db):
        from finance_agent.outcome.track_record.metrics import RISK_FREE_RATE
        from finance_agent.outcome.track_record.risk_free import risk_free_series

        got = risk_free_series(["2026-09-28"], db_path=db)
        assert got["2026-09-28"] == pytest.approx(RISK_FREE_RATE)


class TestSyncRiskFreeRates:
    def test_sync_converts_pct_to_decimal_and_stores(self, db, monkeypatch):
        import pandas as pd

        from finance_agent.outcome.track_record import risk_free as rf_mod
        from finance_agent.outcome.track_record.model import (
            earliest_mark_date,
            insert_daily_mark,
            list_risk_free_rates,
        )

        insert_daily_mark("p1", "2026-09-10", 101.0, 0.01, None, 3100.0, db_path=db)

        class FakeClient:
            def fetch_bond_yield_curve(self, start_date, end_date):
                return pd.DataFrame(
                    {"日期": ["2026-09-10", "2026-09-11"], "1年": [1.2257, 1.2301]}
                )

        result = rf_mod.sync_risk_free_rates(client=FakeClient(), db_path=db)
        assert result["stored"] == 2
        rows = list_risk_free_rates(db_path=db)
        assert rows[0]["rate"] == pytest.approx(0.012257)
        assert rows[0]["source"] == "chinabond-cgb-1y"
        # 区间起点 = earliest_mark_date − 7 天缓冲
        assert result["start"] == "2026-09-03"

    def test_sync_raises_on_fetch_failure(self, db):
        from finance_agent.outcome.track_record import risk_free as rf_mod

        class BoomClient:
            def fetch_bond_yield_curve(self, start_date, end_date):
                raise RuntimeError("chinabond down")

        with pytest.raises(RuntimeError):
            rf_mod.sync_risk_free_rates(client=BoomClient(), db_path=db)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/outcome/test_risk_free_rate.py::TestRiskFreeSeries tests/outcome/test_risk_free_rate.py::TestSyncRiskFreeRates -v`
Expected: FAIL（ModuleNotFoundError: risk_free）

- [ ] **Step 3: Write minimal implementation**

```python
# src/finance_agent/outcome/track_record/risk_free.py
"""update-risk-free-rate-source：无风险利率序列（中债国债收益率曲线 1 年期）。

sync_risk_free_rates：拉取→年化小数→幂等落库（日批与回填脚本共用）；
risk_free_series：目标日期 → 年化 rf，回退链 库内 carry-forward（序列起点前
backward-fill 到最早记录）→ 空表回退常数 RISK_FREE_RATE（TRACK_RISK_FREE_RATE）。
口径细则见 openspec/changes/update-risk-free-rate-source。
"""

from __future__ import annotations

import logging
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from finance_agent.outcome.track_record.model import (
    earliest_mark_date,
    list_risk_free_rates,
    upsert_risk_free_rates,
)

logger = logging.getLogger(__name__)

RF_SOURCE = "chinabond-cgb-1y"
_BACKFILL_BUFFER_DAYS = 7
_DEFAULT_LOOKBACK_DAYS = 400


def _today() -> str:
    from datetime import datetime
    from zoneinfo import ZoneInfo

    return datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d")


def sync_risk_free_rates(
    *,
    client: Any = None,
    db_path: str | Path | None = None,
) -> dict[str, Any]:
    """拉取中债 1Y 国债收益率并落库；取数失败抛异常（调用方负责隔离）。"""
    if client is None:
        from finance_agent.data.akshare_client import AKShareClient

        client = AKShareClient()
    start = None
    if (first := earliest_mark_date(db_path=db_path)) is not None:
        start = (date.fromisoformat(first) - timedelta(days=_BACKFILL_BUFFER_DAYS)).isoformat()
    if start is None:
        start = (date.fromisoformat(_today()) - timedelta(days=_DEFAULT_LOOKBACK_DAYS)).isoformat()
    end = _today()
    df = client.fetch_bond_yield_curve(start, end)
    if df is None or df.empty:
        raise RuntimeError(f"国债收益率曲线取数为空 [{start}..{end}]")
    rows = [
        (str(d)[:10], float(r) / 100.0, RF_SOURCE)
        for d, r in zip(df["日期"], df["1年"], strict=False)
    ]
    stored = upsert_risk_free_rates(rows, db_path=db_path)
    return {"stored": stored, "start": start, "end": end}


def risk_free_series(
    dates: list[str],
    db_path: str | Path | None = None,
) -> dict[str, float]:
    """目标日期序列 → 年化 rf（小数）。as-of join：最近历史日优先，序列前
    backward-fill 最早记录，空表全常数 RISK_FREE_RATE。"""
    from finance_agent.outcome.track_record.metrics import RISK_FREE_RATE

    records = list_risk_free_rates(db_path=db_path)
    out: dict[str, float] = {}
    if not records:
        return {d: RISK_FREE_RATE for d in dates}
    rec_dates = [r["rate_date"] for r in records]
    rec_rates = [r["rate"] for r in records]
    import bisect

    for d in dates:
        i = bisect.bisect_right(rec_dates, d) - 1
        if i >= 0:
            out[d] = rec_rates[i]  # 最近历史日（含当日命中）
        else:
            out[d] = rec_rates[0]  # 序列前 backward-fill 最早记录
    return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/outcome/test_risk_free_rate.py -v`
Expected: PASS（全部 9 用例）

- [ ] **Step 5: Commit**

```bash
git add src/finance_agent/outcome/track_record/risk_free.py tests/outcome/test_risk_free_rate.py
git commit -m "feat(track-record): risk_free 同步与序列解析——中债1Y回退链"
```

---

### Task 4: metrics.py 新口径（夏普逐日超额 + α 逐日 rf_t）

**Files:**
- Modify: `src/finance_agent/outcome/track_record/metrics.py`（`_beta_alpha`、`compute_metrics_from_marks`、`compute_metrics_snapshot`）
- Test: `tests/outcome/test_risk_free_rate.py`（追加类）；`tests/outcome/test_track_record_metrics.py`（旧口径断言适配，见 Step 3b）

**Interfaces:**
- Consumes: Task 3 的 `risk_free_series`
- Produces:
  - `compute_metrics_from_marks(marks, risk_free_rate=RISK_FREE_RATE, rf_series: dict[str, float] | None = None, calendar_dates=None, benchmark_by_date=None, exclude_prediction_ids=None)`——`rf_series` 为 date→年化小数；缺省 None → 全窗口常数 `risk_free_rate`（公式仍为逐日超额新口径）
  - `_beta_alpha(rets, bench_ret, rf_daily: dict[str, float])`——rf_daily 为 date→**日频** rf（已 ÷252）
  - `compute_metrics_snapshot` 内部从 DB 装载 rf（对 marks 日期调 `risk_free_series`），签名不变

- [ ] **Step 1: Write the failing test**

```python
class TestSharpeNewCaliber:
    """夏普 = mean(r_t − rf_t/252)/std_pop(r_t − rf_t)×√252（手算对照）。"""

    def _marks(self, rets):
        import itertools

        cum, marks = 1.0, []
        for d, r in zip(itertools.count(1), rets):
            cum *= 1.0 + r
            marks.append(
                {
                    "prediction_id": "p1",
                    "mark_date": f"2026-09-{d:02d}",
                    "cum_return": cum - 1.0,
                    "benchmark_price": 3000.0,
                }
            )
        return marks

    def test_daily_rf_series_sharpe(self, db):
        import math

        from finance_agent.outcome.track_record.metrics import compute_metrics_from_marks

        rets = [0.01, -0.02, 0.015, 0.005, -0.008]
        rf = {f"2026-09-{i + 1:02d}": 0.012 for i in range(len(rets))}
        pm = compute_metrics_from_marks(self._marks(rets), rf_series=rf)
        ex = [r - 0.012 / 252 for r in rets]
        mu = sum(ex) / len(ex)
        sd = (sum((e - mu) ** 2 for e in ex) / len(ex)) ** 0.5
        expected = mu / sd * math.sqrt(252)
        assert pm.sharpe == pytest.approx(expected, abs=1e-6)
        # 波动率口径不变（仍为原始收益的 std）
        vol = (sum((r - sum(rets) / len(rets)) ** 2 for r in rets) / len(rets)) ** 0.5 * math.sqrt(252)
        assert pm.volatility == pytest.approx(vol, abs=1e-5)

    def test_constant_rf_still_new_formula(self, db):
        import math

        from finance_agent.outcome.track_record.metrics import compute_metrics_from_marks

        rets = [0.01, -0.02, 0.015, 0.005, -0.008]
        pm = compute_metrics_from_marks(self._marks(rets), risk_free_rate=0.012)
        ex = [r - 0.012 / 252 for r in rets]
        mu = sum(ex) / len(ex)
        sd = (sum((e - mu) ** 2 for e in ex) / len(ex)) ** 0.5
        assert pm.sharpe == pytest.approx(mu / sd * math.sqrt(252), abs=1e-6)

    def test_alpha_uses_daily_rf(self, db):
        """α = mean(ra − rf_d − β(rb − rf_d))×252，逐日 rf_t。"""
        import math

        from finance_agent.outcome.track_record.metrics import _beta_alpha

        dates = [f"2026-09-{i:02d}" for i in range(3, 43)]  # 40 对 ≥ 20
        ra = {d: 0.001 for d in dates}
        rb = {d: (0.001 if i % 2 else -0.001) for i, d in enumerate(dates)}
        rf_daily = {d: 0.012 / 252 for d in dates}
        beta, alpha = _beta_alpha(ra, rb, rf_daily)
        assert beta == pytest.approx(0.0, abs=1e-6)  # ra 恒定 → 与 rb 无协方差 → β=0（var(rb)>0）
        expected_alpha = (0.001 - 0.012 / 252) * 252
        assert alpha == pytest.approx(expected_alpha, abs=1e-4)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/outcome/test_risk_free_rate.py::TestSharpeNewCaliber -v`
Expected: FAIL（TypeError: unexpected keyword 'rf_series'；_beta_alpha 第 3 参为 float）

- [ ] **Step 3: Write minimal implementation**

`_beta_alpha` 改造（rf_daily 为 date→日频 rf）：

```python
def _beta_alpha(
    rets: dict[str, float],
    bench_ret: dict[str, float],
    rf_daily: dict[str, float],
) -> tuple[float | None, float | None]:
    """组合 β（OLS 斜率）与年化 Jensen α；重叠对 < 20 → (None, None)。

    首个共同日期剔除：组合首日收益是相对入场日的口径混合点，基准首日恒 0，
    入对会污染斜率。α = mean(ra_t − rf_t − β×(rb_t − rf_t))×252，rf_t 逐日
    （update-risk-free-rate-source，与夏普同源）。
    """
    dates = sorted(set(rets) & set(bench_ret))[1:]  # 剔除首个共同日
    if len(dates) < _BETA_MIN_PAIRS:
        return None, None
    ra = [rets[d] for d in dates]
    rb = [bench_ret[d] for d in dates]
    n = len(dates)
    mu_a, mu_b = sum(ra) / n, sum(rb) / n
    cov = sum((rb[i] - mu_b) * (ra[i] - mu_a) for i in range(n))
    var = sum((rb[i] - mu_b) ** 2 for i in range(n))
    if var <= 0:
        return None, None
    beta = cov / var
    resid = [
        rets[d] - rf_daily.get(d, 0.0) - beta * (bench_ret[d] - rf_daily.get(d, 0.0))
        for d in dates
    ]
    alpha = sum(resid) / len(resid) * 252
    return round(beta, 6), round(alpha, 6)
```

`compute_metrics_from_marks` 改造（签名加 `rf_series`；夏普与 rf_daily 构造）：

```python
def compute_metrics_from_marks(
    marks: list[dict[str, Any]],
    risk_free_rate: float = RISK_FREE_RATE,
    rf_series: dict[str, float] | None = None,
    calendar_dates: list[str] | None = None,
    benchmark_by_date: dict[str, float] | None = None,
    exclude_prediction_ids: set[str] | None = None,
) -> PortfolioMetrics:
    # …（docstring 补一句 rf 口径）…
    # dates 解析出后：
    rf_annual = {
        d: (rf_series.get(d) if rf_series is not None else None) for d in dates
    }
    # rf_series 内未覆盖/缺省 → 常数兜底（库内回退链在 risk_free_series 已处理，
    # 这里防调用方传入稀疏序列）
    rf_annual = {d: (v if v is not None else risk_free_rate) for d, v in rf_annual.items()}
    rf_daily = {d: v / _TRADING_DAYS for d, v in rf_annual.items()}

    beta, jensen_alpha = _beta_alpha(rets, bench_ret, rf_daily)

    # 夏普：mean/std（总体）×√252 的逐日超额（update-risk-free-rate-source）
    sharpe = None
    if n > 1:
        excess = [rets[d] - rf_daily[d] for d in dates]
        mu_e = sum(excess) / n
        sd_e = (sum((e - mu_e) ** 2 for e in excess) / n) ** 0.5
        if sd_e > 0:
            sharpe = mu_e / sd_e * math.sqrt(_TRADING_DAYS)
    # （年化 annual、volatility、回撤、风险分、nav_points 逻辑不变）
```

`compute_metrics_snapshot` 内部装载 rf（其余不动）：

```python
    from finance_agent.outcome.track_record.risk_free import risk_free_series

    marks = list_daily_marks(db_path=db_path)
    # …excl 计算…
    rf_series = risk_free_series(
        sorted({str(m["mark_date"]) for m in marks}), db_path=db_path
    )
    pm = compute_metrics_from_marks(
        marks,
        rf_series=rf_series,
        calendar_dates=calendar_dates,
        benchmark_by_date=benchmark_by_date,
        exclude_prediction_ids=excl,
    )
```

- [ ] **Step 3b: 适配既有旧口径断言**

`tests/outcome/test_track_record_metrics.py` 现有断言为 `sharpe > 0` / `not None`（宽松），预期直接通过；若有个别用例硬编码旧公式数值，改为按新公式手算期望值（口径变更是 delta 授权的合法更新，commit message 注明）。

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/outcome/ -q`
Expected: PASS（含既有 454 用例 + 新增）

- [ ] **Step 5: Commit**

```bash
git add src/finance_agent/outcome/track_record/metrics.py tests/outcome/test_risk_free_rate.py tests/outcome/test_track_record_metrics.py
git commit -m "feat(track-record): 夏普逐日超额口径+α逐日rf——中债1Y序列接入指标引擎"
```

---

### Task 5: 日批挂钩（快照前同步，失败隔离）

**Files:**
- Modify: `src/finance_agent/outcome/track_record/marking.py`（`run_daily_marking`）
- Test: `tests/outcome/test_risk_free_rate.py`（追加类）

**Interfaces:**
- Consumes: Task 3 的 `sync_risk_free_rates`
- Produces: `run_daily_marking` 返回 dict 新增键 `rf_stored: int` / `rf_failed: bool`（其余键不变）

- [ ] **Step 1: Write the failing test**

```python
class TestDailyBatchRfHook:
    def _run(self, db, rf_client):
        """复用现有日批测试造数路径：一条 open 观点 + 合成个股/基准 K。"""
        import pandas as pd

        from finance_agent.outcome.track_record.marking import run_daily_marking
        from finance_agent.outcome.track_record.model import insert_prediction

        insert_prediction(
            {
                "source_type": "live",
                "symbol": "600519.SH",
                "symbol_name": "茅台",
                "direction": "long",
                "entry_price": 100.0,
                "target_price": 120.0,
                "horizon_days": 252,
                "confidence": 0.8,
                "benchmark": "000300.SH",
                "rationale_snapshot": {"markdown": "x"},
                "created_at": "2026-09-01",
            },
            db_path=db,
        )

        dates = pd.bdate_range("2026-09-02", periods=30).strftime("%Y-%m-%d")
        kline = pd.DataFrame({"日期": dates, "收盘": [100.0 + i for i in range(len(dates))]})
        bench = pd.DataFrame({"日期": dates, "收盘": [3000.0 + i for i in range(len(dates))]})

        class FakeClient:
            def fetch_kline(self, code, days=280, **kw):
                return kline

            def fetch_index_kline(self, code, days=280, **kw):
                return bench

            fetch_bond_yield_curve = rf_client.fetch_bond_yield_curve

        return run_daily_marking(client=FakeClient(), db_path=db)

    def test_rf_synced_before_snapshot(self, db):
        import pandas as pd

        from finance_agent.outcome.track_record.model import list_risk_free_rates

        class RfOk:
            def fetch_bond_yield_curve(self, start_date, end_date):
                return pd.DataFrame({"日期": ["2026-09-02", "2026-09-03"], "1年": [1.22, 1.23]})

        result = self._run(db, RfOk())
        assert result["rf_stored"] == 2
        assert result["rf_failed"] is False
        assert len(list_risk_free_rates(db_path=db)) == 2

    def test_rf_failure_isolated(self, db):
        class RfBoom:
            def fetch_bond_yield_curve(self, start_date, end_date):
                raise RuntimeError("chinabond down")

        result = self._run(db, RfBoom())
        assert result["rf_failed"] is True
        assert result["marked"] > 0  # 盯市不受影响
        assert result["metrics_date"]  # 快照照常落库
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/outcome/test_risk_free_rate.py::TestDailyBatchRfHook -v`
Expected: FAIL（KeyError: 'rf_stored'）

- [ ] **Step 3: Write minimal implementation**

`run_daily_marking` 中，equity 落库之后、`persist_metrics_snapshot` 之前插入（对齐 index sync 的隔离风格）：

```python
    # update-risk-free-rate-source：指标快照前同步无风险利率（快照当轮即用新值）。
    # 失败隔离：同步异常不阻断盯市/快照，快照侧走回退链（carry-forward/常数）。
    rf_summary: dict[str, Any] = {"rf_stored": 0, "rf_failed": True}
    try:
        from finance_agent.outcome.track_record.risk_free import sync_risk_free_rates

        rf_summary = {
            "rf_stored": sync_risk_free_rates(client=client, db_path=db_path)["stored"],
            "rf_failed": False,
        }
    except Exception as e:  # noqa: BLE001 - rf 缺失不得放大成盯市失败
        logger.warning("无风险利率同步失败(回退链兜底): %s", e)

    metrics_date = persist_metrics_snapshot(...)
    # 返回 dict 追加 **rf_summary
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/outcome/test_risk_free_rate.py tests/outcome/test_track_record_metrics.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/finance_agent/outcome/track_record/marking.py tests/outcome/test_risk_free_rate.py
git commit -m "feat(track-record): 日批指标快照前同步无风险利率——失败隔离+回退链"
```

---

### Task 6: as-of 历史重算 + 回填脚本

**Files:**
- Modify: `src/finance_agent/outcome/track_record/metrics.py`（新增 `recompute_rf_metrics_history`）
- Create: `scripts/backfill_risk_free_rates.py`
- Test: `tests/outcome/test_risk_free_rate.py`（追加类）

**Interfaces:**
- Consumes: Task 1/3/4 全部接口
- Produces:
  - `recompute_rf_metrics_history(db_path=None) -> list[dict[str, Any]]`——对 `agent_metrics_daily` 每行：marks ≤ metric_date（neutral 排除）→ marks-only 口径 rets/bench_ret → rf as-of → 重算 `sharpe`/`beta`/`jensen_alpha` 三列并 UPDATE（其余列不动）；返回逐行 {metric_date, old/new × sharpe/beta/jensen_alpha}
  - 脚本 CLI：`--db-path`（缺省 SESSIONS_DB_PATH/data/sessions.db）、`--days`（sync 下限兜底，默认 400）、`--recompute-metrics`（默认开，`--no-recompute-metrics` 关）

- [ ] **Step 1: Write the failing test**

```python
class TestRecomputeHistory:
    def test_asof_updates_only_rf_columns(self, db):
        import sqlite3

        from finance_agent.outcome.track_record.metrics import (
            compute_metrics_snapshot,
            recompute_rf_metrics_history,
        )
        from finance_agent.outcome.track_record.model import (
            insert_daily_mark,
            insert_prediction,
            upsert_risk_free_rates,
        )

        # 两条非 neutral 观点 × 22 个交易日（β/α 达 20 对门槛），基准逐日变
        for d in range(1, 23):
            day = f"2026-09-{(d + 7):02d}"  # 09-08..09-29
            for pid in ("pA", "pB"):
                insert_daily_mark(pid, day, 100.0 + d, 0.01 * d, None, 3000.0 + d, db_path=db)
        insert_prediction(
            {
                "source_type": "live", "symbol": "600519.SH", "symbol_name": "茅台",
                "direction": "long", "entry_price": 100.0, "target_price": 120.0,
                "horizon_days": 252, "confidence": 0.8, "benchmark": "000300.SH",
                "rationale_snapshot": {"markdown": "x"}, "created_at": "2026-09-01",
            },
            db_path=db,
        )
        # 常数时代旧快照（模拟 rf=2% 时写入的历史行；annual/vol 为任意已知值）
        old = compute_metrics_snapshot(db_path=db)
        old["annual_return"] = 0.123456  # 口径未变列的原值，重算后必须保持
        from finance_agent.outcome.track_record.model import upsert_metrics_daily

        upsert_metrics_daily("2026-09-29", old, db_path=db)

        upsert_risk_free_rates(
            [(f"2026-09-{(d + 7):02d}", 0.0122, "chinabond-cgb-1y") for d in range(1, 23)],
            db_path=db,
        )
        diffs = recompute_rf_metrics_history(db_path=db)
        assert len(diffs) == 1
        row = diffs[0]
        assert row["metric_date"] == "2026-09-29"
        assert row["old_sharpe"] != row["new_sharpe"]  # rf 2%→1.22% 必然改变夏普
        conn = sqlite3.connect(db)
        stored = conn.execute(
            "SELECT annual_return, sharpe FROM agent_metrics_daily WHERE metric_date='2026-09-29'"
        ).fetchone()
        conn.close()
        assert stored[0] == pytest.approx(0.123456)  # 未涉及列保持原值
        assert stored[1] == pytest.approx(row["new_sharpe"], abs=1e-6)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/outcome/test_risk_free_rate.py::TestRecomputeHistory -v`
Expected: FAIL（ImportError: recompute_rf_metrics_history）

- [ ] **Step 3: Write minimal implementation**

`metrics.py` 追加：

```python
def recompute_rf_metrics_history(db_path: Any = None) -> list[dict[str, Any]]:
    """rf 口径切换后重算 agent_metrics_daily 历史行（update-risk-free-rate-source）。

    as-of 纪律：重算某 metric_date 行只用 mark_date ≤ 该日数据；基准收益取
    marks 内 benchmark_price（marks-only 口径，不拉今日行情——as-of 保真）。
    只 UPDATE sharpe/beta/jensen_alpha 三列（rf 相关）；年化/波动/回撤/风险分
    口径未变，保留原值。返回逐行前后对照。
    """
    from finance_agent.outcome.track_record.model import (
        list_daily_marks,
        list_metric_dates,
        prediction_ids_by_direction,
        update_metrics_daily_columns,
    )
    from finance_agent.outcome.track_record.risk_free import risk_free_series

    marks_all = list_daily_marks(db_path=db_path)
    excl = prediction_ids_by_direction("neutral", db_path=db_path)
    out: list[dict[str, Any]] = []
    for md in list_metric_dates(db_path=db_path):
        marks = [m for m in marks_all if str(m["mark_date"]) <= md]
        rets = daily_portfolio_returns(marks, exclude_prediction_ids=excl)
        bench_ret = _benchmark_returns(marks)
        dates = sorted(rets)
        rf_daily = {
            d: v / _TRADING_DAYS
            for d, v in risk_free_series(dates, db_path=db_path).items()
        }
        n = len(dates)
        sharpe = None
        if n > 1:
            excess = [rets[d] - rf_daily[d] for d in dates]
            mu_e = sum(excess) / n
            sd_e = (sum((e - mu_e) ** 2 for e in excess) / n) ** 0.5
            if sd_e > 0:
                sharpe = round(mu_e / sd_e * math.sqrt(_TRADING_DAYS), 6)
        beta, alpha = _beta_alpha(rets, bench_ret, rf_daily)
        out.append(
            update_metrics_daily_columns(
                md, {"sharpe": sharpe, "beta": beta, "jensen_alpha": alpha}, db_path=db_path
            )
        )
    return out
```

`model.py` 追加两个辅助函数：

```python
def list_metric_dates(db_path: str | Path | None = None) -> list[str]:
    """agent_metrics_daily 全部 metric_date 升序。"""
    conn = _connect(db_path)
    try:
        return [str(r[0]) for r in conn.execute("SELECT metric_date FROM agent_metrics_daily ORDER BY metric_date")]
    finally:
        conn.close()


def update_metrics_daily_columns(
    metric_date: str,
    columns: dict[str, float | None],
    db_path: str | Path | None = None,
) -> dict[str, Any]:
    """部分列 UPDATE agent_metrics_daily 指定行；返回 {metric_date, old_*, new_*} 前后对照。"""
    if not columns:
        return {"metric_date": metric_date}
    conn = _connect(db_path)
    try:
        col_names = ", ".join(columns)
        old = conn.execute(
            f"SELECT {col_names} FROM agent_metrics_daily WHERE metric_date = ?", (metric_date,)  # noqa: S608
        ).fetchone()
        if old is None:
            return {"metric_date": metric_date}
        sets = ", ".join(f"{c} = ?" for c in columns)
        conn.execute(
            f"UPDATE agent_metrics_daily SET {sets} WHERE metric_date = ?",  # noqa: S608
            (*columns.values(), metric_date),
        )
        conn.commit()
        row: dict[str, Any] = {"metric_date": metric_date}
        for c, o in zip(columns, old, strict=True):
            row[f"old_{c}"] = o
            row[f"new_{c}"] = columns[c]
        return row
    finally:
        conn.close()
```

`scripts/backfill_risk_free_rates.py`：

```python
"""回填无风险利率历史 + as-of 重算指标快照（update-risk-free-rate-source）。

与日批 sync_risk_free_rates 共用同一幂等写入，重跑安全。
用法:
    uv run python scripts/backfill_risk_free_rates.py
    uv run python scripts/backfill_risk_free_rates.py --db-path data/sessions.db --no-recompute-metrics
"""

import argparse
import os

from finance_agent.outcome.track_record.model import init_track_record_tables
from finance_agent.outcome.track_record.risk_free import sync_risk_free_rates


def main() -> None:
    parser = argparse.ArgumentParser(description="回填中债1Y无风险利率历史")
    parser.add_argument("--db-path", default=os.getenv("SESSIONS_DB_PATH", "data/sessions.db"))
    parser.add_argument("--no-recompute-metrics", action="store_true", help="只回填利率，不重算快照")
    args = parser.parse_args()
    init_track_record_tables(args.db_path)
    result = sync_risk_free_rates(db_path=args.db_path)
    print(f"rf stored={result['stored']} range=[{result['start']}..{result['end']}]")
    if not args.no_recompute_metrics:
        from finance_agent.outcome.track_record.metrics import recompute_rf_metrics_history

        for row in recompute_rf_metrics_history(db_path=args.db_path):
            print(row)


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/outcome/test_risk_free_rate.py -v`；脚本 smoke：`uv run python scripts/backfill_risk_free_rates.py --db-path <tmp库> --no-recompute-metrics`
Expected: PASS / 脚本打印 stored（联网取真数据）

- [ ] **Step 5: Commit**

```bash
git add src/finance_agent/outcome/track_record/metrics.py src/finance_agent/outcome/track_record/model.py scripts/backfill_risk_free_rates.py tests/outcome/test_risk_free_rate.py
git commit -m "feat(track-record): as-of历史重算+回填脚本——rf口径切换收口"
```

---

### Task 7: 验证收口（verification-before-completion）

- [ ] `uv run pytest tests/outcome/ -q` 全绿
- [ ] `uv run ruff check src/finance_agent/outcome/track_record/ src/finance_agent/data/akshare_client.py scripts/backfill_risk_free_rates.py tests/outcome/test_risk_free_rate.py`
- [ ] `uv run mypy`（全量，对齐仓库惯例）
- [ ] 生产库演练：拷贝 `data/sessions.db` → 跑回填脚本 → 人工核读新旧 sharpe/α 对照（差异归因：rf 2%→~1.22% + 定义切换），报告落 `tests/validation/2026-10-03-update-risk-free-rate-source-validation.md`
- [ ] `openspec/changes/update-risk-free-rate-source/tasks.md` 逐项回填勾选
- [ ] PR：`gh pr create --base fix/track-record-data-integrity`（堆叠；#213 合并后 retarget main 并 rebase，注意堆叠 PR 的 CI 触发坑）
