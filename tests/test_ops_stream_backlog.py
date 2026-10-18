"""GET /api/v1/ops/stream-backlog：流投递积压运维查询端点。

对应 delta spec: add-event-delivery-hardening（issue #227 第 3 项），
spec session-streaming「发布积压可观测与背压」场景「积压状态可查询」的
运维出口实现：活跃会话的订阅者队列深度、订阅者数、丢弃计数与 last_seq。

零网络零 LLM：registry 为进程内内存结构，直接操纵裸 stream 构造夹具。
"""

from __future__ import annotations

import asyncio

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from finance_agent.ops_api import router
from finance_agent.stream_registry import SessionStream
from finance_agent.stream_registry import registry as stream_registry


@pytest.fixture()
def client():
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


@pytest.fixture()
def clean_registry():
    """隔离全局单例：清空 _streams 与 drop 计数，用例后还原。"""
    streams = dict(stream_registry._streams)  # noqa: SLF001
    stream_registry._streams.clear()  # noqa: SLF001
    yield stream_registry
    stream_registry._streams.clear()  # noqa: SLF001
    stream_registry._streams.update(streams)  # noqa: SLF001


def test_stream_backlog_lists_active_sessions(client, clean_registry):
    """活跃会话的队列深度 / 订阅者数 / last_seq / drops 全量可查。"""
    stream = SessionStream()
    stream.lastSeq = 7
    queue: asyncio.Queue[dict | None] = asyncio.Queue(maxsize=8)
    queue.put_nowait({"type": "thinking_token"})
    stream.subscribers.append(queue)
    clean_registry._streams["s-live"] = stream  # noqa: SLF001
    clean_registry.record_drop("s-live", "thinking")

    resp = client.get("/api/v1/ops/stream-backlog")
    assert resp.status_code == 200
    payload = resp.json()
    assert set(payload["sessions"].keys()) == {"s-live"}
    stats = payload["sessions"]["s-live"]
    assert stats["queue_depths"] == [1]
    assert stats["subscribers"] == 1
    assert stats["last_seq"] == 7
    assert stats["drops"] == {"thinking": 1}
    # 投递时延埋点（#328）：无样本时全 None（不冒充 0）
    assert stats["delivery_latency_ms"] == {"n": 0, "p50": None, "p95": None, "max": None}


def test_stream_backlog_empty_when_no_active_sessions(client, clean_registry):
    """无活跃会话返回空 sessions 映射（200，不以 500/空数据冒充异常）。"""
    resp = client.get("/api/v1/ops/stream-backlog")
    assert resp.status_code == 200
    assert resp.json() == {"sessions": {}}
