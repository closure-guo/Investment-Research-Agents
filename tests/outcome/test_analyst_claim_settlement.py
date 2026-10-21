"""逐主张结算测试（add-analyst-ex-post-verification）。

覆盖：T+1 开盘入场、per-claim horizon、复权入场价、停牌/缺行情/缺基准的
unresolved 语义、未到期保持 pending、幂等。
"""

from __future__ import annotations

import sqlite3
from datetime import date, timedelta

from finance_agent.data import analyst_claims_store as store
from finance_agent.outcome.analyst_claims import settle


def _calendar(start: str, count: int) -> list[str]:
    # 简单工作日日历（测试注入，不依赖真实节假日）
    current = date.fromisoformat(start)
    days: list[str] = []
    while len(days) < count:
        if current.weekday() < 5:
            days.append(current.isoformat())
        current += timedelta(days=1)
    return days


def _seed_claim(db, **over):
    row = {
        "claim_key": "k1",
        "claim_type": "rating_upgrade",
        "code": "600519",
        "org": "中信证券",
        "analyst": "张三",
        "published_at": "2026-10-20",
        "rating": "买入",
        "prev_rating": "增持",
        "horizon_days": 20,
        "industry": "白酒",
        "source": "cninfo",
    }
    row.update(over)
    store.upsert_claims([row], db_path=db)


def _bar(open_=10.0, close_qfq=10.0, close_raw=10.0, suspended=0):
    return {
        "open": open_,
        "close_qfq": close_qfq,
        "close_raw": close_raw,
        "is_suspended": suspended,
    }


def _outcome(db, key="k1"):
    return store.get_outcome(key, db_path=db)


class TestSettlement:
    def test_resolved_with_excess_return(self, tmp_path):
        db = tmp_path / "rr.db"
        _seed_claim(db)
        calendar = _calendar("2026-10-21", 22)
        prices = {calendar[0]: _bar(open_=10.0, close_qfq=10.0), calendar[20]: _bar(close_qfq=11.0)}
        result = settle.settle_claims(
            as_of=calendar[-1],
            claims_db_path=db,
            calendar=calendar,
            stock_prices=lambda code, d: prices.get(d),
            benchmark_closes={calendar[0]: 100.0, calendar[20]: 102.0},
        )
        out = _outcome(db)
        assert result["settled"] == 1
        assert out["status"] == "resolved"
        assert out["entry_date"] == calendar[0]
        assert out["exit_date"] == calendar[20]
        assert abs(out["raw_return"] - 0.1) < 1e-9
        assert abs(out["benchmark_return"] - 0.02) < 1e-9
        assert abs(out["excess_return"] - 0.08) < 1e-9

    def test_not_due_stays_pending(self, tmp_path):
        db = tmp_path / "rr.db"
        _seed_claim(db)
        calendar = _calendar("2026-10-21", 5)
        settle.settle_claims(
            as_of=calendar[-1],
            claims_db_path=db,
            calendar=calendar,
            stock_prices=lambda code, d: _bar(),
            benchmark_closes=dict.fromkeys(calendar, 100.0),
        )
        assert _outcome(db) is None
        assert store.list_pending_claims(db_path=db)

    def test_suspended_entry_is_unresolved(self, tmp_path):
        db = tmp_path / "rr.db"
        _seed_claim(db)
        calendar = _calendar("2026-10-21", 22)
        prices = {
            calendar[0]: _bar(suspended=1),
            calendar[20]: _bar(close_qfq=11.0),
        }
        result = settle.settle_claims(
            as_of=calendar[-1],
            claims_db_path=db,
            calendar=calendar,
            stock_prices=lambda code, d: prices.get(d),
            benchmark_closes={calendar[0]: 100.0, calendar[20]: 102.0},
        )
        out = _outcome(db)
        assert result["unresolved"] == 1
        assert out["status"] == "unresolved"
        assert out["reason"] == "suspended_entry"
        assert out["raw_return"] is None

    def test_missing_price_is_unresolved(self, tmp_path):
        db = tmp_path / "rr.db"
        _seed_claim(db)
        calendar = _calendar("2026-10-21", 22)
        result = settle.settle_claims(
            as_of=calendar[-1],
            claims_db_path=db,
            calendar=calendar,
            stock_prices=lambda code, d: None,
            benchmark_closes=dict.fromkeys(calendar, 100.0),
        )
        out = _outcome(db)
        assert result["unresolved"] == 1
        assert out["status"] == "unresolved"
        assert out["reason"] == "missing_price"

    def test_missing_benchmark_is_unresolved(self, tmp_path):
        db = tmp_path / "rr.db"
        _seed_claim(db)
        calendar = _calendar("2026-10-21", 22)
        prices = {calendar[0]: _bar(), calendar[20]: _bar(close_qfq=11.0)}
        result = settle.settle_claims(
            as_of=calendar[-1],
            claims_db_path=db,
            calendar=calendar,
            stock_prices=lambda code, d: prices.get(d),
            benchmark_closes={},
        )
        out = _outcome(db)
        assert result["unresolved"] == 1
        assert out["reason"] == "benchmark_missing"

    def test_entry_is_next_trading_day_not_publish_day(self, tmp_path):
        db = tmp_path / "rr.db"
        _seed_claim(db)
        calendar = _calendar("2026-10-21", 22)
        seen: list[str] = []

        def provider(code, d):
            seen.append(d)
            return _bar(open_=10.0, close_qfq=11.0)

        settle.settle_claims(
            as_of=calendar[-1],
            claims_db_path=db,
            calendar=calendar,
            stock_prices=provider,
            benchmark_closes={calendar[0]: 100.0, calendar[20]: 102.0},
        )
        assert "2026-10-20" not in seen
        assert seen[0] == calendar[0]

    def test_adjusted_entry_open_uses_qfq_ratio(self, tmp_path):
        db = tmp_path / "rr.db"
        _seed_claim(db)
        calendar = _calendar("2026-10-21", 22)
        prices = {
            calendar[0]: _bar(open_=10.0, close_qfq=9.0, close_raw=10.0),
            calendar[20]: _bar(close_qfq=9.9),
        }
        settle.settle_claims(
            as_of=calendar[-1],
            claims_db_path=db,
            calendar=calendar,
            stock_prices=lambda code, d: prices.get(d),
            benchmark_closes={calendar[0]: 100.0, calendar[20]: 100.0},
        )
        out = _outcome(db)
        assert abs(out["entry_price"] - 9.0) < 1e-9
        assert abs(out["raw_return"] - 0.1) < 1e-9

    def test_settlement_idempotent(self, tmp_path):
        db = tmp_path / "rr.db"
        _seed_claim(db)
        calendar = _calendar("2026-10-21", 22)
        prices = {calendar[0]: _bar(), calendar[20]: _bar(close_qfq=11.0)}
        kwargs = {
            "as_of": calendar[-1],
            "claims_db_path": db,
            "calendar": calendar,
            "stock_prices": lambda code, d: prices.get(d),
            "benchmark_closes": {calendar[0]: 100.0, calendar[20]: 102.0},
        }
        settle.settle_claims(**kwargs)
        second = settle.settle_claims(**kwargs)
        assert second["settled"] == 0
        conn = sqlite3.connect(db)
        try:
            count = conn.execute("SELECT COUNT(*) FROM analyst_claim_outcomes").fetchone()[0]
        finally:
            conn.close()
        assert count == 1
