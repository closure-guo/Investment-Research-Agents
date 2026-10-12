"""TDD tests for add-market-snapshot 取数包装（akshare_client.py T5）。

覆盖 4 个包装：``fetch_market_spot`` / ``fetch_trade_calendar`` /
``fetch_listing_info`` / ``fetch_symbol_history_raw_qfq``（日批与回填的取数入口）。
全部 ``@patch("finance_agent.data.akshare_client.ak")``，禁止真实网络。
"""

from __future__ import annotations

from datetime import date, datetime
from unittest.mock import patch

import pandas as pd
import pytest

from finance_agent.data.akshare_client import AKShareClient


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
        """raw（adjust=""）+ qfq 两次调用按「日期」内连接；输出五列、按日期升序。"""
        raw = pd.DataFrame(
            {
                "日期": [date(2026, 10, 10), date(2026, 10, 9)],  # 乱序：出口必须排序
                "开盘": [10.2, 10.0],
                "收盘": [10.3, 10.1],
                "最高": [10.6, 10.5],
                "成交量": [1200.0, 1000.0],
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
        assert list(out.columns) == ["日期", "开盘", "收盘_raw", "收盘_qfq", "成交量"]
        assert list(out["日期"]) == [
            date(2026, 10, 9),
            date(2026, 10, 10),
        ]  # 10-12 仅 qfq 有：内连接剔除
        assert list(out["收盘_raw"]) == [10.1, 10.3]
        assert list(out["收盘_qfq"]) == [9.1, 9.3]
        assert list(out["成交量"]) == [1000.0, 1200.0]  # 成交量取 raw 口径

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
