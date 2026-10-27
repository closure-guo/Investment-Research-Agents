# tests/llm/test_gateway_stream_async.py
"""gateway complete_stream_async 测试（delta 5.1-C Task 2）。

mock raw_acompletion 返回 async iterable chunk 流，验证：
reasoning/text 事件、tool_call 增量合并、tool_choice 透传、
可重试退避、重试耗尽上抛、不可重试立即抛、per-chunk 超时。
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import litellm
import pytest

from finance_agent.llm.errors import LLMError
from finance_agent.llm.gateway import complete_stream_async

CFG = {"model": "glm-5.2", "baseUrl": "https://x/v1", "apiKey": "k"}


def _chunk(*, text="", reasoning="", tool_calls=None, finish=None):
    delta = SimpleNamespace(
        reasoning_content=reasoning or None,
        content=text or None,
        tool_calls=tool_calls,
    )
    return SimpleNamespace(choices=[SimpleNamespace(delta=delta, finish_reason=finish)])


def _tc(index=0, id=None, name=None, arguments=None):
    return SimpleNamespace(
        index=index,
        id=id,
        function=SimpleNamespace(name=name, arguments=arguments),
    )


class _AsyncIter:
    def __init__(self, items):
        self._items = list(items)

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self._items:
            return self._items.pop(0)
        raise StopAsyncIteration


class _SlowAsyncIter:
    """每个 __anext__ 都 sleep，触发 per-chunk 超时。"""

    def __aiter__(self):
        return self

    async def __anext__(self):
        await asyncio.sleep(1)
        return _chunk(text="x")


async def _collect(coro_iter):
    return [ev async for ev in coro_iter]


async def test_reasoning_text_finished(monkeypatch):
    async def fake_acompletion(**kwargs):  # noqa: ARG001
        return _AsyncIter([_chunk(reasoning="思"), _chunk(text="答"), _chunk(finish="stop")])

    monkeypatch.setattr(
        "finance_agent.llm.adapters.litellm_adapter.raw_acompletion", fake_acompletion
    )
    events = await _collect(
        complete_stream_async([{"role": "user", "content": "hi"}], llm_config=CFG)
    )
    kinds = [e.kind for e in events]
    assert kinds == ["reasoning", "text", "finished"]
    assert events[-1].finish_reason == "stop"


async def test_tool_calls_merged_across_chunks(monkeypatch):
    async def fake_acompletion(**kwargs):  # noqa: ARG001
        return _AsyncIter(
            [
                _chunk(tool_calls=[_tc(id="call_0", name="get_price", arguments='{"s')]),
                _chunk(tool_calls=[_tc(arguments='ymbol": "000001"}')]),
                _chunk(finish="tool_calls"),
            ]
        )

    monkeypatch.setattr(
        "finance_agent.llm.adapters.litellm_adapter.raw_acompletion", fake_acompletion
    )
    events = await _collect(
        complete_stream_async([{"role": "user", "content": "hi"}], llm_config=CFG)
    )
    tc_events = [e for e in events if e.kind == "tool_call"]
    assert len(tc_events) == 1
    calls = tc_events[0].tool_call["calls"]
    assert len(calls) == 1
    assert calls[0]["id"] == "call_0"
    assert calls[0]["function"]["name"] == "get_price"
    import json

    assert json.loads(calls[0]["function"]["arguments"]) == {"symbol": "000001"}
    assert events[-1].kind == "finished"
    assert events[-1].finish_reason == "tool_calls"


async def test_finish_stop_with_tool_calls(monkeypatch):
    async def fake_acompletion(**kwargs):  # noqa: ARG001
        return _AsyncIter(
            [
                _chunk(tool_calls=[_tc(id="c1", name="f", arguments="{}")]),
                _chunk(finish="stop"),
            ]
        )

    monkeypatch.setattr(
        "finance_agent.llm.adapters.litellm_adapter.raw_acompletion", fake_acompletion
    )
    events = await _collect(
        complete_stream_async([{"role": "user", "content": "hi"}], llm_config=CFG)
    )
    assert [e.kind for e in events] == ["tool_call", "finished"]
    assert events[-1].finish_reason == "tool_calls"


async def test_tool_choice_passed_through(monkeypatch):
    captured = {}

    async def fake_acompletion(**kwargs):
        captured.update(kwargs)
        return _AsyncIter([_chunk(text="答", finish="stop")])

    monkeypatch.setattr(
        "finance_agent.llm.adapters.litellm_adapter.raw_acompletion", fake_acompletion
    )
    # 现有 preset 均未声明 tool_choice_required 能力，此用例只验证透传，
    # guard 行为由 test_gateway_params_guard 覆盖，这里 no-op guard。
    monkeypatch.setattr("finance_agent.llm.gateway.guard_params_supported", lambda *a, **kw: None)
    await _collect(
        complete_stream_async(
            [{"role": "user", "content": "hi"}],
            llm_config=CFG,
            tools=[{"type": "function", "function": {"name": "f"}}],
            tool_choice="required",
        )
    )
    assert captured["tool_choice"] == "required"
    assert captured["tools"][0]["function"]["name"] == "f"


async def test_glm_tool_choice_required_proceeds(monkeypatch):
    """终审 I2：GLM 模型（env 分支解析到 ark-glm preset，声明 tool_choice_required）
    在真实 guard 下允许 tool_choice='required'（force_tool ReAct 轮不再硬失败）。

    注：请求级 llm_config 分支一律强绑 openai-compatible preset（无 required 能力），
    故此处用 env 分支复现生产 harness ReAct 路径（chat_stream 未带完整 trio 时
    走 env/preset 解析）。
    """
    captured = {}

    async def fake_acompletion(**kwargs):
        captured.update(kwargs)
        return _AsyncIter([_chunk(text="答", finish="stop")])

    monkeypatch.setattr(
        "finance_agent.llm.adapters.litellm_adapter.raw_acompletion", fake_acompletion
    )
    monkeypatch.setenv("LLM_MODEL", "glm-5.2")
    monkeypatch.setenv("LLM_BASE_URL", "https://x/v1")
    monkeypatch.setenv("LLM_API_KEY", "k")
    await _collect(
        complete_stream_async(
            [{"role": "user", "content": "hi"}],
            tools=[{"type": "function", "function": {"name": "f"}}],
            tool_choice="required",
        )
    )
    assert captured["tool_choice"] == "required"
    assert captured["tools"][0]["function"]["name"] == "f"


async def test_no_tools_omits_tool_choice(monkeypatch):
    """无 tools 时不得发 tool_choice（issue #481）。

    nightly @live 实证：opencode 网关对「tool_choice 无 tools」返回
    400 `tool_choice requires at least one available tool`——纯 reasoning 流
    （未带 tools）默认 tool_choice="auto" 即报错。语义上 tool_choice 只在
    有 tools 时有意义，无 tools 的请求不得携带。
    """
    captured = {}

    async def fake_acompletion(**kwargs):
        captured.update(kwargs)
        return _AsyncIter([_chunk(text="答", finish="stop")])

    monkeypatch.setattr(
        "finance_agent.llm.adapters.litellm_adapter.raw_acompletion", fake_acompletion
    )
    monkeypatch.setattr("finance_agent.llm.gateway.guard_params_supported", lambda *a, **kw: None)
    await _collect(complete_stream_async([{"role": "user", "content": "hi"}], llm_config=CFG))
    assert "tools" not in captured
    assert "tool_choice" not in captured


async def test_retryable_error_then_success(monkeypatch):
    calls = {"n": 0}

    async def fake_acompletion(**kwargs):  # noqa: ARG001
        calls["n"] += 1
        if calls["n"] == 1:
            raise litellm.exceptions.RateLimitError(
                message="limit", llm_provider="openai", model="m"
            )
        return _AsyncIter([_chunk(text="答", finish="stop")])

    async def fake_sleep(_):
        return None

    monkeypatch.setattr(
        "finance_agent.llm.adapters.litellm_adapter.raw_acompletion", fake_acompletion
    )
    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    events = await _collect(
        complete_stream_async([{"role": "user", "content": "hi"}], llm_config=CFG, max_retries=3)
    )
    assert calls["n"] == 2
    assert events[-1].kind == "finished"


async def test_retry_exhaustion_raises(monkeypatch):
    async def fake_acompletion(**kwargs):  # noqa: ARG001
        raise litellm.exceptions.RateLimitError(message="limit", llm_provider="openai", model="m")

    async def fake_sleep(_):
        return None

    monkeypatch.setattr(
        "finance_agent.llm.adapters.litellm_adapter.raw_acompletion", fake_acompletion
    )
    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    with pytest.raises(LLMError) as ei:
        await _collect(
            complete_stream_async(
                [{"role": "user", "content": "hi"}], llm_config=CFG, max_retries=2
            )
        )
    assert ei.value.retryable


async def test_non_retryable_raises_immediately(monkeypatch):
    calls = {"n": 0}

    async def fake_acompletion(**kwargs):  # noqa: ARG001
        calls["n"] += 1
        raise litellm.exceptions.AuthenticationError(
            message="bad key", llm_provider="openai", model="m"
        )

    monkeypatch.setattr(
        "finance_agent.llm.adapters.litellm_adapter.raw_acompletion", fake_acompletion
    )
    with pytest.raises(LLMError) as ei:
        await _collect(complete_stream_async([{"role": "user", "content": "hi"}], llm_config=CFG))
    assert not ei.value.retryable
    assert calls["n"] == 1


async def test_per_chunk_timeout(monkeypatch):
    calls = {"n": 0}

    async def fake_acompletion(**kwargs):  # noqa: ARG001
        calls["n"] += 1
        return _SlowAsyncIter()

    # 注意：不可 patch asyncio.sleep（_SlowAsyncIter 依赖真实 sleep 触发超时）
    monkeypatch.setattr(
        "finance_agent.llm.adapters.litellm_adapter.raw_acompletion", fake_acompletion
    )
    from finance_agent.llm.errors import LLMTimeoutError

    with pytest.raises(LLMTimeoutError):
        await _collect(
            complete_stream_async(
                [{"role": "user", "content": "hi"}],
                llm_config=CFG,
                chunk_timeout=0.05,
                max_retries=2,
                retry_delay=0.0,
            )
        )
    assert calls["n"] == 2


class TestStreamFlag:
    async def test_raw_acompletion_called_with_stream_true(self, monkeypatch):
        """流式必须下发 stream=True（否则拿到非流式 ModelResponse，无 __aiter__）。

        真实验证暴露：raw_acompletion 非流式返回 ModelResponse 非 async 迭代器，
        complete_stream_async 若漏传 stream=True 会在 __aiter__ 抛 AttributeError。
        """
        captured: dict = {}

        async def fake_raw(**kwargs):
            captured.update(kwargs)
            return _AsyncIter([_chunk(finish="stop")])

        monkeypatch.setattr("finance_agent.llm.adapters.litellm_adapter.raw_acompletion", fake_raw)
        events = await _collect(
            complete_stream_async(
                [{"role": "user", "content": "hi"}],
                llm_config={
                    "model": "deepseek/deepseek-chat",
                    "baseUrl": "https://api.deepseek.com/v1",
                    "apiKey": "k",
                },
            )
        )
        assert captured["stream"] is True
        assert any(e.kind == "finished" for e in events)


async def test_exhaustion_writes_error_to_observation(monkeypatch):
    """重试耗尽上抛前，观测写 output={error}/level=ERROR（对齐旧 harness 失败路径）。"""
    from finance_agent.langfuse_tracing import get_langfuse

    class _Obs:
        def __init__(self):
            self.updated = {}

        def update(self, **kw):
            self.updated.update(kw)

    class _CM:
        def __enter__(self):
            return obs

        def __exit__(self, *a):
            return False

    obs = _Obs()
    monkeypatch.setattr(
        "finance_agent.langfuse_tracing.get_langfuse",
        lambda: type("_LF", (), {"start_as_current_observation": lambda self, **kw: _CM()})(),
    )
    assert get_langfuse  # noqa: B018 -- 引用锚定
    import asyncio as _aio

    async def fake_acompletion(**kwargs):  # noqa: ARG001
        raise litellm.exceptions.RateLimitError(message="limit", llm_provider="openai", model="m")

    async def fake_sleep(_):
        return None

    monkeypatch.setattr(
        "finance_agent.llm.adapters.litellm_adapter.raw_acompletion", fake_acompletion
    )
    monkeypatch.setattr(_aio, "sleep", fake_sleep)
    with pytest.raises(LLMError):
        await _collect(
            complete_stream_async(
                [{"role": "user", "content": "hi"}],
                llm_config={
                    "model": "deepseek/deepseek-chat",
                    "baseUrl": "https://x/v1",
                    "apiKey": "k",
                },
                trace={"name": "t"},
            )
        )
    assert obs.updated.get("level") == "ERROR"
    assert "error" in obs.updated.get("output", {})


async def test_generator_exit_ends_observation_without_cm_exit(monkeypatch):
    """调用方提前终止流（GeneratorExit）时不触发 OTel detach（跨 context 噪音源）。

    复现：litellm_client 在 tool_call 事件后 return → async generator 被
    aclose/GC → GeneratorExit 在 yield 处抛出 → gateway finally 若仍调
    ``_gen_cm.__exit__``，其 OTel detach 会因 token 创建于不同 Context 而抛
    ValueError（opentelemetry 内部 logger.exception 刷 "Failed to detach context"
    噪音）。修复：GeneratorExit 场景改为 ``_gen.end()`` 优雅收口（span 数据不丢、
    零告警），不再调用 CM 的 ``__exit__``。
    """
    from finance_agent.langfuse_tracing import get_langfuse

    class _Obs:
        def __init__(self):
            self.ended = False

        def update(self, **kw):  # noqa: ARG002 -- 正常路径调用
            pass

        def end(self, *a, **kw):
            self.ended = True

    class _CM:
        def __init__(self, obs):
            self._obs = obs
            self.exited = False

        def __enter__(self):
            return self._obs

        def __exit__(self, *a):
            self.exited = True
            return False

    obs = _Obs()
    cm = _CM(obs)
    monkeypatch.setattr(
        "finance_agent.langfuse_tracing.get_langfuse",
        lambda: type("_LF", (), {"start_as_current_observation": lambda self, **kw: cm})(),
    )
    assert get_langfuse  # noqa: B018 -- 引用锚定

    async def fake_acompletion(**kwargs):  # noqa: ARG001
        return _AsyncIter(
            [
                _chunk(tool_calls=[_tc(id="c1", name="f", arguments="{}")]),
                _chunk(finish="tool_calls"),
            ]
        )

    monkeypatch.setattr(
        "finance_agent.llm.adapters.litellm_adapter.raw_acompletion", fake_acompletion
    )

    async def consume_and_abandon():
        gen = complete_stream_async(
            [{"role": "user", "content": "hi"}],
            llm_config={"model": "glm-5.2", "baseUrl": "https://x/v1", "apiKey": "k"},
            trace={"name": "t"},
        )
        async for ev in gen:
            if ev.kind == "tool_call":
                break  # 拿到 tool_call 就停（生产 ReAct 在 tool_call 后 return 的同款路径）
        await gen.aclose()  # 显式关闭 → GeneratorExit

    await consume_and_abandon()
    assert cm.exited is False, (
        "GeneratorExit 场景不应走 __exit__（OTel detach 跨 context 会刷 "
        "'Failed to detach context' 告警），应改为 _gen.end() 收口"
    )
    assert obs.ended is True, "observation 应以 end() 收口，span 数据不丢"


async def test_retry_observation_output_excludes_failed_attempt_text(monkeypatch):
    """重试后观测 output 只含成功那次尝试的文本（issue #50 症状复核）。

    issue #50 记录：`_accumulated_text/_accumulated_reasoning` 在 for-attempt 重试循环外
    初始化，重试后 trace output 会把多次 attempt 的文本拼接。当前实现（complete_stream_async）
    的 answer/reasoning_acc 已在循环体内重置——本用例把「第 1 次尝试先出正文再失败」这一
    最坏场景钉住，防回归：观测 output 必须等于第 2 次尝试的文本。
    """

    class _Obs:
        def __init__(self):
            self.updated: dict = {}

        def update(self, **kw):
            self.updated.update(kw)

    obs = _Obs()

    class _CM:
        def __enter__(self):
            return obs

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(
        "finance_agent.langfuse_tracing.get_langfuse",
        lambda: type("_LF", (), {"start_as_current_observation": lambda self, **kw: _CM()})(),
    )

    calls = {"n": 0}

    class _FailingIter:
        """先吐一段正文，再抛可重试错误（半途失败）。"""

        def __aiter__(self):
            return self

        async def __anext__(self):
            if calls["n"] == 1:
                calls["n"] = 2
                return _chunk(text="第一段不该进 trace")
            raise litellm.exceptions.RateLimitError(
                message="limit", llm_provider="openai", model="m"
            )

    async def fake_acompletion(**kwargs):  # noqa: ARG001
        if calls["n"] == 0:
            calls["n"] = 1
            return _FailingIter()
        return _AsyncIter([_chunk(text="第二次答案", finish="stop")])

    async def fake_sleep(_):
        return None

    monkeypatch.setattr(
        "finance_agent.llm.adapters.litellm_adapter.raw_acompletion", fake_acompletion
    )
    monkeypatch.setattr(asyncio, "sleep", fake_sleep)

    events = await _collect(
        complete_stream_async(
            [{"role": "user", "content": "hi"}],
            llm_config=CFG,
            max_retries=3,
            trace={"name": "t"},
        )
    )

    assert events[-1].kind == "finished"
    # 场景非空转：第 1 次尝试确实先吐过正文（否则本用例证明不了「重置」语义）
    streamed = "".join(e.text for e in events if e.kind == "text")
    assert "第一段不该进 trace" in streamed
    assert "第二次答案" in streamed
    answer = obs.updated.get("output", {}).get("answer", "")
    assert answer == "第二次答案"
    assert "第一段不该进 trace" not in answer


def _usage_chunk(prompt=11, completion=22):
    """仅带 usage 的 chunk（OpenAI 流末尾 usage 帧，choices 为空）。"""
    return SimpleNamespace(
        choices=[],
        usage=SimpleNamespace(
            prompt_tokens=prompt, completion_tokens=completion, total_tokens=prompt + completion
        ),
    )


async def test_finished_event_carries_usage(monkeypatch):
    """#77：finished 事件携带 usage（calibrate 接线的前提：真值要能到消费者）。"""

    async def fake_acompletion(**kwargs):  # noqa: ARG001
        return _AsyncIter([_chunk(text="答"), _usage_chunk(), _chunk(finish="stop")])

    monkeypatch.setattr(
        "finance_agent.llm.adapters.litellm_adapter.raw_acompletion", fake_acompletion
    )
    events = await _collect(
        complete_stream_async([{"role": "user", "content": "hi"}], llm_config=CFG)
    )
    assert events[-1].kind == "finished"
    assert events[-1].usage == {"prompt_tokens": 11, "completion_tokens": 22, "total_tokens": 33}


async def test_tool_call_event_carries_usage(monkeypatch):
    """工具调用路径：usage 必须挂在 tool_call 事件上。

    ReAct 循环消费到 is_finished 即 break——跟在 tool_call 之后的 finished 事件
    不会被读到，usage 只有挂在 tool_call 事件上才到得了循环。
    """

    async def fake_acompletion(**kwargs):  # noqa: ARG001
        return _AsyncIter(
            [
                _chunk(tool_calls=[_tc(index=0, id="c1", name="echo", arguments="{}")]),
                _usage_chunk(prompt=7, completion=3),
                _chunk(finish="tool_calls"),
            ]
        )

    monkeypatch.setattr(
        "finance_agent.llm.adapters.litellm_adapter.raw_acompletion", fake_acompletion
    )
    events = await _collect(
        complete_stream_async([{"role": "user", "content": "hi"}], llm_config=CFG)
    )
    tool_ev = next(e for e in events if e.kind == "tool_call")
    assert tool_ev.usage == {"prompt_tokens": 7, "completion_tokens": 3, "total_tokens": 10}


async def test_preset_passed_to_resolve_profile(monkeypatch):
    """preset 参数透传 resolve_profile（fallback 链成员按命名 preset 选中，#77）。"""
    import dataclasses
    from unittest.mock import patch

    from finance_agent.llm.registry import get_profile_preset

    async def fake_acompletion(**kwargs):  # noqa: ARG001
        return _AsyncIter([_chunk(text="答"), _chunk(finish="stop")])

    monkeypatch.setattr(
        "finance_agent.llm.adapters.litellm_adapter.raw_acompletion", fake_acompletion
    )
    pinned = dataclasses.replace(
        get_profile_preset("deepseek-official"), fallback=("openai-official",)
    )
    with patch("finance_agent.llm.gateway.resolve_profile", return_value=pinned) as rp_mock:
        events = await _collect(
            complete_stream_async(
                [{"role": "user", "content": "hi"}],
                llm_config=None,
                preset="openai-official",
            )
        )
    assert [e.kind for e in events] == ["text", "finished"]
    assert rp_mock.call_args.kwargs["preset"] == "openai-official"
    assert rp_mock.call_args.kwargs["llm_config"] is None
