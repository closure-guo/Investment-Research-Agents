"""sessions/lifecycle 状态流转唯一 owner 测试（C5：refactor-session-lifecycle-owner）。

fake session_store 函数注入（monkeypatch 模块属性）即可测完整状态机，
无需 FastAPI/SQLite。
"""

from __future__ import annotations

import pytest

from finance_agent.sessions import lifecycle


class _FakeStore:
    """session_store 关键函数的记录器（断言调用序列与参数）。"""

    def __init__(self):
        self.calls: list[tuple[str, tuple, dict]] = []
        self._next_id = 0

    def create_session(self, *, stock_code, stock_name, display_name, status, session_type):
        self._next_id += 1
        sid = f"s-{self._next_id}"
        self.calls.append(
            (
                "create_session",
                (),
                {
                    "stock_code": stock_code,
                    "stock_name": stock_name,
                    "display_name": display_name,
                    "status": status,
                    "session_type": session_type,
                },
            )
        )
        return sid

    def append_chat(self, session_id, role, content):
        self.calls.append(("append_chat", (session_id, role, content), {}))

    def set_pipeline_anchor(self, session_id):
        self.calls.append(("set_pipeline_anchor", (session_id,), {}))

    def update_session_for_clarify(self, session_id, **fields):
        self.calls.append(("update_session_for_clarify", (session_id,), fields))

    def update_session_status(self, session_id, status, **fields):
        self.calls.append(("update_session_status", (session_id, status), fields))

    def append_session_event(self, session_id, event):
        self.calls.append(("append_session_event", (session_id,), {"event": event}))


@pytest.fixture
def fake(monkeypatch):
    store = _FakeStore()
    monkeypatch.setattr(lifecycle, "create_session", store.create_session)
    monkeypatch.setattr(lifecycle, "append_chat", store.append_chat)
    monkeypatch.setattr(lifecycle, "set_pipeline_anchor", store.set_pipeline_anchor)
    monkeypatch.setattr(lifecycle, "update_session_for_clarify", store.update_session_for_clarify)
    monkeypatch.setattr(lifecycle, "update_session_status", store.update_session_status)
    monkeypatch.setattr(lifecycle, "append_session_event", store.append_session_event)
    return store


class TestBeginAnalysis:
    def test_full_startup_sequence_in_order(self, fake):
        """create→chat→anchor→clarify→running→session_created 一次调用完成。"""
        sid = lifecycle.begin_analysis(
            display_name="600029.SH 选股分析",
            user_message="对 600029 进行深度分析",
            stock_code="600029.SH",
        )
        assert sid == "s-1"
        seq = [name for name, _, _ in fake.calls]
        assert seq == [
            "create_session",
            "append_chat",
            "set_pipeline_anchor",
            "update_session_for_clarify",
            "append_session_event",
        ]
        # create 为 clarifying；clarify 步带 stock_code 且 status=running
        assert fake.calls[0][2]["status"] == "clarifying"
        assert fake.calls[3][2] == {
            "stock_code": "600029.SH",
            "stock_name": "",
            "status": "running",
        }
        # session_created 事件语义不变
        assert fake.calls[4][2]["event"]["type"] == "session_created"
        assert fake.calls[4][2]["event"]["display_name"] == "600029.SH 选股分析"

    def test_event_timestamp_present(self, fake):
        sid = lifecycle.begin_analysis(display_name="x 事件分析", user_message="u")
        event = fake.calls[-1][2]["event"]
        assert event["session_id"] == sid and event.get("timestamp")


class TestMarkFamily:
    def test_mark_running(self, fake):
        lifecycle.mark_running("s-1")
        assert fake.calls == [("update_session_status", ("s-1", "running"), {})]

    def test_mark_complete(self, fake):
        lifecycle.mark_complete("s-1")
        assert fake.calls == [("update_session_status", ("s-1", "completed"), {})]

    def test_mark_failed_carries_reason(self, fake):
        lifecycle.mark_failed("s-1", failure_reason="决策价位门禁 fail")
        assert fake.calls == [
            ("update_session_status", ("s-1", "failed"), {"failure_reason": "决策价位门禁 fail"})
        ]

    def test_mark_interrupted(self, fake):
        lifecycle.mark_interrupted("s-1")
        assert fake.calls == [("update_session_status", ("s-1", "interrupted"), {})]


class TestUpdateClarify:
    def test_passthrough_fields(self, fake):
        """clarify 对话环的 focus/代码解析回写统一走 owner 透传。"""
        lifecycle.update_clarify("s-1", focus="关注负债率", status="clarifying")
        assert fake.calls == [
            (
                "update_session_for_clarify",
                ("s-1",),
                {"focus": "关注负债率", "status": "clarifying"},
            )
        ]

    def test_code_resolution_callback_shape(self, fake):
        lifecycle.update_clarify(
            "s-1", stock_code="600029.SH", stock_name="南方航空", display_name="南方航空(600029.SH)"
        )
        assert fake.calls[0][2]["stock_code"] == "600029.SH"
