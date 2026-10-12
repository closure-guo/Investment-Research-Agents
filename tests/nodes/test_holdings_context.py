"""add-holdings-context：受众持仓上下文（模型 / 声明 / 请求接入 / 注入 / 门禁）。

三态语义：True=已申报持有、False=已申报未持有、None=未申报。未申报 MUST NOT
被等同于「未持有」——二者对方向可执行性的含义不同。
"""

from __future__ import annotations

import time
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from finance_agent.models import HoldingsContext
from finance_agent.nodes._llm_utils import holdings_context_line, holdings_holds
from finance_agent.state import AnalysisState

# 三态 → 注入行固定前缀。Trader / Risk 两层共用同一 holdings_context_line 实现，
# 任一层文案漂移都会被两侧表驱动用例同时拦下。
_HOLDINGS_LINE_PREFIX: dict[bool | None, str] = {
    True: "受众持仓: 已申报持有该标的",
    False: "受众持仓: 已申报未持有该标的",
    None: "受众持仓: 未申报",
}

# 三态 → (声明值, 期望 holds)：逐态断言，防止「未申报」被静默归一
_HOLDS_ROUNDTRIP = [
    pytest.param(True, True, id="declared-holding"),
    pytest.param(False, False, id="declared-not-holding"),
    pytest.param(None, None, id="undeclared"),
]

# 三态 → (声明值, 注入行固定前缀)
_HOLDS_LINE_CASES = [
    pytest.param(True, _HOLDINGS_LINE_PREFIX[True], id="declared-holding"),
    pytest.param(False, _HOLDINGS_LINE_PREFIX[False], id="declared-not-holding"),
    pytest.param(None, _HOLDINGS_LINE_PREFIX[None], id="undeclared"),
]


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
        """本用例守的是 incident 027 的静默丢弃，不是 LangGraph API 契约。

        （键是否在 AnalysisState.__annotations__ 已由 test_keys_declared 覆盖，
        此处不重复；只有键进了图 channels 才不会被图合并丢掉。）
        """
        from finance_agent.graph import build_5layer_graph

        graph = build_5layer_graph()
        for key in ("holdings_context", "holdings_executability_check"):
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

    @pytest.mark.parametrize(
        "state",
        [
            pytest.param({"holdings_context": None}, id="context-none"),
            pytest.param({"holdings_context": {"holds": None}}, id="dict-holds-none"),
            pytest.param({"holdings_context": {"holds": "yes"}}, id="non-bool-truthy-str"),
        ],
    )
    def test_malformed_forms_render_undeclared_line(self, state):
        """非 bool 值 ⇒ 未申报：缺键 / 显式 None / 误传非 bool 一律不得臆测持有与否。"""
        line = holdings_context_line(state)
        assert "受众持仓: 未申报" in line
        # 且不得渲染成已申报文案
        assert "已申报" not in line

    @pytest.mark.parametrize(
        "state",
        [
            pytest.param({"holdings_context": None}, id="context-none"),
            pytest.param({"holdings_context": {"holds": None}}, id="dict-holds-none"),
            pytest.param({"holdings_context": {"holds": "yes"}}, id="non-bool-truthy-str"),
        ],
    )
    def test_non_bool_normalized_to_none(self, state):
        """holdings_holds 返回类型契约为 bool | None（非 bool 值归一 None，不把 "yes" 传出去）。"""
        assert holdings_holds(state) is None


class _CapturingGraph:
    """图替身：记录 graph.stream 收到的 initial_state，并按需产出 updates 流。"""

    def __init__(self, chunks: list[Any] | None = None) -> None:
        self.received_states: list[dict] = []
        self._chunks = chunks or []

    def stream(self, state: dict, config: dict | None = None, stream_mode: Any = None):  # noqa: ARG002
        self.received_states.append(state)
        yield from self._chunks


def _run_graph_streaming_with(req: Any, fake_graph: _CapturingGraph) -> list[str]:
    """在 Langfuse 关闭 + fake graph 下跑完 api._run_graph_streaming（同 tests/test_api_blocked_terminal.py）。"""
    from finance_agent.api import _run_graph_streaming

    with (
        patch("finance_agent.langfuse_tracing.get_langfuse", return_value=None),
        patch("finance_agent.langfuse_tracing.get_callback_handler", return_value=None),
        patch("finance_agent.api.graph", fake_graph),
    ):
        return list(
            _run_graph_streaming(
                stock_code=req.stock_code,
                stock_name="贵州茅台",
                req=req,
                analysis_id="a1",
                start_time=time.time(),
            )
        )


class TestRequestHoldingsIntake:
    """spec Scenario：GIVEN 分析请求携带声明 WHEN 构建初始 state THEN 声明三态保真。"""

    @pytest.mark.parametrize(("declared", "expected"), _HOLDS_ROUNDTRIP)
    def test_declared_holdings_reaches_graph_initial_state(self, declared, expected):
        from finance_agent.api import AnalyzeRequest

        fake = _CapturingGraph()
        req = AnalyzeRequest(stock_code="600519", query="分析贵州茅台", holdings=declared)
        events = _run_graph_streaming_with(req, fake)

        assert events, "SSE 事件流不应为空（确认真的跑进了图）"
        assert len(fake.received_states) == 1
        ctx = fake.received_states[0]["holdings_context"]
        assert isinstance(ctx, HoldingsContext), "initial_state 必须携带 HoldingsContext 对象"
        assert ctx.holds is expected


class TestTraderContextInjection:
    @pytest.mark.parametrize(("holds", "expected_line"), _HOLDS_LINE_CASES)
    def test_trader_context_carries_holdings_line(self, holds, expected_line):
        from finance_agent.nodes.trader import _build_trader_context

        ctx = _build_trader_context({"holdings_context": HoldingsContext(holds=holds)})
        assert expected_line in ctx


class TestRiskContextInjection:
    @pytest.mark.parametrize(("holds", "expected_line"), _HOLDS_LINE_CASES)
    def test_risk_context_carries_holdings_line(self, holds, expected_line):
        """三个风险辩论者共用 _build_risk_context——三态均须注入且不省略未申报行。"""
        from finance_agent.nodes.risk import _build_risk_context

        ctx = _build_risk_context({"holdings_context": HoldingsContext(holds=holds)})
        assert expected_line in ctx


_FINAL_REPORT_CHUNK = (
    "updates",
    {
        "generate_report": {
            "final_report": "# 报告\n\n正文",
            "chart_data": {},
            "analyst_reports": {},
        }
    },
)


class TestReactPathHoldings:
    """追问/复用旧会话的 ReAct 路径（build_agent → run_deep_analysis 工具）三态保真。

    此前该路径 initial_state 不含 holdings_context：显式声明被静默降级为「未申报」，
    恰好放行本变更要拦的情形（未持有受众仍输出 exit）。
    """

    @pytest.mark.parametrize(("declared", "expected"), _HOLDS_ROUNDTRIP)
    async def test_build_agent_holdings_reaches_tool_initial_state(self, declared, expected):
        from finance_agent.agent_factory import build_agent
        from finance_agent.tool_registry import TOOL_RUN_DEEP_ANALYSIS

        fake = _CapturingGraph([_FINAL_REPORT_CHUNK])
        with (
            # 预算派生会对真实 endpoint 做能力探测（延迟取决于本机网络，数秒~数十秒），
            # 与 holdings 接线无关，stub 掉避免用例受网络抖动影响；
            # 被测链路（build_agent → 工具闭包 → initial_state）保持真实
            patch("finance_agent.agent_factory._build_context_budget", return_value=None),
            patch("finance_agent.graph.build_5layer_graph", return_value=fake),
        ):
            agent = build_agent(mode="deep", api_key="test-key", holdings=declared)
            tool_fn = agent.tools.tools[TOOL_RUN_DEEP_ANALYSIS]
            async for _ in tool_fn(stock_code="600519", stock_name="贵州茅台"):
                pass

        assert len(fake.received_states) == 1
        ctx = fake.received_states[0]["holdings_context"]
        assert isinstance(ctx, HoldingsContext)
        assert ctx.holds is expected

    async def test_react_analysis_forwards_request_holdings_to_build_agent(self):
        """api._run_react_analysis（追问入口）必须把 req.holdings 交给 build_agent。"""
        from finance_agent.api import AnalyzeRequest, _run_react_analysis

        captured: dict = {}

        def fake_build_agent(**kwargs):
            captured.update(kwargs)
            return MagicMock()

        async def fake_stream_agent_to_sse(agent, query, **kwargs):  # noqa: ARG001
            return
            yield  # pragma: no cover -- 空异步生成器（无事件即结束）

        fake_registry = MagicMock()
        fake_registry.publish = AsyncMock()
        fake_registry.publish_many = AsyncMock()
        fake_registry.publish_terminal = AsyncMock()

        req = AnalyzeRequest(stock_code="600519", query="分析贵州茅台", holdings=False)
        with (
            patch("finance_agent.agent_factory.build_agent", new=fake_build_agent),
            patch("finance_agent.agent_factory.stream_agent_to_sse", new=fake_stream_agent_to_sse),
            patch("finance_agent.api.registry", fake_registry),
        ):
            await _run_react_analysis(
                session_id="holdings-wiring-sess",
                req=req,
                analysis_id="a1",
                start_time=time.time(),
                display_name=None,
                api_key=None,
            )

        assert captured["mode"] == "deep"
        assert captured["holdings"] is False


class TestNotHoldingExitGate:
    """holds=False + sell/exit → 打回重申一次 → 仍犯降级 watch（复用滞回归一模式）。"""

    def _state(self, holds):
        from finance_agent.nodes.risk import risk_judge  # noqa: F401

        return {
            "holdings_context": HoldingsContext(holds=holds),
            "trader_plan": {
                "action": "sell",
                "confidence": 0.5,
                "reasoning": "看空",
                "sell_type": "exit",
                "exit_schedule": "现价清仓",
            },
            "analyst_reports": {},
        }

    def test_not_holding_exit_retries_once_then_downgrades(self, monkeypatch):
        from finance_agent.nodes import risk as risk_mod

        calls: list[str] = []

        def _fake_llm(context, **kwargs):
            calls.append(context)
            # reeval_triggers / trigger_low 是夹具中性字段（同 test_risk._sell_untyped_json
            # 惯例）：否则「再评估触发条件」「触发位申报」两条无关门禁会各多打回一次，
            # 污染本用例「恰打回一次」的调用计数；降级后的 watch 终稿继承它们，
            # 不再误入后续打回回路
            return {
                "action": "sell",
                "confidence": 0.5,
                "reasoning": "看空",
                "sell_type": "exit",
                "exit_schedule": "现价清仓",
                "reeval_triggers": ["跌破 600 重估"],
                "trigger_low": 600.0,
            }

        monkeypatch.setattr(risk_mod, "call_llm_for_json", _fake_llm)
        monkeypatch.setattr(risk_mod, "_build_hysteresis_context", lambda s: "")
        result = risk_mod.risk_judge(self._state(False))
        assert len(calls) == 2, "应恰打回重申一次"
        assert "无敞口可退" in calls[1]
        decision = result["final_trade_decision"]
        assert decision.action == "watch"
        assert decision.sell_type is None
        assert decision.exit_schedule is None
        assert decision.entry_price is None and decision.stop_loss is None
        check = result["holdings_executability_check"]
        assert check["result"] == "fail"
        assert "降级观望" in check["note"]
        assert "未持有无敞口可退" in decision.inaction_reason

    def test_not_holding_exit_corrected_on_retry_passes(self, monkeypatch):
        from finance_agent.nodes import risk as risk_mod

        seq = [
            {
                "action": "sell",
                "confidence": 0.5,
                "reasoning": "看空",
                "sell_type": "exit",
                "exit_schedule": "现价清仓",
            },
            {
                "action": "watch",
                "confidence": 0.5,
                "reasoning": "改判观望",
                "inaction_reason": "受众未持有，无敞口可退",
                "reeval_triggers": ["跌破 600"],
                "trigger_low": 600.0,
            },
        ]
        monkeypatch.setattr(risk_mod, "call_llm_for_json", lambda context, **k: seq.pop(0))
        monkeypatch.setattr(risk_mod, "_build_hysteresis_context", lambda s: "")
        result = risk_mod.risk_judge(self._state(False))
        assert result["final_trade_decision"].action == "watch"
        assert result["holdings_executability_check"]["result"] == "pass"
        assert "改判" in result["holdings_executability_check"]["note"]

    @pytest.mark.parametrize("holds", [True, None])
    def test_gate_not_triggered_for_holding_or_undeclared(self, holds, monkeypatch):
        from finance_agent.nodes import risk as risk_mod

        calls: list[str] = []

        def _fake_llm(context, **kwargs):
            calls.append(context)
            # 夹具中性字段（同 test_risk._sell_untyped_json 惯例）：避免「再评估触发条件」
            # 门禁多打回一次，污染「未触发门禁时恰一次调用」的计数
            return {
                "action": "sell",
                "confidence": 0.5,
                "reasoning": "看空",
                "sell_type": "exit",
                "exit_schedule": "现价清仓",
                "reeval_triggers": ["跌破 600 重估"],
            }

        monkeypatch.setattr(risk_mod, "call_llm_for_json", _fake_llm)
        monkeypatch.setattr(risk_mod, "_build_hysteresis_context", lambda s: "")
        result = risk_mod.risk_judge(self._state(holds))
        assert len(calls) == 1, "未触发门禁时不得额外调用 LLM"
        assert result["final_trade_decision"].action == "sell"
        assert result["holdings_executability_check"]["result"] == "pass"


class TestFinalSellTypeCollapse:
    """T5 收口：打回换代（减仓节奏/理由/再评估/触发位/价位交叉校验）后 LLM 再丢
    sell_type 时终稿不得停在 None——全部重试结束后幂等兜底 exit 并如实改注，
    MUST NOT 再触发新打回（死循环）；未持有受众的门禁判据不得因此失明。
    """

    def _state(self, holds):
        return {
            "holdings_context": HoldingsContext(holds=holds),
            "trader_plan": {"action": "sell", "confidence": 0.5, "reasoning": "看空"},
            "analyst_reports": {},
        }

    def test_schedule_retry_dropping_sell_type_collapses_to_exit(self, monkeypatch):
        """节奏打回后的新终稿丢 sell_type：兜底 exit + 改注，不再打回。"""
        from finance_agent.nodes import risk as risk_mod

        seq = [
            # call1：申报 exit 但缺减仓节奏 → 触发「减仓节奏申报打回」
            {
                "action": "sell",
                "confidence": 0.5,
                "reasoning": "看空",
                "sell_type": "exit",
                "reeval_triggers": ["跌破 600 重估"],
            },
            # call2：打回换代后补了节奏却丢了 sell_type（终稿停在 None 的路径）
            {
                "action": "sell",
                "confidence": 0.5,
                "reasoning": "看空",
                "exit_schedule": "现价清仓",
                "reeval_triggers": ["跌破 600 重估"],
            },
        ]
        calls: list[str] = []

        def _fake_llm(context, **kwargs):
            calls.append(context)
            return seq.pop(0)

        monkeypatch.setattr(risk_mod, "call_llm_for_json", _fake_llm)
        monkeypatch.setattr(risk_mod, "_build_hysteresis_context", lambda s: "")
        result = risk_mod.risk_judge(self._state(None))
        assert len(calls) == 2, "收口不得再触发新打回"
        decision = result["final_trade_decision"]
        assert decision.sell_type == "exit"
        assert decision.exit_schedule == "现价清仓"
        # 兜底不得编造建仓价位
        assert decision.entry_price is None and decision.stop_loss is None
        note = result["final_price_check"]["note"]
        assert "打回后仍未申报" in note
        assert "默认 exit" in note
        # 未申报受众不触发门禁：判定仍是 pass（收口不伪造门禁结论）
        assert result["holdings_executability_check"]["result"] == "pass"

    def test_collapse_exit_on_not_holding_audience_downgraded_at_final_recheck(self, monkeypatch):
        """holds=False：门禁因 sell_type 未申报失明，收口出的 exit 仍须终稿复核降级。"""
        from finance_agent.nodes import risk as risk_mod

        seq = [
            # call1：门禁打回一次
            {
                "action": "sell",
                "confidence": 0.5,
                "reasoning": "看空",
                "sell_type": "exit",
                "reeval_triggers": ["跌破 600 重估"],
            },
            # call2：门禁判据失明的形态（sell_type 未申报，价位/节奏齐备）
            {
                "action": "sell",
                "confidence": 0.5,
                "reasoning": "看空",
                "exit_schedule": "现价清仓",
                "entry_price": 640.0,
                "stop_loss": 660.0,
                "target_price": 571.0,
                "reeval_triggers": ["跌破 600 重估"],
            },
        ]
        calls: list[str] = []

        def _fake_llm(context, **kwargs):
            calls.append(context)
            return seq.pop(0)

        monkeypatch.setattr(risk_mod, "call_llm_for_json", _fake_llm)
        monkeypatch.setattr(risk_mod, "_build_hysteresis_context", lambda s: "")
        result = risk_mod.risk_judge(self._state(False))
        assert len(calls) == 2, "终稿复核不得再触发 LLM 打回"
        decision = result["final_trade_decision"]
        assert decision.action == "watch"
        assert decision.sell_type is None
        assert decision.exit_schedule is None and decision.entry_price is None
        check = result["holdings_executability_check"]
        assert check["result"] == "fail"
        assert "降级观望" in check["note"]
        assert "未持有无敞口可退" in decision.inaction_reason
        # 降级后触发位缺失须如实改注（不得留空注假阳性）
        assert "触发位" in result["final_trigger_check"]["note"]
        # 终稿复核降级换代后价位注必须订正（评审 I-1）：收口注/重试注可能仍写着
        # 对 sell/exit 的结论——终稿已是完整 watch，旧注失真；且注经 report.
        # _fm_incomplete_integrity_block（marker「仍未申报」）渲染用户可见标注，
        # 并进 FM 审批上下文（fund_manager.final_integrity_notes）
        price_note = result["final_price_check"]["note"]
        assert "默认 exit" not in price_note, "降级后价位注残留 sell 结论"
        assert "exit_schedule" not in price_note, "降级后价位注残留 sell 结论"
        assert "仍未申报" not in price_note, "降级后价位注含 FM/report 不完整标记词"
        assert "终稿复核降级覆盖" in price_note, "缺换代措辞（须如实说明已被降级覆盖）"
        # 「LLM 丢过 sell_type」的事实在换代注中不保留，改记门禁判定键（trace
        # 可观测；本键不进 report/FM 消费链）——来源可溯不丢
        assert "sell_type" in check["note"]
