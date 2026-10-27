"""会话 failure_reason 文案（issue #490）。

2026-10-26 生产实证：8 个池分析会话落库 ``'TimeoutError: '``——空 message 异常经
``f"{type(e).__name__}: {e}"`` 只剩类型名加空尾巴，运维无法判断是哪一层超时。
"""

from __future__ import annotations

from finance_agent.failure_text import failure_reason_text


def test_empty_timeout_gets_explicit_placeholder():
    err = TimeoutError()
    assert failure_reason_text(err) == "TimeoutError（无错误消息）"


def test_empty_timeout_with_context_names_wait_site():
    err = TimeoutError()
    assert failure_reason_text(err, context="等待 LLM 流超时") == (
        "TimeoutError（无错误消息，等待 LLM 流超时）"
    )


def test_nonempty_message_keeps_type_and_message():
    err = TimeoutError("流式 chunk 超过 6s 未到达")
    assert failure_reason_text(err) == "TimeoutError: 流式 chunk 超过 6s 未到达"


def test_whitespace_only_message_treated_as_empty():
    err = RuntimeError("   ")
    assert failure_reason_text(err) == "RuntimeError（无错误消息）"
