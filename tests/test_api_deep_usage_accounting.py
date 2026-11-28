# tests/test_api_deep_usage_accounting.py
"""composer-stat-pills「会话用量记账」：api 深度分析快路径（池分析/`/api/analyze`）。

背景：``agent_factory._run_graph``（ReAct 深分析）已在线程内激活
``usage_collector`` 并落库；但 ``api._run_graph_streaming``（快路径与池分析
经 ``PipelineRunner`` 共用）从未激活——生产实测 11-27 完成会话
``tokens_prompt/completion/total`` 全 0。规范要求「深度分析在后台线程执行时
SHALL 在该线程内激活 usage 收集器…落库到 sessions 表」。

本测试让假图在 ``stream`` 内部记录收集器是否激活并注入两次 usage，再断言会话
记录得到真值（无 usage 的 stub 路径保持 0 由既有测试覆盖）。
"""

from __future__ import annotations

import time
from typing import Any
from unittest.mock import patch

import pytest

from finance_agent import session_store


@pytest.fixture
def isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(session_store, "_DB_PATH", tmp_path / "t.db")
    session_store.init_db()
    return tmp_path / "t.db"


class _UsageEmittingGraph:
    """假图：在 stream 内部记录收集器状态；激活时注入两次 usage，末尾产出报告。"""

    def __init__(self) -> None:
        self.saw_collector = False

    def stream(self, initial_state, config=None, stream_mode=None):  # noqa: ARG002
        from finance_agent.nodes._llm_utils import _usage_acc

        acc = _usage_acc.get()
        self.saw_collector = acc is not None
        if acc is not None:
            acc.add({"prompt_tokens": 700, "completion_tokens": 30, "total_tokens": 730})
            acc.add({"prompt_tokens": 900, "completion_tokens": 50, "total_tokens": 950})
        yield (
            "updates",
            {
                "after_citation": {
                    "final_report": "# 贵州茅台分析报告\n完整正文……",
                    "chart_data": {},
                    "analyst_reports": {},
                }
            },
        )


def _run_stream(sid: str, graph: Any) -> list[str]:
    from finance_agent.api import AnalyzeRequest, _run_graph_streaming

    req = AnalyzeRequest(stock_code="600519", query="分析贵州茅台")
    with (
        patch("finance_agent.langfuse_tracing.get_langfuse", return_value=None),
        patch("finance_agent.langfuse_tracing.get_callback_handler", return_value=None),
        patch("finance_agent.api.graph", graph),
    ):
        return list(
            _run_graph_streaming(
                stock_code="600519",
                stock_name="贵州茅台",
                req=req,
                analysis_id="a-usage",
                start_time=time.time(),
                session_id=sid,
            )
        )


def test_fast_path_persists_provider_usage(isolated_db):
    sid = session_store.create_session(stock_code="600519", stock_name="贵州茅台", status="running")
    graph = _UsageEmittingGraph()

    events = _run_stream(sid, graph)

    assert graph.saw_collector, "api 深度分析线程内必须激活 usage_collector（composer-stat-pills）"
    assert any('"report_ready"' in event for event in events)
    row: Any = session_store.get_session(sid)
    assert row is not None
    assert row["tokens_prompt"] == 1600
    assert row["tokens_completion"] == 80
    assert row["tokens_total"] == 1680
    assert row["context_peak_tokens"] == 900


def test_fast_path_without_usage_keeps_zero(isolated_db):
    """无 usage 时 MUST NOT 造数（规范「无 usage 不造数」）。"""

    class _NoUsageGraph:
        def __init__(self) -> None:
            self.saw_collector = False

        def stream(self, initial_state, config=None, stream_mode=None):  # noqa: ARG002
            from finance_agent.nodes._llm_utils import _usage_acc

            self.saw_collector = _usage_acc.get() is not None
            yield (
                "updates",
                {
                    "after_citation": {
                        "final_report": "# 报告",
                        "chart_data": {},
                        "analyst_reports": {},
                    }
                },
            )

    sid = session_store.create_session(stock_code="600519", stock_name="贵州茅台", status="running")
    graph = _NoUsageGraph()

    _run_stream(sid, graph)

    assert graph.saw_collector
    row: Any = session_store.get_session(sid)
    assert row is not None
    assert row["tokens_total"] == 0
    assert row["context_peak_tokens"] == 0
