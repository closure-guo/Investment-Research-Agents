"""update-risk-free-rate-source：无风险利率序列（中债 1Y 国债）测试。

数据层（risk_free_rates 表）+ 取数封装（fetch_bond_yield_curve）+ 同步与
回退链（risk_free.py）+ 新夏普/α 口径 + 日批挂钩 + as-of 历史重算。
"""

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
            [
                ("2026-09-28", 0.012257, "chinabond-cgb-1y"),
                ("2026-09-29", 0.012197, "chinabond-cgb-1y"),
            ],
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


class TestRiskFreeSeries:
    def test_carry_forward_and_backward_fill(self, db):
        from finance_agent.outcome.track_record.model import upsert_risk_free_rates
        from finance_agent.outcome.track_record.risk_free import risk_free_series

        upsert_risk_free_rates(
            [
                ("2026-09-28", 0.012257, "chinabond-cgb-1y"),
                ("2026-09-30", 0.012197, "chinabond-cgb-1y"),
            ],
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
    def test_sync_converts_pct_to_decimal_and_stores(self, db):
        import pandas as pd

        from finance_agent.outcome.track_record import risk_free as rf_mod
        from finance_agent.outcome.track_record.model import (
            insert_daily_mark,
            list_risk_free_rates,
        )

        insert_daily_mark("p1", "2026-09-10", 101.0, 0.01, None, 3100.0, db_path=db)

        class FakeClient:
            def fetch_bond_yield_curve(self, start_date, end_date):
                return pd.DataFrame({"日期": ["2026-09-10", "2026-09-11"], "1年": [1.2257, 1.2301]})

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


class TestSharpeNewCaliber:
    """夏普 = mean(r_t − rf_t/252)/std_pop(r_t − rf_t)×√252（手算对照）。"""

    def _marks(self, rets):
        """造 marks 并同步给出引擎视角的日收益序列（首日 0 + cum 差分）。"""
        import itertools

        cum, marks, cums = 1.0, [], []
        for d, r in zip(itertools.count(1), rets):
            cum *= 1.0 + r
            cums.append(cum)
            marks.append(
                {
                    "prediction_id": "p1",
                    "mark_date": f"2026-09-{d:02d}",
                    "cum_return": cum - 1.0,
                    "benchmark_price": 3000.0,
                }
            )
        # 引擎口径：首盯市日贡献 0（incident 032），其后 = cum 差分（含复利交叉项）
        engine_rets = [0.0] + [cums[i] - cums[i - 1] for i in range(1, len(cums))]
        return marks, engine_rets

    def test_daily_rf_series_sharpe(self):
        import math

        from finance_agent.outcome.track_record.metrics import compute_metrics_from_marks

        rets = [0.01, -0.02, 0.015, 0.005, -0.008]
        rf = {f"2026-09-{i + 1:02d}": 0.012 for i in range(len(rets))}
        marks, engine_rets = self._marks(rets)
        pm = compute_metrics_from_marks(marks, rf_series=rf)
        ex = [r - 0.012 / 252 for r in engine_rets]
        mu = sum(ex) / len(ex)
        sd = (sum((e - mu) ** 2 for e in ex) / len(ex)) ** 0.5
        expected = mu / sd * math.sqrt(252)
        assert pm.sharpe == pytest.approx(expected, abs=1e-6)
        # 波动率口径不变（仍为引擎日收益序列的 std）
        vol = (
            sum((r - sum(engine_rets) / len(engine_rets)) ** 2 for r in engine_rets)
            / len(engine_rets)
        ) ** 0.5 * math.sqrt(252)
        assert pm.volatility == pytest.approx(vol, abs=1e-5)

    def test_constant_rf_still_new_formula(self):
        import math

        from finance_agent.outcome.track_record.metrics import compute_metrics_from_marks

        rets = [0.01, -0.02, 0.015, 0.005, -0.008]
        marks, engine_rets = self._marks(rets)
        pm = compute_metrics_from_marks(marks, risk_free_rate=0.012)
        ex = [r - 0.012 / 252 for r in engine_rets]
        mu = sum(ex) / len(ex)
        sd = (sum((e - mu) ** 2 for e in ex) / len(ex)) ** 0.5
        assert pm.sharpe == pytest.approx(mu / sd * math.sqrt(252), abs=1e-6)

    def test_alpha_uses_daily_rf(self):
        """α = mean(ra − rf_d − β(rb − rf_d))×252，逐日 rf_t。"""
        from finance_agent.outcome.track_record.metrics import _beta_alpha

        dates = [f"2026-09-{i:02d}" for i in range(3, 43)]  # 40 对 ≥ 20
        ra = dict.fromkeys(dates, 0.001)
        rb = {d: (0.001 if i % 2 else -0.001) for i, d in enumerate(dates)}
        rf_daily = dict.fromkeys(dates, 0.012 / 252)
        beta, alpha = _beta_alpha(ra, rb, rf_daily)
        assert beta == pytest.approx(0.0, abs=1e-6)  # ra 恒定 → 与 rb 无协方差 → β=0（var(rb)>0）
        expected_alpha = (0.001 - 0.012 / 252) * 252
        assert alpha == pytest.approx(expected_alpha, abs=1e-4)


class TestDailyBatchRfHook:
    def _run(self, db, rf_client):
        """一条 open 观点 + 合成个股/基准 K 跑日批（对齐既有 FakeClient 形态）。"""
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
            upsert_metrics_daily,
            upsert_risk_free_rates,
        )

        # 两条非 neutral 观点 × 22 个交易日（β/α 达 20 对门槛），基准逐日变
        for d in range(1, 23):
            day = f"2026-09-{(d + 7):02d}"  # 09-08..09-29
            for pid in ("pA", "pB"):
                insert_daily_mark(pid, day, 100.0 + d, 0.01 * d, None, 3000.0 + d, db_path=db)
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
        # 常数时代旧快照（模拟 rf=2% 时写入的历史行；annual 为任意已知值）
        old = compute_metrics_snapshot(db_path=db)
        old["annual_return"] = 0.123456  # 口径未变列的原值，重算后必须保持
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
