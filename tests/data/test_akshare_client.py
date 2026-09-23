"""TDD tests for data/akshare_client.py — AKShare 数据清洗逻辑。

测试重点：
- 年报过滤（只保留报告日以 1231 结尾的行）
- 列名提取和重映射
- 缺失值处理（NaN → None）
- 数据降级策略（必需数据缺失报错，非必需标记 N/A）
API 调用用 mock，不测外部网络。
"""

from unittest.mock import patch

import pandas as pd
import pytest

from finance_agent.data.akshare_client import AKShareClient


@pytest.fixture
def client():
    return AKShareClient()


class TestFilterAnnualReports:
    """年报过滤：只保留 1231 结尾的报告日。"""

    def test_keeps_annual_only(self, client):
        df = pd.DataFrame(
            {
                "报告日": ["20241231", "20240930", "20240630", "20240331", "20231231"],
                "value": [1, 2, 3, 4, 5],
            }
        )
        result = client._filter_annual(df)
        assert len(result) == 2
        assert list(result["报告日"]) == ["20241231", "20231231"]

    def test_empty_dataframe(self, client):
        df = pd.DataFrame({"报告日": [], "value": []})
        result = client._filter_annual(df)
        assert len(result) == 0

    def test_no_annual_reports(self, client):
        df = pd.DataFrame({"报告日": ["20240930", "20240630"], "value": [1, 2]})
        result = client._filter_annual(df)
        assert len(result) == 0


class TestNormalizeNaN:
    """NaN → None 转换。"""

    def test_nan_becomes_none(self, client):
        df = pd.DataFrame({"a": [1.0, float("nan"), 3.0], "b": ["x", None, "z"]})
        result = client._normalize_nan(df)
        assert pd.isna(result.iloc[1]["a"])
        assert result.iloc[0]["a"] == 1.0


class TestFetchBalanceSheet:
    @patch("finance_agent.data.akshare_client.ak")
    def test_returns_annual_only(self, mock_ak, client):
        mock_ak.stock_financial_report_sina.return_value = pd.DataFrame(
            {
                "报告日": ["20241231", "20240930", "20231231"],
                "资产总计": [1000.0, 950.0, 900.0],
                "负债合计": [400.0, 380.0, 350.0],
            }
        )
        result = client.fetch_balance_sheet("600519")
        assert len(result) == 2
        assert "资产总计" in result.columns

    @patch("finance_agent.data.akshare_client.ak")
    def test_stocks_prefix_handling(self, mock_ak, client):
        """股票代码自动加 sh/sz 前缀。"""
        mock_ak.stock_financial_report_sina.return_value = pd.DataFrame(
            {"报告日": ["20241231", "20231231"], "资产总计": [100.0, 90.0]}
        )
        client.fetch_balance_sheet("600519")
        args = mock_ak.stock_financial_report_sina.call_args
        assert args[1]["stock"] == "sh600519" or args[0][0] == "sh600519"

    @patch("finance_agent.data.akshare_client.ak")
    def test_sz_prefix(self, mock_ak, client):
        mock_ak.stock_financial_report_sina.return_value = pd.DataFrame(
            {"报告日": ["20241231", "20231231"], "资产总计": [100.0, 90.0]}
        )
        client.fetch_balance_sheet("000858")
        args = mock_ak.stock_financial_report_sina.call_args
        stock_arg = args[1].get("stock", args[0][0] if args[0] else "")
        assert "sz000858" in stock_arg


class TestFetchIncomeStatement:
    @patch("finance_agent.data.akshare_client.ak")
    def test_returns_annual_only(self, mock_ak, client):
        mock_ak.stock_financial_report_sina.return_value = pd.DataFrame(
            {
                "报告日": ["20241231", "20240930", "20231231"],
                "营业收入": [1000.0, 750.0, 900.0],
                "净利润": [170.0, 130.0, 153.0],
            }
        )
        result = client.fetch_income_statement("600519")
        assert len(result) == 2
        assert result.iloc[0]["营业收入"] == 1000.0


class TestFetchCashFlow:
    @patch("finance_agent.data.akshare_client.ak")
    def test_returns_annual_only(self, mock_ak, client):
        mock_ak.stock_financial_report_sina.return_value = pd.DataFrame(
            {
                "报告日": ["20241231", "20240630", "20231231"],
                "经营活动产生的现金流量净额": [250.0, 120.0, 220.0],
            }
        )
        result = client.fetch_cash_flow("600519")
        assert len(result) == 2


class TestFetchIndicators:
    @patch("finance_agent.data.akshare_client.ak")
    def test_returns_dataframe(self, mock_ak, client):
        mock_ak.stock_financial_analysis_indicator.return_value = pd.DataFrame(
            {
                "日期": ["2024-12-31", "2023-12-31"],
                "销售毛利率(%)": [40.0, 38.0],
                "净资产收益率(%)": [28.0, 27.0],
            }
        )
        result = client.fetch_indicators("600519")
        assert len(result) == 2
        assert "销售毛利率(%)" in result.columns


class TestFetchIndustry:
    @patch("finance_agent.data.akshare_client.ak")
    def test_returns_industry_info(self, mock_ak, client):
        mock_ak.stock_individual_info_em.return_value = pd.DataFrame(
            {
                "item": ["行业", "公司名称", "总市值"],
                "value": ["白酒", "贵州茅台", "2000000000000"],
            }
        )
        result = client.fetch_industry("600519")
        assert result["industry"] == "白酒"
        assert result["name"] == "贵州茅台"
        assert "行业" not in result


class TestFetchStockQuote:
    @patch("finance_agent.data.akshare_client.ak")
    def test_returns_quote(self, mock_ak, client):
        mock_ak.stock_zh_a_spot_em.return_value = pd.DataFrame(
            {
                "代码": ["600519", "000858"],
                "名称": ["贵州茅台", "五粮液"],
                "最新价": [1800.0, 150.0],
                "市盈率-动态": [25.0, 20.0],
                "市净率": [10.0, 5.0],
            }
        )
        result = client.fetch_stock_quote("600519")
        assert result["name"] == "贵州茅台"
        assert result["code"] == "600519"
        assert result["price"] == 1800.0
        assert result["PE"] == 25.0
        assert "名称" not in result


class TestMinYearCheck:
    def test_less_than_2_years_raises(self, client):
        """不足 2 年报错。"""
        df = pd.DataFrame({"报告日": ["20241231"], "value": [1]})
        with pytest.raises(ValueError):  # "不足 2 年"
            client._check_min_years(df, "600519")

    def test_exactly_2_years_ok(self, client):
        df = pd.DataFrame({"报告日": ["20241231", "20231231"], "value": [1, 2]})
        client._check_min_years(df, "600519")  # should not raise


class TestFetchIndexKlineFallback:
    """fetch_index_kline 主源失败时回退新浪（TDD: data-source-benchmark-fallback）。"""

    @staticmethod
    def _em_hist_df() -> pd.DataFrame:
        return pd.DataFrame(
            {
                "日期": pd.to_datetime(["2026-08-01", "2026-08-02", "2026-08-03"]),
                "开盘": [4500.0, 4520.0, 4530.0],
                "收盘": [4510.0, 4530.0, 4540.0],
                "最高": [4520.0, 4540.0, 4550.0],
                "最低": [4490.0, 4510.0, 4520.0],
                "成交量": [100.0, 110.0, 120.0],
            }
        )

    @staticmethod
    def _sina_daily_df() -> pd.DataFrame:
        return pd.DataFrame(
            {
                "date": ["2026-08-01", "2026-08-02", "2026-08-03"],
                "open": [4500.0, 4520.0, 4530.0],
                "high": [4520.0, 4540.0, 4550.0],
                "low": [4490.0, 4510.0, 4520.0],
                "close": [4510.0, 4530.0, 4540.0],
                "volume": [100.0, 110.0, 120.0],
            }
        )

    @patch("finance_agent.data.akshare_client.ak")
    def test_em_success_no_sina_call(self, mock_ak, client, monkeypatch):
        """东财正常时直接返回，不触发新浪回退。"""
        monkeypatch.setattr("finance_agent.data.akshare_client._AK_MAX_RETRIES", 1)
        monkeypatch.setattr("finance_agent.data.akshare_client._AK_RETRY_DELAY", 0)
        mock_ak.index_zh_a_hist.return_value = self._em_hist_df()
        result = client.fetch_index_kline("000300", days=2)
        assert len(result) == 2
        assert list(result.columns) == list(self._em_hist_df().columns)
        mock_ak.stock_zh_index_daily.assert_not_called()

    @patch("finance_agent.data.akshare_client.ak")
    def test_em_exception_falls_back_to_sina(self, mock_ak, client, monkeypatch):
        """东财抛连接异常时回退新浪，列名归一化为中文。"""
        monkeypatch.setattr("finance_agent.data.akshare_client._AK_MAX_RETRIES", 1)
        monkeypatch.setattr("finance_agent.data.akshare_client._AK_RETRY_DELAY", 0)
        mock_ak.index_zh_a_hist.side_effect = ConnectionError("RST")
        mock_ak.stock_zh_index_daily.return_value = self._sina_daily_df()
        result = client.fetch_index_kline("000300", days=3)
        assert len(result) == 3
        assert "日期" in result.columns and "收盘" in result.columns
        assert result.iloc[-1]["收盘"] == 4540.0
        mock_ak.stock_zh_index_daily.assert_called_once_with(symbol="sh000300")

    @patch("finance_agent.data.akshare_client.ak")
    def test_em_empty_falls_back_to_sina(self, mock_ak, client, monkeypatch):
        """东财返回空 DataFrame 时回退新浪。"""
        monkeypatch.setattr("finance_agent.data.akshare_client._AK_MAX_RETRIES", 1)
        monkeypatch.setattr("finance_agent.data.akshare_client._AK_RETRY_DELAY", 0)
        mock_ak.index_zh_a_hist.return_value = pd.DataFrame()
        mock_ak.stock_zh_index_daily.return_value = self._sina_daily_df()
        result = client.fetch_index_kline("000300", days=3)
        assert len(result) == 3
        assert "收盘" in result.columns

    @patch("finance_agent.data.akshare_client.ak")
    def test_both_fail_returns_empty(self, mock_ak, client, monkeypatch):
        """双源均失败返回空 DataFrame 且不抛异常。"""
        monkeypatch.setattr("finance_agent.data.akshare_client._AK_MAX_RETRIES", 1)
        monkeypatch.setattr("finance_agent.data.akshare_client._AK_RETRY_DELAY", 0)
        mock_ak.index_zh_a_hist.side_effect = ConnectionError("RST")
        mock_ak.stock_zh_index_daily.return_value = pd.DataFrame()
        result = client.fetch_index_kline("000300", days=3)
        assert result.empty

    @patch("finance_agent.data.akshare_client.ak")
    def test_sina_missing_optional_cols_ok(self, mock_ak, client, monkeypatch):
        """新浪缺 amount/turnover 列时不报错、只含现有列的重命名。"""
        monkeypatch.setattr("finance_agent.data.akshare_client._AK_MAX_RETRIES", 1)
        monkeypatch.setattr("finance_agent.data.akshare_client._AK_RETRY_DELAY", 0)
        mock_ak.index_zh_a_hist.side_effect = ConnectionError("RST")
        sina = self._sina_daily_df().drop(columns=["volume"])
        mock_ak.stock_zh_index_daily.return_value = sina
        result = client.fetch_index_kline("000300", days=3)
        assert "日期" in result.columns and "收盘" in result.columns
        assert "成交量" not in result.columns


class TestSinaIndexSymbolMapping:
    """指数代码 → 新浪符号映射。"""

    def test_csi300_is_sh(self):
        assert AKShareClient._to_sina_index_symbol("000300") == "sh000300"

    def test_shanghai_index_is_sh(self):
        assert AKShareClient._to_sina_index_symbol("000001") == "sh000001"

    def test_shenzhen_index_is_sz(self):
        assert AKShareClient._to_sina_index_symbol("399001") == "sz399001"

    def test_chinext_is_sz(self):
        assert AKShareClient._to_sina_index_symbol("399006") == "sz399006"


class TestFetchKlineTencentFallback:
    """kline-tencent-fallback：东财+新浪均失败→腾讯三级回退。"""

    @staticmethod
    def _tx_daily_df():
        return pd.DataFrame(
            {
                "date": ["2026-08-01", "2026-08-02", "2026-08-03"],
                "open": [4500.0, 4520.0, 4530.0],
                "high": [4520.0, 4540.0, 4550.0],
                "low": [4490.0, 4510.0, 4520.0],
                "close": [4510.0, 4530.0, 4540.0],
                "volume": [100.0, 110.0, 120.0],
                "amount": [1e8, 1.1e8, 1.2e8],
                "turnover": [0.01, 0.011, 0.012],
            }
        )

    @patch("finance_agent.data.akshare_client.ak")
    def test_em_sina_fail_falls_back_to_tx(self, mock_ak, client, monkeypatch):
        """东财+新浪均失败 → 回退腾讯，列归一化为中文。"""
        monkeypatch.setattr("finance_agent.data.akshare_client._AK_MAX_RETRIES", 1)
        monkeypatch.setattr("finance_agent.data.akshare_client._AK_RETRY_DELAY", 0)
        mock_ak.stock_zh_a_hist.side_effect = ConnectionError("RST")
        mock_ak.stock_zh_a_daily.return_value = pd.DataFrame()
        mock_ak.stock_zh_a_hist_tx.return_value = self._tx_daily_df()
        result = client.fetch_kline("600519", days=3)
        assert len(result) == 3
        assert "日期" in result.columns and "收盘" in result.columns
        assert result.iloc[-1]["收盘"] == 4540.0
        mock_ak.stock_zh_a_hist_tx.assert_called_once()
        _, kwargs = mock_ak.stock_zh_a_hist_tx.call_args
        assert kwargs.get("symbol") == "sh600519"
        assert kwargs.get("adjust") == "qfq"

    @patch("finance_agent.data.akshare_client.ak")
    def test_em_success_no_fallback(self, mock_ak, client, monkeypatch):
        """东财正常时不触发新浪/腾讯。"""
        monkeypatch.setattr("finance_agent.data.akshare_client._AK_MAX_RETRIES", 1)
        monkeypatch.setattr("finance_agent.data.akshare_client._AK_RETRY_DELAY", 0)
        em_df = pd.DataFrame(
            {"日期": ["2026-08-03", "2026-08-02", "2026-08-01"], "收盘": [4540.0, 4530.0, 4510.0]}
        )
        mock_ak.stock_zh_a_hist.return_value = em_df
        client.fetch_kline("600519", days=2)
        mock_ak.stock_zh_a_daily.assert_not_called()
        mock_ak.stock_zh_a_hist_tx.assert_not_called()

    @patch("finance_agent.data.akshare_client.ak")
    def test_all_fail_returns_empty(self, mock_ak, client, monkeypatch):
        """三级全失败返回空且不抛异常。"""
        monkeypatch.setattr("finance_agent.data.akshare_client._AK_MAX_RETRIES", 1)
        monkeypatch.setattr("finance_agent.data.akshare_client._AK_RETRY_DELAY", 0)
        mock_ak.stock_zh_a_hist.side_effect = ConnectionError("RST")
        mock_ak.stock_zh_a_daily.return_value = pd.DataFrame()
        mock_ak.stock_zh_a_hist_tx.return_value = pd.DataFrame()
        result = client.fetch_kline("600519", days=3)
        assert result.empty


class TestFetchKlineAdjustCaliber:
    """复权口径（delta add-backtest-leakage-controls）：

    默认仍前复权（管线输入口径，向后兼容硬约束）；结算/回测路径显式传后复权（hfq，
    as-of 保真：后复权历史价 = 当时真实成交价）。本类钉死三源透传契约。
    """

    @staticmethod
    def _em_df():
        return pd.DataFrame(
            {"日期": ["2026-08-03", "2026-08-02", "2026-08-01"], "收盘": [4540.0, 4530.0, 4510.0]}
        )

    @staticmethod
    def _sina_df():
        return pd.DataFrame(
            {
                "date": ["2026-08-01", "2026-08-02", "2026-08-03"],
                "open": [4500.0, 4520.0, 4530.0],
                "high": [4520.0, 4540.0, 4550.0],
                "low": [4490.0, 4510.0, 4520.0],
                "close": [4510.0, 4530.0, 4540.0],
                "volume": [100.0, 110.0, 120.0],
                "amount": [1e8, 1.1e8, 1.2e8],
                "turnover": [0.01, 0.011, 0.012],
            }
        )

    @staticmethod
    def _tx_df():
        return pd.DataFrame(
            {
                "date": ["2026-08-01", "2026-08-02", "2026-08-03"],
                "open": [4500.0, 4520.0, 4530.0],
                "high": [4520.0, 4540.0, 4550.0],
                "low": [4490.0, 4510.0, 4520.0],
                "close": [4510.0, 4530.0, 4540.0],
                "volume": [100.0, 110.0, 120.0],
                "amount": [1e8, 1.1e8, 1.2e8],
                "turnover": [0.01, 0.011, 0.012],
            }
        )

    @patch("finance_agent.data.akshare_client.ak")
    def test_fetch_kline_default_adjust_is_qfq(self, mock_ak, client, monkeypatch):
        """不传 adjust → 东财 kwargs 收到 qfq（默认参数 = 管线输入口径）。"""
        monkeypatch.setattr("finance_agent.data.akshare_client._AK_MAX_RETRIES", 1)
        monkeypatch.setattr("finance_agent.data.akshare_client._AK_RETRY_DELAY", 0)
        mock_ak.stock_zh_a_hist.return_value = self._em_df()
        client.fetch_kline("600519", days=2)
        _, kwargs = mock_ak.stock_zh_a_hist.call_args
        assert kwargs.get("adjust") == "qfq"

    @patch("finance_agent.data.akshare_client.ak")
    def test_fetch_kline_hfq_passthrough_all_sources(self, mock_ak, client, monkeypatch):
        """显式 adjust="hfq" → 东财/新浪/腾讯三源 kwargs 均收到 hfq（逐级回退均透传）。"""
        monkeypatch.setattr("finance_agent.data.akshare_client._AK_MAX_RETRIES", 1)
        monkeypatch.setattr("finance_agent.data.akshare_client._AK_RETRY_DELAY", 0)

        # 方案1 东财
        mock_ak.stock_zh_a_hist.return_value = self._em_df()
        client.fetch_kline("600519", days=2, adjust="hfq")
        _, em_kwargs = mock_ak.stock_zh_a_hist.call_args
        assert em_kwargs.get("adjust") == "hfq"

        # 方案2 新浪（东财空 → 回退）
        mock_ak.stock_zh_a_hist.return_value = pd.DataFrame()
        mock_ak.stock_zh_a_daily.return_value = self._sina_df()
        client.fetch_kline("600519", days=2, adjust="hfq")
        _, sina_kwargs = mock_ak.stock_zh_a_daily.call_args
        assert sina_kwargs.get("adjust") == "hfq"

        # 方案3 腾讯（东财+新浪均空 → 二级回退）
        mock_ak.stock_zh_a_daily.return_value = pd.DataFrame()
        mock_ak.stock_zh_a_hist_tx.return_value = self._tx_df()
        client.fetch_kline("600519", days=2, adjust="hfq")
        _, tx_kwargs = mock_ak.stock_zh_a_hist_tx.call_args
        assert tx_kwargs.get("adjust") == "hfq"


class TestDataGapLogging:
    """数据未正确拉取时必须有日志报错（可观测性：静默降级 = 隐性数据缺失）。

    背景（2026-09-08 审计）：东财行情接口被 TLS 风控封锁时 stock_quote 静默
    降级为仅名称（PE/PB/市值丢失无日志）、news/macro 失败静默返回空——下游
    与人工都无从得知数据维度缺失。以下用例钉死「降级/空返回必须留日志」契约。
    """

    @patch("finance_agent.data.akshare_client.ak")
    def test_quote_degraded_to_name_only_logs_error(self, mock_ak, client, caplog):
        """行情主源失败、降级为仅名称时 MUST 留 ERROR 日志（PE/PB 丢失可观测）。"""
        mock_ak.stock_zh_a_spot_em.return_value = None  # 主源全失败
        mock_ak.stock_info_a_code_name.return_value = pd.DataFrame(
            {"code": ["600519"], "name": ["贵州茅台"]}
        )
        with caplog.at_level("ERROR", logger="finance_agent.data.akshare_client"):
            result = client.fetch_stock_quote("600519")
        assert result.get("name") == "贵州茅台"  # fallback 仍返回名称
        assert any(
            "行情" in r.message or "PE" in r.message or "spot_em" in r.message
            for r in caplog.records
        ), "行情降级无 ERROR 日志"

    @patch("finance_agent.data.akshare_client.ak")
    def test_news_empty_logs_error(self, mock_ak, client, caplog):
        """新闻接口失败返回空列表时 MUST 留 ERROR 日志。"""
        mock_ak.stock_news_em.return_value = None
        with caplog.at_level("ERROR", logger="finance_agent.data.akshare_client"):
            result = client.fetch_news("600519")
        assert result == []
        assert any("news" in r.message or "新闻" in r.message for r in caplog.records), (
            "新闻空返回无 ERROR 日志"
        )

    @patch("finance_agent.data.akshare_client.ak")
    def test_macro_indicator_failure_logs_error(self, mock_ak, client, caplog):
        """宏观指标接口失败返回空列表时 MUST 留 ERROR 日志。"""
        mock_ak.macro_china_cpi.side_effect = ConnectionError("refused")
        with caplog.at_level("ERROR", logger="finance_agent.data.akshare_client"):
            result = client.fetch_macro_indicators()
        assert result["cpi"] == []
        assert any("cpi" in r.message for r in caplog.records), "宏观指标失败无 ERROR 日志"

    @patch("finance_agent.data.akshare_client.ak")
    def test_industry_fallback_logs_warning(self, mock_ak, client, caplog):
        """个股信息主源失败、走 cninfo fallback 时 MUST 留 WARNING 日志（降级路径可观测）。"""
        mock_ak.stock_individual_info_em.return_value = None
        mock_ak.stock_info_a_code_name.return_value = pd.DataFrame(
            {"code": ["600519"], "name": ["贵州茅台"]}
        )
        mock_ak.stock_industry_change_cninfo.return_value = pd.DataFrame({"行业名称": ["白酒"]})
        with caplog.at_level("WARNING", logger="finance_agent.data.akshare_client"):
            result = client.fetch_industry("600519")
        assert result.get("name") == "贵州茅台"
        assert any(
            "降级" in r.message or "fallback" in r.message or "cninfo" in r.message
            for r in caplog.records
        ), "行业 fallback 无 WARNING 日志"


class TestFetchStockQuoteBaiduFallback:
    """add-quote-baidu-fallback：东财行情失败时回退百度估值+腾讯日线。"""

    @patch("finance_agent.data.akshare_client.ak")
    def test_baidu_market_cap_pb_and_tencent_price(self, mock_ak, client):
        """东财失败 → 百度总市值/PB + 腾讯最新收盘价并入 result。"""
        mock_ak.stock_zh_a_spot_em.return_value = None  # 东财主源失败
        # 百度估值：总市值 + 市净率（各返回 df，末行最新）
        mock_ak.stock_zh_valuation_baidu.side_effect = [
            __import__("pandas").DataFrame(
                {"date": [__import__("datetime").date(2026, 9, 7)], "value": [1846.54]}
            ),
            __import__("pandas").DataFrame(
                {"date": [__import__("datetime").date(2026, 9, 7)], "value": [14.52]}
            ),
        ]
        # 腾讯日线：最新收盘 632.0
        mock_ak.stock_zh_a_hist_tx.return_value = __import__("pandas").DataFrame(
            {"date": [__import__("datetime").date(2026, 9, 8)], "close": [632.0]}
        )
        mock_ak.stock_info_a_code_name.return_value = __import__("pandas").DataFrame(
            {"code": ["688072"], "name": ["拓荆科技"]}
        )

        result = client.fetch_stock_quote("688072")

        assert result.get("market_cap") == 1846.54
        assert result.get("PB") == 14.52
        assert result.get("price") == 632.0
        assert result.get("name") == "拓荆科技"
        # PE 不推导（留给下游），不出现
        assert "PE" not in result or result["PE"] is None

    @patch("finance_agent.data.akshare_client.ak")
    def test_baidu_pb_failure_keeps_market_cap(self, mock_ak, client):
        """百度市净率失败不影响总市值（部分成功，不抛异常）。"""
        mock_ak.stock_zh_a_spot_em.return_value = None
        mock_ak.stock_zh_valuation_baidu.side_effect = [
            __import__("pandas").DataFrame(
                {"date": [__import__("datetime").date(2026, 9, 7)], "value": [1846.54]}
            ),
            ConnectionError("baidu pb refused"),
        ]
        mock_ak.stock_zh_a_hist_tx.return_value = __import__("pandas").DataFrame(
            {"date": [__import__("datetime").date(2026, 9, 8)], "close": [632.0]}
        )
        mock_ak.stock_info_a_code_name.return_value = __import__("pandas").DataFrame(
            {"code": ["688072"], "name": ["拓荆科技"]}
        )

        result = client.fetch_stock_quote("688072")

        assert result.get("market_cap") == 1846.54  # 总市值保留
        assert "PB" not in result or result["PB"] is None  # PB 缺失不抛
        assert result.get("price") == 632.0

    @patch("finance_agent.data.akshare_client.ak")
    def test_all_fallback_fail_returns_name_only(self, mock_ak, client, caplog):
        """百度腾讯全失败 → 仅名称 + ERROR 日志。"""
        mock_ak.stock_zh_a_spot_em.return_value = None
        mock_ak.stock_zh_valuation_baidu.side_effect = ConnectionError("baidu refused")
        mock_ak.stock_zh_a_hist_tx.side_effect = ConnectionError("tencent refused")
        mock_ak.stock_info_a_code_name.return_value = __import__("pandas").DataFrame(
            {"code": ["688072"], "name": ["拓荆科技"]}
        )
        with caplog.at_level("ERROR", logger="finance_agent.data.akshare_client"):
            result = client.fetch_stock_quote("688072")
        assert result.get("name") == "拓荆科技"
        assert "PE" not in result or result["PE"] is None
        assert any("行情" in r.message for r in caplog.records), "全部回退失败无 ERROR 日志"
