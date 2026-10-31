"""管线停滞看门狗集成测试（update-pipeline-stall-watchdog，issue #521）。

覆盖 spec「管线停滞看门狗」四场景：
- 管线内零事件超阈值判停滞（asyncio 路径 + fast path 看门狗线程）
- thinking 流动不得判停滞（回归锚：steady chunk 不触发停滞）
- fast path 独立看门狗线程（阻塞读期间循环内检查不可达——10-28 opencode
  宕机 16 管线吊死的直接根因）
- 迟到完成不得覆盖停滞终态
"""

from __future__ import annotations

import json
import threading
import time

import pytest

from finance_agent import pipeline_watchdog as pw
from finance_agent import session_store
from finance_agent.agent_factory import _make_run_deep_analysis
from finance_agent.pipeline_runner import PipelineRunner


def _setup_db(tmp_path, monkeypatch) -> str:
    monkeypatch.setattr(session_store, "_DB_PATH", tmp_path / "t.db")
    session_store.init_db()
    return session_store.create_session(
        stock_code="600519", stock_name="贵州茅台", status="running"
    )


def _sse(d: dict) -> str:
    return f"data: {json.dumps(d, ensure_ascii=False)}\n\n"


# ── asyncio 消费路径（agent_factory._background_consume） ──────────────────


@pytest.mark.asyncio
async def test_asyncio_stall_marks_failed_with_stall_reason(tmp_path, monkeypatch):
    """零事件超停滞阈值 → failed + 「停滞」文案（区别于总预算「超时」）。"""
    monkeypatch.setenv("PIPELINE_TIMEOUT_SECONDS", "30")  # 总预算远未到
    monkeypatch.setenv("PIPELINE_STALL_TIMEOUT_SECONDS", "0.5")
    sid = _setup_db(tmp_path, monkeypatch)

    def _stall_stream(initial_state, config=None, session_id=None):
        yield ("custom", {"type": "node_start", "node": "check_cache", "ts": 1000})
        time.sleep(3)  # 停滞：0.5s 阈值后无任何事件
        yield ("updates", {"check_cache": {"cached": True}})

    monkeypatch.setattr("finance_agent.agent_factory._stream_graph", _stall_stream)

    run_deep_analysis = _make_run_deep_analysis(api_key="fake", session_id=sid)
    gen = run_deep_analysis("600519", "贵州茅台")
    await gen.__anext__()
    await gen.aclose()

    from finance_agent.agent_factory import _background_tasks

    bg_task = _background_tasks.get(sid)
    if bg_task is not None:
        await bg_task

    row = session_store.get_session(sid)
    assert row is not None
    assert row["status"] == "failed"
    assert row["failure_reason"] is not None
    assert "停滞" in row["failure_reason"]
    assert "管线执行超时" not in row["failure_reason"]


@pytest.mark.asyncio
async def test_thinking_flow_does_not_trigger_stall(tmp_path, monkeypatch):
    """thinking 持续流动（间隔 0.05s << 0.5s 阈值）MUST NOT 判停滞。"""
    monkeypatch.setenv("PIPELINE_TIMEOUT_SECONDS", "30")
    monkeypatch.setenv("PIPELINE_STALL_TIMEOUT_SECONDS", "0.5")
    sid = _setup_db(tmp_path, monkeypatch)

    def _steady_stream(initial_state, config=None, session_id=None):
        for i in range(30):
            yield ("custom", {"type": "thinking", "node": "trader", "token": f"t{i}"})
            time.sleep(0.05)

    monkeypatch.setattr("finance_agent.agent_factory._stream_graph", _steady_stream)

    run_deep_analysis = _make_run_deep_analysis(api_key="fake", session_id=sid)
    gen = run_deep_analysis("600519", "贵州茅台")
    await gen.__anext__()
    await gen.aclose()

    from finance_agent.agent_factory import _background_tasks

    bg_task = _background_tasks.get(sid)
    if bg_task is not None:
        await bg_task

    row = session_store.get_session(sid)
    assert row is not None
    reason = row["failure_reason"] or ""
    assert "停滞" not in reason


# ── fast path（PipelineRunner 看门狗线程） ─────────────────────────────────


def test_fast_path_watchdog_marks_failed_while_blocked(tmp_path, monkeypatch):
    """阻塞读期间循环内检查不可达 → 独立看门狗线程判停滞。

    复刻 10-28 opencode 宕机形态：事件源先出一个事件，然后永久阻塞在
    外部事件上（模拟 SSL 读挂死）。
    """
    monkeypatch.setenv("PIPELINE_STALL_TIMEOUT_SECONDS", "0.3")
    sid = _setup_db(tmp_path, monkeypatch)
    release = threading.Event()

    def _hung_events():
        yield _sse({"type": "analysis_start", "session_id": sid})
        release.wait(timeout=30)  # 挂死：外部解除前零事件
        yield _sse({"type": "node_start", "node_id": "check_cache"})

    initial = {"layerTree": [], "currentNodeId": "", "progress": 0.0, "updatedAt": 0}
    PipelineRunner.start(sid, _hung_events, initial)

    # 看门狗（0.3s 阈值 + 轮询）应在 ~2s 内判停滞
    deadline = time.time() + 5
    row = None
    while time.time() < deadline:
        row = session_store.get_session(sid)
        if row and row["status"] == "failed":
            break
        time.sleep(0.1)
    assert row is not None and row["status"] == "failed"
    assert row["failure_reason"] is not None
    assert "停滞" in row["failure_reason"]
    assert "analysis_start" in row["failure_reason"]  # failure_reason 带最后事件类型
    assert not PipelineRunner.is_running(sid)

    # 迟到完成守卫：解除阻塞后管线复活，MUST NOT 改回 completed
    release.set()
    time.sleep(1.0)
    row = session_store.get_session(sid)
    assert row is not None
    assert row["status"] == "failed"  # 终态以先到者为准


def test_fast_path_normal_run_not_stalled(tmp_path, monkeypatch):
    """正常快速完成（事件密集）不得触发停滞。"""
    monkeypatch.setenv("PIPELINE_STALL_TIMEOUT_SECONDS", "5")
    sid = _setup_db(tmp_path, monkeypatch)

    def _fake_events():
        yield _sse({"type": "analysis_start", "session_id": sid})
        yield _sse({"type": "node_start", "node_id": "check_cache"})
        yield _sse({"type": "node_complete", "node_id": "check_cache", "layer": "PREP"})

    initial = {"layerTree": [], "currentNodeId": "", "progress": 0.0, "updatedAt": 0}
    PipelineRunner.start(sid, _fake_events, initial)
    deadline = time.time() + 5
    while time.time() < deadline and PipelineRunner.is_running(sid):
        time.sleep(0.05)
    assert not PipelineRunner.is_running(sid)
    assert not pw.is_stalled(sid)


def test_rerun_clears_stalled_flag(tmp_path, monkeypatch):
    """重跑（start）清除停滞标记：同会话再次分析不受上次停滞影响。"""
    pw._STALLED.clear()
    pw.mark_stalled("sess-rerun")
    monkeypatch.setenv("PIPELINE_STALL_TIMEOUT_SECONDS", "5")
    monkeypatch.setattr(session_store, "_DB_PATH", tmp_path / "t.db")
    session_store.init_db()
    sid = session_store.create_session(stock_code="600519", stock_name="贵州茅台", status="running")

    def _fake_events():
        yield _sse({"type": "analysis_start", "session_id": sid})

    initial = {"layerTree": [], "currentNodeId": "", "progress": 0.0, "updatedAt": 0}
    PipelineRunner.start(sid, _fake_events, initial)
    deadline = time.time() + 5
    while time.time() < deadline and PipelineRunner.is_running(sid):
        time.sleep(0.05)
    assert not pw.is_stalled(sid)
