"""观察清单构建测试（delta add-event-driven-lane T5）：outperform 入清单 / 去重。

口径权威：docs/evals/metrics.md §1.15 + spec「非 outperform 不进清单」。

add-convertible-bond-coverage 扩展：``source=cbb`` 分支（条款事件规则化入清单，
**零 LLM 调用**，风险要素四键随行；口径见 §1.17）。
"""

from __future__ import annotations

import json
from unittest.mock import patch

from finance_agent.outcome.events.cb_model import (
    cb_event_key,
    upsert_cb_events,
    upsert_cb_masters,
    upsert_cb_quotes,
)
from finance_agent.outcome.events.model import (
    forecast_key,
    insert_watchlist,
    list_watchlist,
    record_triage,
    upsert_forecasts,
)
from finance_agent.outcome.events.watchlist import build_watchlist

_NET = "归属于上市公司股东的净利润"

_CALENDAR = ["2026-10-15", "2026-10-16", "2026-10-19", "2026-10-20"]


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


def _cb_event(
    code: str = "113046",
    announce: str = "2026-10-15",
    *,
    count_text: str | None = "12/15 | 30",
    last_trade_date: str | None = "2026-10-20",
    redeem_status: str = "已公告强赎",
    remain_scale: float | None = 9.905,
) -> dict:
    return {
        "event_key": cb_event_key(code, "强赎", announce),
        "code": code,
        "event_type": "强赎",
        "announce_date": announce,
        "trigger_price": 13.234,
        "trigger_ratio": 130.0,
        "count_text": count_text,
        "last_trade_date": last_trade_date,
        "remain_scale": remain_scale,
        "raw_json": json.dumps({"强赎状态": redeem_status}, ensure_ascii=False),
    }


class TestBuildWatchlistCbb:
    """条款事件 → 清单（``source=cbb``）：规则化标注、零 LLM、四要素随行。"""

    def test_admits_events_with_rule_annotation_and_zero_llm(self, tmp_path):
        db = tmp_path / "events.db"
        upsert_cb_masters(
            [{"code": "113046", "name": "金田转债", "premium_rate": 1.63}], db_path=db
        )
        upsert_cb_quotes(
            [{"trade_date": "2026-10-16", "code": "113046", "close": 101.332}], db_path=db
        )
        upsert_cb_events(
            [
                _cb_event(),  # 已公告强赎（倒计时 2 个交易日）
                _cb_event(
                    "110076",
                    count_text="临近到期 <span>…",
                    last_trade_date=None,
                    redeem_status="",
                    remain_scale=None,
                ),  # 不可判定（缺要素）
            ],
            db_path=db,
        )
        # 零 LLM：条款事件准入不依赖分诊（LLM 入口一旦被调用即红）
        with patch(
            "finance_agent.outcome.events.triage.complete_text",
            side_effect=AssertionError("条款事件准入不得调用 LLM"),
        ):
            added = build_watchlist(db_path=db, as_of="2026-10-16", calendar=_CALENDAR)
        assert added == 2
        items = {item["forecast_key"]: item for item in list_watchlist(source="cbb", db_path=db)}
        assert set(items) == {
            cb_event_key("113046", "强赎", "2026-10-15"),
            cb_event_key("110076", "强赎", "2026-10-15"),
        }
        announced = items[cb_event_key("113046", "强赎", "2026-10-15")]
        assert announced["source"] == "cbb" and announced["status"] == "pending"
        assert announced["cb_code"] == "113046" and announced["cb_name"] == "金田转债"
        assert announced["cb_close"] == 101.332 and announced["cb_premium_rate"] == 1.63
        assert (
            announced["cb_remain_scale"] == 9.905 and announced["cb_announce_date"] == "2026-10-15"
        )
        note = json.loads(announced["note"])
        assert note["state"] == "announced" and note["call_countdown_days"] == 2
        assert "已公告强赎" in note["reason"]
        undetermined = items[cb_event_key("110076", "强赎", "2026-10-15")]
        note2 = json.loads(undetermined["note"])
        assert note2["state"] == "undetermined" and note2["call_countdown_days"] is None
        assert "last_trade_date" in note2["reason"], "不可判定须注明缺失字段"
        assert undetermined["verdict"] is None, "条款事件无 LLM 分诊结论"

    def test_counting_event_gets_counting_state(self, tmp_path):
        db = tmp_path / "events.db"
        upsert_cb_events([_cb_event(last_trade_date=None, redeem_status="")], db_path=db)
        assert build_watchlist(db_path=db, as_of="2026-10-16", calendar=_CALENDAR) == 1
        note = json.loads(list_watchlist(db_path=db)[0]["note"])
        assert note["state"] == "counting" and note["call_countdown_days"] is None
        assert "天计数 12/15" in note["reason"]

    def test_rerun_refreshes_countdown_without_touching_status(self, tmp_path):
        """每日构建刷新倒计时注记；status/session/created_at 零改写（去重语义保持）。"""
        db = tmp_path / "events.db"
        upsert_cb_events([_cb_event()], db_path=db)
        assert build_watchlist(db_path=db, as_of="2026-10-16", calendar=_CALENDAR) == 1
        assert build_watchlist(db_path=db, as_of="2026-10-16", calendar=_CALENDAR) == 0
        first = list_watchlist(db_path=db)[0]
        assert json.loads(first["note"])["call_countdown_days"] == 2

        # 次一交易日重跑：倒计时递减，条目仍单条、状态不动
        assert build_watchlist(db_path=db, as_of="2026-10-19", calendar=_CALENDAR) == 0
        items = list_watchlist(db_path=db)
        assert len(items) == 1
        assert json.loads(items[0]["note"])["call_countdown_days"] == 1
        assert items[0]["status"] == "pending" and items[0]["created_at"] == first["created_at"]

    def test_earnings_branch_untouched_by_cbb_rows(self, tmp_path):
        """两来源并存：各自入清单、互不干扰（业绩预告仍按 outperform 准入）。"""
        db = tmp_path / "events.db"
        _seed_triage(db, "600519", "2025-04-15", "outperform")
        upsert_cb_events([_cb_event()], db_path=db)
        added = build_watchlist(db_path=db, as_of="2026-10-16", calendar=_CALENDAR)
        assert added == 2
        assert len(list_watchlist(source="earnings", db_path=db)) == 1
        assert len(list_watchlist(source="cbb", db_path=db)) == 1

    def test_missing_calendar_keeps_state_with_null_countdown(self, tmp_path):
        """日历缺失 → 状态照判、倒计时 null + note（不猜自然日）。"""
        db = tmp_path / "events.db"
        upsert_cb_events([_cb_event()], db_path=db)
        build_watchlist(db_path=db, as_of="2026-10-16")
        note = json.loads(list_watchlist(db_path=db)[0]["note"])
        assert note["state"] == "announced" and note["call_countdown_days"] is None
        assert "交易日历" in note["reason"]

    def test_corrupt_raw_json_is_undetermined_not_guessed(self, tmp_path):
        """源行留痕损坏 → 状态不可判定（写明原因），不猜「未公告」也不炸批次。"""
        db = tmp_path / "events.db"
        broken = _cb_event()
        broken["raw_json"] = "{坏"
        upsert_cb_events([broken], db_path=db)
        assert build_watchlist(db_path=db, as_of="2026-10-16", calendar=_CALENDAR) == 1
        note = json.loads(list_watchlist(db_path=db)[0]["note"])
        assert note["state"] == "undetermined" and note["call_countdown_days"] is None
        assert "损坏" in note["reason"]

    def test_adjustment_event_gets_rule_reason_from_raw(self, tmp_path):
        """下修事件（无强赎倒计时）：理由取源行要素（转股价前后/生效日），四键其余如实 null。"""
        db = tmp_path / "events.db"
        upsert_cb_events(
            [
                {
                    "event_key": cb_event_key("113046", "下修", "2026-10-14"),
                    "code": "113046",
                    "event_type": "下修",
                    "announce_date": "2026-10-14",
                    "trigger_price": None,
                    "trigger_ratio": None,
                    "count_text": None,
                    "last_trade_date": None,
                    "remain_scale": None,
                    "raw_json": json.dumps(
                        {
                            "下修前转股价": 12.0,
                            "下修后转股价": 10.18,
                            "新转股价生效日期": "2026-10-16",
                        },
                        ensure_ascii=False,
                    ),
                }
            ],
            db_path=db,
        )
        assert build_watchlist(db_path=db, as_of="2026-10-16", calendar=_CALENDAR) == 1
        note = json.loads(list_watchlist(db_path=db)[0]["note"])
        assert note["state"] is None and note["call_countdown_days"] is None
        assert "12.0" in note["reason"] and "10.18" in note["reason"]
