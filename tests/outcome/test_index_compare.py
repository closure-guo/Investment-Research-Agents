"""add-index-performance-compare:指数收盘存储、日批同步、区间收益读数单测。"""

import pandas as pd
import pytest

from finance_agent.outcome.track_record.index_compare import (
    INDEX_COMPARE_UNIVERSE,
    build_index_compare,
    sync_index_closes,
)
from finance_agent.outcome.track_record.marking import run_daily_marking
from finance_agent.outcome.track_record.model import (
    init_track_record_tables,
    list_index_closes,
    upsert_equity_point,
    upsert_index_closes,
)


@pytest.fixture()
def db(tmp_path):
    db = tmp_path / "t.db"
    init_track_record_tables(db)
    return db


class TestIndexClosesStorage:
    def test_upsert_and_list(self, db):
        n = upsert_index_closes(
            [("000300", "2026-09-28", 4000.0), ("000300", "2026-09-29", 4040.0)], db_path=db
        )
        assert n == 2
        rows = list_index_closes("000300", db_path=db)
        assert [r["trade_date"] for r in rows] == ["2026-09-28", "2026-09-29"]
        assert rows[0]["close"] == pytest.approx(4000.0)

    def test_idempotent_replace(self, db):
        upsert_index_closes([("000300", "2026-09-28", 4000.0)], db_path=db)
        upsert_index_closes([("000300", "2026-09-28", 4010.0)], db_path=db)
        rows = list_index_closes("000300", db_path=db)
        assert len(rows) == 1 and rows[0]["close"] == pytest.approx(4010.0)

    def test_list_end_boundary_inclusive_and_empty(self, db):
        upsert_index_closes(
            [("000905", "2026-09-28", 6000.0), ("000905", "2026-10-09", 6600.0)], db_path=db
        )
        assert (
            list_index_closes("000905", end="2026-09-28", db_path=db)[0]["trade_date"]
            == "2026-09-28"
        )
        assert list_index_closes("000852", db_path=db) == []
        assert list_index_closes("000905", end="2026-01-01", db_path=db) == []


def _fake_client(
    closes_by_code: dict[str, list[tuple[str, float]]], fail_codes: set[str] | None = None
):
    """构造 AKShareClient 替身:fetch_index_kline 返回中文列名 DataFrame,失败码抛异常。"""
    fail_codes = fail_codes or set()

    class _C:
        def fetch_index_kline(self, index_code: str, days: int = 250):
            if index_code in fail_codes:
                raise RuntimeError(f"rate limited: {index_code}")
            rows = closes_by_code.get(index_code, [])
            return pd.DataFrame({"日期": [d for d, _ in rows], "收盘": [c for _, c in rows]})

    return _C()


class TestSyncIndexCloses:
    def test_universe_has_five_major_indices(self):
        assert [u["code"] for u in INDEX_COMPARE_UNIVERSE] == [
            "000001",
            "000300",
            "000905",
            "000852",
            "399006",
        ]
        assert all(u["name"] for u in INDEX_COMPARE_UNIVERSE)

    def test_sync_stores_all_and_reports_count(self, db):
        client = _fake_client(
            {
                "000300": [("2026-09-28", 4000.0), ("2026-09-29", 4040.0)],
                "000001": [("2026-09-28", 3000.0)],
                "000905": [("2026-09-28", 6000.0)],
                "000852": [("2026-09-28", 2500.0)],
                "399006": [("2026-09-28", 2000.0)],
            }
        )
        result = sync_index_closes(client=client, db_path=db)
        assert result["stored"] == 6 and result["failed"] == []
        assert len(list_index_closes("000300", db_path=db)) == 2

    def test_sync_single_failure_isolated(self, db):
        client = _fake_client(
            {
                "000300": [("2026-09-28", 4000.0)],
                "000001": [("2026-09-28", 3000.0)],
                "000852": [("2026-09-28", 2500.0)],
            },
            fail_codes={"000905", "399006"},
        )
        result = sync_index_closes(client=client, db_path=db)
        assert result["failed"] == ["000905", "399006"]
        assert result["stored"] >= 3  # 其余指数照常落库
        assert len(list_index_closes("000300", db_path=db)) == 1

    def test_sync_truncates_datetime64_dates(self, db):
        """新浪回退源返回 datetime64 日期;落库必须截断到 YYYY-MM-DD(Fix review finding)。"""
        df_dates = pd.DataFrame(
            {"日期": pd.to_datetime(["2026-09-28", "2026-09-29"]), "收盘": [4000.0, 4040.0]}
        )

        class _TsClient:
            def fetch_index_kline(self, index_code: str, days: int = 250):
                return df_dates if index_code == "000300" else pd.DataFrame()

        result = sync_index_closes(client=_TsClient(), db_path=db)
        assert result["failed"] == [
            "000001",
            "000905",
            "000852",
            "399006",
        ]  # 空行情=失败(修正后语义)
        rows = list_index_closes("000300", db_path=db)
        assert [r["trade_date"] for r in rows] == ["2026-09-28", "2026-09-29"]


class TestDailyMarkingIndexSync:
    def _seed_open_prediction(self, db):
        from finance_agent.outcome.track_record.model import insert_prediction

        insert_prediction(
            {
                "source_type": "live",
                "symbol": "600519.SH",
                "symbol_name": "贵州茅台",
                "direction": "long",
                "entry_price": 100.0,
                "horizon_days": 20,
                "confidence": 0.8,
                "rationale_snapshot": {"action": "buy"},
                "created_at": "2026-09-01T10:00:00",
            },
            db_path=db,
        )

    def test_marking_summary_has_index_keys_and_stores(self, db):
        self._seed_open_prediction(db)
        # 需给全宇宙 5 个指数都喂数据:空行情=失败(Task 2 定案语义),
        # 否则 index_failed 断言与「全部成功」的测试意图相悖
        client = _fake_client({u["code"]: [("2026-09-28", 4000.0)] for u in INDEX_COMPARE_UNIVERSE})
        result = run_daily_marking(client=client, db_path=db, kline_days=5)
        assert result["index_stored"] >= 1
        assert result["index_failed"] == []
        # 盯市三键不受指数落库影响(errors 仍为盯市自身口径)
        assert set(result) >= {"marked", "skipped", "errors", "index_stored", "index_failed"}

    def test_marking_index_sync_all_failed_still_ok(self, db):
        self._seed_open_prediction(db)
        client = _fake_client({}, fail_codes={u["code"] for u in INDEX_COMPARE_UNIVERSE})
        result = run_daily_marking(client=client, db_path=db, kline_days=5)
        assert result["index_stored"] == 0
        assert len(result["index_failed"]) == 5
        # 盯市主链路照常返回(不抛错、无额外 errors)
        assert "metrics_date" in result


def _seed_curve(db, points: list[tuple[str, float]]):
    for d, nav in points:
        upsert_equity_point(d, nav, benchmark_nav=1.0, db_path=db)


class TestBuildIndexCompare:
    def test_window_agent_return_and_beat(self, db):
        _seed_curve(db, [("2026-09-28", 1.0), ("2026-10-09", 1.05)])
        upsert_index_closes(
            [
                ("000300", "2026-09-26", 4000.0),  # 基期回退:窗口首日 09-28 无值 → 用 09-26
                ("000300", "2026-10-09", 4040.0),  # +1.0% → agent 5% 跑赢
                ("000905", "2026-09-28", 6000.0),  # +10% → agent 跑输
                ("000905", "2026-10-09", 6600.0),
            ],
            db_path=db,
        )
        out = build_index_compare("all", db_path=db)
        assert out["window"] == {"start": "2026-09-28", "end": "2026-10-09"}
        assert out["agent_return"] == pytest.approx(0.05)
        by = {i["code"]: i for i in out["indices"]}
        assert by["000300"]["return"] == pytest.approx(0.01)
        assert by["000300"]["beat"] is True
        assert by["000300"]["effective_start_date"] == "2026-09-26"
        assert by["000905"]["beat"] is False
        assert [i["code"] for i in out["indices"]] == [u["code"] for u in INDEX_COMPARE_UNIVERSE]

    def test_index_starting_mid_window_uses_earliest_in_window(self, db):
        _seed_curve(db, [("2026-09-28", 1.0), ("2026-10-09", 1.05)])
        upsert_index_closes(
            [("000852", "2026-10-01", 2500.0), ("000852", "2026-10-09", 2525.0)], db_path=db
        )
        out = build_index_compare("all", db_path=db)
        by = {i["code"]: i for i in out["indices"]}
        assert by["000852"]["effective_start_date"] == "2026-10-01"
        assert by["000852"]["return"] == pytest.approx(0.01)

    def test_index_all_missing_in_window_is_null(self, db):
        _seed_curve(db, [("2026-09-28", 1.0), ("2026-10-09", 1.05)])
        upsert_index_closes([("399006", "2026-12-01", 2000.0)], db_path=db)  # 全在窗口外
        out = build_index_compare("all", db_path=db)
        by = {i["code"]: i for i in out["indices"]}
        assert by["399006"]["return"] is None
        assert by["399006"]["beat"] is None
        assert by["399006"]["effective_start_date"] is None

    def test_curve_insufficient_points(self, db):
        _seed_curve(db, [("2026-09-28", 1.0)])
        out = build_index_compare("all", db_path=db)
        assert out["agent_return"] is None
        assert all(i["beat"] is None for i in out["indices"])
        assert out["window"]["start"] == "2026-09-28"

    def test_curve_empty(self, db):
        out = build_index_compare("all", db_path=db)
        assert out["window"] == {"start": None, "end": None}
        assert out["agent_return"] is None

    def test_span_window_truncates_to_actual_coverage(self, db):
        _seed_curve(db, [("2026-06-01", 1.0), ("2026-09-28", 1.02), ("2026-10-09", 1.05)])
        out = build_index_compare("1y", db_path=db)
        assert out["span"] == "1y"
        assert out["window"] == {"start": "2026-06-01", "end": "2026-10-09"}  # 1y 截断到实际覆盖

    def test_span_3m_cutoff(self, db):
        _seed_curve(db, [("2026-01-05", 1.0), ("2026-09-28", 1.10), ("2026-10-09", 1.05)])
        out = build_index_compare("3m", db_path=db)
        # 3m 截点 ≈ 2026-07-09 → 窗口从 09-28 起
        assert out["window"]["start"] == "2026-09-28"
        assert out["agent_return"] == pytest.approx(round(1.05 / 1.10 - 1, 6))


# ── add-track-record-caliber-disclosure：对比读数必须披露组合构成与样本量 ──


def _seed_pred(
    db,
    *,
    direction: str,
    created_at: str,
    status: str = "open",
    resolved_at: str | None = None,
) -> str:
    from finance_agent.outcome.track_record.model import insert_prediction, update_prediction_status

    pid = insert_prediction(
        {
            "source_type": "live",
            "symbol": "600000.SH",
            "symbol_name": "浦发银行",
            "direction": direction,
            "entry_price": 10.0,
            "horizon_days": 20,
            "confidence": 0.6,
            "rationale_snapshot": {"action": "buy" if direction == "long" else "sell"},
            "created_at": created_at,
        },
        db_path=db,
        status=status,
    )
    if resolved_at is not None:
        update_prediction_status(
            pid,
            {
                "status": "resolved_win",
                "resolved_at": resolved_at,
                "exit_price": 9.9,
                "raw_return": 0.01,
                "excess_return": None,
                "resolution_rule": "superseded",
            },
            db_path=db,
        )
    return pid


def _mark(db, pid: str, dates: list[str], cum_return: float = 0.0) -> None:
    from finance_agent.outcome.track_record.model import insert_daily_mark

    for d in dates:
        insert_daily_mark(pid, d, mark_price=10.0, cum_return=cum_return, db_path=db)


class TestCaliberDisclosure:
    """spec index-comparison「跑赢指数对比的口径披露」。"""

    def test_caliber_counts_directional_views_and_settled(self, db):
        _seed_curve(db, [("2026-09-28", 1.0), ("2026-09-30", 1.01), ("2026-10-09", 1.05)])
        s1 = _seed_pred(db, direction="short", created_at="2026-09-25T10:00:00")
        s2 = _seed_pred(db, direction="short", created_at="2026-09-26T10:00:00")
        long1 = _seed_pred(db, direction="long", created_at="2026-09-25T10:00:00")
        neutral1 = _seed_pred(db, direction="neutral", created_at="2026-09-25T10:00:00")
        dup1 = _seed_pred(
            db, direction="short", created_at="2026-09-26T10:00:00", status="duplicate_of_day"
        )
        win = _seed_pred(
            db, direction="short", created_at="2026-09-27T10:00:00", resolved_at="2026-10-01"
        )
        outside = _seed_pred(
            db, direction="short", created_at="2026-09-27T10:00:00", resolved_at="2026-10-20"
        )
        for pid in (s1, s2, long1, neutral1, dup1, win, outside):
            _mark(db, pid, ["2026-09-28", "2026-09-30"])

        cal = build_index_compare("all", db_path=db)["caliber"]
        assert cal["nav_points"] == 3
        # short: s1 s2 win outside → 4；long: 1；neutral 与 duplicate_of_day 不计
        assert cal["directional_views"] == 5
        assert cal["long_views"] == 1
        assert cal["short_views"] == 4
        # 仅 resolved_at 落在窗口 [09-28, 10-09] 内的 win 计入
        assert cal["settled_count"] == 1

    def test_caliber_empty_db_all_zero(self, db):
        cal = build_index_compare("all", db_path=db)["caliber"]
        assert cal == {
            "nav_points": 0,
            "directional_views": 0,
            "long_views": 0,
            "short_views": 0,
            "settled_count": 0,
        }

    def test_caliber_neutral_only_has_no_directional_views(self, db):
        _seed_curve(db, [("2026-09-28", 1.0), ("2026-10-09", 1.0)])
        neutral1 = _seed_pred(db, direction="neutral", created_at="2026-09-25T10:00:00")
        _mark(db, neutral1, ["2026-09-28", "2026-10-09"])

        cal = build_index_compare("all", db_path=db)["caliber"]
        assert (cal["directional_views"], cal["long_views"], cal["short_views"]) == (0, 0, 0)
        assert cal["nav_points"] == 2

    def test_caliber_does_not_change_existing_readings(self, db):
        """披露为纯追加：既有 agent_return / 指数读数不受影响。"""
        _seed_curve(db, [("2026-09-28", 1.0), ("2026-10-09", 1.05)])
        upsert_index_closes(
            [("000300", "2026-09-28", 4000.0), ("000300", "2026-10-09", 4040.0)], db_path=db
        )
        short1 = _seed_pred(db, direction="short", created_at="2026-09-25T10:00:00")
        _mark(db, short1, ["2026-09-28", "2026-10-09"])

        out = build_index_compare("all", db_path=db)
        assert out["agent_return"] == pytest.approx(0.05)
        assert out["window"] == {"start": "2026-09-28", "end": "2026-10-09"}
        assert {i["code"]: i["beat"] for i in out["indices"]}["000300"] is True
