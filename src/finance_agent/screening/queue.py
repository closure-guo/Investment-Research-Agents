"""批量深度分析队列（add-screening-pipeline T3；S3 逐字）。

进程内队列（单 worker 前提同 StreamRegistry）：并发上限默认 10
（设置中心可改，env ``SCREENING_ANALYZE_CONCURRENCY`` 为回退），超出部分排队；
**补位者才真正启动**（并发上限因而约束真实在跑的分析数，而非仅标签）：每个补位条目经
``starter`` 回调启动后台管线，随后由 ``session_store.get_session`` 状态轮询至终态
（``completed``/``failed``/``interrupted``）后自动补位下一个（FIFO）。

纪律：本模块只调度会话，不碰 SSE——每个 session 的进度经既有 journal/subscribe
通道（终态事件优先送达等韧性纪律不变）。

``snapshot()`` 返回队列全量条目 ``{symbol, session_id, state}``（``state`` 四态
``queued``/``running``/``done``/``failed``），供 API 与前端排队徽标消费。
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from finance_agent import session_store
from finance_agent.analysis_concurrency import DEFAULT_MAX_CONCURRENCY

_logger = logging.getLogger("finance_agent.screening.queue")

# 并发上限默认值由设置模块单一来源提供（delta add-analysis-concurrency-setting：
# 默认 10，设置中心可改；env SCREENING_ANALYZE_CONCURRENCY 为未保存时的回退）。

# 终态判定沿用 session status 语义（S3；其余状态视为仍在进行）
TERMINAL_STATES = frozenset({"completed", "failed", "interrupted"})

# 队列四态（S3 逐字）
STATE_QUEUED = "queued"
STATE_RUNNING = "running"
STATE_DONE = "done"
STATE_FAILED = "failed"
QUEUE_STATES = (STATE_QUEUED, STATE_RUNNING, STATE_DONE, STATE_FAILED)

# 会话状态轮询间隔（秒；测试可注入更小值）
DEFAULT_POLL_INTERVAL = 1.0


@dataclass
class _Entry:
    symbol: str
    session_id: str
    state: str = STATE_QUEUED
    task: asyncio.Task | None = None


class AnalyzeQueue:
    """进程内批量分析队列：入队排队（FIFO），补位时空位启动、终态后自动接续。"""

    def __init__(
        self,
        max_concurrency: int = DEFAULT_MAX_CONCURRENCY,
        *,
        poll_interval: float = DEFAULT_POLL_INTERVAL,
        starter: Callable[[str, str], None] | None = None,
    ) -> None:
        """``starter(symbol, session_id)``：补位启动回调（缺省 None = 只标记 running）。

        生产由 ``api`` 注入「启动后台管线」实现；``starter`` 抛异常 → 该条目记
        ``failed`` 并释放并发位（不让队列永久卡住）。
        """
        if (
            isinstance(max_concurrency, bool)
            or not isinstance(max_concurrency, int)
            or max_concurrency < 1
        ):
            raise ValueError(f"max_concurrency 必须是正整数: {max_concurrency!r}")
        if poll_interval <= 0:
            raise ValueError(f"poll_interval 必须为正数: {poll_interval!r}")
        self._max_concurrency = max_concurrency
        self._poll_interval = poll_interval
        self._starter = starter
        self._entries: list[_Entry] = []

    @property
    def max_concurrency(self) -> int:
        return self._max_concurrency

    def set_max_concurrency(self, value: int) -> None:
        """运行期调整并发上限：提高 → 立即补位；降低 → 只约束后续补位，绝不终止运行中。"""
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise ValueError(f"max_concurrency 必须是正整数: {value!r}")
        self._max_concurrency = value
        self._promote()

    def enqueue(self, symbol: str, session_id: str) -> None:
        """入队并尽量补位（补位者经 ``starter`` 真启动 + 建后台轮询任务）。

        无运行中事件循环（纯同步上下文）→ 保持 queued，等下次 enqueue 时再补位
        （API 端点恒在事件循环内调用本方法）。
        """
        self._entries.append(_Entry(symbol=symbol, session_id=session_id))
        self._promote()

    def snapshot(self) -> list[dict[str, str]]:
        """队列全量条目（入队序）：``{symbol, session_id, state}``。"""
        return [
            {"symbol": entry.symbol, "session_id": entry.session_id, "state": entry.state}
            for entry in self._entries
        ]

    def seed(self, entries: Sequence[dict]) -> int:
        """造数入口（TESTING seed 专用）：按给定状态逐条追加，不启动轮询/补位。

        仅用于 E2E 确定性构造「排队/运行/完成」徽标态；``state`` 须 ∈ 四态。
        """
        added = 0
        for entry in entries:
            state = str(entry.get("state") or "")
            if state not in QUEUE_STATES:
                raise ValueError(f"非法队列状态 {state!r}；允许: {list(QUEUE_STATES)}")
            self._entries.append(
                _Entry(
                    symbol=str(entry["symbol"]), session_id=str(entry["session_id"]), state=state
                )
            )
            added += 1
        return added

    # ── 内部：补位与终态轮询 ────────────────────────────────────────────────

    def _running_count(self) -> int:
        return sum(1 for entry in self._entries if entry.state == STATE_RUNNING)

    def _promote(self) -> None:
        """把队首 queued 条目补进空位（FIFO）：先经 ``starter`` 真启动，再建终态轮询任务。

        顺序：starter 失败 → 该条目记 ``failed`` 并继续补位（不占并发位、不阻断后续）。
        """
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return  # 无事件循环：保持 queued（端点恒有循环；纯同步调用不吞条目）
        for entry in self._entries:
            if entry.state != STATE_QUEUED:
                continue
            if self._running_count() >= self._max_concurrency:
                break
            if self._starter is not None:
                try:
                    self._starter(entry.symbol, entry.session_id)
                except Exception:  # noqa: BLE001 -- 启动失败不阻断队列（记 failed 释放位）
                    _logger.exception(
                        "队列补位启动失败（%s/%s），标记 failed 释放并发位",
                        entry.symbol,
                        entry.session_id,
                    )
                    entry.state = STATE_FAILED
                    continue
            entry.state = STATE_RUNNING
            entry.task = loop.create_task(self._watch(entry))

    async def _watch(self, entry: _Entry) -> None:
        """轮询会话状态至终态（completed → done；failed/interrupted → failed；会话消失 → failed）。"""
        while True:
            session = await asyncio.to_thread(session_store.get_session, entry.session_id)
            if session is None:
                # 会话被删除：无终态可达，立即释放并发位（不让队列永久占位）
                _logger.warning("队列会话不存在（%s），标记 failed 释放并发位", entry.session_id)
                entry.state = STATE_FAILED
                break
            status = session.get("status")
            if status in TERMINAL_STATES:
                entry.state = STATE_DONE if status == "completed" else STATE_FAILED
                break
            await asyncio.sleep(self._poll_interval)
        self._promote()
