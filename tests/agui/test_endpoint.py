"""endpoint TDD：POST /api/agui/quick（AG-UI SSE + 三路落库 + 会话状态流转）。

契约：openspec/changes/add-assistant-ui-thread/specs/chat-stream/spec.md。
对接点以调研文档 §3.1 为准（build_agent 同一注入路径 / 落库等价 _ChatCollector /
update_session_status / 不 publish 进 registry）。
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from typing import Any

import pytest
from ag_ui.encoder import EventEncoder
from fastapi.testclient import TestClient

from finance_agent import agent_factory, session_store
from finance_agent.agui.endpoint import _agui_run_stream
from finance_agent.api import app
from finance_agent.harness.types import StreamEvent, ToolCallRequest, ToolResult


class _StubAgent:
    """脚本化 stub agent：按给定 StreamEvent 序列回放 agent.run()。"""

    def __init__(self, events: list[StreamEvent]) -> None:
        self._events = events
        self.calls: list[str] = []

    async def run(self, user_input: str, force_tool: bool = False):
        self.calls.append(user_input)
        for ev in self._events:
            yield ev


@pytest.fixture
def isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(session_store, "_DB_PATH", tmp_path / "t.db")
    session_store.init_db()
    return tmp_path / "t.db"


def _post_agui(client: TestClient, payload: dict[str, Any]) -> list[dict[str, Any]]:
    """POST /api/agui/quick 并解析 SSE data 行为事件 dict 列表。"""
    resp = client.post("/api/agui/quick", json=payload)
    assert resp.status_code == 200, resp.text
    events = []
    for chunk in resp.text.split("\n\n"):
        for line in chunk.splitlines():
            if line.startswith("data: "):
                events.append(json.loads(line[len("data: ") :]))
    return events


def _types(events: list[dict[str, Any]]) -> list[str]:
    return [e["type"] for e in events]


def test_normal_sequence_and_persistence(isolated_db, monkeypatch):
    """场景 1：RUN_STARTED → TEXT_MESSAGE_* → RUN_FINISHED；拼接 == 落库全文；user 落库。"""
    sid = session_store.create_chat_session("测试会话")
    stub = _StubAgent(
        [StreamEvent.answer("你好"), StreamEvent.answer("，"), StreamEvent.answer("世界")]
    )
    monkeypatch.setattr(agent_factory, "build_agent", lambda **kwargs: stub)

    with TestClient(app) as client:
        events = _post_agui(
            client,
            {
                "threadId": sid,
                "runId": "run-1",
                "messages": [{"id": "m1", "role": "user", "content": "贵州茅台怎么样？"}],
            },
        )

    assert _types(events) == [
        "RUN_STARTED",
        "TEXT_MESSAGE_START",
        "TEXT_MESSAGE_CONTENT",
        "TEXT_MESSAGE_CONTENT",
        "TEXT_MESSAGE_CONTENT",
        "TEXT_MESSAGE_END",
        "RUN_FINISHED",
    ]
    assert events[0]["threadId"] == sid
    joined = "".join(e["delta"] for e in events if e["type"] == "TEXT_MESSAGE_CONTENT")
    assert joined == "你好，世界"
    # agent 收到 user 消息（build_agent 注入路径被调用）
    assert stub.calls == ["贵州茅台怎么样？"]

    session = session_store.get_session(sid)
    assert session is not None
    assert session["status"] == "completed"
    history = session["chat_history"]
    assert [m["role"] for m in history] == ["user", "assistant"]
    assert history[0]["content"] == "贵州茅台怎么样？"
    # 分块拼接 == 落库全文
    assert history[1]["content"] == "你好，世界"


def test_error_yields_run_error_and_no_success_persist(isolated_db, monkeypatch):
    """场景 2：LLM 异常 → RUN_ERROR 终止（无半截 RUN_FINISHED）+ 不落库成功回复。"""
    sid = session_store.create_chat_session("测试会话")
    stub = _StubAgent([StreamEvent.error("API key 无效")])
    monkeypatch.setattr(agent_factory, "build_agent", lambda **kwargs: stub)

    with TestClient(app) as client:
        events = _post_agui(
            client,
            {
                "threadId": sid,
                "runId": "run-2",
                "messages": [{"id": "m1", "role": "user", "content": "问题"}],
            },
        )

    assert _types(events)[-1] == "RUN_ERROR"
    assert "RUN_FINISHED" not in _types(events)
    assert events[-1]["message"] == "API key 无效"

    session = session_store.get_session(sid)
    assert session is not None
    assert session["status"] == "failed"
    history = session["chat_history"]
    # user 消息落库，但不落库成功回复
    assert [m["role"] for m in history] == ["user"]


def test_unknown_thread_returns_404(isolated_db, monkeypatch):
    monkeypatch.setattr(agent_factory, "build_agent", lambda **kwargs: _StubAgent([]))

    with TestClient(app) as client:
        resp = client.post(
            "/api/agui/quick",
            json={
                "threadId": "nonexistent-session",
                "runId": "run-3",
                "messages": [{"id": "m1", "role": "user", "content": "问题"}],
            },
        )
    assert resp.status_code == 404


def test_empty_thread_creates_session(isolated_db, monkeypatch):
    """thread_id 为空 → create_chat_session 新建会话，RUN_STARTED 回传新 thread_id。"""
    stub = _StubAgent([StreamEvent.answer("答")])
    monkeypatch.setattr(agent_factory, "build_agent", lambda **kwargs: stub)

    with TestClient(app) as client:
        events = _post_agui(
            client,
            {
                "threadId": "",
                "runId": "run-4",
                "messages": [{"id": "m1", "role": "user", "content": "新对话问题"}],
            },
        )

    new_sid = events[0]["threadId"]
    assert new_sid  # 非空
    session = session_store.get_session(new_sid)
    assert session is not None
    assert session["session_type"] == "chat"
    assert session["status"] == "completed"
    history = session["chat_history"]
    assert [m["role"] for m in history] == ["user", "assistant"]
    assert history[0]["content"] == "新对话问题"
    assert history[1]["content"] == "答"


def test_tool_call_channel_persists_structured_agent_timeline(isolated_db, monkeypatch):
    """工具调用 run 落库结构化 agentTimeline（按事件顺序，含搜索类工具）。

    回归（E2E agui-toolcall 刷新恢复场景发现的保真度缺口）：此前 _AguiCollector
    只落 response/thinking/tool_calls 平铺字段，恢复走 buildTimelineFromHistory
    fallback，其按 design 决策 7 跳过搜索类工具 → 刷新后 web_search 步骤整个
    不可见。修复后按事件顺序构建 agentTimeline（thinking/tool_call 交错）落库，
    恢复端 deserializeTimeline 直接消费，时序与实时渲染一致。
    """
    sid = session_store.create_chat_session("测试会话")
    stub = _StubAgent(
        [
            StreamEvent.think("第一段思考"),
            StreamEvent.for_tool_call(
                ToolCallRequest(id="tc-1", name="web_search", arguments={"query": "茅台"})
            ),
            StreamEvent.for_tool_result(
                ToolResult(tool_call_id="tc-1", name="web_search", output="搜索结果")
            ),
            StreamEvent.think("第二段思考"),
            StreamEvent.answer("最终回答"),
        ]
    )
    monkeypatch.setattr(agent_factory, "build_agent", lambda **kwargs: stub)

    with TestClient(app) as client:
        _post_agui(
            client,
            {
                "threadId": sid,
                "runId": "run-timeline",
                "messages": [{"id": "m1", "role": "user", "content": "查一下茅台"}],
            },
        )

    session = session_store.get_session(sid)
    assert session is not None
    entry = session["chat_history"][-1]
    assert entry["role"] == "assistant"

    timeline = entry.get("agentTimeline")
    assert timeline is not None, "assistant 条目应携带结构化 agentTimeline"
    # 按事件顺序：思考1 → 工具调用（web_search 不再被跳过）→ 思考2
    assert [item["type"] for item in timeline] == ["thinking", "tool_call", "thinking"]
    assert timeline[0]["content"] == "第一段思考"
    assert timeline[0]["done"] is True, "思考段结束后应置完成态"
    assert timeline[1]["name"] == "web_search"
    assert timeline[1]["args"] == "茅台"
    assert "搜索结果" in timeline[1]["result"]
    assert timeline[1]["done"] is True
    assert timeline[2]["content"] == "第二段思考"
    assert timeline[2]["done"] is True


def test_tool_call_channel_persists_tool_records(isolated_db, monkeypatch):
    """补充：TOOL_CALL/RESULT 映射 + 落库 tool_calls（等价 _ChatCollector 行为）。"""
    sid = session_store.create_chat_session("测试会话")
    stub = _StubAgent(
        [
            StreamEvent.think("搜索前思考"),
            StreamEvent.for_tool_call(
                ToolCallRequest(id="tc-1", name="web_search", arguments={"query": "茅台"})
            ),
            StreamEvent.for_tool_result(
                ToolResult(tool_call_id="tc-1", name="web_search", output="搜索结果")
            ),
            StreamEvent.answer("最终回答"),
        ]
    )
    monkeypatch.setattr(agent_factory, "build_agent", lambda **kwargs: stub)

    with TestClient(app) as client:
        events = _post_agui(
            client,
            {
                "threadId": sid,
                "runId": "run-5",
                "messages": [{"id": "m1", "role": "user", "content": "查一下茅台"}],
            },
        )

    types = _types(events)
    assert types[0] == "RUN_STARTED"
    assert types[-1] == "RUN_FINISHED"
    start_idx = types.index("TOOL_CALL_START")
    args_idx = types.index("TOOL_CALL_ARGS")
    result_idx = types.index("TOOL_CALL_RESULT")
    assert events[start_idx]["toolCallName"] == "web_search"
    # START/ARGS/RESULT id 一致
    assert (
        events[start_idx]["toolCallId"]
        == events[args_idx]["toolCallId"]
        == events[result_idx]["toolCallId"]
    )

    session = session_store.get_session(sid)
    assert session is not None
    history = session["chat_history"]
    assert history[-1]["role"] == "assistant"
    assert history[-1]["content"] == "最终回答"
    assert history[-1]["thinking"] == "搜索前思考"
    assert history[-1]["tool_calls"][0]["name"] == "web_search"
    assert history[-1]["tool_calls"][0]["done"] is True


# ---------------------------------------------------------------------------
# 审查修复：终态先落库再下发 / 断开中断落库 / 增量 upsert / api_key 透传
# ---------------------------------------------------------------------------


class _SlowAgent:
    """产出部分回复后长时间阻塞（模拟 LLM 长连接，供取消/中断测试）。"""

    def __init__(self, partial: str) -> None:
        self.partial = partial

    async def run(self, user_input: str, force_tool: bool = False):
        yield StreamEvent.answer(self.partial)
        await asyncio.Event().wait()


class _TwoPartAgent:
    """两段回复，中间 sleep 拉开间隔（供增量 upsert 节奏测试）。"""

    async def run(self, user_input: str, force_tool: bool = False):
        yield StreamEvent.answer("第一段")
        await asyncio.sleep(0.2)
        yield StreamEvent.answer("第二段")


async def test_run_finished_persisted_before_yield(isolated_db, monkeypatch):
    """终态先落库再下发：消费端收到 RUN_FINISHED 的瞬间，assistant 全文已在 session_store。"""
    sid = session_store.create_chat_session("测试会话")
    stub = _StubAgent([StreamEvent.answer("你好"), StreamEvent.answer("，世界")])
    monkeypatch.setattr(agent_factory, "build_agent", lambda **kwargs: stub)

    encoder = EventEncoder()
    agen = _agui_run_stream(sid, "run-order", "贵州茅台怎么样？", None, encoder)
    snapshot: dict[str, Any] | None = None
    async for chunk in agen:
        if "RUN_FINISHED" in chunk:
            # 收到终态事件的瞬间读 session_store（不等待生成器收尾）
            session = session_store.get_session(sid)
            assert session is not None
            snapshot = session
            break
    with contextlib.suppress(Exception):
        await agen.aclose()

    assert snapshot is not None
    assert snapshot["status"] == "completed"
    history = snapshot["chat_history"]
    assert [m["role"] for m in history] == ["user", "assistant"]
    assert history[1]["content"] == "你好，世界"


async def test_quick_run_persists_provider_usage(isolated_db, monkeypatch):
    """add-composer-stat-pills：quick run 收尾把 Agent 累计 usage 写入会话。"""
    sid = session_store.create_chat_session("计量会话")
    stub = _StubAgent([StreamEvent.answer("你好")])
    stub.usage_calls = 2
    stub.usage_prompt_tokens = 1500
    stub.usage_completion_tokens = 100
    stub.usage_total_tokens = 1600
    stub.usage_max_prompt_tokens = 900
    monkeypatch.setattr(agent_factory, "build_agent", lambda **kwargs: stub)

    encoder = EventEncoder()
    agen = _agui_run_stream(sid, "run-usage", "问题", None, encoder)
    async for _chunk in agen:
        pass
    with contextlib.suppress(Exception):
        await agen.aclose()

    row = session_store.get_session(sid)
    assert row is not None
    assert row["tokens_prompt"] == 1500
    assert row["tokens_completion"] == 100
    assert row["tokens_total"] == 1600
    assert row["context_peak_tokens"] == 900


async def test_client_disconnect_persists_interrupted(isolated_db, monkeypatch):
    """客户端断开（消费任务被 cancel）→ 中断占位落库 + status=interrupted。"""
    sid = session_store.create_chat_session("测试会话")
    stub = _SlowAgent("部分回复")
    monkeypatch.setattr(agent_factory, "build_agent", lambda **kwargs: stub)

    encoder = EventEncoder()
    agen = _agui_run_stream(sid, "run-cancel", "问题", None, encoder)
    chunks: list[str] = []

    async def _consume() -> None:
        async for chunk in agen:
            chunks.append(chunk)

    task = asyncio.create_task(_consume())
    await asyncio.sleep(0.2)  # 让生成器推进到阻塞点（agent 挂起、外层 wait 等待中）
    assert chunks  # RUN_STARTED 等事件已下发
    task.cancel()  # 模拟客户端断开 → 服务端取消
    with contextlib.suppress(asyncio.CancelledError):
        await task

    # 中断落库在独立任务中异步完成（已取消任务里 await 不安全，detach 执行），
    # 轮询收敛
    session: dict[str, Any] | None = None
    for _ in range(100):
        session = session_store.get_session(sid)
        if session is not None and session["status"] == "interrupted":
            break
        await asyncio.sleep(0.02)

    assert session is not None
    assert session["status"] == "interrupted"
    history = session["chat_history"]
    # 中断占位：部分回复 + [输出中断]，无悬空 user 消息
    assert [m["role"] for m in history] == ["user", "assistant"]
    assert history[1]["content"] == "部分回复\n\n[输出中断]"


async def test_client_disconnect_via_aclose_persists_interrupted(isolated_db, monkeypatch):
    """客户端断开的另一条路径：响应生成器被 aclose（GeneratorExit 注入）而非任务 cancel。

    生产实证（E2E debug-cursor，2026-09-01）：quick 会话切换触发 abortRun，经
    vite 代理 + uvicorn 的断连传播表现为 StreamingResponse 清理（aclose），
    GeneratorExit 是 BaseException 不经 except CancelledError/except Exception，
    终态落库被整体跳过 → 会话永久泄漏 running（该会话之后被「生成中」守卫锁死）。
    本用例钉死该路径：aclose 后同样必须 中断落库 + status=interrupted。
    """
    sid = session_store.create_chat_session("测试会话")
    stub = _SlowAgent("部分回复")
    monkeypatch.setattr(agent_factory, "build_agent", lambda **kwargs: stub)

    encoder = EventEncoder()
    agen = _agui_run_stream(sid, "run-aclose", "问题", None, encoder)
    chunks: list[str] = []
    async for chunk in agen:
        chunks.append(chunk)
        if "TEXT_MESSAGE_CONTENT" in chunk:
            break  # 拿到部分内容后模拟客户端断开
    assert chunks

    await agen.aclose()  # GeneratorExit 注入（StreamingResponse 清理路径）

    # 中断落库允许在独立任务中异步完成（aclose 上下文里 await 不安全），轮询收敛
    session: dict[str, Any] | None = None
    for _ in range(100):
        session = session_store.get_session(sid)
        if session is not None and session["status"] == "interrupted":
            break
        await asyncio.sleep(0.02)

    assert session is not None
    assert session["status"] == "interrupted"
    history = session["chat_history"]
    assert [m["role"] for m in history] == ["user", "assistant"]
    assert history[1]["content"] == "部分回复\n\n[输出中断]"


async def test_incremental_upsert_mid_run(isolated_db, monkeypatch):
    """增量 upsert：终态未到时按间隔 upsert 进行中回复（进程崩溃可兜底）。"""
    monkeypatch.setattr("finance_agent.agui.endpoint._PERSIST_INTERVAL", 0.05)
    sid = session_store.create_chat_session("测试会话")
    stub = _TwoPartAgent()
    monkeypatch.setattr(agent_factory, "build_agent", lambda **kwargs: stub)

    encoder = EventEncoder()
    agen = _agui_run_stream(sid, "run-incr", "问题", None, encoder)
    mid_run_snapshot: str | None = None
    async for chunk in agen:
        if "TEXT_MESSAGE_CONTENT" in chunk and "第二段" in chunk:
            # 收到第二段内容块的瞬间：RUN_FINISHED 尚未下发，
            # 落库内容只能来自间隔触发的增量 upsert
            session = session_store.get_session(sid)
            assert session is not None
            history = session["chat_history"]
            if history and history[-1]["role"] == "assistant":
                mid_run_snapshot = history[-1]["content"]
    with contextlib.suppress(Exception):
        await agen.aclose()

    assert mid_run_snapshot == "第一段第二段"
    session = session_store.get_session(sid)
    assert session is not None
    assert session["status"] == "completed"
    assert session["chat_history"][-1]["content"] == "第一段第二段"


def test_llm_config_forwarded_props_passthrough(isolated_db, monkeypatch):
    """主规范 Quick Chat Entry：「api_key 与 llm_config 经 forwardedProps 透传」。

    回归（2026-09-06）：端点只透传 apiKey，llmConfig 被静默丢弃——
    quick 模式设置面板的自定义 model/baseUrl 永远不生效（阿里云 key 配
    glm 代理模型名 → 对端报 key 格式错误）。
    """
    sid = session_store.create_chat_session("测试会话")
    captured: dict[str, Any] = {}

    def _fake_build(**kwargs: Any) -> _StubAgent:
        captured.update(kwargs)
        return _StubAgent([StreamEvent.answer("答")])

    monkeypatch.setattr(agent_factory, "build_agent", _fake_build)

    llm_config = {
        "model": "openai/deepseek-v4-flash-0731",
        "baseUrl": "https://token-plan.cn-beijing.maas.aliyuncs.com/compatible-mode/v1",
        "apiKey": "sk-test-aliyun",
        "thinking": "enabled",
        "apiForm": "chat_completion",
    }

    with TestClient(app) as client:
        events = _post_agui(
            client,
            {
                "threadId": sid,
                "runId": "run-llmcfg",
                "messages": [{"id": "m1", "role": "user", "content": "问题"}],
                "forwardedProps": {"apiKey": "sk-test-aliyun", "llmConfig": llm_config},
            },
        )
        assert _types(events)[-1] == "RUN_FINISHED"
        got = captured.get("llm_config")
        assert got is not None, "build_agent 未收到 llm_config"
        assert got.model == "openai/deepseek-v4-flash-0731"
        assert got.baseUrl == "https://token-plan.cn-beijing.maas.aliyuncs.com/compatible-mode/v1"
        assert got.apiKey == "sk-test-aliyun"
        assert got.thinking == "enabled"
        assert got.apiForm == "chat_completion"

        # 不带 llmConfig 时保持 None（回退环境变量）
        captured.clear()
        events = _post_agui(
            client,
            {
                "threadId": sid,
                "runId": "run-llmcfg-absent",
                "messages": [{"id": "m2", "role": "user", "content": "问题"}],
                "forwardedProps": {"apiKey": "k"},
            },
        )
        assert _types(events)[-1] == "RUN_FINISHED"
        assert captured.get("llm_config") is None


def test_api_key_non_sk_prefix_passthrough(isolated_db, monkeypatch):
    """api_key 放宽：非 sk- 前缀的非空字符串也透传；空字符串不透传。"""
    sid = session_store.create_chat_session("测试会话")
    captured: dict[str, Any] = {}

    def _fake_build(**kwargs: Any) -> _StubAgent:
        captured.update(kwargs)
        return _StubAgent([StreamEvent.answer("答")])

    monkeypatch.setattr(agent_factory, "build_agent", _fake_build)

    with TestClient(app) as client:
        events = _post_agui(
            client,
            {
                "threadId": sid,
                "runId": "run-key",
                "messages": [{"id": "m1", "role": "user", "content": "问题"}],
                "forwardedProps": {"apiKey": "custom-token-123"},
            },
        )
        assert _types(events)[-1] == "RUN_FINISHED"
        assert captured.get("api_key") == "custom-token-123"

        captured.clear()
        events = _post_agui(
            client,
            {
                "threadId": sid,
                "runId": "run-key-empty",
                "messages": [{"id": "m2", "role": "user", "content": "问题"}],
                "forwardedProps": {"apiKey": ""},
            },
        )
        assert _types(events)[-1] == "RUN_FINISHED"
        assert captured.get("api_key") is None


def test_capability_tool_call_false_disables_operator_tools(isolated_db, monkeypatch):
    """probe 事实 tool_call=false → build_agent 收到 operator_enabled=False。"""
    sid = session_store.create_chat_session("能力门禁")
    captured: dict[str, Any] = {}

    def _fake_build(**kwargs: Any) -> _StubAgent:
        captured.update(kwargs)
        return _StubAgent([StreamEvent.answer("ok")])

    monkeypatch.setattr(agent_factory, "build_agent", _fake_build)

    with TestClient(app) as client:
        events = _post_agui(
            client,
            {
                "threadId": sid,
                "runId": "run-cap",
                "messages": [{"id": "m1", "role": "user", "content": "分析茅台"}],
                "forwardedProps": {
                    "llmConfig": {"capability": {"tool_call": False, "stream": True}}
                },
            },
        )

    assert _types(events)[-1] == "RUN_FINISHED"
    assert captured["operator_enabled"] is False
