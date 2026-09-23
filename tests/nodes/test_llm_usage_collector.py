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


class TestUsageAccumulatorConcurrency:
    """Δ3 终审 I1：LangGraph 并行分支经 executor ``copy_context`` 把累加器传播到
    worker 线程，fan-out 会**并发**调用同一实例的 ``add``——无锁时读-改-写竞态丢计数。
    """

    def test_concurrent_adds_do_not_lose_counts(self):
        import threading

        from finance_agent.nodes._llm_utils import UsageAccumulator

        acc = UsageAccumulator()
        threads_n, per_thread = 8, 200
        barrier = threading.Barrier(threads_n)

        def worker() -> None:
            barrier.wait()  # 尽量同时冲入，最大化竞态窗口
            for _ in range(per_thread):
                acc.add({"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15})

        threads = [threading.Thread(target=worker) for _ in range(threads_n)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        expected_calls = threads_n * per_thread
        assert acc.calls == expected_calls, "并发 add 丢计数 → 无锁（实例级 Lock 缺失）"
        assert acc.prompt_tokens == 10 * expected_calls
        assert acc.completion_tokens == 5 * expected_calls
        assert acc.total_tokens == 15 * expected_calls

    def test_add_holds_instance_lock(self):
        """确定性守卫（GIL 下统计竞态难触发）：``add`` 必须持实例锁。

        主线程先持锁 → worker 的 ``add`` 必须阻塞到释放；若 ``add`` 未持锁
        （或锁属性不存在），worker 会立即完成 → 本断言失败。
        """
        import threading

        from finance_agent.nodes._llm_utils import UsageAccumulator

        acc = UsageAccumulator()
        done = threading.Event()

        def worker() -> None:
            acc.add({"total_tokens": 15})
            done.set()

        with acc._lock:
            t = threading.Thread(target=worker)
            t.start()
            assert not done.wait(0.2), "add 未持实例锁 → 并发 fan-out 会丢计数"
        t.join(2)
        assert done.is_set(), "释放锁后 add 应完成"
        assert acc.total_tokens == 15


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


class TestNoDoubleCounting:
    """gateway tool_calls 分支先发 tool_call 再发 finished，两者携带同一份 usage。"""

    def test_tool_call_then_finished_same_usage_counted_once(self):
        from finance_agent.nodes._llm_utils import call_llm_streaming, usage_collector

        usage = {"prompt_tokens": 7, "completion_tokens": 3, "total_tokens": 10}
        stream = _fake_stream(
            CanonicalEvent(kind="tool_call", tool_call={"calls": []}, usage=usage),
            CanonicalEvent(kind="finished", finish_reason="tool_calls", usage=usage),
        )
        with patch(_STREAM, side_effect=stream), usage_collector() as acc:
            call_llm_streaming("p", node_name="trader")

        assert acc.calls == 1, "tool_call + finished 携带同一份 usage，只能计一次"
        assert acc.total_tokens == 10, "重复计数会把 tokens 翻倍"


class TestMalformedUsageIsNonFatal:
    """记账异常绝不允许中断原本正常的节点（调用点在只捕 LLM 错误的 try 内）。"""

    def test_add_tolerates_malformed_values(self):
        from finance_agent.nodes._llm_utils import UsageAccumulator

        acc = UsageAccumulator()
        acc.add({"prompt_tokens": "abc", "completion_tokens": None, "total_tokens": "12"})
        acc.add({"prompt_tokens": object(), "completion_tokens": [1], "total_tokens": {}})
        acc.add("not-a-dict")  # 形态违约：仍只计 calls
        # Δ3 终审 C2：int(inf) 抛 OverflowError、int(nan) 抛 ValueError → 均吞掉记 0
        acc.add({"prompt_tokens": float("inf"), "completion_tokens": float("nan")})
        assert acc.calls == 4
        assert acc.prompt_tokens == 0
        assert acc.completion_tokens == 0
        assert acc.total_tokens == 12, "可解析的字符串计数保留；不可解析记 0"

    def test_malformed_usage_does_not_break_call(self):
        from finance_agent.nodes._llm_utils import call_llm_streaming, usage_collector

        stream = _fake_stream(
            CanonicalEvent(kind="text", text="答"),
            _finished({"prompt_tokens": object(), "completion_tokens": "x"}),
        )
        with patch(_STREAM, side_effect=stream), usage_collector() as acc:
            assert call_llm_streaming("p", node_name="trader") == "答"

        assert acc.calls == 1


class TestRealGatewayPathFeedsCollector:
    """端到端锚点（Δ3T3 审查）：deep 管线真实调用链必须把 usage 送到收集器。

    不经 gateway 打桩——patch 的是最底层 adapter `raw_stream`，让**同步
    complete_stream 真实执行**；原先 `finished` 不挂 usage 时本用例 tokens 为 0
    （cohort 记账恒 0 / 预算熔断永不触发的根因）。
    """

    def test_sync_stream_usage_reaches_collector(self, monkeypatch):
        from types import SimpleNamespace

        from finance_agent.nodes._llm_utils import call_llm_streaming, usage_collector

        def real_shape_stream(**kwargs):  # noqa: ARG001
            delta = SimpleNamespace(reasoning_content="思考", content="正文")
            yield SimpleNamespace(
                choices=[SimpleNamespace(delta=delta, finish_reason=None)], usage=None
            )
            yield SimpleNamespace(
                choices=[],
                usage=SimpleNamespace(prompt_tokens=100, completion_tokens=40, total_tokens=140),
            )
            yield SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        delta=SimpleNamespace(reasoning_content=None, content=None),
                        finish_reason="stop",
                    )
                ],
                usage=None,
            )

        monkeypatch.setattr(
            "finance_agent.llm.adapters.litellm_adapter.raw_stream", real_shape_stream
        )
        with usage_collector() as acc:
            answer = call_llm_streaming(
                "p",
                node_name="trader",
                llm_config={"model": "openai/glm-5.3", "baseUrl": "https://x/v1", "apiKey": "k"},
            )

        assert answer == "正文"
        assert acc.calls == 1
        assert acc.total_tokens == 140, "同步 complete_stream 必须把 provider usage 挂到 finished"
