"""管线停滞看门狗共享语义纯函数测试（update-pipeline-stall-watchdog）。

spec pipeline-events「管线停滞看门狗」：双执行路径（asyncio 消费 / fast path
线程）共用的阈值读取、等待计算、过期分类与 failure_reason 文案。
"""

from __future__ import annotations

from finance_agent import pipeline_watchdog as pw


class TestStallTimeoutSeconds:
    def test_default_600(self, monkeypatch):
        monkeypatch.delenv("PIPELINE_STALL_TIMEOUT_SECONDS", raising=False)
        assert pw.stall_timeout_seconds() == 600.0

    def test_env_override(self, monkeypatch):
        monkeypatch.setenv("PIPELINE_STALL_TIMEOUT_SECONDS", "900")
        assert pw.stall_timeout_seconds() == 900.0


class TestStallFailureReason:
    def test_contains_duration_type_and_node(self):
        reason = pw.stall_failure_reason(612.0, "thinking_token", "fundamental")
        assert "612" in reason
        assert "thinking_token" in reason
        assert "fundamental" in reason
        assert "停滞" in reason

    def test_without_node(self):
        reason = pw.stall_failure_reason(600.0, "node_start", "")
        assert "600" in reason and "node_start" in reason

    def test_total_reason_constant_distinct(self):
        # 双文案可区分（spec：先触发者生效、failure_reason MUST 可区分）
        assert "停滞" not in pw.TOTAL_TIMEOUT_FAILURE_REASON
        assert "超时" in pw.TOTAL_TIMEOUT_FAILURE_REASON


class TestCompletionGuard:
    """迟到完成守卫：停滞终态已发布 → completed 回写必须被拦。"""

    def test_not_stalled_allows_completed(self):
        pw._STALLED.clear()
        assert pw.completion_status_or_skip("sess-x") == "completed"

    def test_stalled_skips_completion(self):
        pw._STALLED.clear()
        pw.mark_stalled("sess-x")
        assert pw.completion_status_or_skip("sess-x") is None
        assert pw.is_stalled("sess-x")

    def test_clear_on_rerun(self):
        pw._STALLED.clear()
        pw.mark_stalled("sess-y")
        pw.clear_stalled("sess-y")
        assert not pw.is_stalled("sess-y")
        assert pw.completion_status_or_skip("sess-y") == "completed"
