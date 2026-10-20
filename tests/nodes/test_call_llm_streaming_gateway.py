# tests/nodes/test_call_llm_streaming_gateway.py
"""migrate-off-legacy-llm-shim Task 2：call_llm_streaming 直连 gateway.complete_stream。

mock 目标改为 ``finance_agent.llm.gateway.complete_stream``（原 mock
``finance_agent.llm.call_llm_stream`` 的用例迁移至此），断言 CanonicalEvent
迭代协议逐字节复刻：
- reasoning → thinking（经 stream writer）、text → answer 拼接、finished 忽略
- error 事件按 finish_reason（typed 类名字符串）还原为对应错误；查不到用 UnknownLLMError
- error(OutputTruncatedError) → 131072 预算复核翻倍重试（截断续写 fallback 保留）
- retryable 非截断错误重试一次，预算不翻倍（escalate 仅截断分支触发）
- 不再经 legacy call_llm_stream（TDD 红锚点）
"""

from unittest.mock import MagicMock, patch

import pytest

_STREAM = "finance_agent.llm.gateway.complete_stream"


class TestLegacyNotUsed:
    def test_call_llm_streaming_uses_gateway_directly(self):
        """迁移锚点：call_llm_streaming 直连 gateway.complete_stream。

        migrate-off-legacy-llm-shim Task 3 收尾：legacy 薄壳已删除，不再有
        包级 call_llm_stream 可被调用；断言直连 gateway 路径不变。
        """
        from finance_agent.llm.types import CanonicalEvent

        with patch(_STREAM) as mock_stream:
            mock_stream.return_value = iter([CanonicalEvent(kind="text", text="ok")])
            from finance_agent.nodes._llm_utils import call_llm_streaming

            assert call_llm_streaming("p", node_name="trader") == "ok"
            mock_stream.assert_called_once()

    def test_legacy_call_llm_stream_symbol_gone(self):
        """包级 call_llm_stream 已移除（Task 3 删除 legacy re-export）。"""
        import finance_agent.llm as llm_pkg

        assert not hasattr(llm_pkg, "call_llm_stream")


class TestCanonicalEventMapping:
    def test_concatenates_answer_and_skips_finished(self):
        from finance_agent.llm.types import CanonicalEvent

        with patch(_STREAM) as mock_stream:
            mock_stream.return_value = iter(
                [
                    CanonicalEvent(kind="reasoning", reasoning="思考A"),
                    CanonicalEvent(kind="text", text="答一"),
                    CanonicalEvent(kind="finished", finish_reason="stop"),
                    CanonicalEvent(kind="text", text="答二"),
                ]
            )
            from finance_agent.nodes._llm_utils import call_llm_streaming

            assert call_llm_streaming("p", node_name="trader") == "答一答二"

    def test_thinking_goes_to_stream_writer(self):
        from finance_agent.llm.types import CanonicalEvent

        writer = MagicMock()
        with (
            patch(_STREAM) as mock_stream,
            patch("langgraph.config.get_stream_writer", return_value=writer),
        ):
            mock_stream.return_value = iter(
                [
                    CanonicalEvent(kind="reasoning", reasoning="思考"),
                    CanonicalEvent(kind="text", text="答"),
                ]
            )
            from finance_agent.nodes._llm_utils import call_llm_streaming

            call_llm_streaming("p", node_name="trader")

        writer.assert_called_once_with({"type": "thinking", "node": "trader", "token": "思考"})

    def test_thinking_without_writer_is_dropped(self):
        from finance_agent.llm.types import CanonicalEvent

        with patch(_STREAM) as mock_stream:
            mock_stream.return_value = iter(
                [
                    CanonicalEvent(kind="reasoning", reasoning="思考"),
                    CanonicalEvent(kind="text", text="答"),
                ]
            )
            from finance_agent.nodes._llm_utils import call_llm_streaming

            assert call_llm_streaming("p", node_name="trader") == "答"


class TestErrorEventRestore:
    def test_error_event_restores_typed_error_and_raises(self):
        """error 事件按 finish_reason（类名）还原 typed error 并上抛（重试耗尽后）。"""
        from finance_agent.llm.errors import OutputTruncatedError
        from finance_agent.llm.types import CanonicalEvent

        def fake(*a, **kw):  # noqa: ARG001
            return iter(
                [
                    CanonicalEvent(
                        kind="error",
                        finish_reason="OutputTruncatedError",
                        raw={"error": "finish_reason=length"},
                    )
                ]
            )

        with (
            patch(_STREAM, side_effect=fake),
            pytest.raises(OutputTruncatedError, match="finish_reason=length"),
        ):
            from finance_agent.nodes._llm_utils import call_llm_streaming

            call_llm_streaming("p", node_name="trader")

    def test_error_event_triggers_budget_escalation_retry(self):
        """截断续写升级（truncation-escalate-resume delta）：重试带续写上下文、预算取翻倍剩余。

        首轮零正文时剩余预算 = 131072 - 估算余量(1) = 131071；请求追加续写指令消息。
        """
        from finance_agent.llm.types import CanonicalEvent

        captured = []
        messages_len = []

        def fake(*a, **kw):
            captured.append(kw.get("max_tokens"))
            messages_len.append(len(a[0]))
            if len(captured) == 1:
                return iter(
                    [
                        CanonicalEvent(
                            kind="error",
                            finish_reason="OutputTruncatedError",
                            raw={"error": "finish_reason=length"},
                        )
                    ]
                )
            return iter([CanonicalEvent(kind="text", text="ok")])

        with patch(_STREAM, side_effect=fake):
            from finance_agent.nodes._llm_utils import call_llm_streaming

            assert call_llm_streaming("p", node_name="trader") == "ok"
        assert captured == [65536, 131071]
        assert messages_len == [1, 2], "升级重试须追加续写指令消息（不从头重跑）"

    def test_retryable_empty_output_retries_with_instruction_without_escalation(self):
        """空输出 repair：最多 3 次、每次追加强化指令；预算不翻倍（escalate 仅截断分支触发）。"""
        from finance_agent.llm.types import CanonicalEvent

        calls = {"n": 0, "max_tokens": [], "messages": []}

        def fake(*a, **kw):
            calls["n"] += 1
            calls["max_tokens"].append(kw.get("max_tokens"))
            calls["messages"].append(a[0] if a else kw.get("messages"))
            if calls["n"] <= 2:
                return iter(
                    [
                        CanonicalEvent(
                            kind="error",
                            finish_reason="EmptyLLMOutputError",
                            raw={"error": "thinking 后即止"},
                        )
                    ]
                )
            return iter([CanonicalEvent(kind="text", text="res")])

        with patch(_STREAM, side_effect=fake):
            from finance_agent.nodes._llm_utils import call_llm_streaming

            assert call_llm_streaming("p") == "res"
        assert calls["n"] == 3
        assert calls["max_tokens"] == [65536, 65536, 65536]
        assert calls["messages"][0][-1]["content"] == "p"
        assert "不要只输出思考" in calls["messages"][1][-1]["content"]
        assert "不要只输出思考" in calls["messages"][2][-1]["content"]
        assert len(calls["messages"][2]) == 2, "强化指令不得重复堆叠"

    def test_unknown_error_event_falls_back_to_unknown_llm_error(self):
        """finish_reason 查不到 typed 类 → UnknownLLMError 还原（对齐 legacy _ERROR_CLASS_BY_NAME 缺省）。"""
        from finance_agent.llm.errors import UnknownLLMError
        from finance_agent.llm.types import CanonicalEvent

        def fake(*a, **kw):  # noqa: ARG001
            return iter(
                [CanonicalEvent(kind="error", finish_reason="BogusClass", raw={"error": "bogus"})]
            )

        with (
            patch(_STREAM, side_effect=fake),
            pytest.raises(UnknownLLMError, match="bogus"),
        ):
            from finance_agent.nodes._llm_utils import call_llm_streaming

            call_llm_streaming("p")


class TestRequestConstruction:
    def test_builds_messages_and_request_dict(self):
        from finance_agent.llm.types import CanonicalEvent

        with patch(_STREAM) as mock_stream:
            mock_stream.return_value = iter([CanonicalEvent(kind="text", text="ok")])
            from finance_agent.nodes._llm_utils import call_llm_streaming

            call_llm_streaming("hi", system="你是助手", node_name="technical_analyst")

        args, kwargs = mock_stream.call_args
        assert args[0] == [
            {"role": "system", "content": "你是助手"},
            {"role": "user", "content": "hi"},
        ]
        assert kwargs["purpose"] == "deep"
        assert kwargs["max_tokens"] == 65536
        assert kwargs["temperature"] == 0.3
        assert kwargs["llm_config"] is None
        assert kwargs["trace"] == {
            "name": "technical_analyst",
            "metadata": {"agent": "technical_analyst"},
        }


class TestTruncationEscalateResumes:
    """truncation-escalate-resume delta：截断升级重试以续写而非从头重跑。

    汉森制药 002412 复盘：首跑 17 分钟部分正文被丢弃、从头重跑 34 分钟
    又失败。升级层应复用 gateway 续写机制（正文尾部注入 + 翻倍剩余配额）。
    """

    def _trunc_error(self):
        from finance_agent.llm.types import CanonicalEvent

        return CanonicalEvent(
            kind="error", finish_reason="OutputTruncatedError", raw={"error": "截断"}
        )

    def test_escalate_continues_from_partial_text(self):
        from finance_agent.llm.types import CanonicalEvent

        calls: list[dict] = []

        def fake(*args, **kw):
            calls.append({"args": args, "kw": kw})
            if len(calls) == 1:
                return iter(
                    [
                        CanonicalEvent(kind="text", text="前半正文"),
                        self._trunc_error(),
                    ]
                )
            return iter([CanonicalEvent(kind="text", text="后半"), CanonicalEvent(kind="finished")])

        with patch(_STREAM, side_effect=fake):
            from finance_agent.nodes._llm_utils import call_llm_streaming

            result = call_llm_streaming("p", node_name="trader")

        assert result == "前半正文后半", "返回值须为两轮正文拼接"
        # 续写请求形态：原始 messages + 1 条续写指令，携带尾部正文
        second_msgs = calls[1]["args"][0]
        assert len(second_msgs) == 2, f"应追加 1 条续写消息: {len(second_msgs)}"
        assert second_msgs[0]["role"] == "user" and second_msgs[0]["content"] == "p"
        resume_msg = second_msgs[1]
        assert resume_msg["role"] == "user"
        assert "前半正文" in resume_msg["content"], "续写指令须携带首轮正文尾部"
        assert "续写" in resume_msg["content"]
        # 配额：翻倍预算扣除已生成（估算），小于全额且为正
        mt = calls[1]["kw"]["max_tokens"]
        assert 0 < mt < 131072, f"剩余配额应在 (0, 131072): {mt}"
        assert calls[0]["kw"]["max_tokens"] == 65536

    def test_escalate_still_truncated_raises(self):
        from finance_agent.llm.errors import OutputTruncatedError
        from finance_agent.llm.types import CanonicalEvent

        def fake(*args, **kw):
            yield CanonicalEvent(kind="text", text="x")
            yield self._trunc_error()

        with patch(_STREAM, side_effect=fake):
            from finance_agent.nodes._llm_utils import call_llm_streaming

            with pytest.raises(OutputTruncatedError):
                call_llm_streaming("p", node_name="trader")

    def test_empty_prior_gets_full_escalated_budget(self):
        from finance_agent.llm.types import CanonicalEvent

        calls: list[dict] = []

        def fake(*args, **kw):
            calls.append(kw)
            if len(calls) == 1:
                return iter([self._trunc_error()])
            return iter([CanonicalEvent(kind="text", text="续")])

        with patch(_STREAM, side_effect=fake):
            from finance_agent.nodes._llm_utils import call_llm_streaming

            assert call_llm_streaming("p", node_name="trader") == "续"
        # 首轮零正文：剩余配额 = 131072 - 估算余量(1)（续写预算契约）
        assert [c["max_tokens"] for c in calls] == [65536, 131071]
