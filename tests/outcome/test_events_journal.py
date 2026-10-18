"""事件记账与结算测试（delta add-event-driven-lane T6）：T+1 开盘 / 顺延留痕 / 到期结算。

口径权威：docs/evals/metrics.md §1.15 + 变更 design.md「记账口径」：
- 入场 = 公告日次一交易日**开盘价**（kline ``adjust=SETTLEMENT_ADJUST``，与执行链同口径）；
- 不可成交（停牌 / 一字板）顺延至下一可成交日并留痕 ``note=deferred:<原因>@<日期>``；
- horizon 默认 5 个交易日；结算 = 入场日之后第 horizon 个交易日收盘价 / 入场价 − 1；
- 参考价缺失 → 保持 open + 留痕，MUST NOT 折算 0。
"""

from __future__ import annotations

import pytest

from finance_agent.data.snapshot import write_snapshot_rows
from finance_agent.outcome.events.journal import (
    DEFAULT_HORIZON_DAYS,
    entry_candidates,
    settle_due,
)
from finance_agent.outcome.events.model import (
    forecast_key,
    insert_journal,
    list_open_journal,
    list_settled_journal,
    record_triage,
    upsert_forecasts,
)

_NET = "归属于上市公司股东的净利润"
_CODE = "600519"


def _forecast(announce: str = "2025-04-15", *, verdict: str = "outperform") -> dict:
    return {
        "code": _CODE,
        "name": "贵州茅台",
        "report_period": "2025-03-31",
        "source_record_id": f"{_NET}|{announce}",
        "forecast_type": "预增",
        "change_low": 60.0,
        "change_high": 90.0,
        "announce_date": announce,
        "available_at": announce,
        "source": "eastmoney",
        "__verdict": verdict,
    }


def _seed_forecast(db, announce: str = "2025-04-15", *, verdict: str = "outperform") -> None:
    row = _forecast(announce, verdict=verdict)
    row.pop("__verdict")
    upsert_forecasts([row], db_path=db)
    record_triage(
        forecast_key(row),
        verdict=verdict,
        reason=f"{verdict} 理由",
        evidence=[{"field": "forecast_type", "value": "预增"}],
        model="m",
        prompt_fingerprint="event_triage@1",
        db_path=db,
    )


class _FakeKlineClient:
    """kline client 桩：按代码给 DataFrame（列与真实 client 一致）；记录调用口径。"""

    def __init__(self, frames: dict[str, list[dict]]) -> None:
        self._frames = frames
        self.calls: list[dict] = []

    def fetch_kline(self, code: str, days: int = 250, *, adjust: str = "qfq"):
        import pandas as pd

        self.calls.append({"code": code, "days": days, "adjust": adjust})
        rows = self._frames.get(code)
        if rows is None:
            return pd.DataFrame()
        return pd.DataFrame(rows).tail(days)


def _rows(days_opens: list[tuple[str, float, float]]) -> list[dict]:
    """``[(日期, 开盘, 收盘)]`` → kline 行（高低价围绕开收盘，非一字板）。"""
    return [
        {
            "日期": day,
            "开盘": open_,
            "收盘": close,
            "最高": max(open_, close) + 1.0,
            "最低": min(open_, close) - 1.0,
        }
        for day, open_, close in days_opens
    ]


def _snapshot(
    db, day: str, *, suspended: int = 0, up_sealed: int = 0, down_sealed: int = 0
) -> None:
    write_snapshot_rows(
        day,
        [
            {
                "code": _CODE,
                "open": 100.0,
                "high": 101.0,
                "low": 99.0,
                "close_raw": 100.5,
                "prev_close": 99.5,
                "is_suspended": suspended,
                "limit_up_sealed": up_sealed,
                "limit_down_sealed": down_sealed,
            }
        ],
        db_path=db,
    )


class TestEntryCandidates:
    def test_entry_at_next_trading_day_open(self, tmp_path) -> None:
        db = tmp_path / "events.db"
        snap = tmp_path / "snap.db"
        _seed_forecast(db)
        _snapshot(snap, "2025-04-16")
        client = _FakeKlineClient(
            {
                _CODE: _rows(
                    [
                        ("2025-04-14", 99.0, 99.5),
                        ("2025-04-15", 99.5, 100.0),
                        ("2025-04-16", 100.0, 101.0),
                        ("2025-04-17", 101.0, 102.0),
                    ]
                )
            }
        )
        summary = entry_candidates(
            as_of="2025-04-17", kline_client=client, snapshot_db_path=snap, db_path=db
        )
        assert summary["entered"] == 1
        rows = list_open_journal(db_path=db)
        assert len(rows) == 1
        row = rows[0]
        assert row["entry_date"] == "2025-04-16"  # 公告日次一交易日
        assert row["entry_price"] == 100.0  # 该日开盘价
        assert row["horizon_days"] == DEFAULT_HORIZON_DAYS == 5
        assert row["note"] is None
        assert client.calls[0]["adjust"] == "hfq"  # 与执行链同源口径（SETTLEMENT_ADJUST）

    def test_entry_defers_when_suspended_with_note(self, tmp_path) -> None:
        db = tmp_path / "events.db"
        snap = tmp_path / "snap.db"
        _seed_forecast(db)
        _snapshot(snap, "2025-04-16", suspended=1)
        _snapshot(snap, "2025-04-17")
        client = _FakeKlineClient(
            {
                _CODE: _rows(
                    [
                        ("2025-04-16", 100.0, 101.0),
                        ("2025-04-17", 101.5, 102.0),
                        ("2025-04-18", 102.0, 103.0),
                    ]
                )
            }
        )
        summary = entry_candidates(
            as_of="2025-04-18", kline_client=client, snapshot_db_path=snap, db_path=db
        )
        assert summary["entered"] == 1 and summary["deferred"] == 1
        row = list_open_journal(db_path=db)[0]
        assert row["entry_date"] == "2025-04-17"
        assert row["entry_price"] == 101.5
        assert row["note"] == "deferred:suspended@2025-04-16"

    def test_entry_defers_on_limit_up_sealed_only(self, tmp_path) -> None:
        """一字板顺延沿用执行链**买入侧**门禁语义：涨停板挡买、跌停板不挡买。"""
        db = tmp_path / "events.db"
        snap = tmp_path / "snap.db"
        _seed_forecast(db)
        _snapshot(snap, "2025-04-16", up_sealed=1)
        _snapshot(snap, "2025-04-17", down_sealed=1)  # 跌停不挡买
        client = _FakeKlineClient(
            {
                _CODE: _rows(
                    [
                        ("2025-04-16", 100.0, 110.0),
                        ("2025-04-17", 101.0, 90.0),
                        ("2025-04-18", 102.0, 103.0),
                    ]
                )
            }
        )
        entry_candidates(as_of="2025-04-18", kline_client=client, snapshot_db_path=snap, db_path=db)
        row = list_open_journal(db_path=db)[0]
        assert row["entry_date"] == "2025-04-17"
        assert row["note"] == "deferred:limit_up_sealed@2025-04-16"

    def test_entry_falls_back_to_kline_seal_when_snapshot_missing(self, tmp_path) -> None:
        """快照行缺失（历史回填场景）→ 按 kline OHLC 判一字板（口径注明，可自洽降级）。"""
        db = tmp_path / "events.db"
        snap = tmp_path / "snap.db"
        _seed_forecast(db)
        client = _FakeKlineClient(
            {
                _CODE: [
                    # 2025-04-16 一字涨停板（开=高=低=收，前收 90 → +11.1% ≥ 4.8%）
                    {"日期": "2025-04-15", "开盘": 90.0, "收盘": 90.0, "最高": 90.0, "最低": 90.0},
                    {
                        "日期": "2025-04-16",
                        "开盘": 100.0,
                        "收盘": 100.0,
                        "最高": 100.0,
                        "最低": 100.0,
                    },
                    {
                        "日期": "2025-04-17",
                        "开盘": 101.0,
                        "收盘": 102.0,
                        "最高": 103.0,
                        "最低": 100.0,
                    },
                ]
            }
        )
        summary = entry_candidates(
            as_of="2025-04-17", kline_client=client, snapshot_db_path=snap, db_path=db
        )
        assert summary["entered"] == 1
        row = list_open_journal(db_path=db)[0]
        assert row["entry_date"] == "2025-04-17"  # 一字板日顺延
        assert row["entry_price"] == 101.0
        assert row["note"] == "deferred:limit_up_sealed_ohlc@2025-04-16"

    def test_no_entry_before_next_trading_day(self, tmp_path) -> None:
        """公告当日（次一交易日未到）不入场：等下一交易日。"""
        db = tmp_path / "events.db"
        snap = tmp_path / "snap.db"
        _seed_forecast(db, "2025-04-15")
        _snapshot(snap, "2025-04-15")
        client = _FakeKlineClient({_CODE: _rows([("2025-04-15", 100.0, 101.0)])})
        summary = entry_candidates(
            as_of="2025-04-15", kline_client=client, snapshot_db_path=snap, db_path=db
        )
        assert summary["entered"] == 0 and summary["not_due"] == 1
        assert list_open_journal(db_path=db) == []

    def test_rerun_is_idempotent(self, tmp_path) -> None:
        db = tmp_path / "events.db"
        snap = tmp_path / "snap.db"
        _seed_forecast(db)
        _snapshot(snap, "2025-04-16")
        client = _FakeKlineClient({_CODE: _rows([("2025-04-16", 100.0, 101.0)])})
        entry_candidates(as_of="2025-04-16", kline_client=client, snapshot_db_path=snap, db_path=db)
        summary = entry_candidates(
            as_of="2025-04-16", kline_client=client, snapshot_db_path=snap, db_path=db
        )
        assert summary["entered"] == 0  # 重跑零重复
        assert len(list_open_journal(db_path=db)) == 1

    def test_non_outperform_not_entered(self, tmp_path) -> None:
        db = tmp_path / "events.db"
        snap = tmp_path / "snap.db"
        _seed_forecast(db, verdict="in_line")
        _snapshot(snap, "2025-04-16")
        client = _FakeKlineClient({_CODE: _rows([("2025-04-16", 100.0, 101.0)])})
        summary = entry_candidates(
            as_of="2025-04-16", kline_client=client, snapshot_db_path=snap, db_path=db
        )
        assert summary["entered"] == 0
        assert list_open_journal(db_path=db) == []


class TestSettleDue:
    _DAYS = [
        "2025-04-16",
        "2025-04-17",
        "2025-04-18",
        "2025-04-21",
        "2025-04-22",
        "2025-04-23",
        "2025-04-24",
    ]

    def _seed_open(self, db, *, entry_date: str = "2025-04-16", entry_price: float = 100.0) -> int:
        _seed_forecast(db, "2025-04-15")
        return insert_journal(
            forecast_key=forecast_key(_forecast("2025-04-15")),
            symbol=f"{_CODE}.SH",
            entry_date=entry_date,
            entry_price=entry_price,
            horizon_days=5,
            db_path=db,
        )

    def _kline(self, close_by_day: dict[str, float]) -> _FakeKlineClient:
        return _FakeKlineClient(
            {
                _CODE: [
                    {
                        "日期": day,
                        "开盘": close_by_day[day] - 1.0,
                        "收盘": close_by_day[day],
                        "最高": close_by_day[day] + 1.0,
                        "最低": close_by_day[day] - 2.0,
                    }
                    for day in sorted(close_by_day)
                ]
            }
        )

    def test_settles_at_horizon_close(self, tmp_path) -> None:
        db = tmp_path / "events.db"
        self._seed_open(db)
        # 结算日 = 入场日之后第 5 个交易日（2025-04-23）
        closes = {day: 100.0 + index for index, day in enumerate(self._DAYS)}
        client = self._kline(closes)
        summary = settle_due(
            as_of="2025-04-24", kline_client=client, calendar=self._DAYS, db_path=db
        )
        assert summary["settled"] == 1
        row = list_settled_journal(db_path=db)[0]
        assert row["settle_date"] == "2025-04-23"
        assert row["settle_price"] == pytest.approx(105.0)
        assert row["raw_return"] == pytest.approx(round(105.0 / 100.0 - 1.0, 6))
        assert list_open_journal(db_path=db) == []

    def test_not_due_before_horizon(self, tmp_path) -> None:
        db = tmp_path / "events.db"
        self._seed_open(db)
        client = self._kline(dict.fromkeys(self._DAYS[:2], 100.0))
        summary = settle_due(
            as_of="2025-04-17", kline_client=client, calendar=self._DAYS, db_path=db
        )
        assert summary["settled"] == 0 and summary["not_due"] == 1
        assert len(list_open_journal(db_path=db)) == 1

    def test_missing_price_keeps_open_with_note(self, tmp_path) -> None:
        """参考价缺失（K 线未覆盖结算日）→ 保持 open + 留痕，MUST NOT 折算 0。"""
        db = tmp_path / "events.db"
        self._seed_open(db)
        closes = {day: 100.0 + index for index, day in enumerate(self._DAYS[:3])}  # 缺结算日
        client = self._kline(closes)
        summary = settle_due(
            as_of="2025-04-24", kline_client=client, calendar=self._DAYS, db_path=db
        )
        assert summary["settled"] == 0
        row = list_open_journal(db_path=db)[0]
        assert row["status"] == "open"
        assert row["note"] and row["note"].startswith("settle_pending:")
        assert row["raw_return"] is None  # 不得以 0/估算值结算

    def test_kline_failure_keeps_open(self, tmp_path) -> None:
        db = tmp_path / "events.db"
        self._seed_open(db)
        client = _FakeKlineClient({})  # 全部取数失败（空表）
        summary = settle_due(
            as_of="2025-04-30", kline_client=client, calendar=self._DAYS, db_path=db
        )
        assert summary["settled"] == 0 and summary["errors"] >= 1
        assert list_open_journal(db_path=db)[0]["status"] == "open"

    def test_settles_on_resume_day_when_suspended_through_settle(self, tmp_path) -> None:
        """结算日停牌无行 → 递延至复牌首日收盘（行驱动顺延，同 settle.py 纪律）并留痕。"""
        db = tmp_path / "events.db"
        self._seed_open(db)
        closes = {"2025-04-16": 100.0, "2025-04-17": 101.0, "2025-04-24": 110.0}
        client = self._kline(closes)
        summary = settle_due(
            as_of="2025-04-24", kline_client=client, calendar=self._DAYS, db_path=db
        )
        assert summary["settled"] == 1 and summary["deferred"] == 1
        row = list_settled_journal(db_path=db)[0]
        assert row["settle_date"] == "2025-04-24"
        assert row["settle_price"] == pytest.approx(110.0)
        assert row["note"] and row["note"].startswith("deferred:")

    def test_settle_without_calendar_uses_symbol_kline_days(self, tmp_path) -> None:
        db = tmp_path / "events.db"
        self._seed_open(db)
        closes = {day: 100.0 + index for index, day in enumerate(self._DAYS)}
        client = self._kline(closes)
        summary = settle_due(as_of="2025-04-24", kline_client=client, calendar=None, db_path=db)
        assert summary["settled"] == 1
        assert list_settled_journal(db_path=db)[0]["settle_date"] == "2025-04-23"


def test_symbol_suffix_mapping_covers_bse_and_sz() -> None:
    """代码 → 库内 symbol 后缀：北交所 92/4/8 → .BJ（不误标 .SZ），6/9 → .SH。"""
    from finance_agent.outcome.events.journal import _symbol_for

    assert _symbol_for("600519") == "600519.SH"
    assert _symbol_for("920188") == "920188.BJ"
    assert _symbol_for("830799") == "830799.BJ"
    assert _symbol_for("000001") == "000001.SZ"
    assert _symbol_for("300750") == "300750.SZ"
