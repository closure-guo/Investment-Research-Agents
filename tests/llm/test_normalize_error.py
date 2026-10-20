# tests/llm/test_normalize_error.py
"""litellm 异常 → typed error 归一化测试（delta Task 2.4）。

错误归一化收口 adapter：调用方只见 LLMError 家族，不见 litellm 细节。
可重试分类驱动调用方重试策略（ContentFiltered 不重试等）。
"""

from __future__ import annotations

import litellm
import pytest

from finance_agent.llm.adapters.litellm_adapter import normalize_exception
from finance_agent.llm.errors import (
    AuthError,
    ContentFiltered,
    ContextOverflow,
    LLMError,
    LLMTimeoutError,
    ModelNotFound,
    ProviderInternalError,
    RateLimitError,
    UnknownLLMError,
)


def _litellm_exc(name: str):
    """构造 litellm 异常实例（兼容不同 __init__ 签名）。"""
    cls = getattr(litellm.exceptions, name)
    try:
        return cls(message="boom", model="openai/glm-5.2", llm_provider="openai")
    except TypeError:
        return cls("boom")


class TestErrorNormalization:
    @pytest.mark.parametrize(
        ("litellm_name", "expected"),
        [
            ("AuthenticationError", AuthError),
            ("RateLimitError", RateLimitError),
            ("Timeout", LLMTimeoutError),
            ("APIConnectionError", LLMTimeoutError),
            ("InternalServerError", ProviderInternalError),
            ("ServiceUnavailableError", ProviderInternalError),
            ("NotFoundError", ModelNotFound),
            ("ContextWindowExceededError", ContextOverflow),
        ],
    )
    def test_known_mapping(self, litellm_name, expected):
        err = normalize_exception(_litellm_exc(litellm_name))
        assert isinstance(err, expected)
        assert isinstance(err, LLMError)

    def test_content_filter_error(self):
        err = normalize_exception(_litellm_exc("ContentPolicyViolationError"))
        assert isinstance(err, ContentFiltered)
        assert err.retryable is False

    def test_unknown_wrapped(self):
        err = normalize_exception(RuntimeError("weird"))
        assert isinstance(err, UnknownLLMError)
        assert "weird" in str(err)

    def test_retryable_flags(self):
        assert AuthError().retryable is False
        assert RateLimitError().retryable is True
        assert LLMTimeoutError().retryable is True
        assert ProviderInternalError().retryable is True
        assert ModelNotFound().retryable is False
        assert ContentFiltered().retryable is False


def _no_tools_cap():
    from finance_agent.llm.types import Capability

    return Capability(
        tools="none",
        tool_choice_required=False,
        streaming=True,
        streaming_tool_calls=False,
        json_schema="none",
        supports_system_role=True,
        reasoning_field=None,
        reasoning_must_echo_on_tool=False,
        reasoning_forced=False,
        max_context=8000,
        max_output=4096,
        extra_body_allowed=False,
    )


class TestParamGuard:
    """关键参数不静默丢弃：不支持时显式 UnsupportedCapabilityError。"""

    def test_tools_none_capability_rejected(self):
        from finance_agent.llm.adapters.litellm_adapter import guard_params_supported
        from finance_agent.llm.errors import UnsupportedCapabilityError

        with pytest.raises(UnsupportedCapabilityError):
            guard_params_supported(_no_tools_cap(), tools=[{"type": "function"}])

    def test_no_tools_request_ok_even_if_cap_none(self):
        from finance_agent.llm.adapters.litellm_adapter import guard_params_supported

        guard_params_supported(_no_tools_cap(), tools=None)

    def test_tool_choice_required_unsupported(self):
        from finance_agent.llm.adapters.litellm_adapter import guard_params_supported
        from finance_agent.llm.errors import UnsupportedCapabilityError
        from finance_agent.llm.registry import get_profile_preset

        # deepseek-official 现声明 tool_choice_required=True（终审 I2），
        # required 拒绝用例改用未声明该能力的 openai-compatible preset。
        cap = get_profile_preset("openai-compatible").capability
        guard_params_supported(cap, tools=None, tool_choice="auto")
        with pytest.raises(UnsupportedCapabilityError):
            guard_params_supported(cap, tools=None, tool_choice="required")

    def test_ark_glm_tool_choice_required_supported(self):
        """终审 I2：ark-glm 声明 tool_choice_required → force_tool 轮不硬失败。
        (loop.py 对 force_tool 首轮发 tool_choice='required'，guard 必须放行。)"""
        from finance_agent.llm.adapters.litellm_adapter import guard_params_supported
        from finance_agent.llm.registry import get_profile_preset

        cap = get_profile_preset("ark-glm").capability
        assert cap.tool_choice_required is True
        guard_params_supported(
            cap, tools=[{"type": "function", "function": {"name": "f"}}], tool_choice="required"
        )

    def test_deepseek_official_tool_choice_required_supported(self):
        """终审 I2：deepseek-official 声明 tool_choice_required → required 放行。"""
        from finance_agent.llm.adapters.litellm_adapter import guard_params_supported
        from finance_agent.llm.registry import get_profile_preset

        cap = get_profile_preset("deepseek-official").capability
        assert cap.tool_choice_required is True
        guard_params_supported(
            cap, tools=[{"type": "function", "function": {"name": "f"}}], tool_choice="required"
        )

    def test_strict_schema_unsupported(self):
        from finance_agent.llm.adapters.litellm_adapter import guard_params_supported
        from finance_agent.llm.errors import UnsupportedCapabilityError
        from finance_agent.llm.types import Capability

        cap = Capability(
            tools="none",
            tool_choice_required=False,
            streaming=True,
            streaming_tool_calls=False,
            json_schema="json_mode",
            supports_system_role=True,
            reasoning_field=None,
            reasoning_must_echo_on_tool=False,
            reasoning_forced=False,
            max_context=8000,
            max_output=4096,
            extra_body_allowed=False,
        )
        with pytest.raises(UnsupportedCapabilityError):
            guard_params_supported(cap, tools=None, response_format="json_schema")
        guard_params_supported(cap, tools=None, response_format="json_object")
