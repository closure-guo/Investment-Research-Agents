"""StreamRegistry：per-session 生成任务与订阅者管理。

对应 delta spec: resume-stream-on-session-switch Task 2。

核心职责：
- 生成任务生命周期与 HTTP 连接解耦（asyncio.create_task 后台运行）
- 事件先落 session_events journal 再 fan-out 给订阅者
- subscribe 先重放 journal 再接续实时，支持 after_seq 断点续传
- single-flight：同一会话至多一个活跃任务
- cancel：显式取消走中断兜底路径
- 任务结束（完成/取消/异常）自动注销

约束：进程内内存结构，限定单 uvicorn worker（design.md D1）。
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import time
from collections import deque
from collections.abc import AsyncGenerator, Coroutine
from dataclasses import dataclass, field
from typing import Any

from finance_agent import session_store

_logger = logging.getLogger("finance_agent.stream_registry")

# 订阅者队列容量：满队列的慢订阅者被断开（design.md D2）
_SUBSCRIBER_QUEUE_MAX = 256

# ── 投递时延埋点（issue #328）──
# publish（事件进入 fan-out）→ deliver（订阅者生成器 yield 交给 HTTP 层）的时延
# 分布：深析阶段 UI 冻结族（#328）需要区分「事件没有产出」与「事件产出后投递
# 停滞」——本埋点量化后者。快照经 backlog_stats → `GET /api/v1/ops/stream-backlog`；
# 超阈单条打 WARNING（速率受限），使停滞在日志侧可见。
DELIVERY_LATENCY_WARN_MS = float(os.environ.get("STREAM_DELIVERY_WARN_MS", "3000"))
DELIVERY_WARN_MIN_INTERVAL_S = 10.0  # 每会话告警限流间隔
_LATENCY_SAMPLES_MAX = 256  # 每流保留最近样本数（滚动分位）
_PUB_TS_MAX = 512  # publish 时点表上限（seq → monotonic，插序裁剪）


@dataclass
class SessionStream:
    """单个会话的流状态：任务句柄、订阅者队列列表、最后 seq。"""

    task: asyncio.Task[Any] | None = None
    subscribers: list[asyncio.Queue[dict | None]] = field(default_factory=list)
    lastSeq: int = 0  # noqa: N815 - camelCase 符合项目命名规范（N806 已全局 ignore）
    terminalPublished: bool = False  # noqa: N815 - per-run 终态 CAS 标志，start() 时天然重置
    # 投递时延埋点（#328）：publish 时点表 + 最近投递时延样本（ms）+ 告警限流时点
    pubTs: dict[int, float] = field(default_factory=dict)  # noqa: N815 - 同 lastSeq 命名规范
    deliveryLatencyMs: deque[float] = field(  # noqa: N815 - 同 lastSeq 命名规范
        default_factory=lambda: deque(maxlen=_LATENCY_SAMPLES_MAX)
    )
    lastLatencyWarnAt: float = 0.0  # noqa: N815 - 同 lastSeq 命名规范


class StreamRegistry:
    """全局 registry：管理所有会话的生成任务与订阅者。"""

    def __init__(self) -> None:
        self._streams: dict[str, SessionStream] = {}
        # 明细丢弃计数（spec「发布积压可观测与背压」：无计数无日志的静默丢弃 MUST NOT 存在）
        self._drop_counts: dict[str, dict[str, int]] = {}

    async def start(self, session_id: str, coro: Coroutine[Any, Any, None]) -> bool:
        """启动后台生成任务。single-flight：已有活跃任务返回 False。

        对应 delta spec Task 2.2。
        """
        existing = self._streams.get(session_id)
        if existing and existing.task and not existing.task.done():
            return False
        stream = SessionStream()
        self._streams[session_id] = stream
        stream.task = asyncio.create_task(self._run_task(session_id, coro))
        return True

    async def _run_task(self, session_id: str, coro: Coroutine[Any, Any, None]) -> None:
        """包装用户协程：异常/取消时发终态事件，finally 中注销。"""
        try:
            await coro
            # 正常完成：发 done 终态事件
            await self.publish(session_id, {"type": "done"})
        except asyncio.CancelledError:
            # 被 cancel：同步写 journal + fan-out（不 await，避免再次被 cancel）
            self._publish_sync(session_id, {"type": "interrupted"})
            raise
        except Exception as exc:
            # 异常：发 error 终态事件
            _logger.exception("生成任务异常 session=%s", session_id)
            await self.publish(session_id, {"type": "error", "message": str(exc)})
        finally:
            self._notify_and_cleanup(session_id)

    def _notify_and_cleanup(self, session_id: str) -> None:
        """通知所有订阅者流结束（哨兵 None），然后从 registry 注销。"""
        stream = self._streams.get(session_id)
        if not stream:
            return
        for q in stream.subscribers:
            # 队列已满，订阅者会被自身消费逻辑断开
            with contextlib.suppress(asyncio.QueueFull):
                q.put_nowait(None)
        # drop 计数随运行终结一并清理（issue #227.1：per-session 计数随进程
        # 驻留是慢性泄漏）。清理后极少数 straggler record_drop（后台线程收尾
        # flush 直调）会经 setdefault 复活单条目，规模有界可接受。
        self._drop_counts.pop(session_id, None)
        # 注销：只删除当前 stream（避免删掉新 start 创建的）
        if self._streams.get(session_id) is stream:
            del self._streams[session_id]

    def _try_mark_terminal(self, session_id: str, event: dict) -> bool:
        """终态事件 per-run CAS：同轮内首个终态置位并放行，重复终态返回 False。

        作用域是单次运行（SessionStream 生命周期），不跨轮次：
        start() 每次新建 stream，标志天然为 False，因此同一会话多轮追问的
        终态事件各自独立下发。CAS 只用于抑制同一轮内的重复终态
        （生成逻辑显式发 done + _run_task 结束时自动发 done）。

        无 stream 时（如 Fast path 未注册 task）不做去重，直接放行。
        """
        if event.get("type") not in ("done", "interrupted", "error"):
            return True
        stream = self._streams.get(session_id)
        if stream is None:
            return True
        if stream.terminalPublished:
            return False
        stream.terminalPublished = True
        return True

    async def publish(self, session_id: str, event: dict) -> int:
        """先落 session_events journal，再 fan-out 到订阅者队列。

        对应 delta spec Task 2.3。返回分配的 seq。
        终态事件（done/interrupted/error）做 per-run CAS：同轮内已有终态则放弃（返回 0）。
        """
        if not self._try_mark_terminal(session_id, event):
            return 0  # 同轮内已有终态，放弃
        # 同步 SQLite 写入放在 executor 中避免阻塞事件循环（design.md D2）
        seq = await asyncio.to_thread(session_store.append_session_event, session_id, event)
        # 将 seq 注入事件 dict，使前端能追踪 lastSeq 实现断点续传
        event["seq"] = seq
        stream = self._streams.get(session_id)
        if stream:
            stream.lastSeq = seq
            self._stamp_pub_ts(stream, seq)
            self._fanout(session_id, stream, event)
        return seq

    async def publish_terminal(self, session_id: str, event: dict) -> int:
        """终态专用发布（spec「终态事件优先送达」）：CAS 去重 → journal 重试写 → fan-out。

        终态落库值得比普通写更固执：5 次 × 1s 重试（_get_db 层短重试之上的
        调用侧兜底）。重试耗尽 raise（显式失败，由调用方日志兜底），
        MUST NOT 静默吞掉造成「管线完成但终态缺失」。raise 前释放 per-run
        CAS：append 事务失败即回滚，journal 无半终态；若保住置位，_run_task
        自动 done/error 与 _publish_sync interrupted 兜底全被挡掉，反而坐实
        「管线完成但终态彻底缺失」。
        """
        if not self._try_mark_terminal(session_id, event):
            return 0
        last_exc: Exception | None = None
        for attempt in range(5):
            try:
                seq = await asyncio.to_thread(session_store.append_session_event, session_id, event)
                break
            except Exception as exc:  # noqa: BLE001 - journal 瞬断重试（终态关键路径）
                last_exc = exc
                _logger.warning(
                    "终态落库重试 attempt=%s session=%s err=%s", attempt + 1, session_id, exc
                )
                await asyncio.sleep(1.0)
        else:
            # 重试耗尽：先释放 per-run CAS 再 raise（final review C1，裁决已定）。
            # append 事务原子失败即回滚，journal 不存在半终态残留；保住置位会把
            # 上游兜底发布（_run_task 自动终态 / _publish_sync interrupted）全挡掉，
            # 终态彻底缺失。释放后兜底路径仍可重试发布。
            if event.get("type") in ("done", "interrupted", "error"):
                # 仅释放本调用真实获取过的 CAS（_try_mark_terminal 只对终态置位）
                stream = self._streams.get(session_id)
                if stream is not None:
                    stream.terminalPublished = False
            raise last_exc  # type: ignore[misc]
        event["seq"] = seq
        stream = self._streams.get(session_id)
        if stream:
            stream.lastSeq = seq
            self._stamp_pub_ts(stream, seq)
            self._fanout(session_id, stream, event)
        return seq

    async def publish_many(self, session_id: str, events: list[dict]) -> list[int]:
        """批量先落 journal 再按序 fan-out（高频 thinking_token 的限速根因修复）。

        与 publish 同契约：终态 per-run CAS 按序检查（批内后续终态被过滤），
        整批经 append_session_events 单事务落库（seq 连续），随后按原顺序
        逐个 fan-out——订阅者看到的顺序与 seq 顺序一致，且每个事件 fan-out
        前必然已持久化。空批次返回 []。
        """
        passed: list[dict] = []
        for ev in events:
            if self._try_mark_terminal(session_id, ev):
                passed.append(ev)
        if not passed:
            return []
        seqs = await asyncio.to_thread(session_store.append_session_events, session_id, passed)
        stream = self._streams.get(session_id)
        out: list[int] = []
        for ev, seq in zip(passed, seqs, strict=True):
            ev["seq"] = seq
            out.append(seq)
            if stream:
                stream.lastSeq = seq
                self._stamp_pub_ts(stream, seq)
                self._fanout(session_id, stream, ev)
        return out

    @staticmethod
    def _fanout(session_id: str, stream: SessionStream, event: dict) -> None:
        """fan-out：满队列的订阅者被断开。"""
        for q in list(stream.subscribers):
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                _logger.warning("慢订阅者断开 session=%s seq=%s", session_id, event.get("seq"))
                stream.subscribers.remove(q)
                with contextlib.suppress(asyncio.QueueFull):
                    q.put_nowait(None)

    @staticmethod
    def _stamp_pub_ts(stream: SessionStream, seq: int) -> None:
        """记录事件进入 fan-out 的时刻（seq → monotonic；插序裁剪保内存有界）。"""
        stream.pubTs[seq] = time.monotonic()
        while len(stream.pubTs) > _PUB_TS_MAX:
            stream.pubTs.pop(next(iter(stream.pubTs)))

    def _record_delivery_latency(self, session_id: str, stream: SessionStream, event: dict) -> None:
        """投递时延 = 订阅者生成器 yield 时刻 − publish 时刻（#328）。

        仅对经 `_stamp_pub_ts` 记录过时点的事件生效（本进程实时发布；重放历史
        事件无时点，天然跳过）。超阈单条打 WARNING（每会话限流
        `DELIVERY_WARN_MIN_INTERVAL_S` 秒；低负载下不产生任何日志）。
        """
        seq = event.get("seq")
        if not isinstance(seq, int):
            return
        pub = stream.pubTs.get(seq)
        if pub is None:
            return
        latency_ms = (time.monotonic() - pub) * 1000.0
        stream.deliveryLatencyMs.append(latency_ms)
        if latency_ms >= DELIVERY_LATENCY_WARN_MS:
            now = time.monotonic()
            if now - stream.lastLatencyWarnAt >= DELIVERY_WARN_MIN_INTERVAL_S:
                stream.lastLatencyWarnAt = now
                _logger.warning(
                    "投递延迟超阈 session=%s seq=%s latency_ms=%.0f threshold_ms=%.0f",
                    session_id,
                    seq,
                    latency_ms,
                    DELIVERY_LATENCY_WARN_MS,
                )

    @staticmethod
    def _latency_stats(stream: SessionStream) -> dict[str, float | int | None]:
        """滚动分位快照（样本按需排序；样本量 ≤256，开销可忽略）。"""
        samples = sorted(stream.deliveryLatencyMs)
        n = len(samples)
        if n == 0:
            return {"n": 0, "p50": None, "p95": None, "max": None}
        return {
            "n": n,
            "p50": samples[n // 2],
            "p95": samples[min(n - 1, int(n * 0.95))],
            "max": samples[-1],
        }

    def record_drop(self, session_id: str, kind: str) -> None:
        """明细事件丢弃计数。

        调用语境（issue #227.5）：事件循环内调用为精确语义（无锁，单线程
        序列化）；后台线程直调（如 PipelineRunner._flush_pending 的收尾
        flush）为尽力语义——CPython GIL 下 dict 单操作原子，计数可容忍
        轻微竞态，不追求精确一致。
        """
        per = self._drop_counts.setdefault(session_id, {})
        per[kind] = per.get(kind, 0) + 1

    def clear_drop_counts(self, session_id: str) -> None:
        """清除指定会话的丢弃计数（运行终结时调用，issue #227.1）。未注册会话静默。"""
        self._drop_counts.pop(session_id, None)

    def backlog_stats(self, session_id: str) -> dict:
        """积压可观测快照：丢弃计数 / 订阅者数 / 各订阅者队列深度 / 最后 seq。"""
        stream = self._streams.get(session_id)
        return {
            "drops": dict(self._drop_counts.get(session_id, {})),
            "subscribers": len(stream.subscribers) if stream else 0,
            "queue_depths": [q.qsize() for q in stream.subscribers] if stream else [],
            "last_seq": stream.lastSeq if stream else 0,
            "delivery_latency_ms": self._latency_stats(stream)
            if stream
            else {"n": 0, "p50": None, "p95": None, "max": None},
        }

    def all_backlog_stats(self) -> dict[str, dict]:
        """全部活跃会话的积压快照（ops 出口 `GET /api/v1/ops/stream-backlog` 数据源）。

        仅含 registry 持有 stream 的会话（含订阅期间的 fast path 临时 stream）；
        已终结会话的计数在运行收尾清理，不在此出现。
        """
        return {sid: self.backlog_stats(sid) for sid in list(self._streams)}

    def _publish_sync(self, session_id: str, event: dict) -> int:
        """同步版 publish：直接调用 session_store（不 await），用于 CancelledError 块。"""
        if not self._try_mark_terminal(session_id, event):
            return 0  # 同轮内已有终态，放弃
        try:
            seq = session_store.append_session_event(session_id, event)
        except Exception:
            _logger.exception("同步写入 journal 失败 session=%s", session_id)
            return 0
        # 将 seq 注入事件 dict，使前端能追踪 lastSeq
        event["seq"] = seq
        stream = self._streams.get(session_id)
        if stream:
            stream.lastSeq = seq
            self._stamp_pub_ts(stream, seq)
            for q in list(stream.subscribers):
                with contextlib.suppress(asyncio.QueueFull):
                    q.put_nowait(event)
        return seq

    async def subscribe(
        self,
        session_id: str,
        after_seq: int = 0,
        replay_user_messages: bool = False,
    ) -> AsyncGenerator[dict, None]:
        """订阅会话事件流：先注册队列，再重放 journal，最后接续实时事件。

        步骤顺序（先注册再读日志，消除重放缝合竞态）：
        1. 检查活跃任务并注册实时队列
        2. 读日志重放（注册队列后新事件不会丢失）
        3. 无活跃任务时下发终态事件
        4. 补漏（重放期间产生的新事件）
        5. 消费实时队列

        seq 去重消解重放与补漏的重叠段。
        对应 delta spec Task 2.4。

        重放/补漏不停于历史终态：多轮会话 journal 中段含上一轮的
        done/interrupted/error，若在此截断，刷新恢复（after_seq=0 全量重放）
        会丢失后续轮次的全部事件（管线 UI 消失 bug 根因）。仅实时尾部
        （步骤 5）在终态事件后结束——那才代表当前任务真正结束。

        replay_user_messages=True 且 after_seq=0（刷新全量重放）时，按
        chat_history 中 user 消息的 ts 在对应位置注入合成的 user_message
        事件：journal 不落用户消息，否则前端无法把 user 气泡插回原始
        交错位置（气泡错位 bug 根因）。实时路径保持默认 False，避免与
        前端提交时的乐观气泡重复。
        """
        # 1. 检查活跃任务并注册实时队列
        stream = self._streams.get(session_id)
        # 延迟导入避免循环依赖：Fast path 由 PipelineRunner 后台线程驱动，
        # 不在 registry._streams 注册 task，需额外检查 PipelineRunner.is_running
        pipelineActive = False
        try:
            from finance_agent.pipeline_runner import PipelineRunner

            pipelineActive = PipelineRunner.is_running(session_id)
        except ImportError:
            pass
        hasActive = (
            stream is not None and stream.task is not None and not stream.task.done()
        ) or pipelineActive

        queue: asyncio.Queue[dict | None] | None = None
        createdEphemeralStream = False
        if hasActive:
            if stream is None:
                # Fast path：PipelineRunner 后台线程驱动，registry 无 task。
                # 创建临时 stream 持有订阅者队列，使 publish 的 fan-out 能触达订阅者；
                # 订阅结束后自动清理，避免残留阻塞后续 start
                stream = SessionStream()
                self._streams[session_id] = stream
                createdEphemeralStream = True
            queue = asyncio.Queue(maxsize=_SUBSCRIBER_QUEUE_MAX)
            stream.subscribers.append(queue)

        try:
            # 2. 读日志重放（注册队列后新事件进入队列，不会丢失）
            events = await asyncio.to_thread(
                session_store.list_session_events, session_id, after_seq
            )
            # 全量重放时准备待注入的 user 消息（ts 升序）
            pendingUsers: list[tuple[str, str]] = []
            if replay_user_messages and after_seq == 0:
                pendingUsers = await asyncio.to_thread(
                    _load_pending_user_messages, session_id, events
                )
            replayedSeq = after_seq
            for row in events:
                try:
                    event = json.loads(row["event_json"])
                except (json.JSONDecodeError, TypeError):
                    continue
                # 注入 ts 早于当前事件的用户消息，恢复原始交错顺序
                createdAt = row.get("created_at") or ""
                while pendingUsers and pendingUsers[0][0] <= createdAt:
                    yield {"type": "user_message", "content": pendingUsers.pop(0)[1]}
                replayedSeq = row["seq"]
                event["seq"] = replayedSeq
                yield event
            # 尾部注入：已提交但尚无 journal 事件的用户消息（ts 晚于全部事件）
            while pendingUsers:
                yield {"type": "user_message", "content": pendingUsers.pop(0)[1]}

            # 3. 无活跃任务：下发终态事件
            if not hasActive:
                session = await asyncio.to_thread(session_store.get_session, session_id)
                if session and session["status"] == "interrupted":
                    yield {"type": "interrupted"}
                else:
                    yield {"type": "done"}
                return

            # 4. 补漏：重放期间产生的新事件（lastSeq > replayedSeq）。
            # 与重放同理：不停于历史终态，终态只结束实时尾部（步骤 5）。
            assert stream is not None  # noqa: S101  # hasActive 为 True 时 stream 必非 None
            if stream.lastSeq > replayedSeq:
                missed = await asyncio.to_thread(
                    session_store.list_session_events, session_id, replayedSeq
                )
                for row in missed:
                    try:
                        event = json.loads(row["event_json"])
                    except (json.JSONDecodeError, TypeError):
                        continue
                    event["seq"] = row["seq"]
                    self._record_delivery_latency(session_id, stream, event)
                    yield event

            # 5. 消费实时队列
            if queue is not None:
                while True:
                    event = await queue.get()
                    if event is None:
                        # 哨兵：任务结束通知
                        break
                    self._record_delivery_latency(session_id, stream, event)
                    yield event
                    if event.get("type") in ("done", "interrupted", "error"):
                        break
        finally:
            # 清理订阅者队列与临时 stream（Fast path 创建的临时 stream 无 task，
            # 需在订阅结束时移除，避免残留阻塞后续 start）
            if queue is not None and stream is not None and queue in stream.subscribers:
                stream.subscribers.remove(queue)
            if (
                createdEphemeralStream
                and stream is not None
                and not stream.subscribers
                and self._streams.get(session_id) is stream
            ):
                del self._streams[session_id]

    async def cancel(self, session_id: str) -> bool:
        """取消会话的活跃任务。无活跃任务返回 False。

        对应 delta spec Task 2.5。取消走中断兜底路径（_run_task 的 CancelledError 分支）。
        """
        stream = self._streams.get(session_id)
        if not stream or not stream.task or stream.task.done():
            return False
        stream.task.cancel()
        # 轮询等待任务结束（不直接 await task，避免 CancelledError 传播到当前协程）
        for _ in range(50):  # 最多等 5 秒
            if stream.task.done():
                break
            await asyncio.sleep(0.1)
        return True

    def is_active(self, session_id: str) -> bool:
        """检查会话是否有活跃生成任务。"""
        stream = self._streams.get(session_id)
        return stream is not None and stream.task is not None and not stream.task.done()


def _load_pending_user_messages(session_id: str, events: list[dict]) -> list[tuple[str, str]]:
    """读取 chat_history 中的 user 消息（ts, content），供全量重放注入。

    跳过已以 user_message 事件落在 journal 中的前 N 条（防御：若未来改为
    提交时直接落 journal，避免重复注入）。ts 缺失按空串处理（排到最前）。
    """
    session = session_store.get_session(session_id)
    if not session:
        return []
    raw = session.get("chat_history")
    # get_session 可能已反序列化为 list（也可能仍是 JSON 字符串），两种都兼容
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return []
    history = raw if isinstance(raw, list) else []
    users = [
        (str(h.get("ts") or ""), str(h.get("content") or ""))
        for h in history
        if isinstance(h, dict) and h.get("role") == "user"
    ]
    journaled = 0
    for row in events:
        try:
            if json.loads(row["event_json"]).get("type") == "user_message":
                journaled += 1
        except (json.JSONDecodeError, TypeError):
            continue
    return users[journaled:]


# 全局单例
registry = StreamRegistry()
