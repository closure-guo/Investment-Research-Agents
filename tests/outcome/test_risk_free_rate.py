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
