"""add-track-record-stage-b：盯市/净值曲线/风险指标引擎/后台任务 测试。

数据层 + 指标引擎 + daily-marking/metrics-snapshot 的 TDD 用例。
行情用 FakeClient 注入（DataFrame 形态对齐 job.py：列 日期/收盘）。
"""

import math

import pandas as pd
import pytest

from finance_agent.outcome.track_record.judgment import DEFAULT_HORIZON_DAYS
from finance_agent.outcome.track_record.marking import (
    mark_open_predictions,
    run_daily_marking,
)
from finance_agent.outcome.track_record.metrics import (
    build_equity_curve_points,
    compute_metrics_from_marks,
    daily_portfolio_returns,
    risk_score_from,
)
from finance_agent.outcome.track_record.model import (
    get_latest_metrics,
    init_track_record_tables,
    insert_daily_mark,
    insert_prediction,
    list_daily_marks,
    upsert_equity_point,
    upsert_metrics_daily,
)

BASE = {
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
}


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "track.db"
    init_track_record_tables(path)
    return path


def _insert(db, **overrides):
    rec = dict(BASE)
    rec.update(overrides)
    return insert_prediction(rec, db_path=db)


class TestTables:
    def test_extra_tables_created(self, db):
        import sqlite3

        conn = sqlite3.connect(db)
        names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        conn.close()
        assert {"daily_marks", "equity_curve", "agent_metrics_daily"} <= names

    def test_daily_mark_upsert(self, db):
        insert_daily_mark("p1", "2026-06-02", 101.0, 0.01, -0.02, 3100.0, db_path=db)
        insert_daily_mark("p1", "2026-06-02", 102.0, 0.02, -0.01, 3110.0, db_path=db)
        marks = list_daily_marks(db_path=db)
        assert len(marks) == 1
        assert marks[0]["mark_price"] == 102.0
        assert marks[0]["cum_return"] == 0.02

    def test_equity_point_upsert(self, db):
        upsert_equity_point("2026-06-02", 1.01, 1.02, 0.01, 2, db_path=db)
        upsert_equity_point("2026-06-02", 1.02, 1.03, 0.02, 2, db_path=db)
        import sqlite3

        conn = sqlite3.connect(db)
        (nav,) = conn.execute(
            "SELECT agent_nav FROM equity_curve WHERE curve_date='2026-06-02'"
        ).fetchone()
        conn.close()
        assert nav == 1.02

    def test_metrics_latest(self, db):
        upsert_metrics_daily(
            "2026-06-02",
            {"sample_size": 2, "risk_score": 5, "segment_json": "{}"},
            db_path=db,
        )
        upsert_metrics_daily(
            "2026-06-03",
            {"sample_size": 3, "risk_score": 4, "segment_json": "{}"},
            db_path=db,
        )
        latest = get_latest_metrics(db_path=db)
        assert latest["metric_date"] == "2026-06-03"
        assert latest["risk_score"] == 4
        assert get_latest_metrics(db_path=db)  # 空表返回 None
        # 先清表再断言空
        import sqlite3

        conn = sqlite3.connect(db)
        conn.execute("DELETE FROM agent_metrics_daily")
        conn.commit()
        conn.close()
        assert get_latest_metrics(db_path=db) is None
        _ = latest  # noqa


def _marks_fixture():
    """两个 long 观点、三日盯市：累积收益逐步上行。"""
    return [
        {
            "prediction_id": "p1",
            "mark_date": "2026-06-02",
            "cum_return": 0.01,
            "benchmark_price": 3000.0,
        },
        {
            "prediction_id": "p1",
            "mark_date": "2026-06-03",
            "cum_return": 0.02,
            "benchmark_price": 3010.0,
        },
        {
            "prediction_id": "p1",
            "mark_date": "2026-06-04",
            "cum_return": 0.03,
            "benchmark_price": 3020.0,
        },
        {
            "prediction_id": "p2",
            "mark_date": "2026-06-02",
            "cum_return": 0.02,
            "benchmark_price": 3000.0,
        },
        {
            "prediction_id": "p2",
            "mark_date": "2026-06-04",
            "cum_return": 0.04,
            "benchmark_price": 3020.0,
        },
    ]


class TestPortfolioReturns:
    def test_equal_weight_mean_and_missing_excluded(self):
        rets = daily_portfolio_returns(_marks_fixture())
        # 06-02: (0.01+0.02)/2；06-03: 只有 p1 Δ0.01；06-04: p1 Δ0.01, p2 Δ0.02 → 0.015
        assert rets["2026-06-02"] == pytest.approx(0.015)
        assert rets["2026-06-03"] == pytest.approx(0.01)
        assert rets["2026-06-04"] == pytest.approx(0.015)

    def test_empty_day_is_zero(self):
        rets = daily_portfolio_returns([])
        assert rets == {}


class TestRiskScore:
    def test_observed_case_maps_to_max_risk(self):
        # 力鼎光电观测案例：回撤 41.2 / 波动 75.6 → 0.6*41.2+0.4*75.6 ≈ 55 → clip 10
        score = risk_score_from(0.412, 0.756)
        assert score == 10

    def test_benign_case_low_risk(self):
        # 低回撤低波动：0.6*1 + 0.4*5 = 2.6 → 3（低）
        score = risk_score_from(0.01, 0.05)
        assert 1 <= score <= 3

    def test_none_inputs(self):
        assert risk_score_from(None, 0.2) is None
        assert risk_score_from(0.1, None) is None


class TestComputeMetrics:
    def test_steady_growth(self):
        marks = _marks_fixture()
        pm = compute_metrics_from_marks(marks, risk_free_rate=0.02)
        assert pm.annual_return is not None and pm.annual_return > 0
        assert pm.volatility is not None and pm.volatility > 0
        assert pm.sharpe is not None and pm.sharpe > 0
        assert pm.max_drawdown is not None and pm.max_drawdown < 0.05
        assert 1 <= pm.risk_score <= 10
        assert pm.risk_label in ("低", "中", "高", "极高")
        assert len(pm.nav_points) == 3
        assert pm.nav_points[0]["date"] == "2026-06-02"
        # 双线以首个盯市日归一 1.0（跟踪起点对齐）
        assert pm.nav_points[0]["agent_nav"] == pytest.approx(1.0)
        assert pm.nav_points[0]["benchmark_nav"] == pytest.approx(1.0)
        assert pm.nav_points[-1]["agent_nav"] > 1.0

    def test_empty_marks(self):
        pm = compute_metrics_from_marks([], risk_free_rate=0.02)
        assert pm.annual_return is None
        assert pm.max_drawdown is None
        assert pm.risk_score is None
        assert pm.nav_points == []


class FakeClient:
    def __init__(self, klines, bench):
        self._k = klines
        self._b = bench
        self.kline_adjusts: list[str] = []

    def fetch_kline(self, code, days=280, *, adjust="qfq"):
        self.kline_adjusts.append(adjust)
        return self._k.get(code)

    def fetch_index_kline(self, code, days=280):
        return self._b


def _df(dates, closes):
    return pd.DataFrame({"日期": dates, "收盘": [float(c) for c in closes]})


class _CaliberAwareClient:
    """按 `adjust` 返回不同序列（qfq 与参考价同尺度；hfq 为后复权序列）并记录取值。"""

    def __init__(self, qfq, hfq, bench):
        self._qfq, self._hfq, self._b = qfq, hfq, bench
        self.adjusts: list[str] = []

    def fetch_kline(self, code, days=280, *, adjust="qfq"):
        self.adjusts.append(adjust)
        return self._hfq if adjust == "hfq" else self._qfq

    def fetch_index_kline(self, code, days=280):
        return self._b


@pytest.fixture
def fake_client():
    klines = {
        "600519": _df(
            ["2026-06-02", "2026-06-03", "2026-06-04"],
            [101.0, 102.0, 103.0],
        )
    }
    bench = _df(
        ["2026-06-01", "2026-06-02", "2026-06-03", "2026-06-04"],
        [3000.0, 3100.0, 3120.0, 3150.0],
    )
    return FakeClient(klines, bench)


class TestMarking:
    def test_marks_open_predictions(self, db, fake_client):
        pid = _insert(db, created_at="2026-06-01T10:00:00")
        result = mark_open_predictions(client=fake_client, db_path=db)
        assert result["marked"] == 3
        marks = list_daily_marks(prediction_id=pid, db_path=db)
        assert len(marks) == 3
        m = marks[0]
        assert m["mark_date"] == "2026-06-02"
        assert m["cum_return"] == pytest.approx(0.01)
        # 超额 = 股票收益 - 基准收益（基准基期 = entry 日 06-01 收盘 3000；落库 6 位小数）
        assert m["cum_excess"] == pytest.approx(0.01 - (3100.0 / 3000.0 - 1.0), abs=1e-6)

    def test_marking_uses_reference_price_caliber(self, db, fake_client):
        """盯市取数用默认 qfq（与存储参考价同尺度），不得传 hfq（口径混用禁令）。

        delta add-backtest-leakage-controls：盯市是近似展示口径，分子必须与
        `entry_price`（实时 quote 原值 / 管线 qfq 收盘）同尺度；判定才用 hfq。
        """
        _insert(db, created_at="2026-06-01T10:00:00")
        mark_open_predictions(client=fake_client, db_path=db)
        assert fake_client.kline_adjusts  # 确有取数
        assert set(fake_client.kline_adjusts) == {"qfq"}

    def test_dividend_sample_interval_return_differs_by_caliber(self, db):
        """tasks.md 1.2：分红除权样本的区间收益对照 → 口径选择是载荷的。

        合成一次除息（06-02 每股派 1.00，实际收盘 10.00 → 9.00）：前复权序列锚定最新价
        而下移历史（06-02=9.00, 06-03=9.50），后复权序列锚定最早价而上移（10.00/10.5556）。
        同一存储参考价 10.00 下，两口径的区间收益不同——盯市必须用与参考价同尺度的 qfq，
        否则 `cum_return = hfq(d)/raw(entry) - 1` 整体偏移并传导到净值曲线/指标快照。
        """
        _insert(db, entry_price=10.0, created_at="2026-06-01T10:00:00")
        qfq = _df(["2026-06-02", "2026-06-03"], [9.0, 9.5])  # 前复权（锚最新）
        hfq = _df(["2026-06-02", "2026-06-03"], [10.0, 10.5556])  # 后复权（锚最早）
        bench = _df(["2026-06-01", "2026-06-02", "2026-06-03"], [3000.0, 3000.0, 3000.0])
        client = _CaliberAwareClient(qfq=qfq, hfq=hfq, bench=bench)

        mark_open_predictions(client=client, db_path=db)

        assert set(client.adjusts) == {"qfq"}  # 盯市取的是参考价口径
        got = [m["cum_return"] for m in list_daily_marks(db_path=db)]
        assert got == pytest.approx([9.0 / 10.0 - 1.0, 9.5 / 10.0 - 1.0], abs=1e-6)
        # hfq 口径会给出不同数值（混用即偏移）——证明口径选择载荷
        hfq_returns = [10.0 / 10.0 - 1.0, 10.5556 / 10.0 - 1.0]
        assert got != pytest.approx(hfq_returns, abs=1e-6)

    def test_no_entry_price_skipped(self, db, fake_client):
        _insert(db, entry_price=None, created_at="2026-06-01T10:00:00")
        result = mark_open_predictions(client=fake_client, db_path=db)
        assert result["marked"] == 0
        assert result["skipped"] == 1

    def test_kline_error_isolated(self, db):
        class Boom:
            def fetch_kline(self, code, days=280, *, adjust="qfq"):
                raise RuntimeError("网络失败")

            def fetch_index_kline(self, code, days=280):
                raise RuntimeError("网络失败")

        _insert(db, created_at="2026-06-01T10:00:00")
        result = mark_open_predictions(client=Boom(), db_path=db)
        assert result["errors"] == 1

    def test_idempotent_rerun(self, db, fake_client):
        _insert(db, created_at="2026-06-01T10:00:00")
        mark_open_predictions(client=fake_client, db_path=db)
        mark_open_predictions(client=fake_client, db_path=db)
        assert len(list_daily_marks(db_path=db)) == 3


class TestEquityCurve:
    def test_points_built_and_persisted(self, db, fake_client):
        _insert(db, created_at="2026-06-01T10:00:00", horizon_days=DEFAULT_HORIZON_DAYS)
        result = run_daily_marking(client=fake_client, db_path=db)
        assert result["marked"] == 3
        assert result["equity_points"] == 3
        # 净值已入库
        import sqlite3

        conn = sqlite3.connect(db)
        rows = conn.execute(
            "SELECT curve_date, agent_nav FROM equity_curve ORDER BY curve_date"
        ).fetchall()
        conn.close()
        assert len(rows) == 3
        assert rows[0][1] == pytest.approx(1.0)  # 双线归一后首点 = 1.0
        assert rows[-1][1] == pytest.approx(1.01 * 1.01)  # 单观点日收益 0.01 累积
        # 指标快照已落库
        latest = get_latest_metrics(db_path=db)
        assert latest is not None
        assert latest["sample_size"] == 1
        assert latest["settled"] == 0
        assert latest["risk_score"] is not None

    def test_benchmark_nav_present(self, db, fake_client):
        _insert(db, created_at="2026-06-01T10:00:00")
        run_daily_marking(client=fake_client, db_path=db)
        points = build_equity_curve_points(db_path=db)
        # 双线首点归一 1.0；末点基准 = 3150/3100（自首个盯市日起）
        assert points[0]["benchmark_nav"] == pytest.approx(1.0)
        assert math.isclose(points[-1]["benchmark_nav"], 3150.0 / 3100.0, abs_tol=1e-6)


class TestMetricsSnapshotCaliber:
    """日批快照头条口径按 horizon_days=DEFAULT_HORIZON_DAYS 过滤（跨切点不混算）。"""

    def test_legacy_252_row_excluded_from_snapshot(self, db, fake_client):
        # 252 存量行（切点前口径）不得进快照读数
        _insert(db, created_at="2026-06-01T10:00:00", horizon_days=252)
        run_daily_marking(client=fake_client, db_path=db)
        latest = get_latest_metrics(db_path=db)
        assert latest is not None
        assert latest["sample_size"] == 0
        assert latest["settled"] == 0
        assert latest["win_rate"] is None

    def test_default_horizon_row_enters_snapshot(self, db, fake_client):
        # 同库混存：仅默认窗口（20）行进入快照读数
        _insert(db, created_at="2026-06-01T10:00:00", horizon_days=DEFAULT_HORIZON_DAYS)
        _insert(
            db,
            created_at="2026-06-01T10:00:00",
            symbol="000001.SZ",
            horizon_days=252,
        )
        run_daily_marking(client=fake_client, db_path=db)
        latest = get_latest_metrics(db_path=db)
        assert latest is not None
        assert latest["sample_size"] == 1


class TestSchedulerJobs:
    def test_start_registers_marking_and_metrics(self):
        import os
        from unittest.mock import MagicMock, patch

        with (
            patch.dict(os.environ, {"TESTING": "", "DECISION_SETTLE_ENABLED": "1"}),
            patch("finance_agent.outcome.scheduler.BackgroundScheduler") as cls,
        ):
            sched = MagicMock()
            cls.return_value = sched
            from finance_agent.outcome.scheduler import start_scheduler

            result = start_scheduler()
            assert result is sched
            jobs = {c.kwargs.get("id"): c for c in sched.add_job.call_args_list}
            assert "decision_settle_daily" in jobs
            assert "daily_marking" in jobs
            assert "metrics_snapshot" in jobs
