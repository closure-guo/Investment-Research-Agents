"""可转债同步层测试（delta add-convertible-bond-coverage T1/T2）：幂等落库与失败不落行。

断言面（brief T2）：字段映射与单位归一（抓取层已测，此处是落库衔接）/ upsert 幂等
/ 事件唯一键（首次观测日公告日复用 → 跨日重跑零重复）/ 接口失败 ``[]``+留痕且不落行
/ 转股价变化触发的有界下修接入。

口径权威：docs/evals/metrics.md §1.17。零网络：全部 fake client。
TDD：先红后绿——本文件先写断言，实现见 outcome/events/cb_sync.py。
"""

from __future__ import annotations

import sqlite3

from finance_agent.outcome.events.cb_model import (
    cb_event_key,
    list_cb_events,
    list_cb_masters,
    upsert_cb_events,
    upsert_cb_masters,
)
from finance_agent.outcome.events.cb_sync import (
    sync_cb_adj_events,
    sync_cb_events,
    sync_cb_master,
    sync_cb_quotes,
)

_CALENDAR = ["2026-10-15", "2026-10-16", "2026-10-19", "2026-10-20"]


class _FakeClient:
    """抓取桩：按源返回行；``fail_sources`` 内的源记 ``none``（失败留痕口径）。"""

    def __init__(
        self,
        *,
        masters: list[dict] | None = None,
        quotes: list[dict] | None = None,
        redeem: list[dict] | None = None,
        adj: dict[str, list[dict]] | None = None,
        fail_sources: set[str] | None = None,
    ) -> None:
        self.masters = masters or []
        self.quotes = quotes or []
        self.redeem = redeem or []
        self.adj = adj or {}
        self.fail_sources = fail_sources or set()
        self.adj_queried: list[str] = []
        self.last_cb_adj_failed: bool | None = None
        self.sources_seen: dict[str, set[str]] = {
            "cb_master": set(),
            "cb_quotes": set(),
            "cb_redeem": set(),
            "cb_adj": set(),
        }

    def _mark(self, tag: str, ok_label: str) -> bool:
        if tag in self.fail_sources:
            self.sources_seen[tag].add("none")
            return True
        self.sources_seen[tag].add(ok_label)
        return False

    def fetch_cb_master(self) -> list[dict]:
        return [] if self._mark("cb_master", "eastmoney") else list(self.masters)

    def fetch_cb_quotes(self) -> list[dict]:
        return [] if self._mark("cb_quotes", "sina") else list(self.quotes)

    def fetch_cb_redeem_events(self) -> list[dict]:
        return [] if self._mark("cb_redeem", "jisilu") else list(self.redeem)

    def fetch_cb_adj_logs(self, code: str) -> list[dict]:
        self.adj_queried.append(code)
        if "cb_adj" in self.fail_sources:
            self.sources_seen["cb_adj"].add("none")
            self.last_cb_adj_failed = True
            return []
        self.sources_seen["cb_adj"].add("jisilu")
        self.last_cb_adj_failed = False
        return list(self.adj.get(code, []))


def _master_row(code: str = "113046", convert_price: float | None = 10.18, **over) -> dict:
    row = {
        "code": code,
        "name": "金田转债",
        "stock_code": "601609",
        "convert_price": convert_price,
        "premium_rate": 1.63,
        "remain_scale": None,
        "rating": "AA+",
    }
    row.update(over)
    return row


def _redeem_row(code: str = "113046", **over) -> dict:
    row = {
        "code": code,
        "name": "金田转债",
        "trigger_price": 13.234,
        "trigger_ratio": 130.0,
        "count_text": "12/15 | 30",
        "last_trade_date": "2026-10-20",
        "remain_scale": 9.905,
        "redeem_status": "已公告强赎",
        "raw_json": '{"强赎状态": "已公告强赎"}',
    }
    row.update(over)
    return row


class TestSyncMaster:
    def test_upserts_rows_and_reports_first_run_without_change(self, tmp_path):
        db = tmp_path / "events.db"
        client = _FakeClient(masters=[_master_row(), _master_row("128136", 55.56)])
        summary = sync_cb_master(db_path=db, client=client)
        assert summary["fetched"] == 2 and summary["upserted"] == 2
        assert summary["source_failed"] is False
        assert summary["convert_price_changed"] == [], "首跑无基线 → 不报变化（不回填历史下修）"
        assert len(list_cb_masters(db_path=db)) == 2

    def test_detects_convert_price_change_before_overwrite(self, tmp_path):
        """变化检出 = 与上次落库值比较（下修必致转股价变化 → 有界接入的触发面）。"""
        db = tmp_path / "events.db"
        upsert_cb_masters([_master_row(), _master_row("128136", 55.56)], db_path=db)
        client = _FakeClient(masters=[_master_row(convert_price=9.9), _master_row("128136", 55.56)])
        summary = sync_cb_master(db_path=db, client=client)
        assert summary["convert_price_changed"] == ["113046"]
        assert list_cb_masters(db_path=db)[0]["convert_price"] == 9.9, "变化后照常刷新主表"

    def test_new_code_and_null_prices_are_not_changes(self, tmp_path):
        db = tmp_path / "events.db"
        upsert_cb_masters([_master_row(convert_price=None)], db_path=db)
        client = _FakeClient(
            masters=[_master_row(convert_price=10.18), _master_row("123290", None)]
        )
        summary = sync_cb_master(db_path=db, client=client)
        assert summary["convert_price_changed"] == [], "缺值与新增代码不算变化（MUST NOT 猜测下修）"

    def test_failure_writes_nothing_and_keeps_existing(self, tmp_path):
        db = tmp_path / "events.db"
        upsert_cb_masters([_master_row()], db_path=db)
        client = _FakeClient(fail_sources={"cb_master"})
        summary = sync_cb_master(db_path=db, client=client)
        assert summary == {
            "fetched": 0,
            "upserted": 0,
            "convert_price_changed": [],
            "source_failed": True,
            "sources": ["none"],
        }
        rows = list_cb_masters(db_path=db)
        assert len(rows) == 1 and rows[0]["premium_rate"] == 1.63, "失败不清空既有数据"


class TestSyncQuotes:
    def test_upserts_with_trade_date_from_calendar(self, tmp_path):
        db = tmp_path / "events.db"
        client = _FakeClient(
            quotes=[
                {"code": "113046", "close": 101.332, "amount": 95_687_931.0},
                {"code": "110076", "close": 109.948, "amount": 1.0},
            ]
        )
        summary = sync_cb_quotes(
            db_path=db, client=client, trade_date="2026-10-16", calendar=_CALENDAR
        )
        assert summary["upserted"] == 2 and summary["trade_date"] == "2026-10-16"
        assert summary["skipped_reason"] is None
        conn = sqlite3.connect(db)
        try:
            conn.row_factory = sqlite3.Row
            rows = [dict(r) for r in conn.execute("SELECT * FROM cb_quotes ORDER BY code")]
        finally:
            conn.close()
        assert [r["trade_date"] for r in rows] == ["2026-10-16", "2026-10-16"]
        assert rows[0]["close"] == 109.948 and rows[0]["amount"] == 1.0

    def test_rerun_same_day_is_idempotent(self, tmp_path):
        db = tmp_path / "events.db"
        client = _FakeClient(quotes=[{"code": "113046", "close": 101.0, "amount": 1.0}])
        sync_cb_quotes(db_path=db, client=client, trade_date="2026-10-16", calendar=_CALENDAR)
        client.quotes = [{"code": "113046", "close": 102.0, "amount": 2.0}]
        sync_cb_quotes(db_path=db, client=client, trade_date="2026-10-16", calendar=_CALENDAR)
        conn = sqlite3.connect(db)
        try:
            rows = conn.execute("SELECT close FROM cb_quotes").fetchall()
        finally:
            conn.close()
        assert rows == [(102.0,)], "同一 (交易日, 代码) 重复抓取仍单条、值为最新"

    def test_non_trading_day_skips_without_stale_rows(self, tmp_path):
        """日历判非交易日 → 不落行情（MUST NOT 拿前一交易日价位冒充当日）。"""
        db = tmp_path / "events.db"
        client = _FakeClient(quotes=[{"code": "113046", "close": 101.0, "amount": 1.0}])
        summary = sync_cb_quotes(
            db_path=db, client=client, trade_date="2026-10-18", calendar=_CALENDAR
        )
        assert summary["upserted"] == 0
        assert summary["skipped_reason"] == "non_trading_day"
        assert summary["fetched"] == 0, "跳过时不得拉取行情"
        assert client.sources_seen["cb_quotes"] == set()
        assert not db.exists()

    def test_calendar_unavailable_falls_back_to_as_of(self, tmp_path):
        db = tmp_path / "events.db"
        client = _FakeClient(quotes=[{"code": "113046", "close": 101.0, "amount": 1.0}])
        summary = sync_cb_quotes(db_path=db, client=client, trade_date="2026-10-18", calendar=None)
        assert summary["upserted"] == 1 and summary["skipped_reason"] is None

    def test_failure_writes_nothing(self, tmp_path):
        db = tmp_path / "events.db"
        client = _FakeClient(fail_sources={"cb_quotes"})
        summary = sync_cb_quotes(
            db_path=db, client=client, trade_date="2026-10-16", calendar=_CALENDAR
        )
        assert summary["upserted"] == 0 and summary["source_failed"] is True
        assert not db.exists()


class TestSyncEvents:
    def test_first_observation_sets_announce_date_and_reuses_across_days(self, tmp_path):
        """公告日 = 首次观测日：跨日重跑复用既有 announce_date → 键稳定、零重复。"""
        db = tmp_path / "events.db"
        first = sync_cb_events(
            db_path=db, client=_FakeClient(redeem=[_redeem_row()]), as_of="2026-10-15"
        )
        assert first["fetched"] == 1 and first["upserted"] == 1
        rows = list_cb_events(db_path=db)
        assert rows[0]["event_key"] == cb_event_key("113046", "强赎", "2026-10-15")
        assert rows[0]["announce_date"] == "2026-10-15"

        client = _FakeClient(redeem=[_redeem_row(count_text="13/15 | 30")])
        second = sync_cb_events(db_path=db, client=client, as_of="2026-10-16")
        assert second["upserted"] == 1
        rows = list_cb_events(db_path=db)
        assert len(rows) == 1, "跨日重跑零重复（键复用首次观测日）"
        assert rows[0]["announce_date"] == "2026-10-15"
        assert rows[0]["count_text"] == "13/15 | 30", "同键刷新为最新观测"

    def test_initial_day_inserts_one_row_per_code(self, tmp_path):
        db = tmp_path / "events.db"
        client = _FakeClient(
            redeem=[_redeem_row(), _redeem_row("110076", name="华海转债", remain_scale=18.384)]
        )
        summary = sync_cb_events(db_path=db, client=client, as_of="2026-10-15")
        assert summary["fetched"] == 2 and summary["upserted"] == 2
        assert len(list_cb_events(db_path=db)) == 2

    def test_failure_writes_nothing_and_reports(self, tmp_path):
        db = tmp_path / "events.db"
        client = _FakeClient(fail_sources={"cb_redeem"})
        summary = sync_cb_events(db_path=db, client=client, as_of="2026-10-15")
        assert summary == {"fetched": 0, "upserted": 0, "source_failed": True, "sources": ["none"]}
        assert not db.exists(), "接口失败不落行（MUST NOT 以零条新增冒充成功）"


class TestSyncAdjEvents:
    def _adj(self, meeting: str = "2026-10-14") -> dict:
        return {
            "code": "113046",
            "name": "金田转债",
            "meeting_date": meeting,
            "convert_price_before": 12.0,
            "convert_price_after": 10.18,
            "effective_date": "2026-10-16",
            "raw_json": '{"下修前转股价": 12.0}',
        }

    def test_upserts_recent_adjustment_with_meeting_date_as_announce(self, tmp_path):
        db = tmp_path / "events.db"
        client = _FakeClient(adj={"113046": [self._adj()]})
        summary = sync_cb_adj_events(
            db_path=db, client=client, codes=["113046"], as_of="2026-10-16"
        )
        assert summary["queried"] == 1 and summary["upserted"] == 1
        assert client.adj_queried == ["113046"]
        rows = list_cb_events(db_path=db)
        assert rows[0]["event_type"] == "下修"
        assert rows[0]["announce_date"] == "2026-10-14", "下修公告日 = 股东大会日（源真实日期）"
        assert rows[0]["event_key"] == cb_event_key("113046", "下修", "2026-10-14")
        assert rows[0]["last_trade_date"] is None and rows[0]["count_text"] is None

    def test_old_history_records_outside_window_are_not_admitted(self, tmp_path):
        """逐代码查询返回全量历史：只收窗口内的（历史条目一次性重放不入清单）。"""
        db = tmp_path / "events.db"
        client = _FakeClient(adj={"113046": [self._adj("2026-10-14"), self._adj("2024-05-21")]})
        summary = sync_cb_adj_events(
            db_path=db, client=client, codes=["113046"], as_of="2026-10-16"
        )
        assert summary["fetched"] == 2 and summary["upserted"] == 1
        assert [row["announce_date"] for row in list_cb_events(db_path=db)] == ["2026-10-14"]

    def test_no_codes_queries_nothing(self, tmp_path):
        db = tmp_path / "events.db"
        client = _FakeClient()
        summary = sync_cb_adj_events(db_path=db, client=client, codes=[], as_of="2026-10-16")
        assert summary == {"queried": 0, "fetched": 0, "upserted": 0, "failed_codes": []}
        assert client.adj_queried == []
        assert not db.exists()

    def test_per_code_failure_recorded_without_raising(self, tmp_path):
        """逐代码接口：单代码失败如实记 failed_codes（不阻断、不落伪行）。"""
        db = tmp_path / "events.db"
        client = _FakeClient(fail_sources={"cb_adj"})
        summary = sync_cb_adj_events(
            db_path=db, client=client, codes=["113046", "110076"], as_of="2026-10-16"
        )
        assert summary["failed_codes"] == ["113046", "110076"]
        assert summary["upserted"] == 0
        assert not db.exists()

    def test_rerun_is_idempotent(self, tmp_path):
        db = tmp_path / "events.db"
        client = _FakeClient(adj={"113046": [self._adj()]})
        sync_cb_adj_events(db_path=db, client=client, codes=["113046"], as_of="2026-10-16")
        sync_cb_adj_events(db_path=db, client=client, codes=["113046"], as_of="2026-10-16")
        assert len(list_cb_events(db_path=db)) == 1

    def test_redeem_and_adj_events_coexist(self, tmp_path):
        db = tmp_path / "events.db"
        upsert_cb_events([_redeem_row_as_event()], db_path=db)
        client = _FakeClient(adj={"113046": [self._adj()]})
        sync_cb_adj_events(db_path=db, client=client, codes=["113046"], as_of="2026-10-16")
        assert {row["event_type"] for row in list_cb_events(db_path=db)} == {"强赎", "下修"}


def _redeem_row_as_event() -> dict:
    return {
        "event_key": cb_event_key("113046", "强赎", "2026-10-15"),
        "code": "113046",
        "event_type": "强赎",
        "announce_date": "2026-10-15",
        "trigger_price": 13.234,
        "trigger_ratio": 130.0,
        "count_text": "12/15 | 30",
        "last_trade_date": "2026-10-20",
        "remain_scale": 9.905,
        "raw_json": "{}",
    }
