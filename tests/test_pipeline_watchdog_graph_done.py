"""看门狗图完成信号 + 消费端分类背压测试。

对应 change: add-event-delivery-resilience Task 3。
spec pipeline-events「管线超时与中断检测」MODIFIED + 「发布积压可观测与背压」
+ 「图完成后工具消费端压缩缓冲明细」。

事故背景（2026-10-04 拓荆科技 688072）：glm-5.3 产生 6.97 万条 thinking_token
把 run_deep_analysis 的事件消费链压出 ~19 分钟滞后。管线本体 21 分钟完成出报告
（预算 2400s 内），但看门狗做的是硬墙钟检查（不区分「图已完成但事件未排空」），
在 21:19:02 误判超时把会话置 failed——报告 33 秒后正常产出。四个场景：

A. 图完成+排空滞后不误判超时（本次事故直接回归）
B. 图未完成预算耗尽仍判超时（回归锚，现状行为）
C. 队列满时 thinking 明细丢弃可观测（计数 + WARNING），边界事件不丢
D. 图完成后 thinking 明细压缩（不入事件流、tail_thinking 计数、终态有界送达）
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time

import pytest

from finance_agent import session_store
from finance_agent.agent_factory import _make_run_deep_analysis
from finance_agent.harness import ActionType
from finance_agent.stream_registry import registry


def _setup(tmp_path, monkeypatch, timeout_seconds: str) -> str:
    """对齐 test_pipeline_timeout.py 的 harness：tmp 库 + running 会话 + 超时预算。"""
    monkeypatch.setenv("PIPELINE_TIMEOUT_SECONDS", timeout_seconds)
    monkeypatch.setattr(session_store, "_DB_PATH", tmp_path / "test.db")
    session_store.init_db()
    return session_store.create_session(
        stock_code="688072", stock_name="拓荆科技", status="running"
    )


def _final_updates_chunk() -> tuple:
    """产出最终报告的 updates chunk（触发正常完成路径）。"""
    return (
        "updates",
        {
            "generate_report": {
                "final_report": "# 拓荆科技深度分析报告\n\n## 1. 封面",
                "chart_data": {},
                "analyst_reports": {},
            }
        },
    )


@pytest.mark.asyncio
async def test_scenario_a_graph_done_drain_lag_does_not_timeout(tmp_path, monkeypatch):
    """场景 A：图完成+排空滞后不误判超时（本次事故直接回归）。

    图流全部产出后正常结束（graph_done 置位 + 哨兵到达），但消费侧排空缓冲
    期间假钟已越过预算线。看门狗 SHALL 核对图完成信号：图已完成时排空延迟
    不构成超时，走正常完成路径（TOOL_RESULT「深度分析完成」+ report_ready，
    会话不置 failed）。
    """
    sid = _setup(tmp_path, monkeypatch, "5")

    # 假钟：可从测试侧「把预算拨到已耗尽」。offset=0 时与真实时钟一致。
    real_time = time.time
    clock = {"offset": 0.0}

    def fake_time():
        return real_time() + clock["offset"]

    monkeypatch.setattr(time, "time", fake_time)

    def _flood_stream(initial_state, config=None, session_id=None):
        # 6.97 万 thinking 洪峰的缩影：先冲一批 thinking（> event_queue maxsize）
        for i in range(120):
            yield ("custom", {"type": "thinking", "node": "trader", "token": f"t{i}"})
        yield _final_updates_chunk()
        # 报告产出后仍缓冲一批 thinking（事故中的排空滞后段）
        for i in range(30):
            yield ("custom", {"type": "thinking", "node": "trader", "token": f"tail{i}"})

    monkeypatch.setattr("finance_agent.agent_factory._stream_graph", _flood_stream)

    # 红路径扳机（消费者同步处理段内扳机，必在后续预算检查之前生效）：
    # 现实现经 NodeTimelineAccumulator.add_thinking_token 累积 timeline
    # （fix-timeline-write-amplification 起纯函数逐 token 调用改为可变累加器）——
    # tail 前缀 token（排空段明细）首现时把假钟拨过预算线。
    # 绿路径下 tail 明细在 timeline 累积前即被压缩，此扳机天然不触发。
    from finance_agent.timeline_builder import NodeTimelineAccumulator

    real_add = NodeTimelineAccumulator.add_thinking_token

    def _arm_on_tail_token(self, node, token):
        if token.startswith("tail"):
            clock["offset"] = 1000.0
        return real_add(self, node, token)

    monkeypatch.setattr(NodeTimelineAccumulator, "add_thinking_token", _arm_on_tail_token)

    # 绿路径扳机：图完成后首个 tail_thinking 压缩丢弃时把假钟拨过预算线——
    # 排空期间若实现仍做预算检查必触发 TimeoutError（绿路径无检查，故无害）。
    real_record_drop = registry.record_drop

    def _arm_on_tail_drop(drop_sid, kind):
        if kind == "tail_thinking":
            clock["offset"] = 1000.0
        real_record_drop(drop_sid, kind)

    monkeypatch.setattr(registry, "record_drop", _arm_on_tail_drop)

    tool = _make_run_deep_analysis(api_key="fake", session_id=sid)
    events = []
    async for ev in tool("688072", "拓荆科技"):
        events.append(ev)

    assert events, "事件流不应为空"
    final = events[-1]
    assert final.event_type == ActionType.TOOL_RESULT, (
        f"最终事件应为 TOOL_RESULT（正常完成路径），实际: {final.event_type}"
    )
    assert final.tool_result is not None
    assert final.tool_result.output.startswith("深度分析完成"), (
        f"应走正常完成路径而非超时/失败，实际 output: {final.tool_result.output[:200]}"
    )
    assert final.tool_result.metadata is not None
    assert final.tool_result.metadata.get("sse_type") == "report_ready"
    assert "pipeline_timeout" not in final.tool_result.metadata

    row = session_store.get_session(sid)
    assert row is not None
    assert row["status"] == "completed", (
        f"图已完成仅排空滞后，会话 MUST NOT 置 failed，实际: {row['status']}"
    )


@pytest.mark.asyncio
async def test_scenario_b_budget_exhausted_before_graph_done_still_timeout(tmp_path, monkeypatch):
    """场景 B：图未完成预算耗尽仍判超时（回归锚：不放松真超时的拦截）。"""
    sid = _setup(tmp_path, monkeypatch, "0.5")

    def _steady_stream(initial_state, config=None, session_id=None):
        # 40 条 × 50ms ≈ 2s，预算 0.5s 在图中途（graph 未完成）耗尽
        for i in range(40):
            yield ("custom", {"type": "thinking", "node": "trader", "token": f"t{i}"})
            time.sleep(0.05)

    monkeypatch.setattr("finance_agent.agent_factory._stream_graph", _steady_stream)

    tool = _make_run_deep_analysis(api_key="fake", session_id=sid)
    events = [ev async for ev in tool("688072", "拓荆科技")]

    tool_results = [e for e in events if e.event_type == ActionType.TOOL_RESULT]
    assert tool_results, "超时必须下发 TOOL_RESULT"
    out = tool_results[-1].tool_result.output if tool_results[-1].tool_result else ""
    assert "管线执行超时" in out
    assert tool_results[-1].tool_result is not None
    # spec「管线异常终止的工具结果显式错误语义」：is_error=True + 机器可读标志
    assert tool_results[-1].tool_result.is_error is True
    assert tool_results[-1].tool_result.metadata.get("pipeline_timeout") is True

    row = session_store.get_session(sid)
    assert row is not None
    assert row["status"] == "failed"
    assert row["failure_reason"] == "管线执行超时"


@pytest.mark.asyncio
async def test_scenario_c_queue_full_drops_thinking_but_keeps_boundary_events(
    tmp_path, monkeypatch, caplog
):
    """场景 C：队列满时 thinking 明细丢弃可观测，节点边界事件不丢。

    event_queue(maxsize=100) 塞满且消费者暂停：继续产 thinking SHALL 丢弃并
    计数（backlog_stats.drops.thinking >= 1）+ WARNING 日志；随后产的节点
    边界事件（node_start/node_complete）SHALL 阻塞入队不丢弃（最终出现在
    事件流中）。
    """
    sid = _setup(tmp_path, monkeypatch, "30")
    caplog.set_level(logging.WARNING, logger="finance_agent.agent_factory")

    def _flood_stream(initial_state, config=None, session_id=None):
        for i in range(150):
            yield ("custom", {"type": "thinking", "node": "trader", "token": f"t{i}"})
        # 队列满后产节点边界事件：MUST NOT 被丢弃
        yield ("updates", {"check_cache": {"cached": True}})
        yield _final_updates_chunk()

    monkeypatch.setattr("finance_agent.agent_factory._stream_graph", _flood_stream)

    tool = _make_run_deep_analysis(api_key="fake", session_id=sid)
    gen = tool("688072", "拓荆科技")
    try:
        first = await gen.__anext__()  # 启动管线，拿到首个事件
        # 暂停消费：让 150 条 thinking 涌入塞满 event_queue(maxsize=100)
        await asyncio.sleep(0.5)
        # drop 计数随运行终结清理（issue #227.1 生命周期契约），采样必须在
        # 会话活跃窗口内：此刻队列满，生产端被阻塞在边界事件入队上未终结
        flood_stats = registry.backlog_stats(sid)

        events = [first]
        while True:
            try:
                ev = await asyncio.wait_for(gen.__anext__(), timeout=5)
            except StopAsyncIteration:
                break
            except TimeoutError:
                # 哨兵未达（现状实现把入队静默抑制）——按超时收口再断言
                break
            events.append(ev)
    finally:
        with contextlib.suppress(Exception):
            await gen.aclose()

    assert flood_stats["drops"].get("thinking", 0) >= 1, (
        f"队列满时 thinking 明细 SHALL 丢弃并计数，实际 drops: {flood_stats['drops']}"
    )
    assert "thinking 明细丢弃" in caplog.text, "丢弃 SHALL 有 WARNING 日志（不可静默）"

    completes = [
        e
        for e in events
        if e.event_type == ActionType.PROGRESS
        and e.metadata
        and e.metadata.get("sse_type") == "node_complete"
        and e.metadata.get("node") == "check_cache"
    ]
    assert completes, "节点边界事件（node_complete）MUST NOT 被丢弃，应出现在事件流中"


@pytest.mark.asyncio
async def test_scenario_e_prereport_backlog_compressed_after_graph_done(tmp_path, monkeypatch):
    """场景 E：图完成后、final_report chunk **之前**的大规模 thinking 积压 SHALL 压缩。

    incident 039（2026-10-08 茅台两会话）：生产者发完流（graph_done 置位）时，
    chunk_queue 中仍缓冲着 final_report 之前的数万条 thinking（风控辩论/裁决/
    FM 的思考在流序上全部先于报告 chunk）。现实现的压缩门槛是 report_seen——
    消费者排到 final_report chunk 才置位——在本场景永不生效：泵对积压全速走
    慢路径（timeline 累积 + 入队/丢弃），终态 TOOL_RESULT 被推迟数十分钟到数小时，
    会话滞留 running 直到重启被 reconcile 补打 interrupted。

    修复语义：graph_done 置位时一次性快照剩余积压，超出 TAIL_KEEP_MAX 的部分
    即刻压缩，仅保留有界尾部走慢路径，保证「图完成 → 终态」有界。

    测试动力学：生产环境靠 timeline 写放大天然形成积压；测试环境全链路内存
    速度、泵与生产者 1:1 交错使 qsize 永不累积，故在 sink 入队处加闸门人为
    制造「消费慢于生产」（闸门关闭期间生产者灌完全部流 + graph_done 置位 +
    积压成形），开闸后断言压缩行为。闸门不改变被测逻辑，仅注入时序。
    """
    from finance_agent.agent_factory import BoundedEventSink

    sid = _setup(tmp_path, monkeypatch, "60")
    backlog = 3000  # > TAIL_KEEP_MAX(512)，代表事故中的数万级积压

    # 闸门：首个 droppable thinking 的入队挂起，直至生产者灌完流
    gate = asyncio.Event()
    real_put = BoundedEventSink.put

    async def _gated_put(self, evt, *, droppable=False):
        if droppable and not gate.is_set():
            await gate.wait()
        return await real_put(self, evt, droppable=droppable)

    monkeypatch.setattr(BoundedEventSink, "put", _gated_put)

    tail_drops = 0
    real_record_drop = registry.record_drop

    def _spy_record_drop(session_id: str, kind: str) -> None:
        nonlocal tail_drops
        if session_id == sid and kind == "tail_thinking":
            tail_drops += 1
        real_record_drop(session_id, kind)

    monkeypatch.setattr(registry, "record_drop", _spy_record_drop)

    def _stream(initial_state, config=None, session_id=None):
        # 真实语序：thinking 洪峰在 final_report 之前（非场景 D 的报告在前）
        for i in range(backlog):
            yield ("custom", {"type": "thinking", "node": "trader", "token": f"flood{i}"})
        yield _final_updates_chunk()
        for i in range(100):
            yield ("custom", {"type": "thinking", "node": "trader", "token": f"tail{i}"})

    monkeypatch.setattr("finance_agent.agent_factory._stream_graph", _stream)

    tool = _make_run_deep_analysis(api_key="fake", session_id=sid)
    t0 = time.perf_counter()
    events: list = []

    async def _drain() -> None:
        async for ev in tool("688072", "拓荆科技"):
            events.append(ev)

    # 消费整体挂成 task：泵挂起在闸门上（首事件不会产出），必须先让生产者
    # 灌完流再开闸——若先 await 首事件再开闸会循环等待死锁
    drain_task = asyncio.create_task(_drain())
    await asyncio.sleep(0.3)  # 生产者灌完 3100 条 + graph_done 置位 + 积压成形
    gate.set()  # 开闸：泵以 graph_done 后的积压快照继续
    await asyncio.wait_for(drain_task, timeout=15)
    elapsed = time.perf_counter() - t0

    assert events, "事件流不应为空"
    final = events[-1]
    assert final.event_type == ActionType.TOOL_RESULT
    assert final.tool_result is not None
    assert final.tool_result.output.startswith("深度分析完成"), (
        "pre-report 积压场景应走正常完成路径（非超时/失败）"
    )
    assert elapsed < 15, f"终态 TOOL_RESULT 应在有界时间内产出: {elapsed:.2f}s"
    assert tail_drops >= backlog - 512, (
        f"graph_done 后 final_report 前的大规模积压 SHALL 压缩丢弃"
        f"（保留 ≤TAIL_KEEP_MAX 有界尾部），实际 tail_thinking 计数 {tail_drops}"
    )


@pytest.mark.asyncio
async def test_scenario_d_tail_thinking_compressed_after_graph_done(tmp_path, monkeypatch):
    """场景 D：图完成后 thinking 明细压缩。

    图完成后仍有多条 thinking chunk 入 chunk_queue：SHALL 不出现在事件流中，
    backlog_stats 的 tail_thinking 计数等于条数；终态 TOOL_RESULT 在有界
    时间内产出（无逐条 timeline 写/入队延迟）。
    """
    sid = _setup(tmp_path, monkeypatch, "30")
    tail_count = 50

    # drop 计数随运行终结清理（issue #227.1 生命周期），终态事件入队前
    # _background_consume 收尾即清——消费侧不存在活跃采样窗口。改以 spy
    # 验证「压缩即计数」契约；record_drop→backlog_stats 集成由
    # test_stream_registry.py 与场景 C 活跃窗口断言覆盖。
    tail_drops = 0
    real_record_drop = registry.record_drop

    def _spy_record_drop(session_id: str, kind: str) -> None:
        nonlocal tail_drops
        if session_id == sid and kind == "tail_thinking":
            tail_drops += 1
        real_record_drop(session_id, kind)

    monkeypatch.setattr(registry, "record_drop", _spy_record_drop)

    def _stream(initial_state, config=None, session_id=None):
        yield ("updates", {"check_cache": {"cached": True}})
        yield _final_updates_chunk()
        # 图完成后（哨兵前）涌入的缓冲 thinking 明细
        for i in range(tail_count):
            yield ("custom", {"type": "thinking", "node": "trader", "token": f"tail{i}"})

    monkeypatch.setattr("finance_agent.agent_factory._stream_graph", _stream)

    tool = _make_run_deep_analysis(api_key="fake", session_id=sid)
    t0 = time.perf_counter()
    events = [ev async for ev in tool("688072", "拓荆科技")]
    elapsed = time.perf_counter() - t0

    tail_thinks = [
        e for e in events if e.event_type == ActionType.THINK and e.content.startswith("tail")
    ]
    assert not tail_thinks, (
        f"图完成后的思考明细 SHALL 压缩丢弃、不出现在事件流中，实际透出 {len(tail_thinks)} 条"
    )
    assert tail_drops == tail_count, (
        f"tail_thinking 计数应等于压缩条数 {tail_count}，实际 {tail_drops}"
    )
    assert events, "事件流不应为空"
    final = events[-1]
    assert final.event_type == ActionType.TOOL_RESULT
    assert final.tool_result is not None
    assert final.tool_result.output.startswith("深度分析完成")
    assert elapsed < 10, f"终态 TOOL_RESULT 应在有界时间内产出: {elapsed:.2f}s"
