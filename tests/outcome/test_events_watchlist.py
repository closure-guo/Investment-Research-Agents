"""观察清单构建测试（delta add-event-driven-lane T5）：outperform 入清单 / 去重。

口径权威：docs/evals/metrics.md §1.15 + spec「非 outperform 不进清单」。
"""

from __future__ import annotations

from finance_agent.outcome.events.model import (
    forecast_key,
    insert_watchlist,
    list_watchlist,
    record_triage,
    upsert_forecasts,
)
from finance_agent.outcome.events.watchlist import build_watchlist

_NET = "归属于上市公司股东的净利润"


def _forecast(code: str, announce: str) -> dict:
    return {
        "code": code,
        "name": f"公司{code}",
        "report_period": "2025-03-31",
        "source_record_id": f"{_NET}|{announce}",
        "forecast_type": "预增",
        "change_low": 60.0,
        "change_high": 90.0,
        "announce_date": announce,
        "available_at": announce,
        "source": "eastmoney",
    }


def _seed_triage(db, code: str, announce: str, verdict: str) -> str:
    row = _forecast(code, announce)
    upsert_forecasts([row], db_path=db)
    key = forecast_key(row)
    record_triage(
        key,
        verdict=verdict,
        reason=f"{verdict} 理由",
        evidence=[{"field": "forecast_type", "value": "预增"}],
        model="m",
        prompt_fingerprint="event_triage@1",
        db_path=db,
    )
    return key


class TestBuildWatchlist:
    def test_only_outperform_enters(self, tmp_path):
        db = tmp_path / "events.db"
        _seed_triage(db, "600519", "2025-04-15", "outperform")
        _seed_triage(db, "000001", "2025-04-15", "in_line")
        _seed_triage(db, "600000", "2025-04-16", "risk")
        added = build_watchlist(db_path=db)
        assert added == 1
        items = list_watchlist(db_path=db)
        assert [item["code"] for item in items] == ["600519"]
        assert items[0]["source"] == "earnings"
        assert items[0]["status"] == "pending"
        assert items[0]["verdict"] == "outperform"

    def test_dedupes_and_is_idempotent(self, tmp_path):
        db = tmp_path / "events.db"
        key = _seed_triage(db, "600519", "2025-04-15", "outperform")
        assert build_watchlist(db_path=db) == 1
        assert build_watchlist(db_path=db) == 0  # 重跑零重复
        assert len(list_watchlist(db_path=db)) == 1
        # 既有条目（含已发起分析）不被重置
        assert insert_watchlist(key, source="earnings", db_path=db) is None
        assert build_watchlist(db_path=db) == 0

    def test_empty_db_returns_zero(self, tmp_path):
        assert build_watchlist(db_path=tmp_path / "missing.db") == 0
