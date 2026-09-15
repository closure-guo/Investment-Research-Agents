# tests/evals/test_task.py
"""run_task:mode 分派、输出可序列化、follow_up 跳过、graph 配置。"""

import asyncio
import json
from unittest.mock import MagicMock, patch

from evals.task import run_task


class _Item:
    def __init__(self, input_):
        self.input = input_


class TestDeepTask:
    @patch("evals.task.build_5layer_graph")
    def test_deep_invokes_graph_and_extracts(self, mock_build):
        final_state = {
            "final_report": "## 结论\n买入。",
            "analyst_reports": {},
            "debate_history": [],
            "research_manager_conclusion": "rm",
            "final_trade_decision": {"action": "buy"},
            "risk_debate_history": [],
            "fund_manager_decision": "approve",
        }
        mock_graph = MagicMock()
        mock_graph.invoke.return_value = final_state
        mock_build.return_value = mock_graph

        out = run_task(
            item=_Item(
                {
                    "query": "分析茅台",
                    "mode": "deep",
                    "stock_code": "600519",
                    "stock_name": "贵州茅台",
                }
            )
        )
        assert out["report"] == "## 结论\n买入。"
        assert out["ticker"] == "600519"
        assert out["mode"] == "deep"
        assert out["skipped"] is None
        assert "买入" in out["judge_vars"]["report_conclusion"]
        # 序列化铁律:不得含 DataFrame 等不可序列化对象
        json.dumps(out)
        # initial_state 关键键
        state_arg = mock_graph.invoke.call_args.args[0]
        assert state_arg["stock_code"] == "600519"
        assert state_arg["enable_web_search"] is False
        # M5:initial_state 多键与 api.py 对齐
        assert state_arg["analysis_type"] == "comprehensive"
        assert state_arg["peer_codes"] is None
        assert state_arg["api_key"] is None
        assert state_arg["llm_config"] is None
        assert state_arg["focus"] == "分析茅台"
        # M4:测试环境无 LANGFUSE 凭据,config 应为 None(零开销,业务无感知)
        assert mock_graph.invoke.call_args.kwargs.get("config") is None


class TestQuickTask:
    @patch("evals.task.build_agent")
    def test_quick_runs_agent_sync(self, mock_build):
        agent = MagicMock()

        async def _run_sync(query):
            return "茅台是好公司"

        agent.run_sync = _run_sync
        mock_build.return_value = agent

        out = run_task(item=_Item({"query": "茅台怎么样", "mode": "quick", "ticker": "600519"}))
        assert out["report"] == "茅台是好公司"
        assert out["ticker"] == "600519"
        assert out["judge_vars"]["query"] == "茅台怎么样"
        mock_build.assert_called_once()
        assert mock_build.call_args.kwargs["mode"] == "quick"
        json.dumps(out)


class TestSkippedModes:
    def test_follow_up_skipped(self):
        out = run_task(item=_Item({"query": "再分析下", "mode": "follow_up", "session_id": "s1"}))
        assert out["skipped"] is not None
        assert "follow_up" in out["skipped"]
        assert out["report"] is None
        json.dumps(out)

    def test_should_clarify_skipped(self):
        out = run_task(
            item=_Item({"query": "帮我看看", "mode": "deep"}),
            expected_output={"should_clarify": True},
        )
        assert out["skipped"] is not None
        assert "意图澄清" in out["skipped"] or "should_clarify" in out["skipped"]
        json.dumps(out)

    def test_should_clarify_skipped_via_dict_expected_output(self):
        """LocalExperimentItem 把 expected_output 放 dict 键;should_clarify 仍要触发跳过。"""
        # 不传 expected_output 关键字,塞进 dict(模拟 langfuse LocalExperimentItem)
        item = {
            "input": {"query": "帮我分析一下", "mode": "deep"},
            "expected_output": {"should_clarify": True},
        }
        out = run_task(item=item)
        assert out["skipped"] is not None
        assert out["report"] is None


class TestNestedLoopSafety:
    def test_quick_task_works_inside_running_loop(self):
        """langfuse run_experiment 在运行 loop 内同步调 task;quick 分派不能崩溃。"""

        async def _drive():
            # 模拟 langfuse 上下文:已在运行 loop 内同步调 run_task
            with patch("evals.task.build_agent") as mock_build:
                agent = MagicMock()

                async def _run_sync(query):
                    return "茅台是好公司"

                agent.run_sync = _run_sync
                mock_build.return_value = agent
                return run_task(
                    item=_Item({"query": "茅台怎么样", "mode": "quick", "ticker": "600519"})
                )

        out = asyncio.run(_drive())  # 外层 loop
        assert out["report"] == "茅台是好公司"
        assert out["skipped"] is None


class TestTaskCitationOutputs:
    def test_deep_output_includes_citation_metrics(self, monkeypatch):
        """deep task 输出携带 citation_pass/citation_coverage（来自管线 state）。"""
        import evals.task as task_mod

        class _FakeGraph:
            def invoke(self, state, config=None):
                return {
                    "final_report": "r",
                    "citation_pass": True,
                    "citation_coverage": 0.92,
                    "value_mismatch_repaired": 2,
                    "citation_unverifiable_comparative_delta": 1,
                }

        monkeypatch.setattr(task_mod, "build_5layer_graph", lambda: _FakeGraph())
        monkeypatch.setattr(task_mod, "extract_judge_vars", lambda state, query="": {})
        monkeypatch.setattr(task_mod, "get_callback_handler", lambda: None)
        out = task_mod._run_deep({"stock_code": "600519", "query": "q"})
        assert out["citation_pass"] == 1.0
        assert out["citation_coverage"] == 0.92
        assert out["citation_unverifiable_comparative_delta"] == 1.0
        # 单点修复计数进拆报（观测修复触发/成功率；同时仍计入 analyst_true_fail）
        assert out["citation_surgical_repaired"] == 2.0


class TestTaskAnchorOutputs:
    """add-debate-argument-anchors Task 5：锚点覆盖率 + 拆项进 deep 输出。"""

    def _run_deep_with_state(self, monkeypatch, state: dict) -> dict:
        import evals.task as task_mod

        class _FakeGraph:
            def invoke(self, initial_state, config=None):
                return {**state, "final_report": state.get("final_report", "r")}

        monkeypatch.setattr(task_mod, "build_5layer_graph", lambda: _FakeGraph())
        monkeypatch.setattr(task_mod, "extract_judge_vars", lambda s, query="": {})
        monkeypatch.setattr(task_mod, "get_callback_handler", lambda: None)
        return task_mod._run_deep({"stock_code": "600519", "query": "q"})

    def test_deep_output_includes_anchor_coverage(self, monkeypatch):
        out = self._run_deep_with_state(
            monkeypatch,
            {
                "debate_anchor_checks": [
                    {
                        "role": "bull",
                        "round": 1,
                        "index": 1,
                        "kind": "data",
                        "anchors": ["x"],
                        "anchor_statuses": ["resolved"],
                        "status": "resolved",
                        "anchored": True,
                    },
                    {
                        "role": "bear",
                        "round": 1,
                        "index": 1,
                        "kind": "inference",
                        "anchors": [],
                        "anchor_statuses": [],
                        "status": "none",
                        "anchored": False,
                    },
                    {
                        "role": "aggressive",
                        "round": 1,
                        "index": 1,
                        "kind": "data",
                        "anchors": ["y"],
                        "anchor_statuses": ["unresolved"],
                        "status": "unresolved",
                        "anchored": False,
                    },
                    {
                        "role": "neutral",
                        "round": 1,
                        "index": 1,
                        "kind": "unspecified",
                        "anchors": [],
                        "anchor_statuses": [],
                        "status": "unspecified",
                        "anchored": False,
                    },
                ]
            },
        )
        assert out["argument_anchor_coverage"] == 0.25
        detail = out["argument_anchor_coverage_detail"]
        assert detail["total"] == 4 and detail["anchored"] == 1
        # 四个拆项键全数透传（归因/comment 依赖）
        assert detail["unanchored_inference"] == 1
        assert detail["unresolved"] == 1
        assert detail["missing_required"] == 0
        assert detail["unspecified"] == 1
        assert "value" not in detail
        json.dumps(out)

    def test_anchor_keys_none_without_checks(self, monkeypatch):
        """旧 trace/quick 无锚点数据 → 两键 None（不计入均值），不得 0/0 假指标。"""
        out = self._run_deep_with_state(monkeypatch, {})
        assert out["argument_anchor_coverage"] is None
        assert out["argument_anchor_coverage_detail"] is None
        json.dumps(out)
