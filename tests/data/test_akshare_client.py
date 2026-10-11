"""TDD tests for data/akshare_client.py — AKShare 数据清洗逻辑。

测试重点：
- 年报过滤（只保留报告日以 1231 结尾的行）
- 列名提取和重映射
- 缺失值处理（NaN → None）
- 数据降级策略（必需数据缺失报错，非必需标记 N/A）
API 调用用 mock，不测外部网络。
"""

from datetime import date
from unittest.mock import patch

import pandas as pd
import pytest
import requests

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
    """update-quote-primary-source：东财 spot 降为回退1（腾讯主源 down 前提钉死）。"""

    @patch("finance_agent.data.akshare_client.ak")
    def test_eastmoney_fallback_after_tencent_fail(self, mock_ak, client, monkeypatch):
        monkeypatch.setattr(client, "_fetch_tencent_quote", lambda code: None)
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
    def test_quote_degraded_to_name_only_logs_error(self, mock_ak, client, caplog, monkeypatch):
        """行情主源失败、降级为仅名称时 MUST 留 ERROR 日志（PE/PB 丢失可观测）。"""
        monkeypatch.setattr(client, "_fetch_tencent_quote", lambda code: None)
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


class TestFetchStockQuoteTencentPrimary:
    """update-quote-primary-source：腾讯单标的直查为主源。

    GOLDEN 内联（与 TestFetchTencentQuote.GOLDEN 同串）——本类在文件中
    位于该类之前，类体执行期不可前向引用。
    """

    GOLDEN = (
        'v_sh688072="1~拓荆科技~688072~640.00~656.68~663.00~3387770~1660152~1727618'
        "~639.79~9~639.75~3~639.47~2~639.00~8~638.99~39~640.00~6~640.04~2~640.14~2"
        "~640.30~10~640.32~4~~20260930161437~-16.68~-2.54~675.00~635.73"
        "~640.00/3387770/2200617467~3387770~220062~1.19~85.97~~675.00~635.73~5.98"
        "~1818.80~1869.91~14.55~788.02~525.34~0.81~~20260930~161437"
    )

    @staticmethod
    def _gbk_response(payload: str):
        resp = requests.Response()
        resp.status_code = 200
        resp._content = payload.encode("gbk")
        return resp

    @patch("finance_agent.data.akshare_client.ak")
    @patch("finance_agent.data.akshare_client.requests.get")
    def test_tencent_primary_no_spot_call(self, mock_get, mock_ak, client):
        mock_get.return_value = self._gbk_response(self.GOLDEN)
        result = client.fetch_stock_quote("688072")
        assert result["price"] == 640.00
        assert result["market_cap"] == pytest.approx(1869.91e8)
        assert "PE" not in result
        # 主源命中 → 不触发东财 spot 翻页
        mock_ak.stock_zh_a_spot_em.assert_not_called()
        assert client.sources_seen["market_cap"] == {"tencent"}

    @patch("finance_agent.data.akshare_client.ak")
    @patch("finance_agent.data.akshare_client.requests.get")
    def test_tencent_fail_falls_back_to_eastmoney(self, mock_get, mock_ak, client):
        mock_get.side_effect = ConnectionError("refused")
        mock_ak.stock_zh_a_spot_em.return_value = pd.DataFrame(
            {
                "代码": ["688072"],
                "名称": ["拓荆科技"],
                "最新价": [640.0],
                "总市值": [1.86e11],
                "市净率": [14.0],
            }
        )
        result = client.fetch_stock_quote("688072")
        assert result["price"] == 640.0
        assert result["market_cap"] == 1.86e11  # 东财主源单位=元（C1 契约）
        assert client.sources_seen["market_cap"] == {"eastmoney"}


class TestFetchStockQuoteBaiduFallback:
    """add-quote-baidu-fallback：东财行情失败时回退百度估值+腾讯日线。"""

    @pytest.fixture(autouse=True)
    def _tencent_primary_down(self, monkeypatch):
        """本类钉死第二回退语义：腾讯主源与东财 spot 均不可达。"""
        monkeypatch.setattr(
            "finance_agent.data.akshare_client.AKShareClient._fetch_tencent_quote",
            lambda self, code: None,
        )

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

        assert result.get("market_cap") == 1846.54e8  # 百度亿元 ×1e8 归一到元（终审 C1）
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

        assert (
            result.get("market_cap") == 1846.54e8
        )  # 百度亿元 ×1e8 归一到元（终审 C1）  # 总市值保留
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


class TestFetchQuarterlyIncomeExtended:
    """季度利润表扩展：单季营收/营业成本列。"""

    @staticmethod
    def _quarterly_df() -> pd.DataFrame:
        return pd.DataFrame(
            {
                "REPORT_DATE": pd.to_datetime(
                    ["2026-06-30", "2026-03-31", "2025-12-31", "2025-06-30"]
                ),
                "PARENT_NETPROFIT": [7.72e8, 5.71e8, 3.70e8, 0.94e8],
                "OPERATE_INCOME": [1.80e9, 1.12e9, 2.10e9, 0.95e9],
                "OPERATE_COST": [1.07e9, 0.68e9, 1.30e9, 0.65e9],
            }
        )

    @patch("finance_agent.data.akshare_client.ak")
    def test_revenue_and_cost_columns_present(self, mock_ak, client):
        mock_ak.stock_profit_sheet_by_quarterly_em.return_value = self._quarterly_df()
        df = client.fetch_quarterly_income("688072", quarters=2)
        assert "营业收入(单季)" in df.columns
        assert "营业成本(单季)" in df.columns
        row = df[df["季度"] == "2026Q2"].iloc[0]
        assert row["营业收入(单季)"] == 1.80e9
        assert row["营业成本(单季)"] == 1.07e9

    @patch("finance_agent.data.akshare_client.ak")
    def test_missing_cost_yields_none_not_zero(self, mock_ak, client):
        df_partial = self._quarterly_df().drop(columns=["OPERATE_COST"])
        mock_ak.stock_profit_sheet_by_quarterly_em.return_value = df_partial
        df = client.fetch_quarterly_income("688072", quarters=4)
        assert df["营业成本(单季)"].isna().all()
        assert df["营业收入(单季)"].notna().all()

    @patch("finance_agent.data.akshare_client.ak")
    def test_revenue_yoy_computed_in_wide_window(self, mock_ak, client):
        """营收同比在宽窗口（截断前）算好随行携带。

        quarters=1 时最终只保留 2026Q2，但宽窗口 head(1*2+2)=4 行仍含
        2025Q2（OPERATE_INCOME=0.95e9），故截断后 2026Q2 行仍携带
        (1.80e9 - 0.95e9) / 0.95e9 * 100 ≈ 89.47。
        """
        mock_ak.stock_profit_sheet_by_quarterly_em.return_value = self._quarterly_df()
        df = client.fetch_quarterly_income("688072", quarters=1)
        assert "营收同比" in df.columns
        assert len(df) == 1  # 最终截断到最近 1 季
        assert df.iloc[0]["营收同比"] == pytest.approx(89.47, abs=0.01)

    @patch("finance_agent.data.akshare_client.ak")
    def test_nan_normalized_to_none_at_exit(self, mock_ak, client):
        """D3：出口 NaN 归一——records 中 None 经 pd.DataFrame 构造变 float64 NaN，
        消费端 `is None` 判空失效，违反「不产出伪值」契约；出口必须归回 None。

        宽窗口仅 2 行（2026-06-30 与 2025-06-30）：2026Q2 的同比/营收同比可算
        （float），2025Q2 找不到 2024 同期 → 同比/营收同比为 None。同比列因
        float 与 None 混列被构造成 float64+NaN——正是判空失效的场景。
        """
        df = pd.DataFrame(
            {
                "REPORT_DATE": pd.to_datetime(["2026-06-30", "2025-06-30"]),
                "PARENT_NETPROFIT": [7.72e8, 0.94e8],
                "OPERATE_INCOME": [1.80e9, 0.95e9],
                "OPERATE_COST": [1.07e9, 0.65e9],
            }
        )
        mock_ak.stock_profit_sheet_by_quarterly_em.return_value = df
        out = client.fetch_quarterly_income("688072", quarters=2)

        # 混列场景成立：2026Q2 同比确实算出（非恒 None），2025Q2 缺同期必须 None
        assert isinstance(out.iloc[0]["同比"], float)
        assert out.iloc[1]["同比"] is None
        # 核心断言：出口全列不允许残留 float NaN——None 是唯一合法「缺失」表示
        for col in out.columns:
            for v in out[col].tolist():
                assert not (isinstance(v, float) and pd.isna(v)), f"列 {col} 残留 NaN: {v!r}"


class TestFetchLatestPeriodSnapshot:
    """最新报告期快照：不限年报，取最新已披露报告期。"""

    @staticmethod
    def _inc_df() -> pd.DataFrame:
        return pd.DataFrame(
            {
                "报告日": ["20260630", "20251231", "20250630", "20241231"],
                "营业总收入": [2912864261.46, 6519094874.63, 1954146173.65, 5000000000.0],
                "营业成本": [1718464963.55, 4240523945.89, 1329638462.72, 3200000000.0],
                "归母净利润": [1342753980.93, 927000000.0, 94287965.0, 700000000.0],
            }
        )

    @staticmethod
    def _bs_df() -> pd.DataFrame:
        return pd.DataFrame(
            {
                "报告日": ["20260630", "20251231", "20250630"],
                "资产总计": [25312149848.91, 19823566917.19, 17553660550.0],
                "负债合计": [12112639298.53, 12708096577.24, 12127248749.77],
                "存货": [8832503986.79, 7825778934.48, 8322530306.59],
                "合同负债": [5130654458.79, 4851847248.0, 4535774320.25],
            }
        )

    @patch("finance_agent.data.akshare_client.ak")
    def test_snapshot_from_h1_report(self, mock_ak, client):
        mock_ak.stock_financial_report_sina.side_effect = lambda stock, symbol: (
            self._inc_df() if symbol == "利润表" else self._bs_df()
        )
        snap = client.fetch_latest_period_snapshot("688072")
        assert snap["报告日"] == "2026-06-30"
        assert snap["期类型"] == "中报"
        assert snap["营业总收入(累计)"] == 29.13
        assert snap["归母净利润(累计)"] == 13.43
        # 毛利率 = 1 - 17.18/29.13 = 41.0%
        assert snap["毛利率(%)"] == 41.0
        # 负债率 = 121.13/253.12 = 47.85%
        assert snap["资产负债率(%)"] == 47.85
        assert snap["存货"] == 88.33
        assert snap["合同负债"] == 51.31

    @patch("finance_agent.data.akshare_client.ak")
    def test_yoy_vs_same_period_prior_year(self, mock_ak, client):
        mock_ak.stock_financial_report_sina.side_effect = lambda stock, symbol: (
            self._inc_df() if symbol == "利润表" else self._bs_df()
        )
        snap = client.fetch_latest_period_snapshot("688072")
        # 同比按 raw 元值计算（#190 修正：舍入基期传播误差）——
        # 营收精确：(29.1286 - 19.5415)/19.5415 = 49.06%
        assert snap["营收同比(%)"] == 49.06
        assert snap["上年同期归母净利润"] == 0.94288  # 元级展示精度
        # 归母精确：(13.42754 - 0.94288)/0.94288 = 1324.10%
        # （旧舍入口径 13.43/0.94 会得 1328.72%——传播误差已修）
        assert snap["归母净利同比(%)"] == 1324.1

    @patch("finance_agent.data.akshare_client.ak")
    def test_no_prior_period_yoy_none_marked_missing(self, mock_ak, client):
        inc = self._inc_df()[self._inc_df()["报告日"] != "20250630"]
        mock_ak.stock_financial_report_sina.side_effect = lambda stock, symbol: (
            inc if symbol == "利润表" else self._bs_df()
        )
        snap = client.fetch_latest_period_snapshot("688072")
        assert snap["营收同比(%)"] is None
        assert "上年同期数据缺失" in " ".join(snap["missing"])

    @patch("finance_agent.data.akshare_client.ak")
    def test_balance_sheet_missing_fields_partial_snapshot(self, mock_ak, client):
        bs = pd.DataFrame({"报告日": ["20260630"]})
        mock_ak.stock_financial_report_sina.side_effect = lambda stock, symbol: (
            self._inc_df() if symbol == "利润表" else bs
        )
        snap = client.fetch_latest_period_snapshot("688072")
        assert snap["存货"] is None
        assert "存货" in snap["missing"]
        assert snap["毛利率(%)"] == 41.0  # 利润表部分照常装配

    @patch("finance_agent.data.akshare_client.ak")
    def test_income_statement_failure_raises(self, mock_ak, client):
        """利润表整体不可用 → _sina_report 重试后抛 RuntimeError（由 fetch 层降级）。"""
        mock_ak.stock_financial_report_sina.side_effect = ConnectionError("RST")
        with pytest.raises(RuntimeError):
            client.fetch_latest_period_snapshot("688072")

    @patch("finance_agent.data.akshare_client.ak")
    def test_balance_sheet_neighbor_period_fallback_marked_missing(self, mock_ak, client):
        """BS 无同报告日行回退最新一期 → missing 标注（M1，邻期值不得伪装同期值）。"""
        bs = self._bs_df()[self._bs_df()["报告日"] == "20251231"]
        mock_ak.stock_financial_report_sina.side_effect = lambda stock, symbol: (
            self._inc_df() if symbol == "利润表" else bs
        )
        snap = client.fetch_latest_period_snapshot("688072")
        assert snap["报告日"] == "2026-06-30"  # 快照期仍为中报
        assert snap["存货"] == 78.26  # 回退取 20251231 行
        assert "资产负债表非同期" in " ".join(snap["missing"])

    @patch("finance_agent.data.akshare_client.ak")
    def test_prior_period_nan_values_marked_missing(self, mock_ak, client):
        """同期行存在但营收/归母净利全为 NaN → 同比 None 且 missing 标注（M2）。"""
        inc = self._inc_df()
        inc.loc[inc["报告日"] == "20250630", ["营业总收入", "归母净利润"]] = float("nan")
        mock_ak.stock_financial_report_sina.side_effect = lambda stock, symbol: (
            inc if symbol == "利润表" else self._bs_df()
        )
        snap = client.fetch_latest_period_snapshot("688072")
        assert snap["上年同期营业总收入"] is None
        assert snap["上年同期归母净利润"] is None
        assert snap["营收同比(%)"] is None
        assert snap["归母净利同比(%)"] is None
        assert "上年同期数据缺失" in " ".join(snap["missing"])


class TestFetchIndustryCninfoLatest:
    """cninfo 行业变更史取现行条目：按变更日期降序取最新非空行业中类。

    bug 实录（Task 12 验收发现）：变更史 iloc[0] 是最旧条目——拓荆科技拿到
    2021 年「其它专用机械」而非现行「半导体设备」，行业阈值覆盖永不命中。
    """

    @staticmethod
    def _history() -> pd.DataFrame:
        return pd.DataFrame(
            {
                "变更日期": ["2021-07-12", "2022-03-29"],
                "行业中类": ["其它专用机械", "半导体设备"],
            }
        )

    @patch("finance_agent.data.akshare_client.ak")
    def test_latest_change_date_wins(self, mock_ak, client):
        mock_ak.stock_industry_change_cninfo.return_value = self._history()
        assert client._fetch_industry_cninfo("688072") == "半导体设备"

    @patch("finance_agent.data.akshare_client.ak")
    def test_nan_industry_skipped(self, mock_ak, client):
        df = pd.DataFrame(
            {"变更日期": ["2022-03-29", "2022-03-29"], "行业中类": [float("nan"), "半导体设备"]}
        )
        mock_ak.stock_industry_change_cninfo.return_value = df
        assert client._fetch_industry_cninfo("688072") == "半导体设备"

    @patch("finance_agent.data.akshare_client.ak")
    def test_same_date_multi_standard_prefers_shenwan_zhongzheng(self, mock_ak, client):
        # 688072 实录：同日 2022-04-20 巨潮「集成电路」与中证「半导体设备」并存，
        # 无 tie-break 时不稳定排序随机取「集成电路」→ 行业阈值覆盖不命中
        df = pd.DataFrame(
            {
                "变更日期": ["2021-07-12", "2022-03-29", "2022-03-29", "2022-04-20", "2022-04-20"],
                "行业中类": ["其它专用机械", "半导体设备", float("nan"), "集成电路", "半导体设备"],
                "分类标准": [
                    "申银万国行业分类标准(旧)",
                    "申银万国行业分类标准",
                    "证监会行业分类标准（2012）",
                    "巨潮行业分类标准",
                    "中证行业分类标准",
                ],
            }
        )
        mock_ak.stock_industry_change_cninfo.return_value = df
        assert client._fetch_industry_cninfo("688072") == "半导体设备"


class TestFetchPeerData:
    """update-quote-primary-source T3：同业财务数据抓取复用三级链。

    腾讯主源 mock + spot 快速失败 → 百度回退路径；spot 命中路径见
    TestFetchPeerDataSharedSpot。
    """

    @pytest.fixture(autouse=True)
    def _fast_spot_failure(self, monkeypatch):
        """共享 spot 表拉取快速失败 → 腾讯失败标的命中本类 mock 的百度回退。"""
        with patch("finance_agent.data.akshare_client.ak") as mock_ak:
            mock_ak.stock_zh_a_spot_em.side_effect = ConnectionError("RST")
            monkeypatch.setattr("finance_agent.data.akshare_client._AK_MAX_RETRIES", 1)
            monkeypatch.setattr("finance_agent.data.akshare_client._AK_RETRY_DELAY", 0)
            yield

    @pytest.fixture(autouse=True)
    def _stub_financial_group(self, client, monkeypatch):
        """add-peer-comparison：存量用例不断言财务组——stub 为全失败（字段组级降级路径）。"""

        def _boom(code):
            raise ValueError("stub: 存量用例不覆盖财务组")

        monkeypatch.setattr(client, "fetch_latest_period_snapshot", _boom)

    def test_mixed_success_skips_failed_peer(self, client, monkeypatch):
        calls = []

        def fake_tencent(code):
            calls.append(code)
            if code == "688012":
                return {"name": "中微公司", "code": code, "PB": 10.0}
            return None  # 腾讯失败 → spot(fixture 失败) → 百度 name-only

        monkeypatch.setattr(client, "_fetch_tencent_quote", fake_tencent)
        monkeypatch.setattr(
            client, "_quote_fallback_baidu_tx", lambda code: {"name": "X", "code": code}
        )
        df = client.fetch_peer_data(["688012", "002371"])
        assert df is not None and len(df) == 1
        assert df.iloc[0]["name"] == "中微公司"
        assert df.iloc[0]["PB"] == 10.0
        assert calls == ["688012", "002371"]

    def test_quote_without_pe_pb_skipped(self, client, monkeypatch):
        monkeypatch.setattr(
            client, "_fetch_tencent_quote", lambda code: {"name": "X", "code": code}
        )
        df = client.fetch_peer_data(["600001"])
        assert df is None

    def test_quote_nan_pe_pb_skipped(self, client, monkeypatch):
        """停牌 peer 的 NaN PE/PB（东财 spot 实测行为）按缺数处理，不得写入行毒化均值。"""
        monkeypatch.setattr(
            client,
            "_fetch_tencent_quote",
            lambda code: {"name": "X", "PE": float("nan"), "PB": float("nan")},
        )
        assert client.fetch_peer_data(["600001"]) is None

    def test_nan_pe_with_valid_pb_normalized(self, client, monkeypatch):
        """NaN PE 但 PB 有值：行内 PE 归一为 None，PB 保留。"""
        monkeypatch.setattr(
            client,
            "_fetch_tencent_quote",
            lambda code: {"name": "X", "PE": float("nan"), "PB": 3.2},
        )
        df = client.fetch_peer_data(["600001"])
        assert df is not None and len(df) == 1
        assert df.iloc[0]["PE"] is None
        assert df.iloc[0]["PB"] == 3.2

    def test_all_failed_returns_none(self, client, monkeypatch):
        monkeypatch.setattr(client, "_fetch_tencent_quote", lambda code: None)
        monkeypatch.setattr(
            client, "_quote_fallback_baidu_tx", lambda code: {"name": "X", "code": code}
        )
        assert client.fetch_peer_data(["600001", "600002"]) is None

    def test_empty_input_returns_none(self, client):
        assert client.fetch_peer_data([]) is None


class TestFetchPeerDataHeterogeneousRows:
    """终审 C1/I2：混合双行（一 peer 回退链无 PE、一 peer 东财 PE 有值）时
    DataFrame 构造把 None 强转回 float64 NaN——行级归一在构造边界失效，
    NaN 毒化同业均值并伪装成 fair。出口必须复用 _normalize_nan 根因归一。
    """

    @pytest.fixture(autouse=True)
    def _stub_financial_group(self, client, monkeypatch):
        """add-peer-comparison：存量用例不断言财务组——stub 为全失败（字段组级降级路径）。"""

        def _boom(code):
            raise ValueError("stub: 存量用例不覆盖财务组")

        monkeypatch.setattr(client, "fetch_latest_period_snapshot", _boom)

    @patch("finance_agent.data.akshare_client.ak")
    def test_mixed_none_and_value_pe_no_nan_leak(self, mock_ak, client, monkeypatch):
        monkeypatch.setattr(client, "_fetch_tencent_quote", lambda code: None)
        monkeypatch.setattr("finance_agent.data.akshare_client._AK_MAX_RETRIES", 1)
        monkeypatch.setattr("finance_agent.data.akshare_client._AK_RETRY_DELAY", 0)
        mock_ak.stock_zh_a_spot_em.return_value = pd.DataFrame(
            {
                "名称": ["中微公司"],
                "代码": ["688012"],
                "最新价": [200.0],
                "总市值": [1.5e11],
                "市盈率-动态": [55.0],
                "市净率": [9.0],
            }
        )
        mock_ak.stock_zh_valuation_baidu.return_value = pd.DataFrame({"value": [11.26]})
        mock_ak.stock_zh_a_hist_tx.return_value = pd.DataFrame(
            {"date": ["2026-09-29"], "open": [1.0], "close": [1.1], "high": [1.2], "low": [0.9]}
        )
        df = client.fetch_peer_data(["688012", "002371"])
        assert df is not None and len(df) == 2
        # 出口无 NaN（is None 可靠判空）：spot 行 PE=55.0，回退行 PE=None
        assert df.iloc[0]["PE"] == 55.0
        assert df.iloc[1]["PE"] is None
        assert not any(isinstance(v, float) and pd.isna(v) for v in df["PE"].tolist())


class TestFetchPeerDataSharedSpot:
    """同业批抓取共享单次全市场 spot 表（效率挂账收口：N×spot → 1×）。

    update-quote-primary-source T3：腾讯主源失败后共享 spot 才启用——主源
    健康时逐标的 1 请求、零 spot 调用（见 test_tencent_primary_serves_peers_no_spot）。
    """

    @pytest.fixture(autouse=True)
    def _stub_financial_group(self, client, monkeypatch):
        """add-peer-comparison：存量用例不断言财务组——stub 为全失败（字段组级降级路径）。"""

        def _boom(code):
            raise ValueError("stub: 存量用例不覆盖财务组")

        monkeypatch.setattr(client, "fetch_latest_period_snapshot", _boom)

    @staticmethod
    def _spot_df() -> pd.DataFrame:
        return pd.DataFrame(
            {
                "名称": ["中微公司", "北方华创", "贵州茅台"],
                "代码": ["688012", "002371", "600519"],
                "最新价": [200.0, 300.0, 1800.0],
                "总市值": [1.5e11, 2.0e11, 2.0e12],
                "市盈率-动态": [55.0, 48.0, 25.0],
                "市净率": [9.0, 8.0, 8.5],
            }
        )

    @patch("finance_agent.data.akshare_client.ak")
    def test_spot_table_serves_all_peers_without_fallback(self, mock_ak, client, monkeypatch):
        monkeypatch.setattr(client, "_fetch_tencent_quote", lambda code: None)
        monkeypatch.setattr("finance_agent.data.akshare_client._AK_MAX_RETRIES", 1)
        monkeypatch.setattr("finance_agent.data.akshare_client._AK_RETRY_DELAY", 0)
        mock_ak.stock_zh_a_spot_em.return_value = self._spot_df()
        df = client.fetch_peer_data(["688012", "002371"])
        assert df is not None and len(df) == 2
        assert df.iloc[0]["PE"] == 55.0 and df.iloc[0]["PB"] == 9.0
        # spot 表全覆盖 → 不触发百度/腾讯回退
        mock_ak.stock_zh_valuation_baidu.assert_not_called()
        mock_ak.stock_zh_a_hist_tx.assert_not_called()
        # 共享断言：N peer 只拉 1 次全市场 spot 表（效率挂账：N× → 1×）
        assert mock_ak.stock_zh_a_spot_em.call_count == 1

    @patch("finance_agent.data.akshare_client.ak")
    def test_tencent_primary_serves_peers_no_spot(self, mock_ak, client, monkeypatch):
        """主源健康：逐标的单请求服务全批，MUST NOT 触发东财 spot 翻页。"""

        def fake_tencent(code):
            return {
                "name": {"688012": "中微公司", "002371": "北方华创"}[code],
                "code": code,
                "PB": {"688012": 10.36, "002371": 11.26}[code],
            }

        monkeypatch.setattr(client, "_fetch_tencent_quote", fake_tencent)
        df = client.fetch_peer_data(["688012", "002371"])
        assert df is not None and len(df) == 2
        assert df.iloc[0]["PB"] == 10.36
        # 腾讯全命中 → 东财 spot/百度/腾讯日线零调用（请求量治理的核心断言）
        mock_ak.stock_zh_a_spot_em.assert_not_called()
        mock_ak.stock_zh_valuation_baidu.assert_not_called()

    def test_peer_pe_follows_serving_source(self, client, monkeypatch):
        """腾讯主源无 PE（spec 不消费）→ peer PE=None、PB 有值仍入行（PB 口径比较）。"""
        monkeypatch.setattr(
            client,
            "_fetch_tencent_quote",
            lambda code: {"name": "中微公司", "code": code, "PB": 10.36},
        )
        df = client.fetch_peer_data(["688012"])
        assert df is not None
        assert df.iloc[0]["PE"] is None
        assert df.iloc[0]["PB"] == 10.36

    @patch("finance_agent.data.akshare_client.ak")
    def test_spot_miss_peer_falls_back_to_quote_chain(self, mock_ak, client, monkeypatch):
        monkeypatch.setattr("finance_agent.data.akshare_client._AK_MAX_RETRIES", 1)
        monkeypatch.setattr("finance_agent.data.akshare_client._AK_RETRY_DELAY", 0)
        # 腾讯主源 down：本类钉的是 spot 未命中 → 百度+腾讯日线回退链
        monkeypatch.setattr(client, "_fetch_tencent_quote", lambda code: None)
        mock_ak.stock_zh_a_spot_em.return_value = self._spot_df()
        mock_ak.stock_zh_valuation_baidu.return_value = pd.DataFrame({"value": [11.26]})
        mock_ak.stock_zh_a_hist_tx.return_value = pd.DataFrame(
            {"date": ["2026-09-29"], "open": [1.0], "close": [1.1], "high": [1.2], "low": [0.9]}
        )
        df = client.fetch_peer_data(["688012", "600300"])
        assert df is not None and len(df) == 2
        # 688012 来自 spot 表；600300 表未命中 → 走 quote 回退链（百度 PB，PE 缺）
        assert df.iloc[0]["PE"] == 55.0
        assert df.iloc[1]["PE"] is None and df.iloc[1]["PB"] == 11.26


class TestFetchPeerDataFinancialGroup:
    """add-peer-comparison：估值组+财务组字段扩展与字段组级降级。"""

    def test_extended_columns_full_data(self, client, monkeypatch):
        monkeypatch.setattr(
            client,
            "_fetch_tencent_quote",
            lambda code: {
                "name": "五粮液",
                "code": code,
                "PE": 15.0,
                "PB": 3.2,
                "market_cap": 5e11,
            },
        )
        monkeypatch.setattr(
            client,
            "fetch_latest_period_snapshot",
            lambda code: {
                "营收同比(%)": 7.1,
                "归母净利同比(%)": 8.2,
                "毛利率(%)": 76.5,
                "报告日": "2026-06-30",
            },
        )
        df = client.fetch_peer_data(["000858"])
        assert df is not None and len(df) == 1
        assert list(df.columns) == [
            "name",
            "code",
            "PE",
            "PB",
            "total_mv",
            "revenue_yoy",
            "netprofit_yoy",
            "gross_margin",
            "report_period",
        ]
        row = df.iloc[0]
        assert row["total_mv"] == 5000.0  # 5e11 元 → 5000 亿
        assert row["revenue_yoy"] == 7.1
        assert row["netprofit_yoy"] == 8.2
        assert row["gross_margin"] == 76.5
        assert row["report_period"] == "2026-06-30"

    def test_financial_group_failure_degrades_fields_not_rows(self, client, monkeypatch):
        """财务组全失败：行保留（估值组完整），财务列缺失占位——字段组级降级。"""
        monkeypatch.setattr(
            client,
            "_fetch_tencent_quote",
            lambda code: {"name": "X", "code": code, "PE": 20.0, "PB": 3.0, "market_cap": 1e11},
        )

        def _boom(code):
            raise ValueError(f"股票 {code} 利润表数据不可用")

        monkeypatch.setattr(client, "fetch_latest_period_snapshot", _boom)
        df = client.fetch_peer_data(["000858"])
        assert df is not None and len(df) == 1
        row = df.iloc[0]
        assert row["PE"] == 20.0 and row["total_mv"] == 1000.0
        assert pd.isna(row["revenue_yoy"]) and pd.isna(row["netprofit_yoy"])
        assert pd.isna(row["gross_margin"]) and pd.isna(row["report_period"])

    def test_market_cap_missing_degrades_field_only(self, client, monkeypatch):
        """回退链 quote 无 market_cap：字段 None 占位，不丢行不阻断。"""
        monkeypatch.setattr(
            client,
            "_fetch_tencent_quote",
            lambda code: {"name": "X", "code": code, "PE": 20.0, "PB": 3.0},
        )
        monkeypatch.setattr(
            client,
            "fetch_latest_period_snapshot",
            lambda code: {
                "营收同比(%)": 1.0,
                "归母净利同比(%)": 2.0,
                "毛利率(%)": 50.0,
                "报告日": "2026-06-30",
            },
        )
        df = client.fetch_peer_data(["000858"])
        assert df is not None and len(df) == 1
        assert pd.isna(df.iloc[0]["total_mv"])
        assert df.iloc[0]["revenue_yoy"] == 1.0


class TestFetchTencentQuote:
    """update-quote-primary-source：腾讯 qt.gtimg.cn 单标的行情直查（新主源）。

    金样本：2026-10-01 sh688072 实抓串（field 位序见 _TENCENT_FIELD_IDX）。
    总市值/流通市值单位=亿 ×1e8 归一到元；PE 字段（TTM 口径）MUST NOT 消费。
    """

    GOLDEN = (
        'v_sh688072="1~拓荆科技~688072~640.00~656.68~663.00~3387770~1660152~1727618'
        "~639.79~9~639.75~3~639.47~2~639.00~8~638.99~39~640.00~6~640.04~2~640.14~2"
        "~640.30~10~640.32~4~~20260930161437~-16.68~-2.54~675.00~635.73"
        "~640.00/3387770/2200617467~3387770~220062~1.19~85.97~~675.00~635.73~5.98"
        "~1818.80~1869.91~14.55~788.02~525.34~0.81~~20260930~161437"
    )

    @staticmethod
    def _gbk_response(payload: str):
        resp = requests.Response()
        resp.status_code = 200
        resp._content = payload.encode("gbk")
        return resp

    @patch("finance_agent.data.akshare_client.requests.get")
    def test_golden_sample_mapping(self, mock_get, client):
        mock_get.return_value = self._gbk_response(self.GOLDEN)
        result = client._fetch_tencent_quote("688072")
        assert result is not None
        assert result["name"] == "拓荆科技"
        assert result["code"] == "688072"
        assert result["price"] == 640.00
        assert result["change"] == -16.68
        assert result["pct_change"] == -2.54
        assert result["high"] == 675.00
        assert result["low"] == 635.73
        assert result["turnover_rate"] == 1.19
        assert result["market_cap"] == pytest.approx(1869.91e8)  # 亿 → 元
        assert result["float_market_cap"] == pytest.approx(1818.80e8)
        assert result["PB"] == 14.55

    @patch("finance_agent.data.akshare_client.requests.get")
    def test_pe_field_not_consumed(self, mock_get, client):
        """腾讯串携带 TTM 口径市盈率（field 39=85.97），quote MUST NOT 输出 PE 键。"""
        mock_get.return_value = self._gbk_response(self.GOLDEN)
        result = client._fetch_tencent_quote("688072")
        assert "PE" not in result
        assert "PE_ttm" not in result
        assert "PE_static" not in result

    @patch("finance_agent.data.akshare_client.requests.get")
    def test_truncated_string_missing_market_cap_returns_none(self, mock_get, client):
        """关键字段（price/market_cap）缺失 → None（触发回退），不抛异常。"""
        truncated = 'v_sh688072="1~拓荆科技~688072~640.00'  # 只有 4 段
        mock_get.return_value = self._gbk_response(truncated)
        assert client._fetch_tencent_quote("688072") is None

    @patch("finance_agent.data.akshare_client.requests.get")
    def test_request_exception_returns_none(self, mock_get, client):
        mock_get.side_effect = ConnectionError("refused")
        assert client._fetch_tencent_quote("688072") is None

    @patch("finance_agent.data.akshare_client.requests.get")
    def test_non_numeric_optional_field_skipped(self, mock_get, client):
        """可选字段非数值（如换手率位是 '-'）跳过该键，不炸解析。"""
        bad = self.GOLDEN.replace("~1.19~85.97~", "~-~85.97~")
        mock_get.return_value = self._gbk_response(bad)
        result = client._fetch_tencent_quote("688072")
        assert result is not None
        assert "turnover_rate" not in result
        assert result["market_cap"] == pytest.approx(1869.91e8)

    def test_symbol_prefix(self, client):
        """深市代码走 sz 前缀（_to_sina_symbol 复用）。"""
        assert client._to_sina_symbol("002371") == "sz002371"


class TestQuoteSourceCaliber:
    """口径裁决单测（update-quote-primary-source design §2）。

    双源形：腾讯主源与百度回退各自的 market_cap/PB 期望值独立钉死，
    MUST NOT 跨源融合；market_cap 统一为元；PE 双路径均不产出。
    """

    @patch("finance_agent.data.akshare_client.requests.get")
    def test_tencent_caliber_golden(self, mock_get, client):
        mock_get.return_value = TestFetchTencentQuote._gbk_response(TestFetchTencentQuote.GOLDEN)
        result = client.fetch_stock_quote("688072")
        assert result["market_cap"] == pytest.approx(1869.91e8)  # 元
        assert result["PB"] == 14.55  # 腾讯口径，原样保留
        assert "PE" not in result

    @patch("finance_agent.data.akshare_client.ak")
    def test_baidu_caliber_golden(self, mock_ak, client, monkeypatch):
        monkeypatch.setattr(
            "finance_agent.data.akshare_client.AKShareClient._fetch_tencent_quote",
            lambda self, code: None,
        )
        mock_ak.stock_zh_a_spot_em.return_value = None
        mock_ak.stock_zh_valuation_baidu.side_effect = [
            pd.DataFrame({"date": [date(2026, 9, 7)], "value": [1869.91]}),
            pd.DataFrame({"date": [date(2026, 9, 7)], "value": [14.71]}),
        ]
        mock_ak.stock_zh_a_hist_tx.return_value = pd.DataFrame(
            {"date": [date(2026, 9, 8)], "close": [640.0]}
        )
        mock_ak.stock_info_a_code_name.return_value = pd.DataFrame(
            {"code": ["688072"], "name": ["拓荆科技"]}
        )
        result = client.fetch_stock_quote("688072")
        assert result["market_cap"] == pytest.approx(1869.91e8)  # 元（×1e8 归一）
        assert result["PB"] == 14.71  # 百度口径，与腾讯原样并存、不融合
        assert "PE" not in result


class TestSnapshotCreditImpairment:
    """add-banking-industry-calibration：快照增列信用减值损失及同比（列存在即纳入）。

    光大 601818 形态：-24% 净利增长的真实驱动（信用减值 208.79 亿 vs 159.02 亿，
    +31.3%）——快照不携带该列则报告无法回答「主动提储还是资产质量恶化」。
    """

    def _bank_inc_df(self):
        return pd.DataFrame(
            {
                "报告日": ["20260630", "20250630", "20241231"],
                "营业总收入": [13_620_000_000.0, 12_950_000_000.0, 26_400_000_000.0],
                "营业成本": [None, None, None],
                "归母净利润": [9_050_000_000.0, 11_540_000_000.0, 41_600_000_000.0],
                "信用减值损失": [20_879_000_000.0, 15_902_000_000.0, 32_100_000_000.0],
            }
        )

    def _bank_bs_df(self):
        return pd.DataFrame(
            {
                "报告日": ["20260630"],
                "资产总计": [770_000_000_000.0],
                "负债合计": [700_000_000_000.0],
            }
        )

    @patch("finance_agent.data.akshare_client.ak")
    def test_bank_snapshot_carries_credit_impairment(self, mock_ak, client):
        mock_ak.stock_financial_report_sina.side_effect = lambda stock, symbol: (
            self._bank_inc_df() if symbol == "利润表" else self._bank_bs_df()
        )
        snap = client.fetch_latest_period_snapshot("601818")
        assert snap["信用减值损失"] == 208.79
        # 同比按原始元值计算（#190 纪律）：(208.79 - 159.02)/159.02 = 31.30%
        assert snap["信用减值损失同比(%)"] == 31.3

    @patch("finance_agent.data.akshare_client.ak")
    def test_no_credit_impairment_column_absent(self, mock_ak, client):
        inc = self._bank_inc_df().drop(columns=["信用减值损失"])
        mock_ak.stock_financial_report_sina.side_effect = lambda stock, symbol: (
            inc if symbol == "利润表" else self._bank_bs_df()
        )
        snap = client.fetch_latest_period_snapshot("601818")
        assert "信用减值损失" not in snap
        assert "信用减值损失同比(%)" not in snap

    @patch("finance_agent.data.akshare_client.ak")
    def test_credit_impairment_no_prior_period_absent(self, mock_ak, client):
        inc = self._bank_inc_df()[self._bank_inc_df()["报告日"] != "20250630"]
        mock_ak.stock_financial_report_sina.side_effect = lambda stock, symbol: (
            inc if symbol == "利润表" else self._bank_bs_df()
        )
        snap = client.fetch_latest_period_snapshot("601818")
        assert "信用减值损失同比(%)" not in snap
