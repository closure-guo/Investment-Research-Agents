"""toolize-price-levels Task 3：quick 行情快照 + 技术派生值注入 测试。"""

from unittest.mock import patch

from finance_agent.agent_factory import _format_stock_result
from finance_agent.nodes.analysts import _build_technical_context


class TestSearchStockSnapshot:
    def test_single_candidate_appends_snapshot(self):
        result = {
            "found": True,
            "candidates": [{"stock_code": "600519", "stock_name": "贵州茅台"}],
        }
        quote = {"price": 1500.0, "pct_change": 2.5}
        with patch("finance_agent.agent_factory._fetch_quote_snapshot", return_value=quote):
            text = _format_stock_result(result)
        assert "现价 1500.0 元" in text
        assert "+2.5%" in text

    def test_quote_missing_notes_absence(self):
        result = {"found": True, "candidates": [{"stock_code": "600519", "stock_name": "贵州茅台"}]}
        with patch("finance_agent.agent_factory._fetch_quote_snapshot", return_value=None):
            text = _format_stock_result(result)
        assert "行情快照缺失" in text

    def test_multi_candidate_no_snapshot(self):
        result = {
            "found": True,
            "candidates": [
                {"stock_code": "600519", "stock_name": "贵州茅台"},
                {"stock_code": "000858", "stock_name": "五粮液"},
            ],
        }
        with patch("finance_agent.agent_factory._fetch_quote_snapshot") as m:
            text = _format_stock_result(result)
        m.assert_not_called()
        assert "600519" in text


class TestDerivedSeriesInjection:
    def test_derived_table_in_context(self):
        state = {
            "technical_indicators": {"MA": {"5": [1, 2]}},
            "derived_series": {
                "chg_5d": 0.03,
                "chg_20d": None,
                "drawdown_from_high_250d": -0.1,
                "rebound_from_low_250d": 0.15,
            },
        }
        ctx = _build_technical_context(state)
        assert "常用派生值" in ctx
        assert "chg_5d" in ctx
        assert "数据不足" in ctx  # None 项如实标注
        assert "derived_series." in ctx  # 前缀必须等于真实 state 键（否则 claim 不可解析）
        assert "前缀 derived." not in ctx  # 旧前缀不得回归（此 ctx 才有派生值段）

    def test_no_derived_no_section(self):
        ctx = _build_technical_context({"technical_indicators": {}})
        assert "常用派生值" not in ctx
