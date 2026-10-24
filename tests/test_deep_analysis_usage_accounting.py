# tests/test_deep_analysis_usage_accounting.py
"""add-composer-stat-pills：deep 分析线程内 usage 收集 → 会话级落库。

核心约束（design D2）：``usage_collector`` 基于 contextvar，而管线在
``_pipeline_executor`` 线程运行；收集器必须在 ``_run_graph`` 线程内激活，
否则记账静默为 0。本测试让假图在 stream 内部断言收集器已激活并注入 usage，
再断言会话记录得到真值。
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from finance_agent import session_store
from finance_agent.agent_factory import _make_run_deep_analysis


class _UsageEmittingGraph:
    """假图：在 stream 内部读取当前 contextvar 并注入两次 usage。"""

    def stream(self, initial_state, config=None, stream_mode="updates"):
        from finance_agent.nodes._llm_utils import _usage_acc

        acc = _usage_acc.get()
        assert acc is not None, "deep 分析线程内必须激活 usage_collector"
        acc.add({"prompt_tokens": 700, "completion_tokens": 30, "total_tokens": 730})
        acc.add({"prompt_tokens": 900, "completion_tokens": 50, "total_tokens": 950})
        yield (
            "updates",
            {
                "generate_report": {
                    "final_report": "# 贵州茅台报告",
                    "chart_data": {},
                    "analyst_reports": {},
                }
            },
        )


@pytest.mark.asyncio
async def test_pipeline_usage_is_persisted_to_session(tmp_path, monkeypatch):
    monkeypatch.setattr(session_store, "_DB_PATH", tmp_path / "test.db")
    session_store.init_db()
    sid = session_store.create_session(stock_code="600519", stock_name="贵州茅台", status="running")

    with patch("finance_agent.graph.build_5layer_graph", return_value=_UsageEmittingGraph()):
        tool_fn = _make_run_deep_analysis(api_key="test", session_id=sid)
        async for _event in tool_fn(stock_code="600519", stock_name="贵州茅台"):
            pass

    row = session_store.get_session(sid)
    assert row is not None
    assert row["tokens_prompt"] == 1600
    assert row["tokens_completion"] == 80
    assert row["tokens_total"] == 1680
    assert row["context_peak_tokens"] == 900
