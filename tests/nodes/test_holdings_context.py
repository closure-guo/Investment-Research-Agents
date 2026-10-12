"""add-holdings-context：受众持仓上下文（模型 / 声明 / 请求接入 / 注入 / 门禁）。

三态语义：True=已申报持有、False=已申报未持有、None=未申报。未申报 MUST NOT
被等同于「未持有」——二者对方向可执行性的含义不同。
"""

from finance_agent.models import HoldingsContext
from finance_agent.nodes._llm_utils import holdings_context_line, holdings_holds
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


class TestHoldingsContextLine:
    """三态均注入（与 focus_hint 的空则零注入刻意不同）——「未申报」本身是决策所需信息。"""

    def _state(self, holds):
        return {"holdings_context": HoldingsContext(holds=holds)}

    def test_declared_holding_line(self):
        line = holdings_context_line(self._state(True))
        assert "已申报持有" in line
        assert "exit" in line

    def test_declared_not_holding_line(self):
        line = holdings_context_line(self._state(False))
        assert "已申报未持有" in line
        assert "watch" in line
        assert "MUST NOT 输出 exit" in line

    def test_undeclared_line_not_omitted(self):
        line = holdings_context_line(self._state(None))
        assert line, "未申报 MUST NOT 省略受众持仓行"
        assert "未申报" in line
        assert "未持有" in line  # 要求说明未持有情形的对应处置

    def test_missing_state_key_falls_back_to_undeclared(self):
        assert "未申报" in holdings_context_line({})

    def test_dict_form_compatible(self):
        assert holdings_holds({"holdings_context": {"holds": False}}) is False
        assert holdings_holds({"holdings_context": {"holds": True}}) is True
        assert holdings_holds({}) is None


class TestTraderContextInjection:
    def test_trader_context_carries_holdings_line(self):
        from finance_agent.nodes.trader import _build_trader_context

        ctx = _build_trader_context({"holdings_context": HoldingsContext(holds=False)})
        assert "受众持仓: 已申报未持有该标的" in ctx


class TestRiskContextInjection:
    def test_risk_context_carries_holdings_line(self):
        from finance_agent.nodes.risk import _build_risk_context

        ctx = _build_risk_context({"holdings_context": HoldingsContext(holds=True)})
        assert "受众持仓: 已申报持有该标的" in ctx
