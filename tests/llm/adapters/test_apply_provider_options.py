# tests/llm/adapters/test_apply_provider_options.py
"""adapter 消费 provider_options + raw_* 默认超时注入测试（Task 2）。

apply_provider_options 是 provider_options 唯一消费点：registry schema
校验 → deepseek 特定 kwargs（extra_body.thinking / reasoning_effort）；
``suppress_temperature`` 是 adapter→gateway 的内部契约标志（deepseek
thinking=enabled 时不发送 temperature，对齐 legacy deep 分支）。
"""

from __future__ import annotations

import dataclasses

import pytest
from pydantic import ValidationError

from finance_agent.llm.adapters.litellm_adapter import (
    apply_api_form_kwargs,
    apply_provider_options,
    raw_acompletion,
    raw_completion,
    raw_stream,
)
from finance_agent.llm.registry import get_profile_preset
from finance_agent.llm.types import ModelProfile


def _deepseek(**options: object) -> ModelProfile:
    """deepseek-official preset + 覆盖 provider_options。"""
    p = get_profile_preset("deepseek-official")
    return dataclasses.replace(p, provider_options=dict(options))


def _ark_glm(**options: object) -> ModelProfile:
    """ark-glm preset + 覆盖 provider_options（provider=openai，name=ark-glm）。"""
    p = get_profile_preset("ark-glm")
    return dataclasses.replace(p, provider_options=dict(options))


class TestApplyProviderOptions:
    def test_enabled_returns_extra_body_effort_and_suppress(self):
        out = apply_provider_options(_deepseek(thinking="enabled", reasoning_effort="max"))
        assert out == {
            "extra_body": {"thinking": {"type": "enabled"}},
            "reasoning_effort": "max",
            "suppress_temperature": True,
        }

    def test_disabled_no_suppress_temperature(self):
        out = apply_provider_options(_deepseek(thinking="disabled", reasoning_effort="low"))
        assert out["extra_body"] == {"thinking": {"type": "disabled"}}
        assert out["reasoning_effort"] == "low"
        assert "suppress_temperature" not in out

    def test_unset_thinking_omits_thinking_key_and_suppress(self):
        out = apply_provider_options(_deepseek(reasoning_effort="high"))
        assert out == {"reasoning_effort": "high"}

    def test_empty_options_returns_empty(self):
        assert apply_provider_options(_deepseek()) == {}

    def test_non_deepseek_returns_empty(self):
        assert apply_provider_options(get_profile_preset("ark-glm")) == {}

    def test_invalid_value_raises_validation_error(self):
        with pytest.raises(ValidationError):
            apply_provider_options(_deepseek(thinking="sometimes"))

    def test_unknown_key_raises_validation_error(self):
        with pytest.raises(ValidationError):
            apply_provider_options(_deepseek(temperature=0.5))

    def test_deepseek_without_extra_body_allowed_returns_empty(self):
        p = _deepseek(thinking="enabled")
        cap = dataclasses.replace(p.capability, extra_body_allowed=False)
        assert apply_provider_options(dataclasses.replace(p, capability=cap)) == {}

    def test_ark_glm_returns_reasoning_effort(self):
        out = apply_provider_options(_ark_glm(reasoning_effort="high"))
        assert out == {"extra_body": {"reasoning_effort": "high"}}

    def test_ark_glm_does_not_emit_thinking_or_suppress(self):
        out = apply_provider_options(_ark_glm(reasoning_effort="max"))
        # 实证（方舟 GLM-5.3）：顶层 reasoning_effort 被 litellm openai 路由拒绝，
        # 必须放 extra_body 透传端点；ark 无 thinking/suppress_temperature 语义。
        assert out == {"extra_body": {"reasoning_effort": "max"}}
        assert "suppress_temperature" not in out

    def test_ark_glm_env_named_profile_hits(self):
        """env 分支 profile（name=env:openai/glm-5.3 不含 ark）也命中 ark-glm。"""
        p = _ark_glm(reasoning_effort="low")
        p = dataclasses.replace(p, name="env:openai/glm-5.3")
        out = apply_provider_options(p)
        assert out == {"extra_body": {"reasoning_effort": "low"}}

    def test_ark_glm_invalid_effort_raises_validation_error(self):
        with pytest.raises(ValidationError):
            apply_provider_options(_ark_glm(reasoning_effort="ultra"))

    def test_ark_glm_unknown_key_raises_validation_error(self):
        with pytest.raises(ValidationError):
            apply_provider_options(_ark_glm(thinking="enabled"))

    def test_unrecognized_provider_with_options_returns_empty(self):
        """#76：schema 查无的 provider（如 openai/gpt-4o）provider_options 不消费 → {}。

        识别锚点是 name/model 语义（ark/glm/kimi/k3），不是 provider=openai 本身——
        换成无名 openai 模型后，携带的 options 一律不落请求。
        """
        p = _ark_glm(reasoning_effort="high")
        p = dataclasses.replace(p, name="env:openai/gpt-4o", model="gpt-4o")
        assert apply_provider_options(p) == {}


class TestRawTimeoutInjection:
    """incident 016/017 卡死防护：调用方未传 timeout 时注入默认超时。"""

    def _patch_completion(self, monkeypatch, captured):
        import litellm

        def fake_completion(**kwargs):
            captured.append(kwargs)
            return "resp"

        monkeypatch.setattr(litellm, "completion", fake_completion)

    def test_raw_completion_injects_default_timeout(self, monkeypatch):
        captured: list[dict] = []
        self._patch_completion(monkeypatch, captured)
        monkeypatch.delenv("LLM_TIMEOUT_SECONDS", raising=False)
        raw_completion(model="openai/m")
        assert captured[0]["timeout"] == 300.0

    def test_raw_stream_injects_default_timeout(self, monkeypatch):
        captured: list[dict] = []
        self._patch_completion(monkeypatch, captured)
        monkeypatch.delenv("LLM_TIMEOUT_SECONDS", raising=False)
        raw_stream(model="openai/m")
        assert captured[0]["timeout"] == 300.0
        assert captured[0]["stream"] is True

    def test_env_overrides_default_timeout(self, monkeypatch):
        captured: list[dict] = []
        self._patch_completion(monkeypatch, captured)
        monkeypatch.setenv("LLM_TIMEOUT_SECONDS", "12.5")
        raw_completion(model="openai/m")
        assert captured[0]["timeout"] == 12.5

    def test_explicit_timeout_not_overwritten(self, monkeypatch):
        captured: list[dict] = []
        self._patch_completion(monkeypatch, captured)
        raw_completion(model="openai/m", timeout=42)
        assert captured[0]["timeout"] == 42

    def test_raw_acompletion_injects_default_timeout(self, monkeypatch):
        """async 路径同样注入请求级默认超时（review finding：旧 harness timeout=120 行为回归）。"""
        import asyncio

        import litellm

        captured: list[dict] = []

        async def fake_acompletion(**kwargs):
            captured.append(kwargs)
            return "resp"

        monkeypatch.setattr(litellm, "acompletion", fake_acompletion)
        monkeypatch.delenv("LLM_TIMEOUT_SECONDS", raising=False)
        asyncio.run(raw_acompletion(model="openai/m"))
        assert captured[0]["timeout"] == 300.0

    def test_raw_acompletion_explicit_timeout_not_overwritten(self, monkeypatch):
        import asyncio

        import litellm

        captured: list[dict] = []

        async def fake_acompletion(**kwargs):
            captured.append(kwargs)
            return "resp"

        monkeypatch.setattr(litellm, "acompletion", fake_acompletion)
        asyncio.run(raw_acompletion(model="openai/m", timeout=42))
        assert captured[0]["timeout"] == 42


class TestApplyApiFormKwargs:
    """add-llm-api-form：profile.api_form → litellm ``api`` 参数映射。"""

    def test_chat_completion_maps_to_chat(self):
        p = get_profile_preset("openai-official")
        import dataclasses

        p = dataclasses.replace(p, api_form="chat_completion")
        assert apply_api_form_kwargs(p) == {"api": "chat"}

    def test_messages_maps_to_messages(self):
        import dataclasses

        p = dataclasses.replace(get_profile_preset("openai-official"), api_form="messages")
        assert apply_api_form_kwargs(p) == {"api": "messages"}

    def test_responses_maps_to_responses(self):
        import dataclasses

        p = dataclasses.replace(get_profile_preset("openai-official"), api_form="responses")
        assert apply_api_form_kwargs(p) == {"api": "responses"}

    def test_no_api_form_returns_empty(self):
        # 默认 profile 无 api_form（None）
        assert apply_api_form_kwargs(get_profile_preset("openai-official")) == {}


def _kimi(model: str = "openai/kimi-for-coding", **options: object) -> ModelProfile:
    """kimi 族 profile（request 分支构造形态：name=request:<model>）。"""
    base = get_profile_preset("openai-compatible")
    return dataclasses.replace(
        base,
        name=f"request:{model}",
        model=model,
        provider_options=dict(options),
    )


class TestKimiApplyProviderOptions:
    """kimi 族 effort 透传（add-kimi-reasoning-effort）：与 ark-glm 同 extra_body 机制。"""

    def test_provider_options_key_kimi_models(self):
        from finance_agent.llm.adapters.litellm_adapter import _provider_options_key

        assert _provider_options_key(_kimi("openai/kimi-for-coding")) == "kimi"
        assert _provider_options_key(_kimi("openai/kimi-for-coding-highspeed")) == "kimi"
        assert _provider_options_key(_kimi("openai/k3")) == "kimi"
        assert _provider_options_key(_kimi("openai/k3-256k")) == "kimi"

    def test_provider_options_key_plain_openai_none(self):
        from finance_agent.llm.adapters.litellm_adapter import _provider_options_key

        assert _provider_options_key(_kimi("openai/gpt-4o")) is None

    def test_apply_none_effort_emits_extra_body_and_suppress(self):
        out = apply_provider_options(_kimi(**{"reasoning_effort": "none"}))
        assert out == {
            "extra_body": {"reasoning_effort": "none"},
            "suppress_temperature": True,
        }

    def test_apply_low_effort_emits_extra_body_and_suppress(self):
        out = apply_provider_options(_kimi(**{"reasoning_effort": "low"}))
        assert out == {
            "extra_body": {"reasoning_effort": "low"},
            "suppress_temperature": True,
        }

    def test_apply_no_options_returns_empty(self):
        assert apply_provider_options(_kimi()) == {}

    def test_apply_invalid_effort_raises(self):
        with pytest.raises(ValidationError):
            apply_provider_options(_kimi(**{"reasoning_effort": "medium"}))

    def test_apply_unknown_key_raises(self):
        with pytest.raises(ValidationError):
            apply_provider_options(_kimi(thinking="enabled"))

    def test_plain_openai_options_not_consumed(self):
        """非 kimi/glm 的 openai 模型：schema 查无 → 不消费（既有语义回归）。"""
        assert apply_provider_options(_kimi("openai/gpt-4o", **{"reasoning_effort": "none"})) == {}

    def test_apply_kimi_suppresses_temperature(self):
        """Kimi 端点采样温度锁死（思考档=1、无思考档=0.6），任何自定义
        temperature 都 400——effort 显式配置时经内部契约抑制 temperature，
        交给端点固定值（#216 降级是兜底而非常态）。"""
        out = apply_provider_options(_kimi(**{"reasoning_effort": "none"}))
        assert out == {
            "extra_body": {"reasoning_effort": "none"},
            "suppress_temperature": True,
        }

    def test_apply_ark_glm_does_not_suppress_temperature(self):
        """回归护栏：方舟 GLM 温度可调，suppress 是 kimi 专属，不得外溢。"""
        out = apply_provider_options(_ark_glm(**{"reasoning_effort": "low"}))
        assert out == {"extra_body": {"reasoning_effort": "low"}}
