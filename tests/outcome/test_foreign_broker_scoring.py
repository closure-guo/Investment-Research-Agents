"""外资事件打分与命中测试（add-region-split-broker-scoring）。"""

from __future__ import annotations

from datetime import date, timedelta

from finance_agent.data import foreign_broker_news as fbn
from finance_agent.outcome import foreign_broker_scoring as scoring


def _calendar(start: str, count: int) -> list[str]:
    current = date.fromisoformat(start)
    days: list[str] = []
    while len(days) < count:
        if current.weekday() < 5:
            days.append(current.isoformat())
        current += timedelta(days=1)
    return days


def _event(db, key: str, **over):
    row = {
        "event_key": key,
        "title": "UBS 中国股票策略",
        "url": f"https://example.invalid/{key}",
        "source": "example",
        "published_at": "2026-10-20",
        "broker": "UBS",
        "action": "rating_up",
        "target_type": "market",
        "target": "中国股市",
        "direction": "positive",
        "summary": "s",
        "confidence": "high",
        "extraction_status": "ok",
    }
    row.update(over)
    row["event_score"] = scoring.event_score(
        row.get("action"), row.get("direction"), row.get("confidence")
    )
    fbn.upsert_event(row, db_path=db)


class TestEventScore:
    def test_up_positive_high(self):
        assert scoring.event_score("rating_up", "positive", "high") == 1.0

    def test_down_negative_high(self):
        assert scoring.event_score("rating_down", "negative", "high") == -1.0

    def test_positioning_negative_medium(self):
        assert abs(scoring.event_score("positioning", "negative", "medium") + 0.7) < 1e-9

    def test_unknown_direction_is_zero(self):
        assert scoring.event_score("rating_up", "unknown", "high") == 0.0
        assert scoring.event_score("rating_up", "neutral", "high") == 0.0

    def test_other_action_is_zero(self):
        assert scoring.event_score("other", "positive", "high") == 0.0


class TestSettlement:
    def test_market_positive_hit(self, tmp_path):
        db = tmp_path / "rr.db"
        _event(db, "e1")
        calendar = _calendar("2026-10-21", 22)
        result = scoring.settle_foreign_events(
            as_of=calendar[-1],
            db_path=db,
            calendar=calendar,
            benchmark_closes={calendar[0]: 100.0, calendar[20]: 101.0},
        )
        assert result["settled"] == 1
        rows = scoring.list_foreign_scorecard(db_path=db)
        assert rows == []  # 尚未生成快照
        # 直接查 outcome 通过 build 的 join 验证
        scorecard = scoring.build_foreign_scorecard(calendar[-1], db_path=db)
        broker = [r for r in scorecard if r["bucket_type"] == "broker"][0]
        assert broker["n"] == 1
        assert broker["hits"] == 1
        assert broker["hit_rate"] == 1.0
        assert abs(broker["mean_return"] - 0.01) < 1e-9

    def test_market_negative_miss(self, tmp_path):
        db = tmp_path / "rr.db"
        _event(db, "e1", direction="negative")
        calendar = _calendar("2026-10-21", 22)
        scoring.settle_foreign_events(
            as_of=calendar[-1],
            db_path=db,
            calendar=calendar,
            benchmark_closes={calendar[0]: 100.0, calendar[20]: 101.0},
        )
        scorecard = scoring.build_foreign_scorecard(calendar[-1], db_path=db)
        broker = [r for r in scorecard if r["bucket_type"] == "broker"][0]
        assert broker["hits"] == 0
        assert broker["hit_rate"] == 0.0

    def test_sector_unresolved(self, tmp_path):
        db = tmp_path / "rr.db"
        _event(db, "e1", target_type="sector", target="银行")
        calendar = _calendar("2026-10-21", 22)
        result = scoring.settle_foreign_events(
            as_of=calendar[-1],
            db_path=db,
            calendar=calendar,
            benchmark_closes={calendar[0]: 100.0, calendar[20]: 101.0},
        )
        assert result["unresolved"] == 1

    def test_unknown_direction_unresolved(self, tmp_path):
        db = tmp_path / "rr.db"
        _event(db, "e1", direction="unknown")
        calendar = _calendar("2026-10-21", 22)
        result = scoring.settle_foreign_events(
            as_of=calendar[-1],
            db_path=db,
            calendar=calendar,
            benchmark_closes={calendar[0]: 100.0, calendar[20]: 101.0},
        )
        assert result["unresolved"] == 1

    def test_not_due_pending(self, tmp_path):
        db = tmp_path / "rr.db"
        _event(db, "e1")
        calendar = _calendar("2026-10-21", 5)
        result = scoring.settle_foreign_events(
            as_of=calendar[-1],
            db_path=db,
            calendar=calendar,
            benchmark_closes=dict.fromkeys(calendar, 100.0),
        )
        assert result["pending"] == 1
        assert result["settled"] == 0


class TestScorecard:
    def test_hit_rate_and_net_score(self, tmp_path):
        db = tmp_path / "rr.db"
        _event(db, "e1")
        _event(db, "e2", action="rating_down", direction="negative")
        calendar = _calendar("2026-10-21", 22)
        scoring.settle_foreign_events(
            as_of=calendar[-1],
            db_path=db,
            calendar=calendar,
            benchmark_closes={calendar[0]: 100.0, calendar[20]: 101.0},
        )
        rows = scoring.build_foreign_scorecard(calendar[-1], db_path=db)
        broker = [r for r in rows if r["bucket_type"] == "broker"][0]
        assert broker["n"] == 2
        assert broker["hits"] == 1
        assert broker["hit_rate"] == 0.5
        assert broker["reliable"] is False
        assert broker["region"] == "foreign"
        assert broker["score_family"] == "sentiment"
        assert broker["net_score"] == 0.0  # +1 与 -1 平均

    def test_persist_and_idempotent(self, tmp_path):
        db = tmp_path / "rr.db"
        _event(db, "e1")
        calendar = _calendar("2026-10-21", 22)
        scoring.settle_foreign_events(
            as_of=calendar[-1],
            db_path=db,
            calendar=calendar,
            benchmark_closes={calendar[0]: 100.0, calendar[20]: 101.0},
        )
        rows = scoring.build_foreign_scorecard(calendar[-1], db_path=db)
        scoring.persist_foreign_scorecard(calendar[-1], rows, db_path=db)
        scoring.persist_foreign_scorecard(calendar[-1], rows, db_path=db)
        got = scoring.list_foreign_scorecard(snapshot_date=calendar[-1], db_path=db)
        assert len(got) == len(rows)
        assert got[0]["snapshot_date"] == calendar[-1]
