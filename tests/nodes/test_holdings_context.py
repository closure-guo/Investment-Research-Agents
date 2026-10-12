"""add-holdings-context：受众持仓上下文（模型 / 声明 / 请求接入 / 注入 / 门禁）。

三态语义：True=已申报持有、False=已申报未持有、None=未申报。未申报 MUST NOT
被等同于「未持有」——二者对方向可执行性的含义不同。
"""

from finance_agent.models import HoldingsContext
from finance_agent.state import AnalysisState


class TestHoldingsContextModel:
    def test_default_is_undeclared(self):
        assert HoldingsContext().holds is None

    def test_declared_not_holding_preserved(self):
        """未持有 MUST NOT 被归一为未申报（三态不是二态）。"""
        assert HoldingsContext(holds=False).holds is False

    def test_declared_holding(self):
        assert HoldingsContext(holds=True).holds is True


class TestStateDeclaration:
    def test_keys_declared(self):
        """未声明的键会被图合并静默丢弃（incident 027）——门禁显式断言。"""
        assert "holdings_context" in AnalysisState.__annotations__
        assert "holdings_executability_check" in AnalysisState.__annotations__

    def test_holdings_context_in_graph_channels(self):
        from finance_agent.graph import build_5layer_graph

        graph = build_5layer_graph()
        for key in ("holdings_context", "holdings_executability_check"):
            assert key in AnalysisState.__annotations__
            assert key in graph.channels
