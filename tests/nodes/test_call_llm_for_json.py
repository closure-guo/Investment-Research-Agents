# tests/nodes/test_call_llm_for_json.py
"""call_llm_for_json 重试收口测试（incident 017 延伸：空 content 非 配额问题）。

实测方舟 GLM-5.2 在风控辩论等 prompt 下稳定触发「thinking 后即止」：
reasoning 正常（994-3485 字符）而 content 为空，与 max_tokens 配额无关
（16384 未吃满、无截断）。下游节点 parse 裸奔会炸整行 → 收口
「LLM 调用 + parse」，失败带强化指令重试一次；仍失败向上抛
（保留 fund_manager「不静默降级」设计：重试 ≠ 降级）。
"""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from finance_agent.nodes._llm_utils import call_llm_for_json


def _mock_call(responses: list[str]):
    """按序返回 responses 的 call_llm_streaming mock，记录调用参数。"""
    calls: list[dict] = []
    it = iter(responses)

    def fake(prompt: str, **kwargs):
        calls.append({"prompt": prompt, **kwargs})
        try:
            return next(it)
        except StopIteration:
            raise AssertionError("unexpected extra LLM call") from None

    return fake, calls


def _pin_single_profile():
    """固定 primary 为无 fallback 配置的 profile（隔离 env / 链长对断言的干扰）。

    本文件钉的是「同 profile 带强化指令重试一次」语义；fallback 链切换单独在
    tests/nodes/test_llm_utils_fallback.py 覆盖。
    """
    import dataclasses

    from finance_agent.llm.registry import get_profile_preset

    return patch(
        "finance_agent.llm.gateway.resolve_profile",
        return_value=dataclasses.replace(get_profile_preset("deepseek-official"), fallback=()),
    )


class TestRetryOnBadOutput:
    def test_empty_then_valid_retries_once(self):
        fake, calls = _mock_call(["", '{"decision": "hold"}'])
        with patch("finance_agent.nodes._llm_utils.call_llm_streaming", side_effect=fake):
            result = call_llm_for_json("分析风险", system="你是风控")
        assert result == {"decision": "hold"}
        assert len(calls) == 2
        # 重试时 prompt 必须追加强化指令（否则同样的空输出概率复现）
        assert "JSON" in calls[1]["prompt"]
        assert "JSON" not in calls[0]["prompt"]

    def test_unparseable_then_valid_retries(self):
        """容错层救不了的输出（纯文本无 JSON）触发重试。"""
        fake, calls = _mock_call(["我认为风险可控，无需 JSON。", '{"a": 1}'])
        with patch("finance_agent.nodes._llm_utils.call_llm_streaming", side_effect=fake):
            result = call_llm_for_json("x")
        assert result == {"a": 1}
        assert len(calls) == 2

    def test_trailing_comma_saved_by_tolerance_no_retry(self):
        """尾逗号由 parse 容错层直接救回，不应浪费重试调用。"""
        fake, calls = _mock_call(['{"a": 1,}'])
        with patch("finance_agent.nodes._llm_utils.call_llm_streaming", side_effect=fake):
            result = call_llm_for_json("x")
        assert result == {"a": 1}
        assert len(calls) == 1

    def test_valid_first_try_no_retry(self):
        fake, calls = _mock_call(['{"ok": true}'])
        with patch("finance_agent.nodes._llm_utils.call_llm_streaming", side_effect=fake):
            result = call_llm_for_json("x")
        assert result == {"ok": True}
        assert len(calls) == 1

    def test_both_bad_raises(self):
        """单 profile 链内两次都失败必须抛（不静默降级），上游节点/管线按原语义中断。"""
        fake, _ = _mock_call(["", "   "])
        with (
            _pin_single_profile(),
            patch("finance_agent.nodes._llm_utils.call_llm_streaming", side_effect=fake),
            pytest.raises(json.JSONDecodeError),
        ):
            call_llm_for_json("x")

    def test_kwargs_passthrough(self):
        """node_name/prompt 元数据等透传给底层调用（Langfuse 命名依赖）。"""
        fake, calls = _mock_call(['{"a": 1}'])
        with patch("finance_agent.nodes._llm_utils.call_llm_streaming", side_effect=fake):
            call_llm_for_json(
                "x",
                system="s",
                node_name="aggressive_debater",
                prompt_name="risk_debater",
                prompt_version="1",
                stock_code="600519",
            )
        assert calls[0]["node_name"] == "aggressive_debater"
        assert calls[0]["prompt_name"] == "risk_debater"
        assert calls[0]["stock_code"] == "600519"


class TestRetryOnApiError:
    def test_api_error_then_valid_retries(self):
        """方舟偶发服务端 500（r4 实测 MidStreamFallbackError）重试一次即恢复。"""
        import litellm

        calls: list[dict] = []

        def fake(prompt: str, **kwargs):
            calls.append({"prompt": prompt})
            if len(calls) == 1:
                raise litellm.exceptions.APIConnectionError(
                    message="service internal error", llm_provider="openai"
                )
            return '{"ok": true}'

        with patch("finance_agent.nodes._llm_utils.call_llm_streaming", side_effect=fake):
            result = call_llm_for_json("x")
        assert result == {"ok": True}
        assert len(calls) == 2

    def test_api_error_twice_raises(self):
        """连续两次 API 错误仍向上抛（不静默吞错）。"""
        import litellm

        calls: list[dict] = []

        def fake(prompt: str, **kwargs):
            calls.append({"prompt": prompt})
            raise litellm.exceptions.APIConnectionError(
                message="still down", model="openai/glm-5.2", llm_provider="openai"
            )

        with (
            patch("finance_agent.nodes._llm_utils.call_llm_streaming", side_effect=fake),
            pytest.raises(litellm.exceptions.APIConnectionError),
        ):
            call_llm_for_json("x")
        assert len(calls) == 2


class TestStreamingRetryOnRetryableLLMError:
    """call_llm_streaming 对 retryable LLMError 重试一次（evals 跑批暴露回归：
    gateway 把 finish_reason=length 归一为 OutputTruncatedError，节点直调无重试
    直接炸管线；旧路径截断只表现为坏 JSON 走 call_llm_for_json 兜底）。

    migrate-off-legacy-llm-shim Task 2：mock 目标改 gateway.complete_stream，
    产出 CanonicalEvent（legacy call_llm_stream 的 (kind,text) 元组迭代被移除）。
    """

    def test_truncated_then_success_retries_once(self):
        from finance_agent.llm.errors import OutputTruncatedError
        from finance_agent.llm.types import CanonicalEvent

        calls = {"n": 0}

        def fake_stream(*a, **kw):  # noqa: ARG001
            calls["n"] += 1
            if calls["n"] == 1:
                raise OutputTruncatedError("finish_reason=length")
            return iter([CanonicalEvent(kind="text", text='{"ok": 1}')])

        with patch("finance_agent.llm.gateway.complete_stream", side_effect=fake_stream):
            from finance_agent.nodes._llm_utils import call_llm_streaming

            result = call_llm_streaming("p", node_name="trader")
        assert result == '{"ok": 1}'
        assert calls["n"] == 2

    def test_non_retryable_raises_immediately(self):
        from finance_agent.llm.errors import AuthError

        calls = {"n": 0}

        def fake_stream(*a, **kw):  # noqa: ARG001
            calls["n"] += 1
            raise AuthError("bad key")

        with (
            patch("finance_agent.llm.gateway.complete_stream", side_effect=fake_stream),
            pytest.raises(AuthError),
        ):
            from finance_agent.nodes._llm_utils import call_llm_streaming

            call_llm_streaming("p", node_name="trader")
        assert calls["n"] == 1

    def test_retryable_exhausted_raises(self):
        from finance_agent.llm.errors import EmptyLLMOutputError

        calls = {"n": 0}

        def fake_stream(*a, **kw):  # noqa: ARG001
            calls["n"] += 1
            raise EmptyLLMOutputError("thinking 后即止")

        with (
            patch("finance_agent.llm.gateway.complete_stream", side_effect=fake_stream),
            pytest.raises(EmptyLLMOutputError),
        ):
            from finance_agent.nodes._llm_utils import call_llm_streaming

            call_llm_streaming("p", node_name="trader")
        assert calls["n"] == 3

    def test_empty_output_not_double_retried_by_json_wrapper(self):
        """call_llm_streaming 内已做强化 repair；外层 JSON 包装不得再翻倍尝试。"""
        from unittest.mock import MagicMock

        from finance_agent.llm.errors import EmptyLLMOutputError

        fake = MagicMock(side_effect=EmptyLLMOutputError("thinking 后即止"))
        with (
            patch("finance_agent.nodes._llm_utils.call_llm_streaming", fake),
            pytest.raises(EmptyLLMOutputError),
        ):
            call_llm_for_json("p")
        assert fake.call_count == 1


def test_truncation_retry_escalates_budget():
    """截断重试必须预算复核（Task 2.2）：第二次调用 max_tokens=131072（官方上限）而非原预算复读。"""
    from finance_agent.llm.errors import OutputTruncatedError
    from finance_agent.llm.types import CanonicalEvent

    captured = []

    def fake_stream(*a, **kw):
        captured.append(kw.get("max_tokens"))
        if len(captured) == 1:
            raise OutputTruncatedError("finish_reason=length")
        return iter([CanonicalEvent(kind="text", text="ok")])

    with patch("finance_agent.llm.gateway.complete_stream", side_effect=fake_stream):
        from finance_agent.nodes._llm_utils import call_llm_streaming

        assert call_llm_streaming("p", node_name="trader") == "ok"
    assert captured == [65536, 131071]


class TestValidateHook:
    """validate 钩子：JSON 合法但字段校验不过 → 带错误摘要重试一次，仍不过向上抛。

    JSON 级重试解决「不是 JSON」，validate 解决「是 JSON 但缺必填/枚举非法」——
    两者同属「坏输出重试一次、重试 ≠ 降级」的收口设计。
    """

    def test_validation_failure_retries_with_error_hint(self):
        from pydantic import BaseModel

        class Out(BaseModel):
            decision: str
            action: str

        fake, calls = _mock_call(
            ['{"decision": "approve"}', '{"decision": "approve", "action": "watch"}']
        )
        with patch("finance_agent.nodes._llm_utils.call_llm_streaming", side_effect=fake):
            result = call_llm_for_json("审批", validate=Out.model_validate)
        assert result == {"decision": "approve", "action": "watch"}
        assert len(calls) == 2
        assert "action" in calls[1]["prompt"]  # 重试 prompt 携带校验错误摘要
        assert calls[1]["prompt"].startswith("审批")

    def test_validation_failure_twice_raises(self):
        from pydantic import BaseModel, ValidationError

        class Out(BaseModel):
            action: str

        fake, calls = _mock_call(['{"x": 1}', '{"y": 2}'])
        with (
            _pin_single_profile(),
            patch("finance_agent.nodes._llm_utils.call_llm_streaming", side_effect=fake),
            pytest.raises(ValidationError),
        ):
            call_llm_for_json("审批", validate=Out.model_validate)
        assert len(calls) == 2

    def test_no_validate_keeps_old_behavior(self):
        fake, calls = _mock_call(['{"x": 1}'])
        with patch("finance_agent.nodes._llm_utils.call_llm_streaming", side_effect=fake):
            assert call_llm_for_json("p") == {"x": 1}
        assert len(calls) == 1
