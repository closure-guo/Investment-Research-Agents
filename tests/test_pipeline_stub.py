"""管线模式确定性 stub 的单元测试（agent-turn-box-display delta task 5.5）。

覆盖三部分：
1. node 字段透传 bug 修复：run_deep_analysis 工具产出的 THINK StreamEvent
   必须携带 node metadata（此前 _stream_graph custom 分支丢弃 chunk["node"]）。
2. 管线 LLM stub：TESTING=1 时 call_llm_streaming 不调真实 LLM，按 node_name
   产出带图节点名的思考 token + 该节点可解析的合法 answer。
3. 数据层 stub：TESTING=1 时 fetch_data 返回确定性三大报表（满足勾稽校验硬等式），
   不触网；StubLLMClient 的 pipeline 场景产出 search_stock + run_deep_analysis 工具调用。
"""

import asyncio
import json
import os
from unittest.mock import patch

import pandas as pd
import pytest

# 测试导入链 finance_agent.api（TESTING 读取）不能依赖 Langfuse 服务；
# 环境密钥存在时清空，避免 get_langfuse 初始化连接失败拖慢测试。
os.environ.pop("LANGFUSE_PUBLIC_KEY", None)
os.environ.pop("LANGFUSE_SECRET_KEY", None)


@pytest.fixture
def testing_env(monkeypatch):
    """TESTING=1 + STUB_SCENARIO=pipeline 环境，测试后恢复。

    api.TESTING 是模块导入时冻结的常量（api.py:95）：若 finance_agent.api
    被更早的无 TESTING 测试先导入，本 fixture 仅改环境变量时 build_agent
    仍会拿到 TESTING=False 走真实 LLM 客户端（测试顺序耦合）。显式 patch
    模块属性，使本 fixture 与导入顺序解耦。
    """
    import finance_agent.api as api_mod

    monkeypatch.setenv("TESTING", "1")
    monkeypatch.setenv("STUB_SCENARIO", "pipeline")
    monkeypatch.setattr(api_mod, "TESTING", True)
    yield


def _collect_events(tool) -> list:
    """驱动 run_deep_analysis 异步生成器，收集全部 StreamEvent。"""

    async def _run():
        events = []
        async for ev in tool(stock_code="600519", stock_name="贵州茅台"):
            events.append(ev)
        return events

    return asyncio.run(_run())


class TestPipelineNodeFieldPassthrough:
    """node 字段透传 bug 修复（真实 bug fix，影响生产链路）。"""

    def test_run_deep_analysis_think_event_carries_node_metadata(self, testing_env):
        """管线节点的 thinking token 必须带 node metadata，前端按此分组到 agent 阶段。"""
        from finance_agent.agent_factory import _make_run_deep_analysis
        from finance_agent.harness import ActionType

        tool = _make_run_deep_analysis()
        events = _collect_events(tool)

        think_events = [e for e in events if e.event_type == ActionType.THINK]
        assert think_events, "管线 stub 应产出 THINK 事件"
        nodes = {(e.metadata or {}).get("node", "") for e in think_events}
        nodes.discard("")
        # 至少出现 3 个不同 agent 节点的思考（前端按 agent 分组的前提）
        assert len(nodes) >= 3, f"应有 >=3 个带 node 的 THINK 事件，实际: {nodes}"
        # node 必须是图节点名（前端 NODE_DISPLAY_NAMES 的 key）
        assert "bull_debater" not in nodes
        assert {"technical_analyst", "bull_r1", "trader"} <= nodes


class TestCallLlmStreamingStub:
    """管线 LLM stub：TESTING=1 时 call_llm_streaming 的确定性输出。"""

    def _run_stub(self, node_name: str) -> str:
        from finance_agent.nodes._llm_utils import call_llm_streaming

        return call_llm_streaming("test prompt", node_name=node_name)

    def test_analyst_answer_parses_to_analyst_report(self, testing_env):
        """分析师节点 stub answer 必须能被 parse_json_response + AnalystReport 解析。"""
        from finance_agent.models import AnalystReport
        from finance_agent.nodes._llm_utils import parse_json_response

        answer = self._run_stub("technical_analyst")
        data = parse_json_response(answer)
        report = AnalystReport.model_validate(data)
        assert report.agent_name == "technical"
        # claims 为空，保证 verify_citations 确定性 PASS
        assert report.claims == []

    def test_bull_debater_answer_parses_to_debate_message(self, testing_env):
        from finance_agent.models import DebateMessage
        from finance_agent.nodes._llm_utils import parse_json_response

        answer = self._run_stub("bull_debater")
        msg = DebateMessage.model_validate(parse_json_response(answer))
        assert msg.role == "bull"

    def test_trader_answer_parses_to_trade_decision(self, testing_env):
        from finance_agent.models import TradeDecision
        from finance_agent.nodes._llm_utils import parse_json_response

        answer = self._run_stub("trader")
        decision = TradeDecision.model_validate(parse_json_response(answer))
        assert decision.action in ("buy", "sell", "hold", "watch")
        assert decision.evidence_refs == []

    def test_fund_manager_answer_has_approve_decision(self, testing_env):
        """fund_manager stub 必须返回 approve，确保管线确定性走 generate_report。"""
        from finance_agent.nodes._llm_utils import parse_json_response

        answer = self._run_stub("fund_manager")
        data = parse_json_response(answer)
        assert data["decision"] == "approve"

    def test_research_manager_answer_is_plain_text(self, testing_env):
        answer = self._run_stub("research_manager")
        assert isinstance(answer, str) and answer.strip()

    def test_stub_returns_answer_without_real_llm(self, testing_env):
        """TESTING=1 下不得调用真实 complete_stream（否则连真实 LLM）。"""
        with patch("finance_agent.llm.gateway.complete_stream") as mock_stream:
            answer = self._run_stub("trader")
        mock_stream.assert_not_called()
        assert answer.strip()

    def test_production_path_unaffected(self):
        """无 TESTING 时仍走真实 gateway.complete_stream（stub 只服务测试）。"""
        from finance_agent.llm.types import CanonicalEvent

        with (
            patch.dict(
                os.environ, {k: v for k, v in os.environ.items() if k != "TESTING"}, clear=True
            ),
            patch(
                "finance_agent.llm.gateway.complete_stream",
                return_value=iter([CanonicalEvent(kind="text", text="{}")]),
            ) as mock_stream,
        ):
            from finance_agent.nodes._llm_utils import call_llm_streaming

            answer = call_llm_streaming("p", node_name="trader")
        mock_stream.assert_called_once()
        assert answer == "{}"


class TestFetchDataStub:
    """数据层 stub：TESTING=1 时 fetch_data 返回确定性数据，不触网。"""

    def test_fetch_data_returns_deterministic_reports(self, testing_env):
        from finance_agent.nodes.fetch import fetch_data

        state = {"stock_code": "600519", "stock_name": "贵州茅台"}
        result = fetch_data(state)
        bs = result["balance_sheet"]
        inc = result["income_statement"]
        cf = result["cash_flow_statement"]
        assert isinstance(bs, pd.DataFrame) and not bs.empty
        assert isinstance(inc, pd.DataFrame) and not inc.empty
        assert isinstance(cf, pd.DataFrame) and not cf.empty

    def test_stub_reports_pass_validation(self, testing_env):
        """stub 三大报表必须通过勾稽校验（尤其规则1硬等式：资产=负债+权益），
        否则管线在 validate_financials 短路终止。"""
        from finance_agent.metrics.validate import validate_financials
        from finance_agent.nodes.fetch import fetch_data

        result = fetch_data({"stock_code": "600519"})
        validation = validate_financials(
            result["balance_sheet"], result["income_statement"], result["cash_flow_statement"]
        )
        assert validation["result"] == "PASS", f"勾稽校验应 PASS: {validation['warnings']}"

    def test_stub_supports_compute_metrics(self, testing_env):
        """stub 数据必须能让 compute_metrics 不抛异常（下游节点依赖其输出）。"""
        from finance_agent.nodes.compute import compute_metrics
        from finance_agent.nodes.fetch import fetch_data

        state = {"stock_code": "600519", "stock_name": "贵州茅台"}
        state.update(fetch_data(state))
        computed = compute_metrics(state)
        assert computed["solvency_metrics"]
        assert computed["profitability_metrics"]


class TestStubLLMClientPipelineScenario:
    """StubLLMClient pipeline 场景：产出 search_stock + run_deep_analysis 工具调用。"""

    async def _collect_responses(self, client, tools=None):
        chunks = []
        async for chunk in client.chat_stream(
            [{"role": "user", "content": "深度分析600519"}], tools=tools
        ):
            chunks.append(chunk)
        return chunks

    def test_pipeline_scenario_calls_search_stock_first(self):
        from finance_agent.harness.stub_llm_client import StubLLMClient

        client = StubLLMClient(scenario="pipeline")
        chunks = asyncio.run(self._collect_responses(client))
        tool_calls = [tc for c in chunks for tc in (c.tool_calls or [])]
        assert tool_calls and tool_calls[0].name == "search_stock"

    def test_pipeline_scenario_calls_run_deep_analysis_second(self):
        from finance_agent.harness.stub_llm_client import StubLLMClient

        client = StubLLMClient(scenario="pipeline")
        asyncio.run(self._collect_responses(client))  # 第 1 轮
        chunks = asyncio.run(self._collect_responses(client))  # 第 2 轮
        tool_calls = [tc for c in chunks for tc in (c.tool_calls or [])]
        assert any(tc.name == "run_deep_analysis" for tc in tool_calls)
        args = next(tc.arguments for tc in tool_calls if tc.name == "run_deep_analysis")
        assert args["stock_code"] == "600519"

    def test_pipeline_scenario_final_round_answers(self):
        from finance_agent.harness.stub_llm_client import StubLLMClient

        client = StubLLMClient(scenario="pipeline")
        for _ in range(2):
            asyncio.run(self._collect_responses(client))
        chunks = asyncio.run(self._collect_responses(client))  # 第 3 轮：回答
        assert any(c.text_delta for c in chunks)

    def test_default_scenario_unaffected(self):
        """默认/无 scenario 行为不变（1 轮完成，不调工具）。"""
        from finance_agent.harness.stub_llm_client import StubLLMClient

        client = StubLLMClient()
        chunks = asyncio.run(self._collect_responses(client))
        assert all(not c.tool_calls for c in chunks)
        assert any(c.text_delta for c in chunks)


class TestStreamAgentToSseNodeField:
    """stream_agent_to_sse 的 THINK 分支必须把 node metadata 写入 SSE thinking_token。"""

    def test_think_event_metadata_forwarded_to_sse(self, testing_env):
        from finance_agent.agent_factory import build_agent, stream_agent_to_sse
        from finance_agent.harness import ActionType, StreamEvent

        agent = build_agent(mode="deep")

        async def fake_run(*args, **kwargs):
            yield StreamEvent(
                event_type=ActionType.THINK,
                content="管线思考片段",
                metadata={"node": "bull_r1"},
            )

        agent.run = fake_run  # type: ignore[assignment]

        async def collect():
            out = []
            async for sse in stream_agent_to_sse(agent, "test"):
                out.append(sse)
            return out

        sse_lines = asyncio.run(collect())
        payloads = [json.loads(line[6:]) for line in sse_lines if line.startswith("data: ")]
        thinking_events = [p for p in payloads if p.get("type") == "thinking_token"]
        assert thinking_events, "应有 thinking_token SSE 事件"
        assert thinking_events[0].get("node") == "bull_r1"


class TestStreamingToolThinkPassthrough:
    """流式工具（run_deep_analysis）经 harness ReAct 循环执行时，其 THINK 事件
    必须透传给消费者（真实 bug：execute_stream 只透传 PROGRESS/TOOL_RESULT，丢 THINK）。"""

    def test_streaming_tool_think_events_reach_agent_run_consumer(self, testing_env):
        """agent.run 驱动含 THINK 的流式工具，消费者应收到带 node metadata 的 THINK。"""
        from finance_agent.agent_factory import build_agent
        from finance_agent.harness import ActionType, StreamEvent, ToolResult

        agent = build_agent(mode="deep")

        async def think_tool():
            """模拟 run_deep_analysis：先 yield THINK，再 yield 最终 TOOL_RESULT。"""
            yield StreamEvent(
                event_type=ActionType.THINK,
                content="管线思考",
                metadata={"node": "trader"},
            )
            yield StreamEvent(
                event_type=ActionType.TOOL_RESULT,
                content="done",
                tool_result=ToolResult(tool_call_id="", name="think_tool", output="done"),
            )

        agent.tools.register(think_tool, name="think_tool")

        async def collect():
            events = []
            async for ev in agent.run("触发 think_tool", force_tool=True):
                events.append(ev)
            return events

        events = asyncio.run(collect())
        think_events = [e for e in events if e.event_type == ActionType.THINK]
        node_think = [e for e in think_events if (e.metadata or {}).get("node") == "trader"]
        assert node_think, (
            f"流式工具的 THINK(node=trader) 应透传给 agent.run 消费者，"
            f"实际 THINK 事件: {[(e.metadata or {}).get('node') for e in think_events]}"
        )


class TestDerivedKeysSurviveGraphMerge:
    """③ close-citation-coverage-gaps 验证：`price_levels` / `derived_series` 穿过真实图合并。

    此前两键未在 AnalysisState 声明 → LangGraph 合并静默丢弃（本地最小实验复现），
    三处消费方（validate 参考带校验 / Trader 价位参考节 / 分析师派生值表）恒读 None。
    本测试走真实图 + TESTING stub，断言带 K 线输入时两键出现在最终 state。
    """

    def test_derived_keys_present_in_graph_output(
        self, testing_env, balance_sheet, income_statement, cash_flow
    ):
        from finance_agent.graph import build_5layer_graph

        n = 40
        kline = pd.DataFrame(
            {
                "日期": pd.date_range("2025-01-02", periods=n, freq="B").strftime("%Y-%m-%d"),
                "开盘": [10.0 + i * 0.1 for i in range(n)],
                "收盘": [10.1 + i * 0.1 for i in range(n)],
                "最高": [10.2 + i * 0.1 for i in range(n)],
                "最低": [9.9 + i * 0.1 for i in range(n)],
                "成交量": [1000 + i for i in range(n)],
            }
        )
        out = build_5layer_graph().invoke(
            {
                "query": "深度分析600519",
                "stock_code": "600519",
                "stock_name": "贵州茅台",
                "balance_sheet": balance_sheet,
                "income_statement": income_statement,
                "cash_flow_statement": cash_flow,
                "kline": kline,
                "benchmark_kline": kline.copy(),
            }
        )
        assert out.get("price_levels", {}).get("available") is True, (
            f"price_levels 未穿过图合并（027 同型静默丢弃）：{out.get('price_levels')}"
        )
        assert out.get("derived_series"), "derived_series 未穿过图合并（027 同型静默丢弃）"
