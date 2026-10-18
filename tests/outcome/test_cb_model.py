"""可转债存储层测试（delta add-convertible-bond-coverage T1/T2）。

断言面：cb 三表并库迁移链（含 event_watchlist.note 增量列）/ 主表与行情幂等 upsert
（标的按代码唯一、行情按 交易日+代码 唯一）/ 事件唯一键（``code|event_type|announce_date``）
/ 清单读取面暴露 cbb 要素（标的/行情/事件联表 + 规则化注记）。

口径权威：docs/evals/metrics.md §1.17 + 变更 design.md「存储」。
TDD：先红后绿——本文件先写断言，实现见 outcome/events/cb_model.py。
"""

from __future__ import annotations

import json
import sqlite3

from finance_agent.outcome.events.cb_model import (
    cb_event_key,
    find_cb_event,
    get_cb_master,
    get_cb_quote,
    init_cb_db,
    list_cb_events,
    list_cb_masters,
    upsert_cb_events,
    upsert_cb_masters,
    upsert_cb_quotes,
)
from finance_agent.outcome.events.model import (
    init_events_db,
    insert_watchlist,
    list_watchlist,
    update_watchlist_note,
)

_LEGACY_WATCHLIST_DDL = """
CREATE TABLE event_watchlist (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  forecast_key TEXT NOT NULL UNIQUE,
  source       TEXT NOT NULL DEFAULT 'earnings',
  status       TEXT NOT NULL DEFAULT 'pending',
  session_id   TEXT,
  created_at   TEXT NOT NULL
);
"""


def _master(code: str = "113046", **over) -> dict:
    row = {
        "code": code,
        "name": "金田转债",
        "stock_code": "601609",
        "convert_price": 10.18,
        "premium_rate": 1.63,
        "remain_scale": None,
        "rating": "AA+",
    }
    row.update(over)
    return row


def _quote(code: str = "113046", trade_date: str = "2026-10-16", **over) -> dict:
    row = {"trade_date": trade_date, "code": code, "close": 101.332, "amount": 95_687_931.0}
    row.update(over)
    return row


def _event(code: str = "113046", announce: str = "2026-10-15", **over) -> dict:
    row = {
        "event_key": cb_event_key(code, "强赎", announce),
        "code": code,
        "event_type": "强赎",
        "announce_date": announce,
        "trigger_price": 13.234,
        "trigger_ratio": 130.0,
        "count_text": "12/15 | 30",
        "last_trade_date": "2026-10-20",
        "remain_scale": 9.905,
        "raw_json": json.dumps({"强赎状态": "已公告强赎"}, ensure_ascii=False),
    }
    row.update(over)
    return row


class TestMigrationChain:
    """cb 表并入事件库 init 迁移链（同一 EVENTS_DB_PATH 库）。"""

    def test_init_events_db_creates_cb_tables(self, tmp_path):
        db = tmp_path / "events.db"
        init_events_db(db)
        conn = sqlite3.connect(db)
        try:
            tables = {
                str(row[0])
                for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
            }
        finally:
            conn.close()
        assert {"cb_master", "cb_quotes", "cb_events"} <= tables

    def test_init_events_db_adds_note_column_to_watchlist(self, tmp_path):
        """清单规则化注记列（cbb 载荷载体）经增量迁移并入既有库。"""
        db = tmp_path / "events.db"
        conn = sqlite3.connect(db)
        try:
            conn.executescript(_LEGACY_WATCHLIST_DDL)
            conn.execute(
                "INSERT INTO event_watchlist (forecast_key, source, status, created_at) "
                "VALUES ('600519|2025-03-31|ind|2025-04-15', 'earnings', 'pending', 't')"
            )
            conn.commit()
        finally:
            conn.close()
        init_events_db(db)
        conn = sqlite3.connect(db)
        try:
            columns = {str(row[1]) for row in conn.execute("PRAGMA table_info(event_watchlist)")}
            rows = conn.execute("SELECT forecast_key, note FROM event_watchlist").fetchall()
        finally:
            conn.close()
        assert "note" in columns
        assert rows == [("600519|2025-03-31|ind|2025-04-15", None)], "迁移不得丢既有行"

    def test_init_cb_db_is_idempotent_standalone(self, tmp_path):
        db = tmp_path / "other.db"
        assert init_cb_db(db) == db
        init_cb_db(db)  # 二次调用不抛


class TestEventKey:
    def test_key_is_code_type_announce_date(self):
        assert cb_event_key("113046", "强赎", "2026-10-15") == "113046|强赎|2026-10-15"
        assert cb_event_key("113046", "下修", "2024-05-21") == "113046|下修|2024-05-21"


class TestMasterUpsert:
    def test_empty_input_is_noop_without_creating_db(self, tmp_path):
        db = tmp_path / "none.db"
        assert upsert_cb_masters([], db_path=db) == 0
        assert not db.exists()

    def test_upsert_is_idempotent_and_updates_latest(self, tmp_path):
        db = tmp_path / "events.db"
        assert upsert_cb_masters([_master()], db_path=db) == 1
        assert upsert_cb_masters([_master(premium_rate=2.5, convert_price=9.9)], db_path=db) == 1
        rows = list_cb_masters(db_path=db)
        assert len(rows) == 1, "同一代码重复写入仍为单条"
        assert rows[0]["premium_rate"] == 2.5 and rows[0]["convert_price"] == 9.9
        assert rows[0]["updated_at"], "落库时刻留痕"
        assert get_cb_master("113046", db_path=db)["name"] == "金田转债"
        assert get_cb_master("999999", db_path=db) is None

    def test_master_remain_scale_stays_null_without_source(self, tmp_path):
        """主表源无剩余规模列：如实 NULL，MUST NOT 以发行规模冒充。"""
        db = tmp_path / "events.db"
        upsert_cb_masters([_master()], db_path=db)
        assert list_cb_masters(db_path=db)[0]["remain_scale"] is None

    def test_master_upsert_coalesces_remain_scale(self, tmp_path):
        """剩余规模回填不被日常同步清空（COALESCE 保值；源缺列只写 NULL 的日行不动既有值）。"""
        db = tmp_path / "events.db"
        upsert_cb_masters([_master(remain_scale=5.0)], db_path=db)
        upsert_cb_masters([_master(remain_scale=None, premium_rate=2.5)], db_path=db)
        row = list_cb_masters(db_path=db)[0]
        assert row["remain_scale"] == 5.0, "缺值日行不得清空已回填的剩余规模"
        assert row["premium_rate"] == 2.5, "其余字段照常刷新"
        upsert_cb_masters([_master(remain_scale=4.2)], db_path=db)
        assert list_cb_masters(db_path=db)[0]["remain_scale"] == 4.2, "有值则覆盖"


class TestQuoteUpsert:
    def test_unique_per_trade_date_and_code(self, tmp_path):
        db = tmp_path / "events.db"
        upsert_cb_quotes([_quote()], db_path=db)
        upsert_cb_quotes([_quote(close=102.5)], db_path=db)
        assert upsert_cb_quotes([_quote(trade_date="2026-10-19", close=103.0)], db_path=db) == 1
        conn = sqlite3.connect(db)
        try:
            count = conn.execute("SELECT COUNT(*) FROM cb_quotes").fetchone()[0]
        finally:
            conn.close()
        assert count == 2, "同一 (交易日, 代码) 唯一，跨交易日各留一行"
        latest = get_cb_quote("113046", db_path=db)
        assert latest is not None
        assert latest["trade_date"] == "2026-10-19" and latest["close"] == 103.0
        same_day = get_cb_quote("113046", trade_date="2026-10-16", db_path=db)
        assert same_day is not None and same_day["close"] == 102.5, "同日重写覆盖为最新值"
        assert get_cb_quote("999999", db_path=db) is None


class TestEventUpsert:
    def test_unique_key_and_refresh(self, tmp_path):
        db = tmp_path / "events.db"
        upsert_cb_events([_event()], db_path=db)
        upsert_cb_events([_event(count_text="13/15 | 30")], db_path=db)
        rows = list_cb_events(db_path=db)
        assert len(rows) == 1, "同一 (代码, 类型, 公告日) 唯一：重跑零重复"
        assert rows[0]["count_text"] == "13/15 | 30", "同键重跑刷新为最新值"
        assert rows[0]["remain_scale"] == 9.905
        assert "已公告强赎" in rows[0]["raw_json"]

    def test_announce_date_is_part_of_key(self, tmp_path):
        db = tmp_path / "events.db"
        upsert_cb_events([_event(announce="2026-10-15"), _event(announce="2026-10-16")], db_path=db)
        assert len(list_cb_events(db_path=db)) == 2

    def test_explicit_none_event_key_is_synthesized(self, tmp_path):
        """显式 ``event_key=None`` 不得绕过合成（否则 NULL 主键在 SQLite 下不唯一 → 重复行）。"""
        db = tmp_path / "events.db"
        row = _event()
        row["event_key"] = None
        upsert_cb_events([row], db_path=db)
        rows = list_cb_events(db_path=db)
        assert [r["event_key"] for r in rows] == ["113046|强赎|2026-10-15"]
        assert upsert_cb_events([{**_event(), "event_key": None}], db_path=db) == 1
        assert len(list_cb_events(db_path=db)) == 1, "空键重写仍为同一行"

    def test_find_cb_event_looks_up_without_announce_date(self, tmp_path):
        """首次观测日语义：重跑按 (代码, 类型) 找既有行复用其 announce_date。"""
        db = tmp_path / "events.db"
        assert find_cb_event("113046", "强赎", db_path=db) is None
        upsert_cb_events([_event()], db_path=db)
        found = find_cb_event("113046", "强赎", db_path=db)
        assert found is not None and found["announce_date"] == "2026-10-15"
        assert find_cb_event("113046", "下修", db_path=db) is None


class TestWatchlistCbReadFace:
    def test_list_watchlist_exposes_cb_elements_and_note(self, tmp_path):
        db = tmp_path / "events.db"
        event = _event()
        upsert_cb_masters([_master(premium_rate=1.63)], db_path=db)
        upsert_cb_quotes([_quote(close=101.332)], db_path=db)
        upsert_cb_events([event], db_path=db)
        note = json.dumps(
            {"state": "announced", "call_countdown_days": 2, "reason": "已公告强赎"},
            ensure_ascii=False,
        )
        item_id = insert_watchlist(event["event_key"], source="cbb", note=note, db_path=db)
        assert item_id is not None

        items = list_watchlist(source="cbb", db_path=db)
        assert len(items) == 1
        item = items[0]
        assert item["source"] == "cbb" and item["note"] == note
        assert item["cb_code"] == "113046" and item["cb_name"] == "金田转债"
        assert item["cb_premium_rate"] == 1.63
        assert item["cb_close"] == 101.332
        assert item["cb_remain_scale"] == 9.905
        assert item["cb_announce_date"] == "2026-10-15"
        assert item["cb_event_type"] == "强赎"
        # 预告侧联表对 cbb 行如实留空（不含业绩预告要素）
        assert item["code"] is None and item["verdict"] is None

    def test_update_watchlist_note_refreshes_derived_annotation(self, tmp_path):
        """每日构建刷新倒计时注记，但不动 status/session（用户状态零改写）。"""
        db = tmp_path / "events.db"
        key = "113046|强赎|2026-10-15"
        insert_watchlist(key, source="cbb", note='{"call_countdown_days": 3}', db_path=db)
        assert update_watchlist_note(key, note='{"call_countdown_days": 2}', db_path=db) is True
        items = list_watchlist(db_path=db)
        assert items[0]["note"] == '{"call_countdown_days": 2}'
        assert items[0]["status"] == "pending" and items[0]["session_id"] is None
        assert update_watchlist_note("missing|强赎|2026-10-15", note="{}", db_path=db) is False
