"""TDD tests for nodes/fetch.py — fetch_data 节点。

fetch_data 职责：
1. Step1 并行拉取无依赖数据（三大报表 + 行情 + 行业归属 + 预计算指标）
2. Step2 拉取同业数据（依赖 Step1 的行业归属）
3. 拉取后写入缓存
4. 数据降级：三大报表缺失 → 报错终止；同业缺失 → 标记 N/A 继续
"""

from contextlib import contextmanager
from unittest.mock import MagicMock

import pandas as pd
import pytest

import finance_agent.nodes.fetch as fetch_mod
from finance_agent.nodes.fetch import _summarize_success_output, fetch_data


def _make_balance_sheet():
    return pd.DataFrame({"报告日": ["20241231", "20231231"], "资产总计": [1000.0, 900.0]})


def _make_income():
    return pd.DataFrame({"报告日": ["20241231", "20231231"], "营业收入": [1000.0, 900.0]})


def _make_cashflow():
    return pd.DataFrame(
        {"报告日": ["20241231", "20231231"], "经营活动产生的现金流量净额": [250.0, 220.0]}
    )


def _make_indicators():
    return pd.DataFrame({"日期": ["2024-12-31", "2023-12-31"], "销售毛利率(%)": [40.0, 38.0]})


def _setup_client():
    mock = MagicMock()
    mock.fetch_balance_sheet.return_value = _make_balance_sheet()
    mock.fetch_income_statement.return_value = _make_income()
    mock.fetch_cash_flow.return_value = _make_cashflow()
    mock.fetch_indicators.return_value = _make_indicators()
    mock.fetch_industry.return_value = {"industry": "白酒", "name": "贵州茅台"}
    mock.fetch_stock_quote.return_value = {"price": 1800.0, "name": "贵州茅台", "code": "600519"}
    return mock


class TestFetchDataBasic:
    def test_fills_all_state_fields(self):
        mock_client = _setup_client()
        mock_cache = MagicMock()
        state = {"stock_code": "600519"}
        result = fetch_data(state, cache=mock_cache, client=mock_client)

        assert result["balance_sheet"] is not None
        assert result["income_statement"] is not None
        assert result["cash_flow_statement"] is not None
        assert result["financial_indicators"] is not None
        assert result["industry_info"]["industry"] == "白酒"
        assert result["stock_quote"]["price"] == 1800.0

    def test_writes_to_cache(self):
        mock_client = _setup_client()
        mock_cache = MagicMock()
        state = {"stock_code": "600519"}
        fetch_data(state, cache=mock_cache, client=mock_client)

        assert mock_cache.set.call_count >= 3
        cached_keys = [call[0][0] for call in mock_cache.set.call_args_list]
        assert "600519:balance_sheet" in cached_keys
        assert "600519:income_statement" in cached_keys
        assert "600519:cash_flow_statement" in cached_keys


class TestFetchDataDegradation:
    def test_required_data_missing_raises(self):
        """三大报表拉取失败应报错终止。"""
        mock_client = MagicMock()
        mock_client.fetch_balance_sheet.side_effect = Exception("API error")
        mock_cache = MagicMock()
        state = {"stock_code": "600519"}
        with pytest.raises(Exception, match="API error"):
            fetch_data(state, cache=mock_cache, client=mock_client)

    def test_peer_data_missing_marks_na(self):
        """同业数据拉取失败时标记 N/A 继续。"""
        mock_client = _setup_client()
        mock_client.fetch_peer_data.side_effect = Exception("no peers")
        mock_cache = MagicMock()
        state = {"stock_code": "600519", "peer_codes": ["000858"]}
        result = fetch_data(state, cache=mock_cache, client=mock_client)
        assert result.get("peer_financials") is None


class TestFetchDataSpanObservability:
    """span 命名与失败 level 追踪（spec trace-observability：data_source:{source} 约定）。

    AKShare 子 span SHALL 命名为 `data_source:akshare:{label}`，失败 SHALL 标
    `level="ERROR"`，使 incident 008 类卡死/失败可定位到具体子调用。
    """

    def test_span_naming_uses_data_source_prefix(self, monkeypatch):
        """span 名以 data_source:akshare: 前缀（spec data_source:{source} 约定）。"""
        mock_client = _setup_client()
        captured: list[str] = []

        @contextmanager
        def _spy_span(name, input=None):
            captured.append(name)
            yield MagicMock()

        monkeypatch.setattr(fetch_mod, "open_span", _spy_span)

        fetch_mod.fetch_data(
            {"stock_code": "600519", "stock_name": "贵州茅台"},
            cache=MagicMock(),
            client=mock_client,
        )

        assert captured, "未捕获任何 span"
        # 收紧：fetch.py 内所有 span SHALL 都用 data_source: 前缀
        assert all(s.startswith("data_source:") for s in captured), (
            f"非所有 span 都用 data_source 前缀: {captured}"
        )
        assert any(s.startswith("data_source:akshare:") for s in captured), (
            f"未捕获 data_source:akshare: 前缀 span: {captured}"
        )

    def test_span_marked_error_on_subcall_failure(self, monkeypatch):
        """AKShare 非必需子调用失败时，对应 span 标 level=ERROR。"""
        mock_client = _setup_client()
        # 非必需指标拉取抛异常 → 走 warning 降级（不 raise），span 应标 ERROR
        mock_client.fetch_indicators.side_effect = RuntimeError("timeout")

        updates: list[dict] = []

        class _FakeObs:
            def update(self, **kwargs):
                updates.append(kwargs)

        @contextmanager
        def _spy_span(name, input=None):
            yield _FakeObs()

        monkeypatch.setattr(fetch_mod, "open_span", _spy_span)

        fetch_mod.fetch_data(
            {"stock_code": "600519", "stock_name": "贵州茅台"},
            cache=MagicMock(),
            client=mock_client,
        )

        assert any(u.get("level") == "ERROR" for u in updates), (
            f"失败 span 未标 level=ERROR: {updates}"
        )

    def test_span_output_summarizes_dataframe_returns(self, monkeypatch):
        """DataFrame 返回时 span output 含 rows + columns 摘要。

        spec trace-observability「DataFrame 返回只记摘要」：AKShare 返回
        pandas DataFrame 时，span output SHALL 只记 {"rows": N, "columns": [...]}
        摘要，且不尝试序列化完整 DataFrame。
        """
        mock_client = _setup_client()
        captured: list[tuple[str, dict]] = []

        class _FakeObs:
            def __init__(self, name):
                self._name = name

            def update(self, **kwargs):
                captured.append((self._name, kwargs))

        @contextmanager
        def _spy_span(name, input=None):
            yield _FakeObs(name)

        monkeypatch.setattr(fetch_mod, "open_span", _spy_span)

        fetch_mod.fetch_data(
            {"stock_code": "600519", "stock_name": "贵州茅台"},
            cache=MagicMock(),
            client=mock_client,
        )

        # balance_sheet 经 _setup_client mock 返回 2 行 DataFrame（报告日 + 资产总计）
        bs_outputs = [
            kwargs["output"]
            for (n, kwargs) in captured
            if n == "data_source:akshare:balance_sheet" and "output" in kwargs
        ]
        assert bs_outputs, f"balance_sheet span output 未捕获: {captured}"
        # 每个 span 只写一次 output（取 [0] 会掩盖重复写：多写一次也没人发现）
        assert len(bs_outputs) == 1, f"balance_sheet span output 应只写一次: {bs_outputs}"
        output = bs_outputs[0]
        assert output["status"] == "success", f"成功路径 status 应为 success: {output}"
        assert output["rows"] == 2, f"balance_sheet 行数应为 2: {output}"
        assert "报告日" in output["columns"], f"columns 缺报告日: {output}"
        assert "资产总计" in output["columns"], f"columns 缺资产总计: {output}"
        # 关键约束：不序列化完整 DataFrame（output 不含任何 DataFrame 引用）
        assert not any(isinstance(v, pd.DataFrame) for v in output.values()), (
            f"output 误含完整 DataFrame: {output}"
        )


class TestSummarizeSuccessOutput:
    """`_summarize_success_output` 直接单测（#49）：列截断分支与兜底分支。

    此前只经 fetch_data 端到端间接覆盖（且只取首个 output），>20 列截断与
    非 DataFrame 兜底两条分支无直测——分支改动无人拦。
    """

    def test_dataframe_summary_rows_and_columns(self):
        df = pd.DataFrame({"a": [1, 2, 3], "b": [4, 5, 6]})
        out = _summarize_success_output(df)
        assert out == {"status": "success", "rows": 3, "columns": ["a", "b"]}

    def test_many_columns_truncated_to_20_plus_ellipsis(self):
        cols = [f"c{i}" for i in range(25)]
        df = pd.DataFrame({c: [1] for c in cols})
        out = _summarize_success_output(df)
        assert out["columns"] == cols[:20] + ["..."]
        assert len(out["columns"]) == 21

    def test_exactly_20_columns_not_truncated(self):
        """边界：恰 20 列不截断（不得平白加 "..." 噪声）。"""
        cols = [f"c{i}" for i in range(20)]
        df = pd.DataFrame({c: [1] for c in cols})
        assert _summarize_success_output(df)["columns"] == cols

    def test_non_dataframe_falls_back_to_status_only(self):
        """非 DataFrame（list/dict/None）只记 status，不臆造 rows/columns。"""
        for value in ([{"x": 1}], {"industry": "白酒"}, None, "text"):
            out = _summarize_success_output(value)
            assert out == {"status": "success"}, f"{value!r} 摘要应为纯 status: {out}"


class TestFetchDataMonitoring:
    """数据源失败埋点（非侵入）：可选数据拉取失败 → monitor 记 fail，不改降级。"""

    def test_fetch_failure_records_fail(self):
        """非必需数据（news_list）抛错 → fails 记对应 label 且降级继续。

        埋点 label 沿用 fetch 结果字段名（news_list；简报示例 'news' 为意图描述，
        实现实际记录值为 fetch 循环的 futures label）。
        """
        from finance_agent.data.monitoring import get_monitor, reset_monitor_for_tests

        reset_monitor_for_tests()
        try:
            mock_client = _setup_client()
            mock_client.fetch_news.side_effect = RuntimeError("akshare timeout")
            state = {"stock_code": "600519"}
            result = fetch_data(state, cache=MagicMock(), client=mock_client)
            # 降级契约不变：可选数据缺失标记 N/A，调用整体成功返回
            assert result["news_list"] == []
            snap = get_monitor().snapshot()
            assert snap["fails"].get("news_list", 0) >= 1
        finally:
            reset_monitor_for_tests()


class TestFetchDataCoverageSources:
    """新信源装配测试（add-analyst-data-coverage Task 3，TDD 先行）。"""

    @staticmethod
    def _full_client():
        mock = MagicMock()
        mock.fetch_balance_sheet.return_value = _make_balance_sheet()
        mock.fetch_income_statement.return_value = _make_income()
        mock.fetch_cash_flow.return_value = _make_cashflow()
        mock.fetch_indicators.return_value = _make_indicators()
        mock.fetch_industry.return_value = {"industry": "白酒", "name": "贵州茅台"}
        mock.fetch_stock_quote.return_value = {
            "price": 1800.0,
            "name": "贵州茅台",
            "code": "600519",
        }
        mock.fetch_kline.return_value = pd.DataFrame({"收盘": [1.0]})
        mock.fetch_benchmark_kline.return_value = pd.DataFrame({"收盘": [1.0]})
        mock.fetch_industry_pe.return_value = {"industry_pe": 30.0}
        mock.fetch_quarterly_income.return_value = pd.DataFrame({"报告日": ["20260630"]})
        mock.fetch_macro_indicators.return_value = {}
        mock.fetch_news.return_value = []
        mock.fetch_announcements.return_value = [
            {
                "title": "2026年半年度报告",
                "date": "2026-08-15",
                "category": "财务报告",
                "url": "http://a/1",
            }
        ]
        mock.fetch_research_reports.return_value = [
            {
                "title": "中报点评",
                "org": "浙商证券",
                "rating": "买入",
                "target_price": 1600.0,
                "date": "2026-08-20",
            }
        ]
        mock.fetch_share_unlock.return_value = [
            {
                "date": "2026-10-09",
                "shares": 120000000,
                "market_value": 14300000000,
                "pct_float": 1.23,
            }
        ]
        mock.fetch_block_trades.return_value = [
            {
                "date": "2026-09-10",
                "price": 1270.0,
                "volume": 100000,
                "amount": 127000000.0,
                "premium": -0.5,
                "buyer": "机构专用",
                "seller": "某营业部",
            }
        ]
        return mock

    def test_new_sources_in_state(self):
        result = fetch_data({"stock_code": "600519"}, cache=MagicMock(), client=self._full_client())
        assert result["announcements"][0]["title"] == "2026年半年度报告"
        assert result["research_reports"][0]["rating"] == "买入"
        assert result["share_unlock"][0]["date"] == "2026-10-09"
        assert result["block_trades"][0]["price"] == 1270.0

    def test_new_sources_cached_with_hourly_ttl(self):
        cache = MagicMock()
        fetch_data({"stock_code": "600519"}, cache=cache, client=self._full_client())
        cached = {call[0][0]: call[1] for call in cache.set.call_args_list}
        for key in (
            "600519:announcements",
            "600519:research_reports",
            "600519:share_unlock",
            "600519:block_trades",
        ):
            assert key in cached, key
            assert cached[key].get("ttl_seconds") == 3600

    def test_failure_degrades_to_empty(self):
        client = self._full_client()
        client.fetch_announcements.side_effect = RuntimeError("boom")
        result = fetch_data({"stock_code": "600519"}, cache=MagicMock(), client=client)
        assert result["announcements"] == []


class TestFetchPeersGuard:
    """终审 I2：_fetch_peers 节点级守卫直测；空表归一 None。

    「未指定 peer_codes 不抓取」契约经 complete-peer-pipeline 收窄：无显式
    列表时先走自动选取（行业成分 Top5 排除自身），成分不可得才保持不抓取。
    """

    def test_no_peer_codes_no_fetch_call(self):
        from finance_agent.nodes.fetch import _fetch_peers

        calls = []

        class _Ak:
            def fetch_peer_data(self, codes):
                calls.append(codes)
                return None

            def fetch_industry_constituents(self, industry):
                # 自动选取不可得（接口失败降级 None）→ 保持不抓取
                return None

        assert (
            _fetch_peers(_Ak(), "688072", {"peer_codes": None}, {"industry": "半导体设备"}) is None
        )
        assert calls == []

    def test_empty_df_normalized_to_none(self):
        import pandas as pd

        from finance_agent.nodes.fetch import _fetch_peers

        class _Ak:
            def fetch_peer_data(self, codes):
                return pd.DataFrame({"name": [], "code": [], "PE": [], "PB": []})

        assert (
            _fetch_peers(_Ak(), "688072", {"peer_codes": ["688012"]}, {"industry": "半导体设备"})
            is None
        )
