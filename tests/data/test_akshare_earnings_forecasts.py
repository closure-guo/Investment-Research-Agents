"""业绩预告抓取测试（delta add-event-driven-lane T3）：映射/归一/失败空态。

口径权威：docs/evals/metrics.md §1.15 + 变更 design.md「数据源与字段」。
接口实测（本机 akshare 1.18.94）：``ak.stock_yjyg_em(date="YYYYMMDD")`` 返回列
``序号/股票代码/股票简称/预测指标/业绩变动/预测数值/业绩变动幅度/业绩变动原因/
预告类型/上年同期值/公告日期``；本文件全部用 fake DataFrame，零网络。
"""

from __future__ import annotations

import logging
from datetime import date
from unittest.mock import patch

import pandas as pd

from finance_agent.data.akshare_client import (
    AKShareClient,
    _default_report_period,
    _norm_report_period,
    _parse_change_range,
)

_LOGGER = "finance_agent.data.akshare_client"

_NET_PROFIT = "归属于上市公司股东的净利润"


def _fake_df(rows: list[dict]) -> pd.DataFrame:
    """按真实列形构造 fake DataFrame（缺失列补 NaN，模拟源列齐全）。"""
    base = {
        "序号": list(range(1, len(rows) + 1)),
        "股票代码": [r.get("code") for r in rows],
        "股票简称": [r.get("name") for r in rows],
        "预测指标": [r.get("indicator", _NET_PROFIT) for r in rows],
        "业绩变动": [r.get("text", "") for r in rows],
        "预测数值": [r.get("value") for r in rows],
        "业绩变动幅度": [r.get("magnitude") for r in rows],
        "业绩变动原因": [r.get("reason", "") for r in rows],
        "预告类型": [r.get("forecast_type", "预增") for r in rows],
        "上年同期值": [None] * len(rows),
        "公告日期": [r.get("announce_date", date(2025, 4, 15)) for r in rows],
    }
    return pd.DataFrame(base)


class TestNormHelpers:
    def test_report_period_normalization(self):
        assert _norm_report_period("20250331") == "2025-03-31"
        assert _norm_report_period("2025-03-31") == "2025-03-31"
        assert _norm_report_period(date(2025, 3, 31)) == "2025-03-31"
        assert _norm_report_period("垃圾") is None
        assert _norm_report_period(None) is None

    def test_default_report_period_is_latest_quarter_end(self):
        assert _default_report_period(today=date(2026, 10, 18)) == "2026-09-30"
        assert _default_report_period(today=date(2026, 1, 1)) == "2025-12-31"
        assert _default_report_period(today=date(2026, 3, 31)) == "2026-03-31"

    def test_parse_change_range_sign_and_order(self):
        """方向词定符号（下降/减少 → 取负），输出恒 low ≤ high。"""
        assert _parse_change_range("同比上年增长:46.6%至146.08%", None) == (46.6, 146.08)
        assert _parse_change_range("同比上年下降:62.56%至67.91%", None) == (-67.91, -62.56)
        assert _parse_change_range("同比上年变动:-4.58%至3.37%", None) == (-4.58, 3.37)
        assert _parse_change_range("同比上年下降:39%左右", None) == (-39.0, -39.0)
        assert _parse_change_range("预计盈利:2,800万元至3,200万元。", 5.0) == (5.0, 5.0)
        assert _parse_change_range("预计盈利，无幅度", None) == (None, None)


class TestFetchEarningsForecasts:
    def test_maps_and_normalizes_rows(self):
        """字段映射（含 available_at=公告日）与归一（6 位代码 / ISO 日期 / 区间）。"""
        df = _fake_df(
            [
                {
                    "code": 600519,
                    "name": "贵州茅台",
                    "magnitude": 15.06,
                    "text": "预计2025年1-3月归属于上市公司股东的净利润盈利,同比上年增长:11.57%至18.55%。",
                    "forecast_type": "略增",
                    "announce_date": date(2025, 4, 15),
                }
            ]
        )
        with patch("finance_agent.data.akshare_client.ak.stock_yjyg_em", return_value=df) as api:
            rows = AKShareClient().fetch_earnings_forecasts("2025-03-31")
        api.assert_called_once_with(date="20250331")
        assert len(rows) == 1
        row = rows[0]
        assert row["code"] == "600519"
        assert row["name"] == "贵州茅台"
        assert row["report_period"] == "2025-03-31"
        assert row["forecast_type"] == "略增"
        assert row["change_low"] == 11.57 and row["change_high"] == 18.55
        assert row["announce_date"] == "2025-04-15"
        assert row["available_at"] == "2025-04-15"
        assert row["source"] == "eastmoney"
        assert row["source_record_id"] == f"{_NET_PROFIT}|2025-04-15"

    def test_filters_non_net_profit_indicators(self):
        """多指标行只取归母净利润口径（源含营业收入/每股收益等行）。"""
        df = _fake_df(
            [
                {"code": "600519", "indicator": _NET_PROFIT, "magnitude": 10.0},
                {"code": "600519", "indicator": "营业收入", "magnitude": 30.0},
                {"code": "600519", "indicator": "扣除非经常性损益后的净利润", "magnitude": 20.0},
            ]
        )
        with patch("finance_agent.data.akshare_client.ak.stock_yjyg_em", return_value=df):
            rows = AKShareClient().fetch_earnings_forecasts("20250331")
        assert len(rows) == 1
        assert rows[0]["change_low"] == 10.0

    def test_skips_rows_without_code_or_announce_date(self):
        """代码/公告日缺失 → 不可落库（无稳定键或无可得时点），跳过并留痕。"""
        df = _fake_df(
            [
                {"code": "600519", "magnitude": 1.0},
                {"code": None, "magnitude": 2.0},
                {"code": "000001", "announce_date": None, "magnitude": 3.0},
            ]
        )
        with patch("finance_agent.data.akshare_client.ak.stock_yjyg_em", return_value=df):
            rows = AKShareClient().fetch_earnings_forecasts("20250331")
        assert [row["code"] for row in rows] == ["600519"]

    def test_records_source_seen_on_success(self):
        df = _fake_df([{"code": "600519", "magnitude": 1.0}])
        client = AKShareClient()
        with patch("finance_agent.data.akshare_client.ak.stock_yjyg_em", return_value=df):
            client.fetch_earnings_forecasts("20250331")
        assert client.sources_seen["earnings_forecast"] == {"eastmoney"}

    def test_failure_returns_empty_and_logs_error(self, caplog):
        """数据源请求失败 → [] + ERROR 留痕 + sources_seen none（MUST NOT 冒充成功）。"""

        def _boom(**kwargs):
            raise ConnectionRefusedError(111, "connection refused")

        client = AKShareClient()
        with (
            patch("finance_agent.data.akshare_client.ak.stock_yjyg_em", side_effect=_boom),
            caplog.at_level(logging.ERROR, logger=_LOGGER),
        ):
            rows = client.fetch_earnings_forecasts("20250331")
        assert rows == []
        assert "业绩预告拉取失败" in caplog.text
        assert client.sources_seen["earnings_forecast"] == {"none"}

    def test_empty_result_returns_empty_and_logs_error(self, caplog):
        """源可达但返回空 → [] + ERROR 留痕；sources_seen 记 eastmoney（源可达≠失败）。"""
        client = AKShareClient()
        with (
            patch(
                "finance_agent.data.akshare_client.ak.stock_yjyg_em",
                return_value=pd.DataFrame(),
            ),
            caplog.at_level(logging.ERROR, logger=_LOGGER),
        ):
            rows = client.fetch_earnings_forecasts("20250331")
        assert rows == []
        assert "返回空" in caplog.text
        assert client.sources_seen["earnings_forecast"] == {"eastmoney"}

    def test_default_period_used_when_none(self):
        df = _fake_df([{"code": "600519", "magnitude": 1.0}])
        with (
            patch("finance_agent.data.akshare_client.ak.stock_yjyg_em", return_value=df) as api,
            patch(
                "finance_agent.data.akshare_client._default_report_period",
                return_value="2026-09-30",
            ),
        ):
            rows = AKShareClient().fetch_earnings_forecasts()
        api.assert_called_once_with(date="20260930")
        assert rows[0]["report_period"] == "2026-09-30"

    def test_missing_magnitude_yields_none_change(self):
        """幅度文本与单值均缺 → change_low/high=None（不折算 0）。"""
        df = _fake_df([{"code": "600519", "text": "预计盈利约:100万元", "magnitude": float("nan")}])
        with patch("finance_agent.data.akshare_client.ak.stock_yjyg_em", return_value=df):
            rows = AKShareClient().fetch_earnings_forecasts("20250331")
        assert rows[0]["change_low"] is None and rows[0]["change_high"] is None
