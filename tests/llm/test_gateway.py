# tests/llm/test_gateway.py
"""LLM Gateway 统一入口 + trace 契约字段测试（delta 4.4）。

generation metadata 必须携带 provider 契约上下文
（design 档案 §14）：profile/provider/model/purpose/capability/
finish_reason/repair_count/fallback_from/degradation。
"""

from __future__ import annotations

from finance_agent.llm.gateway import build_trace_metadata, complete_text
from finance_agent.llm.registry import get_profile_preset


def test_build_trace_metadata_basic_fields():
    profile = get_profile_preset("ark-glm")
    md = build_trace_metadata(profile, purpose="deep")
    assert md["profile"] == "ark-glm"
    assert md["provider"] == "openai"
    assert md["model"] == "openai/glm-5.2"
    assert md["purpose"] == "deep"
    assert md["capability"]["tools"] == "single"
    assert md["capability"]["json_schema"] == "json_mode"


def test_build_trace_optional_fields_default_to_none():
    profile = get_profile_preset("ark-glm")
    md = build_trace_metadata(profile, purpose="quick")
    assert md["finish_reason"] is None
    assert md["repair_count"] == 0
    assert md["fallback_from"] is None
    assert md["degradation"] is None


def test_build_trace_with_contract_facts():
    profile = get_profile_preset("deepseek-official")
    md = build_trace_metadata(
        profile,
        purpose="judge",
        finish_reason="tool_calls",
        repair_count=2,
        fallback_from="deepseek-official",
        degradation="action_protocol",
    )
    assert md["finish_reason"] == "tool_calls"
    assert md["repair_count"] == 2
    assert md["fallback_from"] == "deepseek-official"
    assert md["degradation"] == "action_protocol"


class TestCompleteTextTemperatureAndProviderOptions:
    def _run(self, monkeypatch, llm_config, *, temperature=None):
        from types import SimpleNamespace

        captured = []

        def fake_completion(**kwargs):
            captured.append(kwargs)
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(message=SimpleNamespace(content="答"), finish_reason="stop")
                ]
            )

        monkeypatch.setattr(
            "finance_agent.llm.adapters.litellm_adapter.raw_completion", fake_completion
        )
        complete_text(
            [{"role": "user", "content": "hi"}], llm_config=llm_config, temperature=temperature
        )
        return captured[0]

    def test_temperature_passed_through(self, monkeypatch):
        kw = self._run(
            monkeypatch,
            {"model": "glm-5.2", "baseUrl": "https://x/v1", "apiKey": "k"},
            temperature=0.2,
        )
        assert kw["temperature"] == 0.2

    def test_deepseek_suppresses_temperature_and_sends_provider_kwargs(self, monkeypatch):
        kw = self._run(
            monkeypatch,
            {
                "model": "deepseek/deepseek-chat",
                "baseUrl": "https://x/v1",
                "apiKey": "k",
            },
            temperature=0.9,
        )
        assert kw["extra_body"] == {"thinking": {"type": "enabled"}}
        assert kw["reasoning_effort"] == "max"
        assert "temperature" not in kw
        assert "suppress_temperature" not in kw


def test_complete_text_hangs_times_out(monkeypatch):
    """非流式 complete_text 半僵连接保护：raw_completion 卡死不挂进程。

    管线 report/nlp/web_fetcher 经 call_llm → complete_text 走此路径——只有
    请求级 300s 超时且对半僵连接不触发（evals 卡死根因同族），须线程泵兜底。
    """
    import threading
    import time

    monkeypatch.setattr(
        "finance_agent.llm.adapters.litellm_adapter.raw_completion",
        lambda **kw: time.sleep(30) or None,  # 永不返回的半僵调用
    )
    from finance_agent.llm.gateway import complete_text

    result: dict = {}

    def run():
        try:
            complete_text(
                [{"role": "user", "content": "hi"}],
                llm_config={"model": "openai/glm-5.3", "baseUrl": "https://x/v1", "apiKey": "k"},
                timeout_seconds=0.3,
            )
            result["err"] = None
        except Exception as e:
            result["err"] = type(e).__name__

    t = threading.Thread(target=run, daemon=True)
    t.start()
    t.join(4)
    assert not t.is_alive(), "complete_text 卡死：timeout 未生效"
    assert result["err"] == "LLMTimeoutError"


class TestCompleteStreamPresetPassthrough:
    """complete_stream 支持 ``preset=``：fallback 链成员按命名 preset 切换 profile。

    fallback 链执行需要按 preset 名选中链成员（非请求级 llm_config）——流式入口
    此前缺该参数，节点路径无法切 profile。
    """

    def test_preset_selects_named_profile(self, monkeypatch):
        from types import SimpleNamespace

        from finance_agent.llm.gateway import complete_stream

        calls = []

        def _chunk(text=None, finish=None):
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        delta=SimpleNamespace(reasoning_content=None, content=text),
                        finish_reason=finish,
                    )
                ]
            )

        def fake_raw_stream(**kwargs):
            calls.append(kwargs)
            return iter([_chunk(text="ok"), _chunk(finish="stop")])

        monkeypatch.setattr(
            "finance_agent.llm.adapters.litellm_adapter.raw_stream", fake_raw_stream
        )
        events = list(complete_stream([{"role": "user", "content": "hi"}], preset="ark-glm"))

        assert "".join(e.text for e in events if e.kind == "text") == "ok"
        assert calls[0]["model"] == "openai/glm-5.2"


class TestFinalizeObservationTruncation:
    """8KB 截断 wiring 直测（#49）：超长字段在写入 generation output 前实际被截断。

    此前只有 helper 级（truncate_for_trace）覆盖，没有「写入路径真的调了截断」的直测——
    `_finalize_observation` 若漏掉截断（或换用未截断的字段），trace 体积会静默膨胀。
    """

    @staticmethod
    def _fake_gen():
        class _Gen:
            def __init__(self):
                self.updated: dict = {}

            def update(self, **kw):
                self.updated.update(kw)

        return _Gen()

    def test_oversized_answer_and_reasoning_truncated(self):
        from finance_agent.llm.gateway import _finalize_observation

        gen = self._fake_gen()
        _finalize_observation(gen, answer="X" * 20000, reasoning="股" * 20000, last_usage=None)

        answer = gen.updated["output"]["answer"]
        reasoning = gen.updated["output"]["reasoning"]
        assert "[truncated" in answer
        assert "[truncated" in reasoning
        assert len(answer.encode("utf-8")) <= 8192 + 200
        assert len(reasoning.encode("utf-8")) <= 8192 + 200
        # 截断后仍是合法 UTF-8（CJK 字节边界不产生孤儿字节）
        reasoning.encode("utf-8").decode("utf-8")

    def test_small_output_written_verbatim(self):
        """未超限不截断（不得平白给正常输出加标记）。"""
        from finance_agent.llm.gateway import _finalize_observation

        gen = self._fake_gen()
        _finalize_observation(gen, answer="短答", reasoning="短思考", last_usage=None)
        assert gen.updated["output"]["answer"] == "短答"
        assert gen.updated["output"]["reasoning"] == "短思考"
        assert gen.updated["usage_details"] == {}


class TestCompleteTextProfileOverride:
    """评审修正 1（add-factor-proposal-agent）：``profile=`` 显式钉死调用 profile。

    路由调用点（GatewayProposalClient）先经 PolicyRouter 选出档位 profile，
    再以一个不可能重解析的 profile 覆盖发起调用——此前 gateway 无覆盖参数，
    路由结果会被 purpose 解析覆盖，产生静默漂移。
    """

    def test_profile_override_skips_resolution_and_pins_request(self, monkeypatch):
        from types import SimpleNamespace

        import finance_agent.llm.gateway as gateway

        def _must_not_resolve(**kwargs):
            raise AssertionError("显式传入 profile 时不得再按 purpose/preset 解析")

        monkeypatch.setattr(gateway, "resolve_profile", _must_not_resolve)
        captured: dict = {}

        def fake_completion(**kwargs):
            captured.update(kwargs)
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(message=SimpleNamespace(content="答"), finish_reason="stop")
                ],
                usage=None,
            )

        monkeypatch.setattr(
            "finance_agent.llm.adapters.litellm_adapter.raw_completion", fake_completion
        )
        profile = get_profile_preset("deepseek-official")
        text, metadata = complete_text(
            [{"role": "user", "content": "hi"}],
            purpose="factor_proposal",
            profile=profile,
        )
        assert text == "答"
        assert captured["model"] == "deepseek/deepseek-chat"
        assert metadata["profile"] == "deepseek-official"
        assert metadata["purpose"] == "factor_proposal"
