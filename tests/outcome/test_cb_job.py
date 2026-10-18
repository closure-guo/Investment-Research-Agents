"""可转债日批测试（delta add-convertible-bond-coverage T3）：编排 / 三态 / 幂等。

断言面（brief T5）：
- 正常态：标的 → 行情 → 强赎（+ 变化触发的下修）→ 观察清单一步编排（零 LLM）；
- 接口失败态：核心源（标的/强赎）失败 → 成功部分先落库、随后上抛留痕
  （MUST NOT 以零条新增冒充成功）；次要源（行情/下修逐代码）失败 → 摘要留痕不阻断；
- 重复运行态：同日重跑零重复（主表/行情/事件/清单各自唯一键幂等）；
- 非交易日：行情跳过（日历判）并在摘要留痕，不进空数据冒充。

口径权威：docs/evals/metrics.md §1.17。零网络：全部 fake client。
TDD：先红后绿——实现见 outcome/events/job.py::run_cb_daily。
"""

from __future__ import annotations

import logging
from unittest.mock import patch

import pytest

from finance_agent.outcome.events import job as events_job
from finance_agent.outcome.events.cb_model import list_cb_events, list_cb_masters
from finance_agent.outcome.events.cb_sync import sync_cb_master
from finance_agent.outcome.events.job import run_cb_daily
from finance_agent.outcome.events.model import list_watchlist

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
        self.masters = masters if masters is not None else [_master_row()]
        self.quotes = (
            quotes
            if quotes is not None
            else [{"code": "113046", "close": 101.332, "amount": 95_687_931.0}]
        )
        self.redeem = redeem if redeem is not None else [_redeem_row()]
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


@pytest.fixture(autouse=True)
def _stub_calendar(monkeypatch):
    """交易日历经快照库读取：桩为固定日历（零网络/零快照依赖）。"""
    monkeypatch.setattr(events_job, "read_snapshot_calendar", lambda *a, **k: _CALENDAR)


class TestCbDailyHappyPath:
    def test_orchestrates_syncs_and_builds_watchlist(self, tmp_path):
        db = tmp_path / "events.db"
        client = _FakeClient()
        with patch(
            "finance_agent.outcome.events.triage.complete_text",
            side_effect=AssertionError("转债日批不得调用 LLM"),
        ):
            summary = run_cb_daily(db_path=db, client=client, as_of="2026-10-16")
        assert summary["as_of"] == "2026-10-16"
        assert summary["master"]["upserted"] == 1
        assert summary["quotes"]["upserted"] == 1 and summary["quotes"]["skipped_reason"] is None
        assert summary["events"]["upserted"] == 1
        assert summary["watchlist_added"] == 1
        assert summary["failed_core_sources"] == []
        assert summary["calendar_available"] is True
        assert len(list_cb_masters(db_path=db)) == 1
        items = list_watchlist(source="cbb", db_path=db)
        assert len(items) == 1 and items[0]["note"] is not None

    def test_rerun_is_idempotent(self, tmp_path):
        db = tmp_path / "events.db"
        client = _FakeClient()
        first = run_cb_daily(db_path=db, client=client, as_of="2026-10-16")
        second = run_cb_daily(db_path=db, client=client, as_of="2026-10-16")
        assert first["watchlist_added"] == 1 and second["watchlist_added"] == 0
        assert len(list_cb_masters(db_path=db)) == 1
        assert len(list_cb_events(db_path=db)) == 1
        assert len(list_watchlist(source="cbb", db_path=db)) == 1

    def test_convert_price_change_triggers_bounded_adj_query(self, tmp_path):
        """转股价变化 → 仅对变化代码查下修（有界接入），命中即入清单。"""
        db = tmp_path / "events.db"
        sync_cb_master(db_path=db, client=_FakeClient())
        client = _FakeClient(
            masters=[_master_row(convert_price=9.9)],
            adj={
                "113046": [
                    {
                        "code": "113046",
                        "name": "金田转债",
                        "meeting_date": "2026-10-14",
                        "convert_price_before": 12.0,
                        "convert_price_after": 10.18,
                        "effective_date": "2026-10-16",
                        "raw_json": '{"下修前转股价": 12.0, "下修后转股价": 10.18}',
                    }
                ]
            },
        )
        summary = run_cb_daily(db_path=db, client=client, as_of="2026-10-16")
        assert summary["master"]["convert_price_changed"] == ["113046"]
        assert client.adj_queried == ["113046"]
        assert summary["adj"]["upserted"] == 1
        events = list_cb_events(db_path=db)
        assert {row["event_type"] for row in events} == {"强赎", "下修"}
        assert summary["watchlist_added"] == 2

    def test_unchanged_prices_query_no_adj_codes(self, tmp_path):
        db = tmp_path / "events.db"
        client = _FakeClient()
        run_cb_daily(db_path=db, client=client, as_of="2026-10-16")
        client2 = _FakeClient()
        summary = run_cb_daily(db_path=db, client=client2, as_of="2026-10-17")
        assert summary["master"]["convert_price_changed"] == []
        assert client2.adj_queried == [], "无变化 → 零下修查询（日调用量有界）"


class TestCbDailyFailures:
    def test_core_source_failure_raises_after_partial_write(self, tmp_path, caplog):
        """强赎源失败：成功部分（标的/行情）先落库，随后上抛留痕、失败源不落行。"""
        db = tmp_path / "events.db"
        client = _FakeClient(fail_sources={"cb_redeem"})
        with (
            caplog.at_level(logging.ERROR),
            pytest.raises(RuntimeError, match="cb_redeem"),
        ):
            run_cb_daily(db_path=db, client=client, as_of="2026-10-16")
        assert len(list_cb_masters(db_path=db)) == 1, "已成功源照常落库（不丢数据）"
        assert list_cb_events(db_path=db) == [], "失败源不落行（MUST NOT 冒充成功）"
        assert list_watchlist(source="cbb", db_path=db) == []

    def test_master_source_failure_also_raises(self, tmp_path):
        db = tmp_path / "events.db"
        with pytest.raises(RuntimeError, match="cb_master"):
            run_cb_daily(
                db_path=db, client=_FakeClient(fail_sources={"cb_master"}), as_of="2026-10-16"
            )

    def test_secondary_source_failure_is_recorded_not_raised(self, tmp_path):
        """次要源（行情/逐代码下修）失败：摘要留痕、不阻断（缺值如实 null）。"""
        db = tmp_path / "events.db"
        client = _FakeClient(fail_sources={"cb_quotes"})
        summary = run_cb_daily(db_path=db, client=client, as_of="2026-10-16")
        assert summary["failed_core_sources"] == []
        assert summary["failed_secondary_sources"] == ["cb_quotes"]
        assert summary["quotes"]["upserted"] == 0
        assert len(list_watchlist(source="cbb", db_path=db)) == 1

    def test_non_trading_day_skips_quotes_with_reason(self, tmp_path):
        db = tmp_path / "events.db"
        client = _FakeClient()
        summary = run_cb_daily(db_path=db, client=client, as_of="2026-10-18")  # 周日
        assert summary["quotes"]["skipped_reason"] == "non_trading_day"
        assert summary["quotes"]["upserted"] == 0
        assert summary["master"]["upserted"] == 1, "标的/事件不受非交易日影响"
