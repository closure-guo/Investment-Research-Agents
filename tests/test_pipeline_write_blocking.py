"""复现：_background_consume 在事件循环线程同步直调 update_pipeline_timelines。

根因（py-spy 堆栈实证）：管线运行时，_background_consume（async 协程，跑在
事件循环线程）对每个 thinking chunk 同步调用 session_store.update_pipeline_timelines
（SQLite 写：open->execute->commit->close）。管线有数百个 thinking chunk（4 分析师
+ 辩论 + Trader + 风控流式思考），高频同步 SQLite 写 + 可能的锁等待，冻结事件循环，
导致 /api/sessions、/api/health 超时 -> 前端刷新会话列表为空。

修复标准：_background_consume 内的 session_store 写 SHALL 经 asyncio.to_thread
移出事件循环，且 thinking chunk 高频写 SHALL 节流，不阻塞事件循环。
"""

from __future__ import annotations

import asyncio
import time

import pytest

from finance_agent import session_store
from finance_agent.agent_factory import _make_run_deep_analysis


def _thinking_heavy_stream():
    """模拟大量 thinking chunk（每个节点流式思考）+ 最终报告。"""
    yield ("custom", {"type": "node_start", "node": "analyst1", "ts": 1000})
    for i in range(30):
        yield ("custom", {"type": "thinking", "node": "analyst1", "token": f"思考片段{i}"})
    yield ("updates", {"analyst1": {"done": True}})
    yield ("custom", {"type": "node_end", "node": "analyst1", "ts": 1001, "duration_ms": 1})
    yield ("updates", {"generate_report": {"final_report": "# 报告"}})


@pytest.mark.asyncio
async def test_pipeline_thinking_writes_do_not_block_event_loop(tmp_path, monkeypatch):
    """管线 thinking chunk 高频写库期间，事件循环 SHALL 保持响应。"""
    monkeypatch.setattr(session_store, "_DB_PATH", tmp_path / "test.db")
    session_store.init_db()
    sid = session_store.create_session(stock_code="600449", stock_name="宁夏建材", status="running")

    # 模拟 update_pipeline_timelines_json 慢写（SQLite 锁等待/磁盘 IO），每次 20ms。
    # （fix-timeline-write-amplification 起写入口统一切到预序列化 _json 变体，
    #   dict 入口亦委托至此——探针钉在新接缝上）
    real_write = session_store.update_pipeline_timelines_json
    write_count = {"n": 0}

    def slow_write(session_id, payload_json):
        write_count["n"] += 1
        time.sleep(0.02)
        return real_write(session_id, payload_json)

    monkeypatch.setattr(session_store, "update_pipeline_timelines_json", slow_write)
    monkeypatch.setattr(
        "finance_agent.agent_factory._stream_graph",
        lambda initial_state, config=None, session_id=None: _thinking_heavy_stream(),
    )

    # 事件循环响应性探针：每 5ms 唤醒计数
    tick = {"n": 0}
    stop = asyncio.Event()

    async def probe():
        while not stop.is_set():
            tick["n"] += 1
            await asyncio.sleep(0.005)

    probe_task = asyncio.create_task(probe())

    run_deep_analysis = _make_run_deep_analysis(api_key="fake", session_id=sid)
    async for _ in run_deep_analysis("600449", "宁夏建材"):
        pass

    from finance_agent.agent_factory import _background_tasks

    bg_task = _background_tasks.get(sid)
    if bg_task is not None:
        await bg_task
    stop.set()
    await probe_task

    # 修复核心目标有二：
    # 1. 节流：30 个 thinking chunk 不应触发 30 次写库（修复前 write_count=31，
    #    节流后应大幅减少，仅剩节点级 + 少量节流写）。
    # 2. 不冻结事件循环：to_thread 移出事件循环后，探针计数应显著高于
    #    修复前的「同步冻结」情形（修复前探针仅 2 次）。
    assert write_count["n"] > 0, "update_pipeline_timelines 未被触发"
    assert write_count["n"] <= 10, (
        f"高频写未节流：30 个 thinking chunk 触发 {write_count['n']} 次写库"
    )
    # 事件循环保持响应：探针计数显著高于修复前的 2 次（同步冻结）
    assert tick["n"] > 5, (
        f"事件循环被高频同步写冻结：探针仅计数 {tick['n']} 次（写入 {write_count['n']} 次）"
    )
