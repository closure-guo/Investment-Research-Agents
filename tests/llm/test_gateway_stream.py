# tests/llm/test_gateway_stream.py
"""gateway 流式/工具入口测试（delta 5.1 后半）。

complete_stream / complete_with_tools 是 legacy 转调 gateway 的前提。
用 mock raw_stream/raw_completion 验证归一事件流与工具守卫。
"""

from __future__ import annotations

from types import SimpleNamespace

from finance_agent.llm.gateway import complete_stream


def _chunk(*, text: str = "", reasoning: str = "", finish: str | None = None, usage=None):
    """构造 litellm 同步流 chunk 形态：choices[0].delta。"""
    delta = SimpleNamespace(
        reasoning_content=reasoning or None,
        content=text or None,
    )
    choice = SimpleNamespace(delta=delta, finish_reason=finish)
    return SimpleNamespace(choices=[choice], usage=usage)


def _usage_chunk(prompt=11, completion=22):
    """仅带 usage 的 chunk（OpenAI 流末尾 usage 帧，choices 为空）。"""
    return SimpleNamespace(
        choices=[],
        usage=SimpleNamespace(
            prompt_tokens=prompt, completion_tokens=completion, total_tokens=prompt + completion
        ),
    )


class TestCompleteStream:
    def test_yields_text_events(self, monkeypatch):

        captured = []

        def fake_stream(**kwargs):  # noqa: ARG001
            captured.append(kwargs)
            yield _chunk(text="你")
            yield _chunk(text="好")
            yield _chunk(finish="stop")

        monkeypatch.setattr("finance_agent.llm.adapters.litellm_adapter.raw_stream", fake_stream)
        events = []
        for ev in complete_stream(
            [{"role": "user", "content": "hi"}],
            llm_config={"model": "glm-5.2", "baseUrl": "https://x/v1", "apiKey": "k"},
        ):
            events.append(ev)
            # raw_stream 内部固定 stream=True，此处仅验证请求构造已下发
            assert captured[0]["model"] == "openai/glm-5.2"
        kinds = [e.kind for e in events]
        assert "text" in kinds
        assert "finished" in kinds

    def test_yields_reasoning_events(self, monkeypatch):
        def fake_stream(**kwargs):  # noqa: ARG001
            yield _chunk(reasoning="思考过程")
            yield _chunk(text="答", finish="stop")

        monkeypatch.setattr("finance_agent.llm.adapters.litellm_adapter.raw_stream", fake_stream)
        kinds = [
            e.kind
            for e in complete_stream(
                [{"role": "user", "content": "hi"}],
                llm_config={"model": "glm-5.2", "baseUrl": "https://x/v1", "apiKey": "k"},
            )
        ]
        assert "reasoning" in kinds
        assert "text" in kinds


class TestCompleteStreamTemperatureAndProviderOptions:
    def _run(self, monkeypatch, llm_config, *, temperature=None):
        captured = []

        def fake_stream(**kwargs):
            captured.append(kwargs)
            yield _chunk(text="答", finish="stop")

        monkeypatch.setattr("finance_agent.llm.adapters.litellm_adapter.raw_stream", fake_stream)
        list(
            complete_stream(
                [{"role": "user", "content": "hi"}],
                llm_config=llm_config,
                temperature=temperature,
            )
        )
        return captured[0]

    def test_temperature_passed_through(self, monkeypatch):
        kw = self._run(
            monkeypatch,
            {"model": "glm-5.2", "baseUrl": "https://x/v1", "apiKey": "k"},
            temperature=0.3,
        )
        assert kw["temperature"] == 0.3

    def test_no_temperature_when_not_given(self, monkeypatch):
        kw = self._run(
            monkeypatch,
            {"model": "glm-5.2", "baseUrl": "https://x/v1", "apiKey": "k"},
        )
        assert "temperature" not in kw

    def test_deepseek_provider_kwargs_and_suppressed_temperature(self, monkeypatch):
        """deepseek thinking=enabled：extra_body/reasoning_effort 下发，不发 temperature。"""
        kw = self._run(
            monkeypatch,
            {
                "model": "deepseek/deepseek-chat",
                "baseUrl": "https://x/v1",
                "apiKey": "k",
            },
            temperature=0.7,
        )
        assert kw["extra_body"] == {"thinking": {"type": "enabled"}}
        assert kw["reasoning_effort"] == "max"
        assert "suppress_temperature" not in kw
        assert "temperature" not in kw

    def test_deepseek_thinking_disabled_still_sends_temperature(self, monkeypatch):
        kw = self._run(
            monkeypatch,
            {
                "model": "deepseek/deepseek-chat",
                "baseUrl": "https://x/v1",
                "apiKey": "k",
                "thinking": "disabled",
            },
            temperature=0.7,
        )
        assert kw["extra_body"] == {"thinking": {"type": "disabled"}}
        assert kw["temperature"] == 0.7


def sets_all_text(events):
    return "".join(e.text for e in events if e.kind == "text")


class TestCompleteStreamObservation:
    def test_trace_updates_generation(self, monkeypatch):
        """观测收口：传 trace 时 generation 结束前 update answer/reasoning。"""
        updated = {}

        class _FakeObs:
            def update(self, **kw):
                updated.update(kw)

        class _FakeCM:
            def __enter__(self):
                return _FakeObs()

            def __exit__(self, *a):
                return False

        got = {}

        class _FakeLF:
            def start_as_current_observation(self, **kw):
                got["name"] = kw.get("name")
                got["metadata"] = kw.get("metadata")
                return _FakeCM()

        monkeypatch.setattr("finance_agent.langfuse_tracing.get_langfuse", lambda: _FakeLF())

        def fake_stream(**kwargs):  # noqa: ARG001
            yield _chunk(reasoning="思")
            yield _chunk(text="答", finish="stop")

        monkeypatch.setattr("finance_agent.llm.adapters.litellm_adapter.raw_stream", fake_stream)
        events = list(
            complete_stream(
                [{"role": "user", "content": "hi"}],
                llm_config={"model": "glm-5.2", "baseUrl": "https://x/v1", "apiKey": "k"},
                trace={"name": "react_agent", "metadata": {"agent": "react"}},
            )
        )
        assert any(e.kind == "text" for e in events)
        assert got["name"] == "react_agent"
        assert updated["output"]["answer"] == "答"
        assert updated["output"]["reasoning"] == "思"


class TestSyncChunkTimeout:
    """同步流半僵流保护（evals 跑批暴露：GLM 流中途停住永不结束，进程静默挂死）。

    complete_stream 必须有 per-chunk 超时（镜像 async 的 wait_for）：
    chunk 超时未到达 → error 事件(LLMTimeoutError)，不无限阻塞。
    """

    def test_stalled_stream_times_out(self, monkeypatch):
        import threading
        import time

        def fake_stream(**kwargs):  # noqa: ARG001
            def gen():
                yield _chunk(text="你")
                time.sleep(30)  # 半僵流：chunk 之间永久停顿
                yield _chunk(text="好", finish="stop")

            return gen()

        monkeypatch.setattr("finance_agent.llm.adapters.litellm_adapter.raw_stream", fake_stream)
        result: dict = {}

        def run():
            result["events"] = list(
                complete_stream(
                    [{"role": "user", "content": "hi"}],
                    llm_config={
                        "model": "openai/glm-5.3",
                        "baseUrl": "https://x/v1",
                        "apiKey": "k",
                    },
                    chunk_timeout=0.3,
                )
            )

        t = threading.Thread(target=run, daemon=True)
        t.start()
        t.join(5)
        assert not t.is_alive(), "同步流卡死：chunk_timeout 未生效"
        evs = result["events"]
        assert any(e.kind == "text" for e in evs)
        assert evs[-1].kind == "error"
        assert evs[-1].finish_reason == "LLMTimeoutError"

    def test_normal_stream_unaffected(self, monkeypatch):
        def fake_stream(**kwargs):  # noqa: ARG001
            yield _chunk(text="你好")
            yield _chunk(finish="stop")

        monkeypatch.setattr("finance_agent.llm.adapters.litellm_adapter.raw_stream", fake_stream)
        evs = list(
            complete_stream(
                [{"role": "user", "content": "hi"}],
                llm_config={"model": "openai/glm-5.3", "baseUrl": "https://x/v1", "apiKey": "k"},
                chunk_timeout=1.0,
            )
        )
        assert evs[-1].kind == "finished"


class TestSyncStreamUsage:
    """Δ3 Task 3 审查：同步 complete_stream 必须把 provider usage 挂到 finished 事件。

    背景：`usage=_canonical_usage(...)` 原本只存在于 `complete_stream_async`
    （ReAct 路径），而 deep 管线消费点 `nodes/_llm_utils.call_llm_streaming`
    调的是**同步** `complete_stream` → usage 永不到达消费方，cohort 记账
    tokens 恒 0、预算熔断（`spent += tokens_total or 0`）永不触发。
    provider 数据本就在 `_last_usage`（adapter 已带
    `stream_options={"include_usage": True}`，见 litellm_adapter.raw_stream）。
    断言与异步侧 `test_finished_event_carries_usage`（#77）对齐。
    """

    CFG = {"model": "openai/glm-5.3", "baseUrl": "https://x/v1", "apiKey": "k"}

    def test_finished_event_carries_usage(self, monkeypatch):
        def fake_stream(**kwargs):  # noqa: ARG001
            yield _chunk(text="答")
            yield _chunk(usage=SimpleNamespace(prompt_tokens=11, completion_tokens=22))
            yield _chunk(finish="stop")

        monkeypatch.setattr("finance_agent.llm.adapters.litellm_adapter.raw_stream", fake_stream)
        events = list(complete_stream([{"role": "user", "content": "hi"}], llm_config=self.CFG))
        assert events[-1].kind == "finished"
        assert events[-1].usage == {
            "prompt_tokens": 11,
            "completion_tokens": 22,
            "total_tokens": 33,
        }

    def test_usage_only_frame_with_empty_choices(self, monkeypatch):
        """OpenAI 风格 usage 帧 `choices: []`（异步侧已有 `if not choices: continue` 护栏）。

        同步循环原先直接 `chunk.choices[0]` → IndexError → normalize_exception
        归一为 error 事件，整次调用失败（节点被拖死）。usage 帧必须被消费
        （累进 _last_usage）而非崩溃，且 finished 携带该 usage。
        """

        def fake_stream(**kwargs):  # noqa: ARG001
            yield _chunk(text="答")
            yield _usage_chunk(prompt=7, completion=3)
            yield _chunk(finish="stop")

        monkeypatch.setattr("finance_agent.llm.adapters.litellm_adapter.raw_stream", fake_stream)
        events = list(complete_stream([{"role": "user", "content": "hi"}], llm_config=self.CFG))
        assert not [e for e in events if e.kind == "error"], "usage 帧不得引发 error 事件"
        assert events[-1].kind == "finished"
        assert events[-1].usage == {"prompt_tokens": 7, "completion_tokens": 3, "total_tokens": 10}

    def test_finished_usage_none_when_provider_silent(self, monkeypatch):
        """provider 不返回 usage → finished.usage 保持 None（消费方据此记 NULL 而非 0）。"""

        def fake_stream(**kwargs):  # noqa: ARG001
            yield _chunk(text="答", finish="stop")

        monkeypatch.setattr("finance_agent.llm.adapters.litellm_adapter.raw_stream", fake_stream)
        events = list(complete_stream([{"role": "user", "content": "hi"}], llm_config=self.CFG))
        assert events[-1].kind == "finished"
        assert events[-1].usage is None

    def test_resume_segment_usage_tracked(self, monkeypatch):
        """截断续写：finished 携带**续写段** usage（镜像异步侧 `last_usage = chunk2.usage`）。"""
        streams: list[list] = []

        def fake_stream(**kwargs):  # noqa: ARG001
            # 首段：length 截断（正文非空触发续写），usage=(1,1)
            if not streams:
                streams.append([1])
                yield _chunk(text="前半")
                yield _chunk(usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1))
                yield _chunk(finish="length")
                return
            streams.append([2])
            yield _chunk(text="后半")
            yield _chunk(usage=SimpleNamespace(prompt_tokens=1, completion_tokens=2))
            yield _chunk(finish="stop")

        monkeypatch.setattr("finance_agent.llm.adapters.litellm_adapter.raw_stream", fake_stream)
        events = list(complete_stream([{"role": "user", "content": "hi"}], llm_config=self.CFG))
        assert len(streams) == 2, "应触发续写二次流"
        assert "".join(e.text for e in events if e.kind == "text") == "前半后半"
        assert events[-1].kind == "finished"
        assert events[-1].usage == {"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3}


def test_tools_none_not_sent_to_raw_stream(monkeypatch):
    """tools=None 不得下发（ark 收 tools:null 会短思考后异常截断 finish=length）。

    旧全局 drop_params 时代 litellm 静默丢弃 None 参数；drop_params 白名单化
    移除后，同步 complete_stream 无条件携带 tools=None 直达端点——evals 跑批
    全部节点 answer=0 + reasoning~4k + finish=length 的根因。
    """
    captured: dict = {}

    def fake_stream(**kwargs):
        captured.update(kwargs)
        yield _chunk(text="好", finish="stop")

    monkeypatch.setattr("finance_agent.llm.adapters.litellm_adapter.raw_stream", fake_stream)
    list(
        complete_stream(
            [{"role": "user", "content": "hi"}],
            llm_config={"model": "openai/glm-5.3", "baseUrl": "https://x/v1", "apiKey": "k"},
        )
    )
    assert "tools" not in captured, "tools=None 被下发到端点"

    def fake_stream_tools(**kwargs):
        captured.update(kwargs)
        yield _chunk(finish="stop")

    monkeypatch.setattr("finance_agent.llm.adapters.litellm_adapter.raw_stream", fake_stream_tools)
    list(
        complete_stream(
            [{"role": "user", "content": "hi"}],
            tools=[{"type": "function", "function": {"name": "f"}}],
            llm_config={"model": "openai/glm-5.3", "baseUrl": "https://x/v1", "apiKey": "k"},
        )
    )
    assert captured["tools"] == [{"type": "function", "function": {"name": "f"}}]
