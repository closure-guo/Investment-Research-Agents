"""管线执行体异常的终态观测契约（SSE 误标问题定性修正后的修复）。

`_run_graph_streaming` 是管线执行体（PipelineRunner 后台线程消费），
其 except 写 failed 是正确职责；缺陷是 ①不带 failure_reason（数据库里
failure_reason=None，排查无门）②error 事件的 traceback 字段存对象 repr
（"<traceback object at 0x...>"，零信息量）。

现场实证：4c038a41 会话 fetch_data 抛「DataFrame truth value ambiguous」，
journal 只有 repr、库无 reason——瞬态异常无法定位。本契约锁定：
异常终态 MUST 携带 failure_reason 与可读 traceback 字符串。
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from finance_agent.api import AnalyzeRequest, _run_graph_streaming


@pytest.fixture
def req():
    return AnalyzeRequest(
        stock_code="600519",
        stock_name="贵州茅台",
        query="分析贵州茅台",
        analysis_type="comprehensive",
    )


class TestPipelineExceptionTerminalObservability:
    def test_generator_exception_carries_reason_and_readable_traceback(self, req):
        """graph.stream 抛异常 → error 事件含 message + format_exc 文本。"""
        sentinel = RuntimeError("管线执行体爆炸：DataFrame truth value ambiguous")

        with (
            patch("finance_agent.api.graph") as mock_graph,
            patch("finance_agent.api.create_session", return_value="sess-test"),
        ):
            mock_graph.stream.side_effect = sentinel
            events = list(_run_graph_streaming("600519", "贵州茅台", req, "a1", start_time=0.0))

        error_events = [e for e in events if '"type": "error"' in e or '"type":"error"' in e]
        assert error_events, f"未发出 error 事件，实际事件类型: {[e[:60] for e in events]}"
        import json

        payload = json.loads(error_events[0].split("data: ", 1)[1].split("\n", 1)[0])
        assert "DataFrame truth value ambiguous" in payload["message"]
        # 可读 traceback：必须包含异常类名与调用行（format_exc 形态），不得是对象 repr
        tb = payload["traceback"]
        assert "RuntimeError" in tb
        assert "Traceback (most recent call last)" in tb
        assert "<traceback object at" not in tb

    def test_session_marked_failed_with_reason(self, req):
        """except 分支状态流转 MUST 带 failure_reason（C5 后经 lifecycle.mark_failed）。"""
        sentinel = RuntimeError("boom")

        with (
            patch("finance_agent.api.graph") as mock_graph,
            patch("finance_agent.api.create_session", return_value="sess-test"),
            patch("finance_agent.sessions.lifecycle.update_session_status") as spy_status,
        ):
            mock_graph.stream.side_effect = sentinel
            list(_run_graph_streaming("600519", "贵州茅台", req, "a1", start_time=0.0))

        spy_status.assert_called_once()
        args, kwargs = spy_status.call_args
        assert args[1] == "failed" or kwargs.get("status") == "failed"
        reason = args[2] if len(args) > 2 else kwargs.get("failure_reason")
        assert reason and "RuntimeError" in str(reason) and "boom" in str(reason)
