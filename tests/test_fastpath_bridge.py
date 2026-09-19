"""Fast path 桥接测试：PipelineRunner._run 中的事件经 publish 写入 journal；cancel 设置标志。

对应 change: 可恢复流式生成架构完善 Task 7。
验证：
- PipelineRunner._run 中事件经 stream_registry.publish 写入 journal（含终态 done）
- cancel 设置取消标志并终止后台线程
- 无运行中任务时 cancel 返回 False
"""

from __future__ import annotations

import asyncio
import json
import time

import pytest

from finance_agent import session_store
from finance_agent.pipeline_runner import PipelineRunner
from finance_agent.stream_registry import registry as stream_registry_instance


def _sse(d: dict) -> str:
    return f"data: {json.dumps(d, ensure_ascii=False)}\n\n"


def _setup_db(tmp_path, monkeypatch):
    """隔离 DB。"""
    monkeypatch.setattr(session_store, "_DB_PATH", tmp_path / "test.db")
    session_store.init_db()


def _fake_events():
    """模拟管线 SSE 事件序列。"""
    yield _sse({"type": "analysis_start", "session_id": "s1"})
    yield _sse({"type": "node_start", "node_id": "check_cache", "layer": "PREP"})
    yield _sse(
        {
            "type": "node_complete",
            "node_id": "check_cache",
            "layer": "PREP",
            "completed": ["check_cache"],
            "progress": 0.03,
            "output": {"summary": "ok"},
        }
    )


@pytest.mark.asyncio
async def test_pipeline_bridge_publishes_to_journal(tmp_path, monkeypatch):
    """PipelineRunner._run 中的事件经 publish 写入 journal。"""
    _setup_db(tmp_path, monkeypatch)
    sid = session_store.create_session(stock_code="600519", stock_name="茅台", status="running")

    loop = asyncio.get_event_loop()
    PipelineRunner.start(
        sid,
        _fake_events,
        {"layerTree": [], "currentNodeId": "", "progress": 0.0, "updatedAt": 0},
        loop=loop,
    )

    # 等后台线程跑完
    deadline = time.time() + 10
    while PipelineRunner.is_running(sid) and time.time() < deadline:
        await asyncio.sleep(0.05)
    assert not PipelineRunner.is_running(sid)

    # 验证事件已写入 journal
    events = session_store.list_session_events(sid)
    event_types = [json.loads(e["event_json"]).get("type") for e in events]
    assert "analysis_start" in event_types
    assert "node_start" in event_types
    assert "node_complete" in event_types
    # 终态事件（done）由 finally 块发布
    assert "done" in event_types


@pytest.mark.asyncio
async def test_done_flag_not_set_before_terminal_event_published(tmp_path, monkeypatch):
    """不变量：`is_running` 转 False 之前，终态 done SHALL 已落库。

    回归（CI 实测 flaky：`test_pipeline_bridge_publishes_to_journal` 报
    'done' not in [...]）：finally 里曾先置 `state.done=True` 再发布 done 事件，
    读到「已不在运行」后立即读 journal 会偶发看不到终态。用慢发布把窗口放大成
    确定性复现——修复后本用例与既有用例都应恒过。
    """
    _setup_db(tmp_path, monkeypatch)
    sid = session_store.create_session(stock_code="600519", stock_name="茅台", status="running")

    orig_publish = stream_registry_instance.publish

    async def slow_publish(session_id, event):
        if event.get("type") == "done":
            await asyncio.sleep(0.2)  # 放大「发布尚未落库」窗口
        return await orig_publish(session_id, event)

    monkeypatch.setattr(stream_registry_instance, "publish", slow_publish)

    loop = asyncio.get_event_loop()
    PipelineRunner.start(
        sid,
        _fake_events,
        {"layerTree": [], "currentNodeId": "", "progress": 0.0, "updatedAt": 0},
        loop=loop,
    )

    deadline = time.time() + 10
    while PipelineRunner.is_running(sid) and time.time() < deadline:
        await asyncio.sleep(0.05)
    assert not PipelineRunner.is_running(sid)

    # 立即读取（不轮询）：done 置位若早于终态落库，这里必然缺失
    event_types = [
        json.loads(e["event_json"]).get("type") for e in session_store.list_session_events(sid)
    ]
    assert "done" in event_types


def test_pipeline_cancel_sets_flag(tmp_path, monkeypatch):
    """PipelineRunner.cancel 设置取消标志并终止后台线程。"""
    _setup_db(tmp_path, monkeypatch)
    sid = session_store.create_session(stock_code="600519", stock_name="茅台", status="running")

    def slow_events():
        for i in range(100):
            time.sleep(0.1)
            yield _sse({"type": "thinking_token", "token": f"token_{i}"})

    PipelineRunner.start(
        sid,
        slow_events,
        {"layerTree": [], "currentNodeId": "", "progress": 0.0, "updatedAt": 0},
    )
    assert PipelineRunner.is_running(sid)

    result = PipelineRunner.cancel(sid)
    assert result is True
    deadline = time.time() + 10
    while PipelineRunner.is_running(sid) and time.time() < deadline:
        time.sleep(0.05)
    assert not PipelineRunner.is_running(sid)


def test_pipeline_cancel_returns_false_when_not_running(tmp_path, monkeypatch):
    """无运行中任务时，cancel 返回 False。"""
    _setup_db(tmp_path, monkeypatch)
    sid = session_store.create_session(stock_code="600519", stock_name="茅台", status="running")
    result = PipelineRunner.cancel(sid)
    assert result is False
