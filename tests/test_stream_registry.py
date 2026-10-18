"""StreamRegistry 单测：任务与订阅管理。

对应 delta spec: resume-stream-on-session-switch Task 2.6。
覆盖：
- single-flight：已有活跃任务时 start 返回 False
- publish：先落库再 fan-out
- subscribe：先重放 journal 再接续实时
- cancel：task.cancel() 后注销
- 断开不杀任务
- 任务完成自动注销
"""

from __future__ import annotations

import asyncio
import json
import time

import pytest

from finance_agent import session_store, stream_registry


def _setup_db(tmp_path, monkeypatch):
    """隔离 DB。"""
    monkeypatch.setattr(session_store, "_DB_PATH", tmp_path / "test.db")
    session_store.init_db()


async def _next_event(gen, timeout=2.0):
    """从 AsyncGenerator 取下一个事件，超时失败。"""
    return await asyncio.wait_for(gen.__anext__(), timeout=timeout)


@pytest.mark.asyncio
async def test_single_flight_rejects_duplicate(tmp_path, monkeypatch):
    """同一会话已有活跃任务时，start 返回 False。"""
    _setup_db(tmp_path, monkeypatch)
    reg = stream_registry.StreamRegistry()
    sid = session_store.create_session(status="running")

    async def dummy_task():
        await asyncio.sleep(10)

    assert await reg.start(sid, dummy_task()) is True
    assert await reg.start(sid, dummy_task()) is False
    await reg.cancel(sid)


@pytest.mark.asyncio
async def test_publish_persists_and_fans_out(tmp_path, monkeypatch):
    """publish 先落库再 fan-out 给订阅者。"""
    _setup_db(tmp_path, monkeypatch)
    reg = stream_registry.StreamRegistry()
    sid = session_store.create_session(status="running")

    async def dummy_task():
        await asyncio.sleep(10)

    await reg.start(sid, dummy_task())

    # 注册订阅者
    gen = reg.subscribe(sid, after_seq=0)

    # 发布事件
    await reg.publish(sid, {"type": "thinking_token", "token": "hello"})

    # 订阅者应收到事件
    event = await _next_event(gen)
    assert event["type"] == "thinking_token"

    # 事件应已落库
    events = session_store.list_session_events(sid, after_seq=0)
    assert len(events) >= 1
    assert reg.is_active(sid)
    await reg.cancel(sid)


@pytest.mark.asyncio
async def test_subscribe_replays_journal(tmp_path, monkeypatch):
    """subscribe 先重放历史事件再接续实时。"""
    _setup_db(tmp_path, monkeypatch)
    reg = stream_registry.StreamRegistry()
    sid = session_store.create_session(status="running")

    async def dummy_task():
        await asyncio.sleep(10)

    await reg.start(sid, dummy_task())

    # 先发布两个事件（无订阅者，仅落库）
    await reg.publish(sid, {"type": "token", "idx": 1})
    await reg.publish(sid, {"type": "token", "idx": 2})

    # 订阅者从 seq=0 开始，应重放历史 + 接续实时
    gen = reg.subscribe(sid, after_seq=0)

    # 收到重放的事件 1
    e1 = await _next_event(gen)
    assert e1["idx"] == 1
    # 收到重放的事件 2
    e2 = await _next_event(gen)
    assert e2["idx"] == 2

    # 发布新事件，订阅者应实时收到
    await reg.publish(sid, {"type": "token", "idx": 3})
    e3 = await _next_event(gen)
    assert e3["idx"] == 3

    await reg.cancel(sid)


@pytest.mark.asyncio
async def test_cancel_task(tmp_path, monkeypatch):
    """cancel 后任务被取消且从 registry 注销。"""
    _setup_db(tmp_path, monkeypatch)
    reg = stream_registry.StreamRegistry()
    sid = session_store.create_session(status="running")

    cancelled = asyncio.Event()

    async def dummy_task():
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            print("[TEST] CancelledError caught in dummy_task")
            cancelled.set()
            raise

    assert await reg.start(sid, dummy_task()) is True
    # 让事件循环有机会调度 _run_task
    await asyncio.sleep(0.05)
    assert reg.is_active(sid)
    print("[TEST] task is active, cancelling...")

    result = await reg.cancel(sid)
    print(f"[TEST] cancel returned: {result}")
    assert result is True

    # 等待 CancelledError 被捕获
    await asyncio.wait_for(cancelled.wait(), timeout=5.0)
    print("[TEST] cancelled event set")
    assert not reg.is_active(sid)


@pytest.mark.asyncio
async def test_task_cleanup_on_complete(tmp_path, monkeypatch):
    """任务正常完成后自动从 registry 注销。"""
    _setup_db(tmp_path, monkeypatch)
    reg = stream_registry.StreamRegistry()
    sid = session_store.create_session(status="running")

    async def quick_task():
        await asyncio.sleep(0.1)

    assert await reg.start(sid, quick_task()) is True
    assert reg.is_active(sid)

    # 等待任务完成
    await asyncio.sleep(0.5)
    assert not reg.is_active(sid)


@pytest.mark.asyncio
async def test_cancel_nonexistent_returns_false(tmp_path, monkeypatch):
    """cancel 无活跃任务的会话返回 False。"""
    _setup_db(tmp_path, monkeypatch)
    reg = stream_registry.StreamRegistry()
    sid = session_store.create_session(status="completed")

    assert await reg.cancel(sid) is False


@pytest.mark.asyncio
async def test_subscribe_no_active_task_sends_terminal(tmp_path, monkeypatch):
    """无活跃任务时 subscribe 重放后下发终态事件。"""
    _setup_db(tmp_path, monkeypatch)
    reg = stream_registry.StreamRegistry()
    sid = session_store.create_session(status="interrupted")

    # 手动写入一些历史事件
    session_store.append_session_event(sid, {"type": "token", "idx": 1})

    # 订阅（无活跃任务）
    gen = reg.subscribe(sid, after_seq=0)

    # 应收到重放的历史事件
    e1 = await _next_event(gen)
    assert e1["idx"] == 1

    # 应收到终态事件（interrupted 或 done）
    terminal = await _next_event(gen)
    assert terminal["type"] in ("interrupted", "done")


# ── 多轮会话全量回放（刷新恢复）──


def _insert_event(sid: str, seq: int, event: dict, created_at: str) -> None:
    """以确定的 created_at 直接写 journal（测试时间戳注入排序用）。"""
    conn = session_store._get_db()  # noqa: SLF001
    conn.execute(
        "INSERT INTO session_events (session_id, seq, event_json, created_at) VALUES (?, ?, ?, ?)",
        (sid, seq, json.dumps(event, ensure_ascii=False), created_at),
    )
    conn.commit()
    conn.close()


def _set_chat_history(sid: str, entries: list[dict]) -> None:
    conn = session_store._get_db()  # noqa: SLF001
    conn.execute(
        "UPDATE sessions SET chat_history = ? WHERE session_id = ?",
        (json.dumps(entries, ensure_ascii=False), sid),
    )
    conn.commit()
    conn.close()


@pytest.mark.asyncio
async def test_subscribe_replay_continues_past_historical_terminal(tmp_path, monkeypatch):
    """多轮会话 journal 含历史 done：全量回放不得在历史终态处截断。

    复现「刷新后管线 UI 消失」：第一轮快速问答的 done 在 journal 中段，
    回放若在此 return，第二轮的 run_deep_analysis/node_start/report_ready
    永远送不到前端。
    """
    _setup_db(tmp_path, monkeypatch)
    reg = stream_registry.StreamRegistry()
    sid = session_store.create_session(status="completed")

    # 第一轮：token → done；第二轮：run_deep_analysis → report_ready
    session_store.append_session_event(sid, {"type": "chat_token", "token": "r1"})
    session_store.append_session_event(sid, {"type": "done"})
    session_store.append_session_event(sid, {"type": "tool_call", "name": "run_deep_analysis"})
    session_store.append_session_event(sid, {"type": "report_ready", "report_markdown": "# r"})

    gen = reg.subscribe(sid, after_seq=0)
    received = []
    async for event in gen:
        received.append(event["type"])

    # 历史 done 后的事件必须送达；无活跃任务时末尾补一个合成 done
    assert received[:4] == ["chat_token", "done", "tool_call", "report_ready"]
    assert received[-1] == "done"


@pytest.mark.asyncio
async def test_subscribe_full_replay_injects_user_messages_in_order(tmp_path, monkeypatch):
    """全量回放按 chat_history 的 ts 注入 user_message，恢复原始交错顺序。

    journal 不含 user_message 事件（用户消息只落 chat_history），刷新后
    全量回放必须合成 user_message，否则前端无法把 user 气泡插回原位。
    """
    _setup_db(tmp_path, monkeypatch)
    reg = stream_registry.StreamRegistry()
    sid = session_store.create_session(status="completed")

    _set_chat_history(
        sid,
        [
            {"role": "user", "content": "今日股票", "ts": "2026-08-13T10:00:00"},
            {"role": "assistant", "content": "热门标的...", "ts": "2026-08-13T10:00:30"},
            {"role": "user", "content": "万邦医药", "ts": "2026-08-13T10:01:00"},
        ],
    )
    _insert_event(sid, 1, {"type": "chat_token", "token": "热门"}, "2026-08-13T10:00:10")
    _insert_event(sid, 2, {"type": "done"}, "2026-08-13T10:00:40")
    _insert_event(sid, 3, {"type": "tool_call", "name": "run_deep_analysis"}, "2026-08-13T10:01:10")

    gen = reg.subscribe(sid, after_seq=0, replay_user_messages=True)
    received = []
    async for event in gen:
        received.append((event["type"], event.get("content") or event.get("name") or ""))

    assert received[:5] == [
        ("user_message", "今日股票"),
        ("chat_token", ""),
        ("done", ""),
        ("user_message", "万邦医药"),
        ("tool_call", "run_deep_analysis"),
    ]


@pytest.mark.asyncio
async def test_subscribe_injects_users_after_all_events(tmp_path, monkeypatch):
    """用户消息 ts 晚于全部 journal 事件（已提交尚无事件）时，在回放末尾注入。"""
    _setup_db(tmp_path, monkeypatch)
    reg = stream_registry.StreamRegistry()
    sid = session_store.create_session(status="completed")

    _set_chat_history(
        sid,
        [
            {"role": "user", "content": "第一问", "ts": "2026-08-13T10:00:00"},
            {"role": "user", "content": "第二问", "ts": "2026-08-13T10:05:00"},
        ],
    )
    _insert_event(sid, 1, {"type": "chat_token", "token": "答"}, "2026-08-13T10:00:10")

    gen = reg.subscribe(sid, after_seq=0, replay_user_messages=True)
    received = [e["type"] async for e in gen]

    assert received[0] == "user_message"
    assert received[1] == "chat_token"
    assert received[2] == "user_message"


@pytest.mark.asyncio
async def test_subscribe_no_injection_when_after_seq_positive(tmp_path, monkeypatch):
    """断点续传（after_seq>0）不注入 user_message——前端已有历史消息。"""
    _setup_db(tmp_path, monkeypatch)
    reg = stream_registry.StreamRegistry()
    sid = session_store.create_session(status="completed")

    _set_chat_history(sid, [{"role": "user", "content": "今日股票", "ts": "2026-08-13T10:00:00"}])
    _insert_event(sid, 1, {"type": "chat_token", "token": "a"}, "2026-08-13T10:00:10")
    _insert_event(sid, 2, {"type": "chat_token", "token": "b"}, "2026-08-13T10:00:20")

    gen = reg.subscribe(sid, after_seq=1, replay_user_messages=True)
    received = [e["type"] async for e in gen]

    assert "user_message" not in received


@pytest.mark.asyncio
async def test_subscribe_no_injection_by_default(tmp_path, monkeypatch):
    """默认（实时路径）不注入 user_message——避免与前端乐观气泡重复。"""
    _setup_db(tmp_path, monkeypatch)
    reg = stream_registry.StreamRegistry()
    sid = session_store.create_session(status="completed")

    _set_chat_history(sid, [{"role": "user", "content": "今日股票", "ts": "2026-08-13T10:00:00"}])
    _insert_event(sid, 1, {"type": "chat_token", "token": "a"}, "2026-08-13T10:00:10")

    gen = reg.subscribe(sid, after_seq=0)
    received = [e["type"] async for e in gen]

    assert "user_message" not in received


# ── add-event-delivery-hardening（issue #227 第 1/3 项）：drop 计数清理与积压快照 ──


@pytest.mark.asyncio
async def test_drop_counts_cleared_on_task_cleanup(tmp_path, monkeypatch):
    """#227.1：任务结束清理 per-session drop 计数，终结慢性泄漏。"""
    _setup_db(tmp_path, monkeypatch)
    reg = stream_registry.StreamRegistry()
    sid = session_store.create_session(status="running")

    async def dropping_task():
        reg.record_drop(sid, "thinking")
        reg.record_drop(sid, "undeliverable")

    assert await reg.start(sid, dropping_task()) is True
    for _ in range(50):
        await asyncio.sleep(0.02)
        if not reg.is_active(sid):
            break
    assert not reg.is_active(sid)
    assert reg.backlog_stats(sid)["drops"] == {}


def test_clear_drop_counts_direct():
    """clear_drop_counts 直清指定会话；未注册会话静默无异常。"""
    reg = stream_registry.StreamRegistry()
    reg.record_drop("s-x", "thinking")
    reg.clear_drop_counts("s-x")
    assert reg.backlog_stats("s-x")["drops"] == {}
    reg.clear_drop_counts("s-never-registered")  # 不抛


def test_backlog_stats_includes_queue_depth():
    """#227.3：积压快照含订阅者队列深度（spec「积压状态可查询」）。"""
    from finance_agent.stream_registry import SessionStream, StreamRegistry

    reg = StreamRegistry()
    sid = "s-backlog-1"
    stream = reg._streams.setdefault(sid, SessionStream())  # noqa: SLF001
    q: asyncio.Queue[dict | None] = asyncio.Queue(maxsize=4)
    stream.subscribers.append(q)
    q.put_nowait({"type": "thinking_token"})
    q.put_nowait({"type": "thinking_token"})
    stats = reg.backlog_stats(sid)
    assert stats["queue_depths"] == [2]
    assert stats["subscribers"] == 1
    assert stats["drops"] == {}


def test_all_backlog_stats_lists_active_streams():
    """#227.3：全会话积压快照（ops 出口数据源）。"""
    from finance_agent.stream_registry import SessionStream, StreamRegistry

    reg = StreamRegistry()
    reg._streams.setdefault("s-a", SessionStream())  # noqa: SLF001
    reg.record_drop("s-a", "thinking")
    reg.record_drop("s-a", "thinking")
    snapshot = reg.all_backlog_stats()
    assert set(snapshot.keys()) == {"s-a"}
    assert snapshot["s-a"]["drops"] == {"thinking": 2}


# ── 投递时延埋点（issue #328：publish→deliver 分布，量化 relay 成批延迟） ──


@pytest.mark.asyncio
async def test_delivery_latency_recorded_on_realtime_consume(tmp_path, monkeypatch):
    """实时队列投递记录 publish→deliver 时延：消费端停滞 → 快照可见高时延样本。"""
    _setup_db(tmp_path, monkeypatch)
    reg = stream_registry.StreamRegistry()
    sid = session_store.create_session(status="running")

    async def dummy_task():
        await asyncio.sleep(10)

    await reg.start(sid, dummy_task())
    gen = reg.subscribe(sid, after_seq=0)
    # 先驱动生成器完成重放（空）并进入实时 queue.get() 等待
    pending = asyncio.ensure_future(_next_event(gen, timeout=5))
    await asyncio.sleep(0.05)

    await reg.publish_many(sid, [{"type": "thinking_token", "token": "t1"}])
    # 模拟事件循环饥饿（#328 机制假设：高 CPU 争用下队列 waiter 调度停滞）：
    # 同步 sleep 阻塞整个循环，waiter 只能在释放后运行 → 投递时延 ≈150ms
    time.sleep(0.15)
    event = await pending
    assert event["type"] == "thinking_token"

    stats = reg.backlog_stats(sid)["delivery_latency_ms"]
    assert stats["n"] >= 1
    assert stats["max"] >= 100, stats
    assert stats["p50"] is not None and stats["p95"] is not None
    await gen.aclose()
    await reg.cancel(sid)


@pytest.mark.asyncio
async def test_delivery_latency_missing_pub_ts_not_counted(tmp_path, monkeypatch):
    """重放历史事件（无 publish 时点）不计入投递时延分布。"""
    _setup_db(tmp_path, monkeypatch)
    reg = stream_registry.StreamRegistry()
    sid = session_store.create_session(status="running")
    # 直接落 journal（模拟上一进程遗留事件），不经 publish
    session_store.append_session_event(sid, {"type": "thinking_token", "token": "old"})

    async def dummy_task():
        await asyncio.sleep(10)

    await reg.start(sid, dummy_task())
    gen = reg.subscribe(sid, after_seq=0)
    event = await _next_event(gen)
    assert event["type"] == "thinking_token"
    stats = reg.backlog_stats(sid)["delivery_latency_ms"]
    assert stats["n"] == 0
    await gen.aclose()
    await reg.cancel(sid)


@pytest.mark.asyncio
async def test_delivery_latency_warning_over_threshold(tmp_path, monkeypatch, caplog):
    """超阈投递延迟打 WARNING（#328 生产可观测入口），阈值可配置。"""
    monkeypatch.setattr(stream_registry, "DELIVERY_LATENCY_WARN_MS", 50.0)
    monkeypatch.setattr(stream_registry, "DELIVERY_WARN_MIN_INTERVAL_S", 0.0)
    _setup_db(tmp_path, monkeypatch)
    reg = stream_registry.StreamRegistry()
    sid = session_store.create_session(status="running")

    async def dummy_task():
        await asyncio.sleep(10)

    await reg.start(sid, dummy_task())
    gen = reg.subscribe(sid, after_seq=0)
    pending = asyncio.ensure_future(_next_event(gen, timeout=5))
    await asyncio.sleep(0.05)
    await reg.publish(sid, {"type": "thinking_token", "token": "t1"})
    time.sleep(0.12)  # 同步阻塞循环模拟投递停滞（同上一用例）
    await pending

    assert any("投递延迟" in str(r.getMessage()) for r in caplog.records)
    await gen.aclose()
    await reg.cancel(sid)
