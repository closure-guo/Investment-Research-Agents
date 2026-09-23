"""Δ3 Task 2: fetch_index_constituents 双源(中证优先/东财回退)。

列名为实跑实测结果(2026-09-23, akshare 1.18.94):
- ak.index_stock_cons_csindex(symbol="000300") → 9 列,含 '成分券代码' / '成分券名称'(300 行)
- ak.index_stock_cons(symbol="000300")(东财)   → 3 列,含 '品种代码' / '品种名称'(300 行)
"""

from unittest.mock import patch

import pandas as pd
import pytest

from finance_agent.data.akshare_client import AKShareClient


@pytest.fixture
def client():
    return AKShareClient()


@pytest.fixture(autouse=True)
def _fast_retries(monkeypatch):
    """避免重试用例真等（同 tests/data 既有范式）。"""
    monkeypatch.setattr("finance_agent.data.akshare_client._AK_MAX_RETRIES", 1)
    monkeypatch.setattr("finance_agent.data.akshare_client._AK_RETRY_DELAY", 0)


def _csindex_df() -> pd.DataFrame:
    """中证官网返回（实测列名，仅保留相关列）。"""
    return pd.DataFrame(
        {
            "日期": ["2026-09-23", "2026-09-23"],
            "指数代码": ["000300", "000300"],
            "指数名称": ["沪深300", "沪深300"],
            "成分券代码": ["000001", "600519"],
            "成分券名称": ["平安银行", "贵州茅台"],
            "交易所": ["深圳证券交易所", "上海证券交易所"],
        }
    )


def _em_df() -> pd.DataFrame:
    """东财返回（实测列名）。"""
    return pd.DataFrame(
        {
            "品种代码": ["000001", "600519"],
            "品种名称": ["平安银行", "贵州茅台"],
            "纳入日期": ["2026-06-15", "2026-06-15"],
        }
    )


class TestFetchIndexConstituents:
    @patch("finance_agent.data.akshare_client.ak")
    def test_csindex_success_parses_ticker_and_name(self, mock_ak, client):
        mock_ak.index_stock_cons_csindex.return_value = _csindex_df()

        result = client.fetch_index_constituents("000300")

        assert result == [
            {"ticker": "000001", "name": "平安银行"},
            {"ticker": "600519", "name": "贵州茅台"},
        ]
        mock_ak.index_stock_cons_csindex.assert_called_once_with(symbol="000300")
        mock_ak.index_stock_cons.assert_not_called()  # 主源成功不触发回退

    @patch("finance_agent.data.akshare_client.ak")
    def test_csindex_exception_falls_back_to_eastmoney(self, mock_ak, client):
        mock_ak.index_stock_cons_csindex.side_effect = ConnectionError("csindex refused")
        mock_ak.index_stock_cons.return_value = _em_df()

        result = client.fetch_index_constituents("000300")

        assert result == [
            {"ticker": "000001", "name": "平安银行"},
            {"ticker": "600519", "name": "贵州茅台"},
        ]
        mock_ak.index_stock_cons.assert_called_once_with(symbol="000300")

    @patch("finance_agent.data.akshare_client.ak")
    def test_csindex_empty_falls_back_to_eastmoney(self, mock_ak, client):
        mock_ak.index_stock_cons_csindex.return_value = pd.DataFrame()
        mock_ak.index_stock_cons.return_value = _em_df()

        result = client.fetch_index_constituents("000300")

        assert [r["ticker"] for r in result] == ["000001", "600519"]
        mock_ak.index_stock_cons.assert_called_once_with(symbol="000300")

    @patch("finance_agent.data.akshare_client.ak")
    def test_ticker_with_exchange_suffix_is_normalized(self, mock_ak, client):
        """ "去交易所后缀"契约：000001.SZ / sh600519 → 6 位数字。"""
        mock_ak.index_stock_cons_csindex.return_value = pd.DataFrame(
            {
                "成分券代码": ["000001.SZ", "sh600519"],
                "成分券名称": ["平安银行", "贵州茅台"],
            }
        )

        result = client.fetch_index_constituents("000300")

        assert [r["ticker"] for r in result] == ["000001", "600519"]

    @patch("finance_agent.data.akshare_client.ak")
    def test_both_sources_fail_returns_empty_and_logs_error(self, mock_ak, client, caplog):
        mock_ak.index_stock_cons_csindex.side_effect = ConnectionError("csindex refused")
        mock_ak.index_stock_cons.side_effect = ConnectionError("em refused")

        with caplog.at_level("ERROR", logger="finance_agent.data.akshare_client"):
            result = client.fetch_index_constituents("000300")

        assert result == []
        assert any("成分" in r.message for r in caplog.records), "成分双源失败无 ERROR 日志"

    @patch("finance_agent.data.akshare_client.ak")
    def test_default_index_code_is_csi300(self, mock_ak, client):
        mock_ak.index_stock_cons_csindex.return_value = _csindex_df()

        client.fetch_index_constituents()

        mock_ak.index_stock_cons_csindex.assert_called_once_with(symbol="000300")

    @patch("finance_agent.data.akshare_client.ak")
    def test_unknown_columns_do_not_crash(self, mock_ak, client, caplog):
        """列名漂移(接口改版)时返回空并留 ERROR,不抛异常炸整条 universe 构建。"""
        mock_ak.index_stock_cons_csindex.return_value = pd.DataFrame({"foo": [1], "bar": [2]})
        mock_ak.index_stock_cons.return_value = pd.DataFrame({"foo": [1], "bar": [2]})

        with caplog.at_level("ERROR", logger="finance_agent.data.akshare_client"):
            result = client.fetch_index_constituents("000300")

        assert result == []
