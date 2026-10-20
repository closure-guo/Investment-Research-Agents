"""Bug 复现测试：追问时 POST /api/analyze 的 _subscribe_sse(after_seq=0)
重放 journal 遇到上一轮 done 事件后 return，导致新事件永远不到前端。

对应 systematic-debugging Phase 4: 复现测试。
"""

from __future__ import annotations

import asyncio
import json

import pytest
from fastapi.testclient import TestClient

import finance_agent.api as api_mod
from finance_agent import session_store, stream_registry
from finance_agent.api import app


def _sse(d: dict) -> str:
    return f"data: {json.dumps(d, ensure_ascii=False)}\n\n"


@pytest.fixture
def isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(session_store, "_DB_PATH", tmp_path / "t.db")
    session_store.init_db()
    return tmp_path / "t.db"


def test_followup_sse_not_terminated_by_previous_done(isolated_db):
    """追问时 SSE 流不应被上一轮的 done 事件终止。

    场景：
    1. 第一轮：session_created → chat_token → done（写入 journal）
    2. 第二轮：POST /api/analyze（带 session_id）→ _subscribe_sse 应发送新事件

    修复前：_subscribe_sse(after_seq=0) 重放 journal，遇到第一轮 done 就 return，
            新事件永远不到前端（UI 卡住）。
    修复后：ReAct 路径用 after_seq=max_seq 跳过历史重放，只发送新事件。
    """
    sid = session_store.create_session(stock_code="", stock_name="", status="completed")
    # 模拟第一轮事件写入 journal
    session_store.append_session_event(sid, {"type": "session_created", "session_id": sid})
    session_store.append_session_event(sid, {"type": "chat_token", "token": "你好"})
    session_store.append_session_event(sid, {"type": "done"})

    max_seq = session_store.get_max_event_seq(sid)
    assert max_seq > 0, "应有历史事件"

    # 模拟第二轮：用 after_seq=max_seq 订阅，不应收到上一轮的 done
    async def collect_events():
        events = []
        gen = stream_registry.registry.subscribe(sid, after_seq=max_seq)
        try:
            result = await asyncio.wait_for(gen.__anext__(), timeout=0.5)
            events.append(result)
        except (StopAsyncIteration, TimeoutError):
            pass
        return events

    events = asyncio.run(collect_events())
    # 修复前（after_seq=0）：会收到上一轮的 done 事件
    # 修复后（after_seq=max_seq）：不应收到任何历史事件（无活跃任务时收到 done）
    # 关键断言：不应收到上一轮的 done（seq <= max_seq）
    for ev in events:
        if ev.get("type") == "done":
            seq = ev.get("seq")
            if seq is not None:
                assert seq > max_seq, f"不应重放上一轮的 done(seq={seq} <= max_seq={max_seq})"


def test_get_max_event_seq(isolated_db):
    """get_max_event_seq 返回 journal 最大 seq，无事件时返回 0。"""
    sid = session_store.create_session(stock_code="", stock_name="", status="completed")

    # 无事件
    assert session_store.get_max_event_seq(sid) == 0

    # 写入事件
    session_store.append_session_event(sid, {"type": "session_created"})
    session_store.append_session_event(sid, {"type": "chat_token", "token": "hi"})
    session_store.append_session_event(sid, {"type": "done"})

    max_seq = session_store.get_max_event_seq(sid)
    assert max_seq == 3  # 3 events


def test_followup_api_sends_new_events(isolated_db, monkeypatch):
    """追问 POST /api/analyze 的 SSE 响应应发送新事件，不被上一轮 done 终止。"""
    sid = session_store.create_session(stock_code="", stock_name="", status="clarifying")
    session_store.append_chat(sid, "user", "分析热门股票")
    session_store.append_chat(sid, "assistant", "我来搜索")
    session_store.append_chat(sid, "user", "中际旭创")

    # 模拟第一轮 journal 事件（含 done 终态）
    session_store.append_session_event(sid, {"type": "session_created", "session_id": sid})
    session_store.append_session_event(sid, {"type": "chat_token", "token": "搜索结果"})
    session_store.append_session_event(sid, {"type": "done"})

    # 模拟第二轮 SSE 流
    newEvents = [
        {"type": "thinking_token", "token": "分析", "timestamp": "2026-08-02T00:00:00Z"},
        {"type": "tool_call", "name": "run_deep_analysis"},
        {"type": "done"},
    ]

    callCount = {"n": 0}

    async def fake_react_task(
        session_id, req, analysis_id, start_time, display_name, api_key, llm_config=None
    ):
        # 等待 subscriber 就绪，避免时序竞争丢失 thinking_token
        await asyncio.sleep(0.05)
        for ev in newEvents:
            await stream_registry.registry.publish(session_id, ev)
        callCount["n"] += 1

    monkeypatch.setattr(api_mod, "_run_react_analysis", fake_react_task)

    with TestClient(app) as client:
        resp = client.post(
            "/api/analyze",
            json={"query": "中际旭创", "session_id": sid, "api_key": "sk-test"},
        )
        assert resp.status_code == 200
        # 收集 SSE 事件
        events = []
        for line in resp.iter_lines():
            if line.startswith("data: "):
                events.append(json.loads(line[6:]))

    # 关键断言：SSE 流应包含第二轮的新事件
    event_types = [e.get("type") for e in events]
    assert "thinking_token" in event_types, f"应收到第二轮 thinking_token，实际收到: {event_types}"
    assert "tool_call" in event_types, f"应收到第二轮 tool_call，实际收到: {event_types}"
    # 不应收到上一轮的 chat_token "搜索结果"（重放的历史事件）
    for e in events:
        if e.get("type") == "chat_token":
            assert e.get("token") != "搜索结果", "不应重放上一轮的 chat_token"


def test_quick_chat_followup_sse_not_terminated_by_previous_done(isolated_db, monkeypatch):
    """快速模式追问：POST /api/chat 的 SSE 不应被上一轮 done 终止。

    场景（用户反馈 bug：快速模式切换会话后提问，新回答丢失/串到旧消息位置）：
    1. 已有 chat 会话：第一轮 session_created → chat_token → done（写入 journal）
    2. 第二轮：POST /api/chat（带 session_id）→ 应只收到本轮新事件

    修复前：_subscribe_sse(after_seq=0) 重放 journal 遇上一轮 done 即 return，
            本轮新事件永远不到前端（新气泡为空；前端 lastSeq=0 时旧事件还会
            渲染进新气泡，表现为输出串台）。
    修复后：与 /api/analyze 对齐，用 after_seq=max_seq 跳过历史重放。
    """
    sid = session_store.create_chat_session("天气问答")
    # 模拟第一轮 journal 事件（含 done 终态）
    session_store.append_session_event(sid, {"type": "session_created", "session_id": sid})
    session_store.append_session_event(sid, {"type": "chat_token", "token": "沈阳天气晴"})
    session_store.append_session_event(sid, {"type": "done"})

    # 模拟第二轮（追问"上海天气"）产生的新事件
    newEvents = [
        {"type": "thinking_token", "token": "查询", "timestamp": "2026-08-03T00:00:00Z"},
        {"type": "chat_token", "token": "上海天气多云"},
        {"type": "done"},
    ]

    async def fake_chat_task(session_id, req, display_name, api_key, llm_config=None):
        # 等待 subscriber 就绪，避免时序竞争丢失事件
        await asyncio.sleep(0.05)
        for ev in newEvents:
            await stream_registry.registry.publish(session_id, ev)

    monkeypatch.setattr(api_mod, "_run_chat_task", fake_chat_task)

    with TestClient(app) as client:
        resp = client.post(
            "/api/chat",
            json={"message": "上海天气", "session_id": sid, "api_key": "sk-test"},
        )
        assert resp.status_code == 200
        events = []
        for line in resp.iter_lines():
            if line.startswith("data: "):
                events.append(json.loads(line[6:]))

    tokens = [e.get("token") for e in events if e.get("type") == "chat_token"]
    # 关键断言：应收到本轮（上海）的新事件，且不应重放上一轮（沈阳）的历史事件
    assert "上海天气多云" in tokens, f"应收到本轮 chat_token，实际收到: {tokens}"
    assert "沈阳天气晴" not in tokens, f"不应重放上一轮 chat_token，实际收到: {tokens}"


def test_new_analyze_stream_keeps_session_created_when_task_wins_race(isolated_db, monkeypatch):
    """新会话 /api/analyze：后台任务先发布 session_created 时不得被 after_seq 跳过。

    竞态（issue #424/#328 根因）：React 路径此前在 registry.start 之后读取
    journal 最大 seq 作为 after_seq。高负载下后台任务可在两步之间先发布
    session_created（seq 1），端点于是从 after_seq=1 订阅，首个事件被跳过。
    前端收不到 session_created → currentSessionId 为空 → 运行中管线卡被
    visibleMessages 过滤，整段深析只剩「思考中」，表现为 UI 冻结。

    /api/chat 已按「先取 after_seq 再 start」修复同一竞态；本测试守护
    /api/analyze 对齐该顺序。
    """

    async def fake_start(session_id, coro):
        # 模拟后台任务在 registry.start 返回前已发布 session_created
        coro.close()
        session_store.append_session_event(
            session_id, {"type": "session_created", "session_id": session_id}
        )
        return True

    monkeypatch.setattr(api_mod.registry, "start", fake_start)

    with TestClient(app) as client:
        resp = client.post(
            "/api/analyze",
            json={"query": "深度分析600519", "api_key": "sk-test"},
        )
        assert resp.status_code == 200
        events = [json.loads(line[6:]) for line in resp.iter_lines() if line.startswith("data: ")]

    types = [e.get("type") for e in events]
    assert "session_created" in types, f"新会话首个事件被 after_seq 跳过: {types}"
    created = next(e for e in events if e.get("type") == "session_created")
    assert created.get("session_id"), "session_created 必须携带 session_id"
