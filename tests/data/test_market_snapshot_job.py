"""TDD tests for add-market-snapshot 取数包装（akshare_client.py T5）+ 日批编排（T6）。

T5：覆盖 4 个包装 ``fetch_market_spot`` / ``fetch_trade_calendar`` /
``fetch_listing_info`` / ``fetch_symbol_history_raw_qfq``（日批与回填的取数入口），
全部 ``@patch("finance_agent.data.akshare_client.ak")``，禁止真实网络。

T6：``run_market_snapshot_batch`` 编排（日历守卫 → spot → rebase → 合成 → 写库 →
meta），用 ``FakeSnapshotClient`` 假实现注入四个取数方法，同样零网络。
"""

from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from finance_agent.data.akshare_client import AKShareClient
from finance_agent.data.snapshot import (
    SnapshotError,
    get_all_meta,
    get_security_master,
    read_universe,
    set_meta,
    set_meta_many,
    upsert_security_master,
    write_snapshot_rows,
)
from finance_agent.data.snapshot_job import (
    MARKET_SNAPSHOT_SCHEDULE,
    run_market_snapshot_batch,
)


@pytest.fixture
def client():
    return AKShareClient()


class TestFetchMarketSpot:
    @patch("finance_agent.data.akshare_client.ak")
    def test_returns_frame_unchanged_when_code_column_present(self, mock_ak, client):
        """列映射由 build_snapshot_rows 负责：包装原样透传（不重塑列不丢列）。"""
        spot = pd.DataFrame(
            {
                "代码": ["600519", "000001"],
                "名称": ["贵州茅台", "平安银行"],
                "最新价": [1700.0, 10.5],
                "涨跌幅": [1.2, -2.5],
                "成交量": [10000.0, 200000.0],
                "成交额": [1.7e7, 2.1e6],
                "今开": [1680.0, 10.8],
                "最高": [1710.0, 10.9],
                "最低": [1675.0, 10.4],
                "昨收": [1679.84, 10.77],
            }
        )
        mock_ak.stock_zh_a_spot_em.return_value = spot

        out = client.fetch_market_spot()

        assert out is spot
        mock_ak.stock_zh_a_spot_em.assert_called_once()

    @patch("finance_agent.data.akshare_client.ak")
    def test_missing_code_column_returns_none(self, mock_ak, client):
        """缺「代码」列的变体（维护窗口/接口漂移）→ None，不把坏表传给日批。"""
        mock_ak.stock_zh_a_spot_em.return_value = pd.DataFrame({"名称": ["贵州茅台"]})

        assert client.fetch_market_spot() is None

    @patch("finance_agent.data.akshare_client.ak")
    def test_empty_or_all_attempts_failed_returns_none(self, mock_ak, client):
        mock_ak.stock_zh_a_spot_em.return_value = pd.DataFrame()
        assert client.fetch_market_spot() is None

        mock_ak.stock_zh_a_spot_em.return_value = None  # _call_ak 全部重试失败
        assert client.fetch_market_spot() is None


class TestFetchTradeCalendar:
    @patch("finance_agent.data.akshare_client.ak")
    def test_normalizes_mixed_date_types_dedups_and_sorts(self, mock_ak, client):
        """trade_date 列可能是 date/datetime/str 混合 → 统一 YYYY-MM-DD 升序去重。"""
        mock_ak.tool_trade_date_hist_sina.return_value = pd.DataFrame(
            {
                "trade_date": [
                    datetime(2026, 10, 13, 0, 0),
                    pd.NaT,
                    "20261009",
                    date(2026, 10, 8),
                    "2026-10-12",
                    date(2026, 10, 12),
                ]
            }
        )

        out = client.fetch_trade_calendar()

        assert out == ["2026-10-08", "2026-10-09", "2026-10-12", "2026-10-13"]

    @patch("finance_agent.data.akshare_client.ak")
    def test_missing_column_or_empty_or_failed_returns_none(self, mock_ak, client):
        mock_ak.tool_trade_date_hist_sina.return_value = pd.DataFrame({"日期": ["2026-10-12"]})
        assert client.fetch_trade_calendar() is None  # 列名漂移

        mock_ak.tool_trade_date_hist_sina.return_value = pd.DataFrame({"trade_date": []})
        assert client.fetch_trade_calendar() is None  # 空表

        mock_ak.tool_trade_date_hist_sina.return_value = None
        assert client.fetch_trade_calendar() is None  # 全部重试失败

    @patch("finance_agent.data.akshare_client.ak")
    def test_all_values_unparseable_returns_none(self, mock_ak, client):
        """全为缺失/不可解析值 → None（宁可日批抛错，不写空日历当权威）。"""
        mock_ak.tool_trade_date_hist_sina.return_value = pd.DataFrame(
            {"trade_date": [pd.NaT, None, "not-a-date"]}
        )

        assert client.fetch_trade_calendar() is None


class TestFetchListingInfo:
    @patch("finance_agent.data.akshare_client.ak")
    def test_merges_partial_sources_and_normalizes(self, mock_ak, client):
        """沪市板内变体/深市/北交所逐个 try：单源失败不拖垮其余；代码/日期归一。"""
        sh = pd.DataFrame(
            {
                "证券代码": ["600519"],
                "证券简称": ["贵州茅台"],
                "上市日期": ["20010827"],  # 8 位串 → YYYY-MM-DD
            }
        )
        mock_ak.stock_info_sh_name_code.side_effect = [
            sh,
            ConnectionRefusedError(111, "Connection refused"),  # 单板连接不可达（跳重试）
            None,  # 单板空/失败
        ]
        mock_ak.stock_info_sz_name_code.return_value = pd.DataFrame(
            {
                "A股代码": [1.0],  # float → 6 位字符串（000001）
                "A股简称": ["平安银行"],
                "A股上市日期": [date(1991, 4, 3)],
            }
        )
        mock_ak.stock_info_bj_name_code.return_value = pd.DataFrame(
            {
                "证券代码": ["830799"],
                "证券简称": ["艾融软件"],
                "上市日期": [pd.NaT],  # 缺失 → None，不编造
            }
        )

        rows = client.fetch_listing_info()

        assert rows == [
            {"code": "600519", "name": "贵州茅台", "list_date": "2001-08-27"},
            {"code": "000001", "name": "平安银行", "list_date": "1991-04-03"},
            {"code": "830799", "name": "艾融软件", "list_date": None},
        ]
        assert [c.kwargs["symbol"] for c in mock_ak.stock_info_sh_name_code.call_args_list] == [
            "主板A股",
            "主板B股",
            "科创板",
        ]

    @patch("finance_agent.data.akshare_client.ak")
    def test_all_sources_fail_returns_none(self, mock_ak, client):
        mock_ak.stock_info_sh_name_code.return_value = None
        mock_ak.stock_info_sz_name_code.return_value = None
        mock_ak.stock_info_bj_name_code.return_value = None

        assert client.fetch_listing_info() is None

    @patch("finance_agent.data.akshare_client.ak")
    def test_frame_without_code_column_is_skipped(self, mock_ak, client):
        """缺代码列的表不可用（不产出行）→ 全源如此时 None。"""
        mock_ak.stock_info_sh_name_code.return_value = pd.DataFrame({"证券简称": ["某公司"]})
        mock_ak.stock_info_sz_name_code.return_value = None
        mock_ak.stock_info_bj_name_code.return_value = None

        assert client.fetch_listing_info() is None


class TestFetchSymbolHistoryRawQfq:
    @patch("finance_agent.data.akshare_client.ak")
    def test_two_calls_joined_on_date(self, mock_ak, client):
        """raw（adjust=""）+ qfq 两次调用按「日期」内连接；输出六列、按日期升序。"""
        raw = pd.DataFrame(
            {
                "日期": [date(2026, 10, 10), date(2026, 10, 9)],  # 乱序：出口必须排序
                "开盘": [10.2, 10.0],
                "收盘": [10.3, 10.1],
                "最高": [10.6, 10.5],
                "成交量": [1200.0, 1000.0],
                "成交额": [3_600_000.0, 3_000_000.0],
            }
        )
        qfq = pd.DataFrame(
            {
                "日期": [date(2026, 10, 9), date(2026, 10, 10), date(2026, 10, 12)],
                "开盘": [9.0, 9.2, 9.5],
                "收盘": [9.1, 9.3, 9.6],
                "成交量": [900.0, 1100.0, 800.0],
            }
        )
        mock_ak.stock_zh_a_hist.side_effect = [raw, qfq]

        out = client.fetch_symbol_history_raw_qfq("600519", "20261001", "20261012")

        assert out is not None
        assert list(out.columns) == ["日期", "开盘", "收盘_raw", "收盘_qfq", "成交量", "成交额"]
        assert list(out["日期"]) == [
            date(2026, 10, 9),
            date(2026, 10, 10),
        ]  # 10-12 仅 qfq 有：内连接剔除
        assert list(out["收盘_raw"]) == [10.1, 10.3]
        assert list(out["收盘_qfq"]) == [9.1, 9.3]
        assert list(out["成交量"]) == [1000.0, 1200.0]  # 成交量取 raw 口径
        assert list(out["成交额"]) == [3_000_000.0, 3_600_000.0]  # 成交额取 raw 口径（元）

        calls = mock_ak.stock_zh_a_hist.call_args_list
        assert [c.kwargs["adjust"] for c in calls] == ["", "qfq"]
        assert [c.kwargs["symbol"] for c in calls] == ["600519", "600519"]
        assert [c.kwargs["start_date"] for c in calls] == ["20261001", "20261001"]
        assert [c.kwargs["end_date"] for c in calls] == ["20261012", "20261012"]
        assert all(c.kwargs["period"] == "daily" for c in calls)

    @patch("finance_agent.data.akshare_client.ak")
    def test_second_call_failure_returns_none(self, mock_ak, client):
        raw = pd.DataFrame(
            {
                "日期": [date(2026, 10, 9)],
                "开盘": [10.0],
                "收盘": [10.1],
                "成交量": [1000.0],
            }
        )
        mock_ak.stock_zh_a_hist.side_effect = [raw, None]  # qfq 腿失败

        assert client.fetch_symbol_history_raw_qfq("600519", "20261001", "20261012") is None

    @patch("finance_agent.data.akshare_client.ak")
    def test_first_call_empty_returns_none_without_second_call(self, mock_ak, client):
        mock_ak.stock_zh_a_hist.return_value = pd.DataFrame()

        assert client.fetch_symbol_history_raw_qfq("600519", "20261001", "20261012") is None
        assert mock_ak.stock_zh_a_hist.call_count == 1  # raw 空即短路，省一次调用

    @patch("finance_agent.data.akshare_client.ak")
    def test_missing_required_columns_returns_none(self, mock_ak, client):
        mock_ak.stock_zh_a_hist.return_value = pd.DataFrame(
            {"日期": [date(2026, 10, 9)], "收盘": [10.1]}  # 缺 开盘/成交量
        )

        assert client.fetch_symbol_history_raw_qfq("600519", "20261001", "20261012") is None


# ── add-market-snapshot T6：日批编排 run_market_snapshot_batch ────────────────
#
# 全部经 FakeSnapshotClient 注入四个取数方法（零网络）；库路径一律显式 db_path。

_D0 = "2026-10-08"  # 周四
_D1 = "2026-10-09"  # 周五
_D2 = "2026-10-12"  # 周一（master 刷新条件用）
_D3 = "2026-10-13"  # 周二（默认批日）


class FakeSnapshotClient:
    """日批 stub 客户端：四个取数方法的假实现（不触碰 akshare）。"""

    def __init__(self, *, calendar=None, spot=None, listing=None):
        self.calendar = calendar
        self.spot = spot
        self.listing = listing
        self.calendar_calls = 0
        self.spot_calls = 0
        self.listing_calls = 0

    def fetch_trade_calendar(self):
        self.calendar_calls += 1
        return self.calendar

    def fetch_market_spot(self):
        self.spot_calls += 1
        return self.spot

    def fetch_listing_info(self):
        self.listing_calls += 1
        return self.listing

    def fetch_symbol_history_raw_qfq(self, code, start, end):  # 日批不调用；回填才用
        return None


def _spot_frame(*rows: dict) -> pd.DataFrame:
    """按 stock_zh_a_spot_em 原样列构造 spot（未给列补 NaN）。"""
    frame = pd.DataFrame(list(rows))
    for col in (
        "代码",
        "名称",
        "最新价",
        "涨跌幅",
        "成交量",
        "成交额",
        "今开",
        "最高",
        "最低",
        "昨收",
    ):
        if col not in frame.columns:
            frame[col] = float("nan")
    return frame


def _spot(close, prev_close, *, code: str = "600519", name: str = "贵州茅台") -> pd.DataFrame:
    return _spot_frame(
        {"代码": code, "名称": name, "最新价": close, "昨收": prev_close, "涨跌幅": 1.0}
    )


def _seed_day_row(db, trade_date: str, close: float) -> None:
    """种一条历史行（raw == qfq，D1 口径的「当前基准」）。"""
    write_snapshot_rows(
        trade_date,
        [
            {
                "trade_date": trade_date,
                "code": "600519",
                "name": "贵州茅台",
                "close_raw": close,
                "close_qfq": close,
                "is_suspended": 0,
            }
        ],
        db_path=db,
    )


def _day_rows(db, trade_date: str) -> dict[str, dict]:
    return {r["code"]: r for r in read_universe(trade_date, db_path=db)}


def _count_rows(db) -> int:
    conn = sqlite3.connect(db)
    try:
        return conn.execute("SELECT COUNT(*) FROM market_snapshot").fetchone()[0]
    finally:
        conn.close()


def _today_beijing() -> str:
    return datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat()


class TestMarketSnapshotScheduleConstant:
    def test_schedule_constant_is_1530_monfri(self):
        """T7 注册表消费的 cron 规格（逐字：mon-fri 15:30 Asia/Shanghai）。"""
        assert MARKET_SNAPSHOT_SCHEDULE == {
            "day_of_week": "mon-fri",
            "hour": 15,
            "minute": 30,
            "timezone": "Asia/Shanghai",
        }


class TestMarketSnapshotBatch:
    def test_batch_writes_rows_and_meta(self, tmp_path):
        """正常日：2 票 → 2 行；成功 meta（last_success_date/attempt/calendar）齐备。"""
        db = tmp_path / "snap.db"
        calendar = [_D1, _D2, _D3]
        client = FakeSnapshotClient(
            calendar=calendar,
            spot=_spot_frame(
                {"代码": "600519", "名称": "贵州茅台", "最新价": 10.5, "昨收": 10.0},
                {"代码": "000001", "名称": "平安银行", "最新价": 5.2, "昨收": 5.0},
            ),
            listing=[{"code": "600519", "name": "贵州茅台", "list_date": "2001-08-27"}],
        )

        out = run_market_snapshot_batch(_D3, client=client, db_path=db)

        assert out["trade_date"] == _D3
        assert out["written"] == 2
        assert out["rebased"] == 0
        assert out["suspended"] == 0
        assert out["calendar"] == "ok"
        assert out["master"] == "refreshed"
        assert {r["code"] for r in read_universe(_D3, db_path=db)} == {"600519", "000001"}
        meta = get_all_meta(db)
        assert meta["last_success_date"] == _D3
        assert "last_attempt_at" in meta
        assert json.loads(meta["trade_calendar"]) == calendar
        assert "last_error" not in meta and "last_error_date" not in meta

    def test_batch_success_clears_previous_error_meta(self, tmp_path):
        """成功路径清掉上次失败标记（否则 D5 因 last_error_date==今天 继续告警）。"""
        db = tmp_path / "snap.db"
        set_meta_many({"last_error": "old", "last_error_date": _D2}, db_path=db)
        client = FakeSnapshotClient(calendar=[_D3], spot=_spot(10.5, 10.0), listing=None)

        run_market_snapshot_batch(_D3, client=client, db_path=db)

        meta = get_all_meta(db)
        assert "last_error" not in meta and "last_error_date" not in meta
        assert meta["last_success_date"] == _D3

    def test_batch_idempotent_second_run(self, tmp_path):
        """同日重跑：行数不变、rebased=0、历史基准不被二次复权。

        重跑必须以「上一交易日」行为 rebase 基准（不得取当日已写行）——否则
        k = 昨收 / 当日收盘 ≠ 1，会把整段历史 qfq 错误地再乘一遍。
        """
        db = tmp_path / "snap.db"
        _seed_day_row(db, _D2, 10.0)
        client = FakeSnapshotClient(calendar=[_D2, _D3], spot=_spot(10.5, 10.0), listing=None)

        first = run_market_snapshot_batch(_D3, client=client, db_path=db)
        second = run_market_snapshot_batch(_D3, client=client, db_path=db)

        assert first["written"] == second["written"] == 1
        assert second["rebased"] == 0
        assert _count_rows(db) == 2
        assert _day_rows(db, _D2)["600519"]["close_qfq"] == 10.0  # 历史未被误乘
        assert _day_rows(db, _D3)["600519"]["close_qfq"] == 10.5

    def test_batch_dividend_rebase_end_to_end(self, tmp_path):
        """除权日：历史行 close_qfq ×k（raw 不动）；当日 close_qfq == close_raw。"""
        db = tmp_path / "snap.db"
        _seed_day_row(db, _D0, 10.0)
        client = FakeSnapshotClient(calendar=[_D0, _D1], spot=_spot(9.5, 9.0), listing=None)

        out = run_market_snapshot_batch(_D1, client=client, db_path=db)

        assert out["rebased"] == 1
        assert _day_rows(db, _D0)["600519"]["close_qfq"] == 9.0  # 10.0 × 0.9
        day1 = _day_rows(db, _D1)["600519"]
        assert day1["close_qfq"] == day1["close_raw"] == 9.5
        assert day1["prev_close"] == 9.0

    def test_batch_halted_exdiv_rebases_once(self, tmp_path):
        """回归（2026-10-13 评审）：停牌遇除权 → 复牌不得二次复权。

        Day0 raw=qfq=10；Day1 停牌且除权（spot 昨收 9.0）→ 历史 ×0.9、合成行
        qfq=9.0（承载复权后基准）；Day2 复牌（昨收 9.0、收 9.5）→ 基准已是 9.0，
        k == 1，Day0 不得再乘一次。
        """
        db = tmp_path / "snap.db"
        _seed_day_row(db, _D0, 10.0)

        halted = FakeSnapshotClient(
            calendar=[_D1, _D2],
            spot=_spot_frame(
                {"代码": "600519", "名称": "贵州茅台", "最新价": float("nan"), "昨收": 9.0}
            ),
            listing=None,
        )
        out1 = run_market_snapshot_batch(_D1, client=halted, db_path=db)

        assert out1["rebased"] == 1
        assert out1["suspended"] == 1
        assert _day_rows(db, _D0)["600519"]["close_qfq"] == 9.0
        day1 = _day_rows(db, _D1)["600519"]
        assert day1["is_suspended"] == 1
        assert day1["close_raw"] == 10.0
        assert day1["close_qfq"] == 9.0  # 合成行以复权后基准承价

        resumed = FakeSnapshotClient(calendar=[_D1, _D2], spot=_spot(9.5, 9.0), listing=None)
        out2 = run_market_snapshot_batch(_D2, client=resumed, db_path=db)

        assert out2["rebased"] == 0  # 基期取 close_qfq：k == 1，无第二次复权
        assert _day_rows(db, _D0)["600519"]["close_qfq"] == 9.0
        assert _day_rows(db, _D2)["600519"]["close_qfq"] == 9.5

    def test_batch_skips_non_trading_day_without_touching_meta(self, tmp_path):
        """D4：非交易日 → skipped 摘要，不拉 spot、不碰 meta、不写行。"""
        db = tmp_path / "snap.db"
        set_meta("last_success_date", _D1, db_path=db)
        client = FakeSnapshotClient(calendar=[_D2], spot=_spot(10.5, 10.0), listing=None)

        out = run_market_snapshot_batch(_D3, client=client, db_path=db)

        assert out == {
            "trade_date": _D3,
            "skipped": "non_trading_day",
            "written": 0,
            "rebased": 0,
            "suspended": 0,
        }
        assert client.spot_calls == 0  # 守卫先于拉取
        assert client.listing_calls == 0
        assert get_all_meta(db) == {"last_success_date": _D1}
        assert _count_rows(db) == 0

    def test_batch_calendar_unavailable_explicit_date_continues(self, tmp_path):
        """日历不可得但显式传入日期 → 继续跑，摘要标 calendar=unavailable。"""
        db = tmp_path / "snap.db"
        client = FakeSnapshotClient(calendar=None, spot=_spot(10.5, 10.0), listing=None)

        out = run_market_snapshot_batch(_D3, client=client, db_path=db)

        assert out["calendar"] == "unavailable"
        assert out["written"] == 1
        assert "trade_calendar" not in get_all_meta(db)  # 未拉到就不写空日历

    def test_batch_calendar_unavailable_without_explicit_date_raises(self, tmp_path):
        """未显式传入日期且日历不可得 → 抛错（宁可告警不可静默写错日期）。"""
        db = tmp_path / "snap.db"
        client = FakeSnapshotClient(calendar=None, spot=_spot(10.5, 10.0), listing=None)

        with pytest.raises(SnapshotError, match="交易日历不可得"):
            run_market_snapshot_batch(client=client, db_path=db)

        assert client.spot_calls == 0
        assert get_all_meta(db)["last_error_date"] == _today_beijing()

    def test_batch_failure_marks_meta_and_reraises(self, tmp_path):
        """spot 拉取失败 → 标记 last_error/last_error_date 后重抛（交 ops 记录重试）。"""
        db = tmp_path / "snap.db"
        client = FakeSnapshotClient(calendar=[_D3], spot=None, listing=None)

        with pytest.raises(SnapshotError, match="全市场行情拉取失败"):
            run_market_snapshot_batch(_D3, client=client, db_path=db)

        meta = get_all_meta(db)
        # 契约：失败标记取北京「今天」，与批参数 trade_date 无关（D5 按当日告警）
        assert meta["last_error_date"] == _today_beijing()
        assert meta["last_error"] == "SnapshotError: 全市场行情拉取失败"
        assert "last_attempt_at" in meta
        assert "last_success_date" not in meta

    def test_batch_suspended_merge(self, tmp_path):
        """D2：昨日 2 票、今日 spot 仅 1 票 → 缺票合成为停牌行（共 2 行）。"""
        db = tmp_path / "snap.db"
        write_snapshot_rows(
            _D2,
            [
                {
                    "trade_date": _D2,
                    "code": "600519",
                    "name": "贵州茅台",
                    "close_raw": 10.0,
                    "close_qfq": 10.0,
                    "is_suspended": 0,
                },
                {
                    "trade_date": _D2,
                    "code": "000001",
                    "name": "平安银行",
                    "close_raw": 5.0,
                    "close_qfq": 5.0,
                    "is_suspended": 0,
                },
            ],
            db_path=db,
        )
        client = FakeSnapshotClient(calendar=[_D2, _D3], spot=_spot(10.2, 10.0), listing=None)

        out = run_market_snapshot_batch(_D3, client=client, db_path=db)

        assert out["written"] == 2
        assert out["suspended"] == 1
        rows = _day_rows(db, _D3)
        assert rows["600519"]["is_suspended"] == 0
        assert rows["000001"]["is_suspended"] == 1
        assert rows["000001"]["close_raw"] == 5.0
        assert rows["000001"]["close_qfq"] == 5.0
        assert rows["000001"]["volume"] == 0.0
        assert rows["000001"]["pct_chg"] == 0.0

    def test_master_refresh_on_empty(self, tmp_path):
        """master 为空 → 拉名单并 upsert。"""
        db = tmp_path / "snap.db"
        client = FakeSnapshotClient(
            calendar=[_D3],
            spot=_spot(10.5, 10.0),
            listing=[{"code": "600519", "name": "贵州茅台", "list_date": "2001-08-27"}],
        )

        out = run_market_snapshot_batch(_D3, client=client, db_path=db)

        assert out["master"] == "refreshed"
        assert client.listing_calls == 1
        assert get_security_master(db)["600519"]["list_date"] == "2001-08-27"

    def test_master_refresh_skipped_when_master_exists_and_not_monday(self, tmp_path):
        db = tmp_path / "snap.db"
        upsert_security_master(
            [{"code": "600519", "name": "贵州茅台", "list_date": "2001-08-27"}], db_path=db
        )
        client = FakeSnapshotClient(
            calendar=[_D3],
            spot=_spot(10.5, 10.0),
            listing=[{"code": "600519", "name": "贵州茅台"}],
        )

        out = run_market_snapshot_batch(_D3, client=client, db_path=db)  # 周二

        assert out["master"] == "skipped"
        assert client.listing_calls == 0

    def test_master_refresh_on_monday_even_when_master_exists(self, tmp_path):
        """每周一强制刷新名单（新股/更名在周内入库）。"""
        db = tmp_path / "snap.db"
        upsert_security_master(
            [{"code": "600519", "name": "贵州茅台", "list_date": "2001-08-27"}], db_path=db
        )
        client = FakeSnapshotClient(
            calendar=[_D2],
            spot=_spot(10.5, 10.0),
            listing=[{"code": "000001", "name": "平安银行"}],
        )

        out = run_market_snapshot_batch(_D2, client=client, db_path=db)  # 周一

        assert out["master"] == "refreshed"
        assert client.listing_calls == 1

    def test_master_refresh_failure_keeps_batch_ok(self, tmp_path):
        """名单拉取失败只 WARN，日批本身仍成功落库。"""
        db = tmp_path / "snap.db"
        client = FakeSnapshotClient(calendar=[_D3], spot=_spot(10.5, 10.0), listing=None)

        out = run_market_snapshot_batch(_D3, client=client, db_path=db)

        assert out["master"] == "skipped"
        assert out["written"] == 1
        assert get_all_meta(db)["last_success_date"] == _D3
