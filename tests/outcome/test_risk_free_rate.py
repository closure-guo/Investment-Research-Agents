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
