# tests/nodes/test_llm_usage_collector.py
"""Δ3 Task 3：usage 收集器（contextvar，默认 noop）——cohort 记账真值来源。

背景：provider usage 经 ``gateway._canonical_usage`` 挂到
``CanonicalEvent.usage``（键 prompt_tokens/completion_tokens/total_tokens），
但 deep 管线消费点 ``call_llm_streaming`` 的流式循环只处理
reasoning/text/error，**丢弃 usage**。本模块加默认 noop 的 contextvar
收集器，供 runner 用 ``with usage_collector() as acc:`` 包裹运行时收集
「一次 deep 分析的 token 真值」（spec：非估算）。

断言契约：
- 未激活时零行为变化（既有用例零回归；``_usage_acc.get() is None``）
- 激活后本上下文内每个 ``kind=="finished"`` 事件把 ``ev.usage`` 汇入累加器
- ``usage is None`` 只计 calls 不计 tokens（provider 无计数时）
- 退出上下文（含嵌套退出）后事件不再累加
"""

from unittest.mock import patch

import pytest

from finance_agent.llm.types import CanonicalEvent

_STREAM = "finance_agent.llm.gateway.complete_stream"


def _finished(usage=None):
    return CanonicalEvent(kind="finished", finish_reason="stop", usage=usage)


def _fake_stream(*events):
    """每次调用返回全新的 CanonicalEvent 迭代器。

    ``iter`` 是一次性消费的：多调用场景若用 ``return_value=iter(...)``，
    第二次调用拿到的是已耗尽的迭代器（测试会假绿）。
    """
    return lambda *_a, **_kw: iter(events)


class TestUsageAccumulator:
    """累加器本身的语义（不依赖 LLM 调用）。"""

    def test_starts_at_zero(self):
        from finance_agent.nodes._llm_utils import UsageAccumulator

        acc = UsageAccumulator()
        assert (acc.calls, acc.prompt_tokens, acc.completion_tokens, acc.total_tokens) == (
            0,
            0,
            0,
            0,
        )

    def test_add_none_counts_call_without_tokens(self):
        from finance_agent.nodes._llm_utils import UsageAccumulator

        acc = UsageAccumulator()
        acc.add(None)
        assert acc.calls == 1
        assert (acc.prompt_tokens, acc.completion_tokens, acc.total_tokens) == (0, 0, 0)

    def test_add_missing_keys_is_safe(self):
        """缺键不抛：缺 total_tokens 时按 prompt+completion 兜底（镜像 _canonical_usage）。"""
        from finance_agent.nodes._llm_utils import UsageAccumulator

        acc = UsageAccumulator()
        acc.add({})
        acc.add({"prompt_tokens": 5})
        acc.add({"prompt_tokens": 3, "completion_tokens": 4})
        assert acc.calls == 3
        assert acc.prompt_tokens == 8
        assert acc.completion_tokens == 4
        assert acc.total_tokens == 5 + 7

    def test_add_sums_all_keys(self):
        from finance_agent.nodes._llm_utils import UsageAccumulator

        acc = UsageAccumulator()
        acc.add({"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12})
        acc.add({"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2})
        assert acc.calls == 2
        assert (acc.prompt_tokens, acc.completion_tokens, acc.total_tokens) == (11, 3, 14)


class TestNoCollectorActive:
    """默认 noop：未激活时行为与加收集器之前完全一致。"""

    def test_contextvar_default_is_none(self):
        from finance_agent.nodes import _llm_utils

        assert _llm_utils._usage_acc.get() is None

    def test_streaming_unaffected_without_collector(self):
        with patch(_STREAM) as mock_stream:
            mock_stream.return_value = iter(
                [
                    CanonicalEvent(kind="text", text="答"),
                    _finished({"prompt_tokens": 7, "completion_tokens": 3, "total_tokens": 10}),
                ]
            )
            from finance_agent.nodes._llm_utils import call_llm_streaming

            assert call_llm_streaming("p", node_name="trader") == "答"

    def test_no_collector_leaked_after_context_exit(self):
        from finance_agent.nodes import _llm_utils
        from finance_agent.nodes._llm_utils import usage_collector

        with usage_collector():
            assert _llm_utils._usage_acc.get() is not None
        assert _llm_utils._usage_acc.get() is None


class TestCollectionInsideStreaming:
    """激活后经 call_llm_streaming 的流式消费循环收集 provider usage。"""

    def test_accumulates_finished_usage(self):
        from finance_agent.nodes._llm_utils import call_llm_streaming, usage_collector

        with patch(_STREAM) as mock_stream:
            mock_stream.return_value = iter(
                [
                    CanonicalEvent(kind="reasoning", reasoning="思考"),
                    CanonicalEvent(kind="text", text="正文"),
                    _finished({"prompt_tokens": 100, "completion_tokens": 40, "total_tokens": 140}),
                ]
            )
            with usage_collector() as acc:
                assert call_llm_streaming("p", node_name="trader") == "正文"

        assert acc.calls == 1
        assert acc.prompt_tokens == 100
        assert acc.completion_tokens == 40
        assert acc.total_tokens == 140

    def test_usage_none_counts_call_only(self):
        from finance_agent.nodes._llm_utils import call_llm_streaming, usage_collector

        with patch(_STREAM) as mock_stream:
            mock_stream.return_value = iter(
                [
                    _finished(None),
                    _finished({"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}),
                ]
            )
            with usage_collector() as acc:
                call_llm_streaming("p", node_name="trader")

        assert acc.calls == 2, "usage=None 的 finished 也要计一次调用"
        assert (acc.prompt_tokens, acc.completion_tokens, acc.total_tokens) == (10, 5, 15)

    def test_multiple_calls_accumulate(self):
        """一次 deep 分析内多次 LLM 调用汇入同一累加器。"""
        from finance_agent.nodes._llm_utils import call_llm_streaming, usage_collector

        stream = _fake_stream(
            _finished({"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3})
        )
        with patch(_STREAM, side_effect=stream), usage_collector() as acc:
            call_llm_streaming("a", node_name="bull_debater")
            call_llm_streaming("b", node_name="bear_debater")

        assert acc.calls == 2
        assert acc.total_tokens == 6

    def test_events_after_context_exit_not_accumulated(self):
        from finance_agent.nodes._llm_utils import call_llm_streaming, usage_collector

        stream = _fake_stream(
            _finished({"prompt_tokens": 4, "completion_tokens": 6, "total_tokens": 10})
        )
        with patch(_STREAM, side_effect=stream):
            with usage_collector() as acc:
                call_llm_streaming("in", node_name="trader")
            snapshot = (acc.calls, acc.prompt_tokens, acc.completion_tokens, acc.total_tokens)
            call_llm_streaming("out", node_name="trader")

        assert snapshot == (1, 4, 6, 10)
        assert (acc.calls, acc.total_tokens) == (1, 10), "退出上下文后不得再累加"

    def test_nested_collectors_are_isolated(self):
        """嵌套时内层独立记账；内层退出后外层恢复收集。"""
        from finance_agent.nodes._llm_utils import call_llm_streaming, usage_collector

        stream = _fake_stream(
            _finished({"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2})
        )
        with patch(_STREAM, side_effect=stream), usage_collector() as outer:
            call_llm_streaming("outer-1", node_name="trader")
            with usage_collector() as inner:
                call_llm_streaming("inner", node_name="trader")
            call_llm_streaming("outer-2", node_name="trader")

        assert inner.calls == 1
        assert inner.total_tokens == 2
        assert outer.calls == 2, "内层事件不得计入外层"
        assert outer.total_tokens == 4

    def test_retry_attempts_each_counted(self):
        """retryable 错误重试：两次实际请求各有一个 finished → 各计一次（真值，非估算）。"""
        from finance_agent.nodes._llm_utils import call_llm_streaming, usage_collector

        calls: list[int] = []

        def fake(*_a, **_kw):
            calls.append(1)
            if len(calls) == 1:
                return iter(
                    [
                        CanonicalEvent(
                            kind="error",
                            finish_reason="EmptyLLMOutputError",
                            raw={"error": "thinking 后即止"},
                        )
                    ]
                )
            return iter(
                [
                    CanonicalEvent(kind="text", text="res"),
                    _finished({"prompt_tokens": 9, "completion_tokens": 1, "total_tokens": 10}),
                ]
            )

        with patch(_STREAM, side_effect=fake), usage_collector() as acc:
            assert call_llm_streaming("p", node_name="trader") == "res"

        assert acc.calls == 1, "失败尝试无 finished 事件，不计调用"
        assert acc.total_tokens == 10


class TestErrorPathsDoNotBreakCollection:
    def test_error_event_raises_but_collector_still_resets(self):
        from finance_agent.llm.errors import UnknownLLMError
        from finance_agent.nodes import _llm_utils
        from finance_agent.nodes._llm_utils import call_llm_streaming, usage_collector

        with patch(_STREAM) as mock_stream:
            mock_stream.return_value = iter(
                [CanonicalEvent(kind="error", finish_reason="BogusClass", raw={"error": "bogus"})]
            )
            with pytest.raises(UnknownLLMError), usage_collector() as acc:
                call_llm_streaming("p", node_name="trader")

        assert acc.calls == 0
        assert _llm_utils._usage_acc.get() is None, "异常退出后 contextvar 必须复位"
