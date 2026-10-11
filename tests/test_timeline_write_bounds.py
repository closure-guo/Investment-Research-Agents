"""fix-timeline-write-amplification：洪峰期中间写带宽有界（TDD 先红，issue #265 子项1）。

incident 039 放大器：固定 0.5s 节流窗对全量 nodeTimelines 做 json.dumps + 整行
UPDATE；内容涨到 MB 级后单次写阻塞消费循环数百毫秒、每 0.5s 一次，泵限速到
~1.3 事件/s。修复后中间写间隔按上次序列化字节数自适应（design D2），
写带宽有界；节点边界即时冲刷与结束 flush 语义不变。

测试动力学：假时钟每 call 步进 1ms，3000×2KB token 洪峰在虚拟 ~6s 内灌入——
旧实现每 0.5s 触发一次全量写（12 次，累计 ~39MB），新实现自适应间隔下
中间写 ≤6 次且累计字节有界；结束 flush 仍写全量。
"""

from __future__ import annotations

import json
import time

import pytest

from finance_agent import session_store


@pytest.fixture()
def fake_clock(monkeypatch):
    """虚拟时钟：每次 time.time() 调用步进 1ms（asyncio 用 monotonic，不受影响）。"""
    now = [1_000_000.0]

    def _fake() -> float:
        now[0] += 0.001
        return now[0]

    monkeypatch.setattr(time, "time", _fake)
    return now


class _WriteSpy:
    """记录 update_pipeline_timelines[/ _json] 的 (payload_bytes, parsed) 序列。"""

    def __init__(self, monkeypatch):
        self.writes: list[tuple[int, dict]] = []
        real_dict = session_store.update_pipeline_timelines

        def _spy_dict(session_id: str, timelines: dict) -> bool:
            payload = json.dumps(timelines, ensure_ascii=False)
            self.writes.append((len(payload.encode("utf-8")), timelines))
            return real_dict(session_id, timelines)

        monkeypatch.setattr(session_store, "update_pipeline_timelines", _spy_dict)
        # 新实现走预序列化入口；旧实现无此函数（raising=False 使红测可跑）
        real_json = getattr(session_store, "update_pipeline_timelines_json", None)
        if real_json is not None:

            def _spy_json(session_id: str, payload_json: str) -> bool:
                self.writes.append((len(payload_json.encode("utf-8")), json.loads(payload_json)))
                return real_json(session_id, payload_json)

            monkeypatch.setattr(session_store, "update_pipeline_timelines_json", _spy_json)


def _flood_token() -> str:
    return "x" * 2048  # ASCII：len == UTF-8 字节数，payload 数学干净


def _assert_writes_bounded(writes: list[tuple[int, dict]], total_tokens: int) -> None:
    """中间写次数与字节双有界；末次 flush 为全量。"""
    assert writes, "至少应有结束 flush 一次写入"
    interim, final = writes[:-1], writes[-1]
    assert len(writes) <= 6, (
        f"洪峰期写次数 SHALL 有界（自适应间隔），实际 {len(writes)} 次"
        f"（旧固定 0.5s 节流在本场景约 13 次）"
    )
    interim_bytes = sum(b for b, _ in interim)
    assert interim_bytes <= 12 * 1024 * 1024, (
        f"中间写累计字节 SHALL 有界（写带宽 ≤256KB/s），实际 {interim_bytes / 1e6:.1f}MB"
        f"（旧实现本场景约 39MB）"
    )
    # 结束 flush 全量：trader 节点 thinking 内容完整
    final_timelines = final[1]
    content = "".join(
        item.get("content", "")
        for item in final_timelines.get("trader", [])
        if item.get("type") == "thinking"
    )
    assert len(content) == total_tokens * 2048, "结束 flush SHALL 写入完整时序"


def test_update_pipeline_timelines_json_roundtrip(tmp_path, monkeypatch):
    """预序列化写入入口：与 dict 入口读出同构结构（session-persistence 场景不变）。"""
    monkeypatch.setattr(session_store, "_DB_PATH", tmp_path / "t.db")
    session_store.init_db()
    sid = session_store.create_session(stock_code="600519", stock_name="茅台", status="running")
    timelines = {"trader": [{"type": "thinking", "content": "abc", "done": True}]}
    ok = session_store.update_pipeline_timelines_json(
        sid, json.dumps(timelines, ensure_ascii=False)
    )
    assert ok is True
    detail = session_store.get_session(sid)
    assert detail["pipeline_timelines"] == timelines  # get_session 已解析 JSON


# ── ReAct 路径（agent_factory._background_consume）──


def _final_updates_chunk() -> tuple:
    return (
        "updates",
        {
            "generate_report": {
                "final_report": "# 报告",
                "chart_data": {},
                "analyst_reports": {},
            }
        },
    )


@pytest.mark.asyncio
async def test_react_flood_interim_writes_bounded(tmp_path, monkeypatch, fake_clock):
    """ReAct 泵 thinking 洪峰：中间写带宽有界 + 结束 flush 全量。"""
    monkeypatch.setenv("PIPELINE_TIMEOUT_SECONDS", "60")
    monkeypatch.setattr(session_store, "_DB_PATH", tmp_path / "t.db")
    session_store.init_db()
    sid = session_store.create_session(stock_code="688072", stock_name="拓荆科技", status="running")
    spy = _WriteSpy(monkeypatch)
    total = 3000

    def _stream(initial_state, config=None, session_id=None):
        for _ in range(total):
            yield ("custom", {"type": "thinking", "node": "trader", "token": _flood_token()})
        yield _final_updates_chunk()

    monkeypatch.setattr("finance_agent.agent_factory._stream_graph", _stream)
    from finance_agent.agent_factory import _make_run_deep_analysis

    tool = _make_run_deep_analysis(api_key="fake", session_id=sid)
    events: list = []
    async for ev in tool("688072", "拓荆科技"):
        events.append(ev)

    assert events, "事件流不应为空"
    _assert_writes_bounded(spy.writes, total)


# ── Fast path（PipelineRunner）──


def test_fastpath_flood_interim_writes_bounded(tmp_path, monkeypatch, fake_clock):
    """Fast path thinking 洪峰：中间写带宽有界 + 结束 flush 全量。"""
    monkeypatch.setenv("PIPELINE_TIMEOUT_SECONDS", "60")
    monkeypatch.setattr(session_store, "_DB_PATH", tmp_path / "t.db")
    session_store.init_db()
    sid = session_store.create_session(stock_code="600519", stock_name="茅台", status="running")
    spy = _WriteSpy(monkeypatch)
    total = 3000

    def _sse(d: dict) -> str:
        return f"data: {json.dumps(d, ensure_ascii=False)}\n\n"

    def _events():
        yield _sse({"type": "node_start", "node_id": "trader", "layer": "DECIDE"})
        for _ in range(total):
            yield _sse({"type": "thinking_token", "node": "trader", "token": _flood_token()})
        yield _sse(
            {
                "type": "node_complete",
                "node_id": "trader",
                "layer": "DECIDE",
                "completed": ["trader"],
                "progress": 1.0,
                "output": {},
            }
        )
        yield _sse({"type": "report_ready", "session_id": sid, "report_markdown": "# 报告"})

    from finance_agent.pipeline_runner import PipelineRunner

    PipelineRunner.start(
        sid,
        _events,
        {"layerTree": [], "currentNodeId": "", "progress": 0.0, "updatedAt": 0},
    )
    deadline = time.monotonic() + 30
    while PipelineRunner.is_running(sid) and time.monotonic() < deadline:
        time.sleep(0.01)
    assert not PipelineRunner.is_running(sid), "管线应在有界时间内完成"

    _assert_writes_bounded(spy.writes, total)
    # 结束时序落库完整（直接读库验证 flush 语义）
    detail = session_store.get_session(sid)
    persisted = detail["pipeline_timelines"]  # get_session 已解析 JSON
    content = "".join(
        item.get("content", "")
        for item in persisted.get("trader", [])
        if item.get("type") == "thinking"
    )
    assert len(content) == total * 2048


# ── 消费滞后回归（fix: 压缩仅作用于事件转发，时间线累积照常）──
#
# 机制（CI 实证 flake，同 commit 一次 4532 全过、一次红）：graph_done 是线程
# Event，生产者线程排完流即置位；若消费者滞后，积压 thinking 在 graph_done 后
# 走 incident-039 压缩分支。旧实现把「不做 timeline 累积」与压缩耦合——落库
# 时序静默丢积压（CI 实测 1743/3000 = 消费位 1231 + TAIL_KEEP_MAX 512，精确
# 吻合），违反 session-persistence「结束 flush 全量」。
#
# 滞后的确定性制造：to_thread 写库 await 是消费循环让出事件循环的真实窗口——
# 期间生产者线程把剩余 chunk 全部调度进 chunk_queue（run_coroutine_threadsafe
# 的 put 也是 loop 回调，仅在循环空闲时批量入队；在消费者里 sleep 反而把 put
# 调度一起堵死，制造不出积压）。给 spy 写库加 worker 线程内 sleep 即可。


@pytest.mark.asyncio
async def test_react_lag_backlog_keeps_full_timeline(tmp_path, monkeypatch):
    monkeypatch.setenv("PIPELINE_TIMEOUT_SECONDS", "60")
    monkeypatch.setattr(session_store, "_DB_PATH", tmp_path / "t.db")
    session_store.init_db()
    sid = session_store.create_session(stock_code="688072", stock_name="拓荆科技", status="running")
    spy = _WriteSpy(monkeypatch)
    total = 1500

    # spy 之上再包一层 50ms worker 线程延迟：每次时序写 await 期间事件循环空闲，
    # 生产者把大量 chunk 批量入队——消费者跨 graph_done 边界时积压必 > 512
    spied_json = session_store.update_pipeline_timelines_json

    def _slow_json(session_id_arg, payload_json):
        time.sleep(0.05)
        return spied_json(session_id_arg, payload_json)

    monkeypatch.setattr(session_store, "update_pipeline_timelines_json", _slow_json)

    def _stream(initial_state, config=None, session_id=None):
        for _ in range(total):
            yield ("custom", {"type": "thinking", "node": "trader", "token": _flood_token()})
        yield _final_updates_chunk()

    monkeypatch.setattr("finance_agent.agent_factory._stream_graph", _stream)
    from finance_agent.agent_factory import _make_run_deep_analysis

    tool = _make_run_deep_analysis(api_key="fake", session_id=sid)
    events: list = []
    async for ev in tool("688072", "拓荆科技"):
        events.append(ev)

    assert events, "事件流不应为空"
    _assert_writes_bounded(spy.writes, total)
