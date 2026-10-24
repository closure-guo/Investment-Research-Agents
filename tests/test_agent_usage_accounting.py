# tests/test_agent_usage_accounting.py
"""add-composer-stat-pills（quick 模式补充）：ReAct Agent 自身的 provider usage 累计。

quick 模式经 AG-UI 通道跑 harness Agent，LLM 调用不经过管线节点，因此
``usage_collector`` 收不到；用量必须在 Agent 层累计，供 AG-UI/SSE 收尾落库。
"""

from __future__ import annotations

import pytest

from finance_agent.harness import Agent, PermissionMode
from finance_agent.harness.llm_client import LLMResponse


class MockLLM:
    """每次 chat_stream 消费一组预设 chunk。"""

    def __init__(self, responses: list[list[LLMResponse]]):
        self._responses = responses
        self._index = 0

    async def chat_stream(self, messages=None, tools=None, temperature=0.7, tool_choice=None):
        chunks = (
            self._responses[self._index]
            if self._index < len(self._responses)
            else [LLMResponse(text_delta="", is_finished=True)]
        )
        self._index += 1
        for chunk in chunks:
            yield chunk


def _usage(prompt: int, completion: int) -> dict[str, int]:
    return {
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "total_tokens": prompt + completion,
    }


@pytest.mark.asyncio
async def test_agent_accumulates_usage_and_tracks_peak():
    llm = MockLLM(
        [
            [LLMResponse(text_delta="答一", usage=_usage(100, 10), is_finished=True)],
            [LLMResponse(text_delta="答二", usage=_usage(500, 50), is_finished=True)],
        ]
    )
    agent = Agent(model="mock", permission_mode=PermissionMode.YOLO, llm=llm)

    async for _ in agent.run("问题一"):
        pass
    async for _ in agent.run("问题二"):
        pass

    assert agent.usage_calls == 2
    assert agent.usage_prompt_tokens == 600
    assert agent.usage_completion_tokens == 60
    assert agent.usage_total_tokens == 660
    # 上下文峰值取单次最大值，不累加
    assert agent.usage_max_prompt_tokens == 500


@pytest.mark.asyncio
async def test_agent_usage_stays_zero_without_provider_usage():
    llm = MockLLM([[LLMResponse(text_delta="无用量", is_finished=True)]])
    agent = Agent(model="mock", permission_mode=PermissionMode.YOLO, llm=llm)

    async for _ in agent.run("问题"):
        pass

    assert agent.usage_calls == 0
    assert agent.usage_total_tokens == 0
    assert agent.usage_max_prompt_tokens == 0


class _UsageAgent:
    usage_calls = 2
    usage_prompt_tokens = 600
    usage_completion_tokens = 60
    usage_total_tokens = 660
    usage_max_prompt_tokens = 500


def test_persist_agent_usage_maps_fields(monkeypatch):
    """收尾落库：字段逐个映射，峰值按 max 写入。"""
    from finance_agent import agent_factory, session_store

    captured: dict[str, object] = {}

    def fake_update(session_id: str, **kwargs: object) -> bool:
        captured["session_id"] = session_id
        captured.update(kwargs)
        return True

    monkeypatch.setattr(session_store, "update_session_usage", fake_update)
    agent_factory._persist_agent_usage("s1", _UsageAgent())

    assert captured == {
        "session_id": "s1",
        "prompt": 600,
        "completion": 60,
        "total": 660,
        "context_peak": 500,
    }


def test_persist_agent_usage_skips_without_calls(monkeypatch):
    """无 usage 的 Agent（如 stub）不写库，避免制造「0 已记账」假象。"""
    from finance_agent import agent_factory, session_store

    called = False

    def fake_update(*args: object, **kwargs: object) -> bool:
        nonlocal called
        called = True
        return True

    monkeypatch.setattr(session_store, "update_session_usage", fake_update)

    class _NoUsageAgent:
        usage_calls = 0

    agent_factory._persist_agent_usage("s1", _NoUsageAgent())
    agent_factory._persist_agent_usage(None, _UsageAgent())
    assert called is False
