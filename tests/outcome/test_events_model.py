"""事件线存储层测试（delta add-event-driven-lane T2）：四表 DDL + 幂等 upsert + 时点过滤。

口径权威：docs/evals/metrics.md §1.15 + 变更 design.md「存储」。
TDD：先红后绿——本文件先写断言，实现见 outcome/events/model.py。
"""

from __future__ import annotations

import sqlite3

from finance_agent.outcome.events.model import (
    _default_db_path,
    forecast_key,
    init_events_db,
    insert_journal,
    insert_watchlist,
    list_open_journal,
    list_settled_journal,
    list_untriaged,
    list_watchlist,
    mark_watchlist_analyzing,
    record_triage,
    settle_journal,
    upsert_forecasts,
)


def _forecast(
    *,
    code: str = "600519",
    report_period: str = "2025-03-31",
    source_record_id: str = "归属于上市公司股东的净利润|2025-04-15",
    name: str = "贵州茅台",
    forecast_type: str = "预增",
    change_low: float | None = 10.0,
    change_high: float | None = 20.0,
    announce_date: str = "2025-04-15",
    available_at: str | None = None,
    source: str = "eastmoney",
) -> dict:
    return {
        "code": code,
        "report_period": report_period,
        "source_record_id": source_record_id,
        "name": name,
        "forecast_type": forecast_type,
        "change_low": change_low,
        "change_high": change_high,
        "announce_date": announce_date,
        "available_at": available_at or announce_date,
        "source": source,
    }


def _tables(db_path) -> set[str]:
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        return {str(r[0]) for r in rows}
    finally:
        conn.close()


class TestInit:
    def test_creates_four_tables_and_returns_path(self, tmp_path):
        db = tmp_path / "events.db"
        result = init_events_db(db)
        assert result == db
        assert {
            "earnings_forecasts",
            "event_triage",
            "event_watchlist",
            "event_journal",
        } <= _tables(db)

    def test_init_is_idempotent(self, tmp_path):
        db = tmp_path / "events.db"
        init_events_db(db)
        init_events_db(db)  # 重复调用不抛
        assert "earnings_forecasts" in _tables(db)

    def test_default_db_path_reads_env_at_call_time(self, tmp_path, monkeypatch):
        monkeypatch.setenv("EVENTS_DB_PATH", str(tmp_path / "custom.db"))
        assert _default_db_path() == tmp_path / "custom.db"
        monkeypatch.delenv("EVENTS_DB_PATH")
        assert _default_db_path().as_posix() == "data/events.db"


class TestForecastUpsert:
    def test_upsert_is_idempotent_and_updates_to_latest(self, tmp_path):
        """同一 (code, report_period, source_record_id) 重复写入 → 单条且字段更新为最新值。"""
        db = tmp_path / "events.db"
        first = upsert_forecasts([_forecast(change_high=20.0)], db_path=db)
        assert first == 1
        upsert_forecasts([_forecast(change_high=33.5, forecast_type="略增")], db_path=db)
        conn = sqlite3.connect(db)
        try:
            rows = conn.execute(
                "SELECT forecast_type, change_high FROM earnings_forecasts"
            ).fetchall()
        finally:
            conn.close()
        assert rows == [("略增", 33.5)]

    def test_upsert_empty_rows_is_noop(self, tmp_path):
        db = tmp_path / "events.db"
        assert upsert_forecasts([], db_path=db) == 0

    def test_forecast_key_is_stable_composite(self):
        row = _forecast()
        assert forecast_key(row) == "600519|2025-03-31|归属于上市公司股东的净利润|2025-04-15"


class TestUntriaged:
    def test_filters_by_available_at_and_excludes_triaged(self, tmp_path):
        """时点截断（available_at <= as_of）与已分诊排除。"""
        db = tmp_path / "events.db"
        upsert_forecasts(
            [
                _forecast(code="600519", announce_date="2025-04-10", available_at="2025-04-10"),
                _forecast(
                    code="000001",
                    announce_date="2025-04-20",
                    available_at="2025-04-20",
                    source_record_id="ind|2025-04-20",
                ),
            ],
            db_path=db,
        )
        rows = list_untriaged(as_of="2025-04-15", db_path=db)
        assert [row["code"] for row in rows] == ["600519"]

        record_triage(
            forecast_key(_forecast(code="600519")),
            verdict="outperform",
            reason="大幅预增",
            evidence=[{"field": "change_low", "value": 10.0}],
            model="test-model",
            prompt_fingerprint="local",
            db_path=db,
        )
        assert list_untriaged(as_of="2025-04-15", db_path=db) == []

    def test_no_as_of_returns_all_untriaged(self, tmp_path):
        db = tmp_path / "events.db"
        upsert_forecasts(
            [
                _forecast(code="600519", available_at="2025-04-10"),
                _forecast(code="000001", available_at="2025-05-20", source_record_id="ind|X"),
            ],
            db_path=db,
        )
        assert len(list_untriaged(db_path=db)) == 2


class TestTriage:
    def test_record_triage_is_idempotent_per_forecast(self, tmp_path):
        db = tmp_path / "events.db"
        key = "600519|2025-03-31|ind|2025-04-15"
        record_triage(
            key,
            verdict="outperform",
            reason="预增",
            evidence=[{"field": "forecast_type", "value": "预增"}],
            model="m1",
            prompt_fingerprint="local",
            db_path=db,
        )
        record_triage(
            key,
            verdict="risk",
            reason="改判",
            evidence=[{"field": "change_low", "value": -5}],
            model="m2",
            prompt_fingerprint="v2",
            db_path=db,
        )
        conn = sqlite3.connect(db)
        try:
            rows = conn.execute(
                "SELECT verdict, reason, evidence_json, model, prompt_fingerprint FROM event_triage"
            ).fetchall()
        finally:
            conn.close()
        assert len(rows) == 1
        assert rows[0][0] == "risk" and rows[0][1] == "改判"
        assert rows[0][3] == "m2" and rows[0][4] == "v2"


class TestWatchlist:
    def _seed_outperform(self, db, key: str) -> None:
        record_triage(
            key,
            verdict="outperform",
            reason="预增",
            evidence=[{"field": "forecast_type", "value": "预增"}],
            model="m1",
            prompt_fingerprint="local",
            db_path=db,
        )

    def test_insert_watchlist_dedupes_same_forecast(self, tmp_path):
        db = tmp_path / "events.db"
        key = "600519|2025-03-31|ind|2025-04-15"
        first = insert_watchlist(key, source="earnings", db_path=db)
        assert isinstance(first, int)
        assert insert_watchlist(key, source="earnings", db_path=db) is None
        assert len(list_watchlist(db_path=db)) == 1

    def test_list_watchlist_joins_forecast_and_triage(self, tmp_path):
        db = tmp_path / "events.db"
        row = _forecast()
        upsert_forecasts([row], db_path=db)
        key = forecast_key(row)
        self._seed_outperform(db, key)
        insert_watchlist(key, source="earnings", db_path=db)

        items = list_watchlist(db_path=db)
        assert len(items) == 1
        item = items[0]
        assert item["status"] == "pending"
        assert item["session_id"] is None
        # 预告要素 + 分诊理由/证据 + 公告日
        assert item["code"] == "600519" and item["name"] == "贵州茅台"
        assert item["announce_date"] == "2025-04-15"
        assert item["verdict"] == "outperform" and item["reason"] == "预增"
        assert item["evidence_json"] == '[{"field": "forecast_type", "value": "预增"}]'

    def test_list_watchlist_source_filter(self, tmp_path):
        db = tmp_path / "events.db"
        key = "600519|2025-03-31|ind|2025-04-15"
        key2 = "000001|2025-03-31|ind|2025-04-16"
        insert_watchlist(key, source="earnings", db_path=db)
        insert_watchlist(key2, source="other", db_path=db)
        assert [i["forecast_key"] for i in list_watchlist(source="earnings", db_path=db)] == [key]

    def test_mark_watchlist_analyzing_sets_session_and_status(self, tmp_path):
        db = tmp_path / "events.db"
        key = "600519|2025-03-31|ind|2025-04-15"
        row_id = insert_watchlist(key, source="earnings", db_path=db)
        assert mark_watchlist_analyzing(row_id, session_id="sess-1", db_path=db) is True
        item = list_watchlist(db_path=db)[0]
        assert item["status"] == "analyzing" and item["session_id"] == "sess-1"
        assert mark_watchlist_analyzing(9999, session_id="s", db_path=db) is False


class TestJournal:
    def test_journal_lifecycle_open_to_settled(self, tmp_path):
        db = tmp_path / "events.db"
        key = "600519|2025-03-31|ind|2025-04-15"
        insert_journal(
            forecast_key=key,
            source="earnings",
            symbol="600519.SH",
            entry_date="2025-04-16",
            entry_price=100.0,
            horizon_days=5,
            db_path=db,
        )
        open_rows = list_open_journal(db_path=db)
        assert len(open_rows) == 1
        assert open_rows[0]["status"] == "open"
        assert open_rows[0]["symbol"] == "600519.SH"
        row_id = open_rows[0]["id"]

        assert (
            settle_journal(
                row_id,
                settle_date="2025-04-23",
                settle_price=105.0,
                raw_return=0.05,
                db_path=db,
            )
            is True
        )
        assert list_open_journal(db_path=db) == []
        settled = list_settled_journal(limit=10, db_path=db)
        assert len(settled) == 1
        assert settled[0]["settle_date"] == "2025-04-23"
        assert settled[0]["raw_return"] == 0.05

    def test_list_open_journal_before_date_filter(self, tmp_path):
        db = tmp_path / "events.db"
        for index, day in enumerate(["2025-04-16", "2025-05-06"]):
            insert_journal(
                forecast_key=f"600519|2025-03-31|ind|{day}",
                source="earnings",
                symbol="600519.SH",
                entry_date=day,
                entry_price=100.0,
                horizon_days=5,
                note=f"n{index}",
                db_path=db,
            )
        rows = list_open_journal(before_date="2025-04-30", db_path=db)
        assert [row["entry_date"] for row in rows] == ["2025-04-16"]

    def test_settle_journal_unknown_id_returns_false(self, tmp_path):
        db = tmp_path / "events.db"
        init_events_db(db)
        assert (
            settle_journal(
                42, settle_date="2025-04-23", settle_price=1.0, raw_return=0.0, db_path=db
            )
            is False
        )

    def test_list_settled_journal_keeps_recent_within_limit(self, tmp_path):
        """超读取上限时保留近期（``settle_date`` 降序），不静默丢最新。"""
        db = tmp_path / "events.db"
        for day, ret in [("2025-04-23", 0.01), ("2025-04-24", -0.02), ("2025-04-25", 0.03)]:
            row_id = insert_journal(
                forecast_key=f"600519|2025-03-31|ind|{day}",
                source="earnings",
                symbol="600519.SH",
                entry_date=day,
                entry_price=100.0,
                horizon_days=5,
                db_path=db,
            )
            settle_journal(row_id, settle_date=day, settle_price=100.0, raw_return=ret, db_path=db)
        rows = list_settled_journal(limit=2, db_path=db)
        assert [row["raw_return"] for row in rows] == [0.03, -0.02]  # 降序取前 limit（保留近期）

    def test_insert_journal_records_deferred_note(self, tmp_path):
        db = tmp_path / "events.db"
        insert_journal(
            forecast_key="600519|2025-03-31|ind|2025-04-15",
            source="earnings",
            symbol="600519.SH",
            entry_date="2025-04-17",
            entry_price=100.0,
            horizon_days=5,
            note="deferred:suspended@2025-04-16",
            db_path=db,
        )
        assert list_open_journal(db_path=db)[0]["note"] == "deferred:suspended@2025-04-16"
