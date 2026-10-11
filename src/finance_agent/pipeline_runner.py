"""会话级管线后台执行器。

graph.stream 是同步生成器，用独立线程执行，与 SSE 订阅解耦：
客户端断开仅停止订阅，后台线程继续推进管线。
进度快照（layerTree JSON）在每节点事件时持久化到 sessions.pipeline_snapshot，
SSE 端通过 get_events 轮询拉取累积事件。
"""

# 项目规范使用 camelCase 变量名（如 nodeTimelines），与 pep8-naming 冲突，统一豁免
# ruff: noqa: N806

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import threading
import time
from collections.abc import Callable, Generator
from typing import Any

from finance_agent import session_store
from finance_agent.stream_registry import registry as stream_registry
from finance_agent.timeline_builder import (
    NodeTimelineAccumulator,
    timeline_persist_interval,
)

# 管线全局超时默认预算（raise-pipeline-timeout-default delta）：
# 2400s（40 分钟）覆盖合法 R1+R2 双轮最坏包络——LLM 端点（方舟 GLM-5.3）
# 单节点生成耗时实测 3.7~15.7 分钟（2026-08-26 天力锂能 fundamental 940s），
# 四分析师并行 R1 单轮可达 ~16 分钟。600s 默认会把「合理但偏慢」的分析
# 误判为超时。部署可用 PIPELINE_TIMEOUT_SECONDS 环境变量覆盖。
PIPELINE_TIMEOUT_DEFAULT_SECONDS = 2400

logger = logging.getLogger(__name__)

_SSE_DATA_RE = re.compile(r"^data: (.*)$", re.MULTILINE)

# 与前端 pipelineTree.ts LAYER_TREE_CONFIG（42-102 行）逐项对齐的静态配置。
# 快照 layerTree 结构即前端 PipelineTimeline 渲染结构：恢复时快照直接替换
# 前端树，因此 layer id/label/children 必须与前端完全一致（6 层 25 节点）。
LAYER_TREE_CONFIG: list[dict] = [
    {
        "id": "prep",
        "label": "PREP",
        "children": [
            {"nodeId": "check_cache", "label": "数据准备"},
            {"nodeId": "fetch_data", "label": "获取数据"},
            {"nodeId": "validate_financials", "label": "勾稽校验"},
            {"nodeId": "compute_metrics", "label": "指标计算"},
            {"nodeId": "verify_citations", "label": "引用校验"},
        ],
    },
    {
        "id": "layer1",
        "label": "Layer I",
        "children": [
            {"nodeId": "fundamental_analyst", "label": "基本面"},
            {"nodeId": "technical_analyst", "label": "技术面"},
            {"nodeId": "macro_analyst", "label": "宏观"},
            {"nodeId": "sentiment_analyst", "label": "舆情"},
        ],
    },
    {
        "id": "layer2",
        "label": "Layer II",
        "children": [
            {"nodeId": "bull_r1", "label": "看多 R1"},
            {"nodeId": "bear_r1", "label": "看空 R1"},
            {"nodeId": "bull_r2", "label": "看多 R2"},
            {"nodeId": "bear_r2", "label": "看空 R2"},
            {"nodeId": "research_manager", "label": "研究结论"},
        ],
    },
    {
        "id": "trader",
        "label": "Trader",
        "children": [{"nodeId": "trader", "label": "交易决策"}],
    },
    {
        "id": "risk",
        "label": "Risk",
        "children": [
            {"nodeId": "aggressive_r1", "label": "激进风控 R1"},
            {"nodeId": "conservative_r1", "label": "保守风控 R1"},
            {"nodeId": "neutral_r1", "label": "中性风控 R1"},
            {"nodeId": "aggressive_r2", "label": "激进风控 R2"},
            {"nodeId": "conservative_r2", "label": "保守风控 R2"},
            {"nodeId": "neutral_r2", "label": "中性风控 R2"},
            {"nodeId": "risk_judge", "label": "风控裁决"},
        ],
    },
    {
        "id": "fund",
        "label": "Fund",
        "children": [
            {"nodeId": "fund_manager", "label": "基金经理"},
            {"nodeId": "generate_report", "label": "报告生成"},
            {"nodeId": "generate_file", "label": "文件导出"},
        ],
    },
]

# 节点 -> layer 映射，模块级一次构建（与前端 NODE_INDEX 等价）
_NODE_TO_LAYER: dict[str, str] = {
    child["nodeId"]: layer["id"] for layer in LAYER_TREE_CONFIG for child in layer["children"]
}


# ── layerTree 快照维护（与前端 pipelineTree.applyNodeEvent 语义等价）──


def build_layer_tree() -> list[dict]:
    """构建初始 layerTree（与前端 buildLayerTree 等价，status 全 pending）。"""
    return [
        {
            "id": layer["id"],
            "label": layer["label"],
            "status": "pending",
            "children": [
                {"nodeId": c["nodeId"], "label": c["label"], "status": "pending"}
                for c in layer["children"]
            ],
        }
        for layer in LAYER_TREE_CONFIG
    ]


def apply_node_event(tree: list[dict], event: dict, now_ms: int) -> list[dict]:
    """应用 node_start/node_complete/node_timing 事件（与前端语义等价，不可变更新）。"""
    node_to_layer = _NODE_TO_LAYER
    layer_id = node_to_layer.get(event.get("node_id", ""))
    if not layer_id:
        return tree

    new_tree = []
    for layer in tree:
        if layer["id"] != layer_id:
            new_tree.append(layer)
            continue
        children = []
        for child in layer["children"]:
            if child["nodeId"] != event["node_id"]:
                children.append(child)
                continue
            etype = event["type"]
            # node_timing：只更新时间戳/耗时，不改状态（不受 completed 不回退限制）
            if etype == "node_timing":
                started = event.get("server_start_ts", child.get("startedAt"))
                duration = event.get("server_duration_ms")
                if (
                    duration is None
                    and event.get("server_end_ts") is not None
                    and started is not None
                ):
                    duration = max(0, event["server_end_ts"] - started)
                else:
                    duration = duration if duration is not None else child.get("durationMs")
                children.append(
                    {
                        **child,
                        "startedAt": started,
                        "completedAt": event.get("server_end_ts", child.get("completedAt")),
                        "durationMs": duration,
                    }
                )
                continue
            # 状态单调：completed 不回退
            if child.get("status") == "completed":
                children.append(child)
                continue
            if etype == "node_start":
                started = event.get("server_start_ts", child.get("startedAt", now_ms))
                children.append({**child, "status": "running", "startedAt": started})
            else:  # node_complete
                started = event.get("server_start_ts", child.get("startedAt", now_ms))
                children.append(
                    {
                        **child,
                        "status": "completed",
                        "startedAt": started,
                        "completedAt": now_ms,
                        "durationMs": max(0, now_ms - started),
                        "output": event.get("output", child.get("output")),
                    }
                )
        # 推导 layer 状态：任一 running → running；全部 completed → completed
        any_running = any(c["status"] == "running" for c in children)
        all_completed = len(children) > 0 and all(c["status"] == "completed" for c in children)
        status = layer["status"]
        if status != "completed":
            if all_completed:
                status = "completed"
            elif any_running:
                status = "running"
        layer_started = layer.get("startedAt")
        if layer_started is None and (any_running or all_completed):
            layer_started = now_ms
        layer_completed = layer.get("completedAt")
        if status == "completed" and layer_completed is None:
            layer_completed = now_ms
        duration = layer.get("durationMs")
        if status == "completed" and layer_started is not None and layer_completed is not None:
            duration = max(0, layer_completed - layer_started)
        new_tree.append(
            {
                **layer,
                "status": status,
                "children": children,
                "startedAt": layer_started,
                "completedAt": layer_completed,
                "durationMs": duration,
            }
        )
    return new_tree


def _current_node(tree: list[dict]) -> str:
    for layer in tree:
        for child in layer["children"]:
            if child["status"] == "running":
                return child["nodeId"]
    return ""


def _progress(tree: list[dict]) -> float:
    total = sum(len(layer["children"]) for layer in tree)
    if total == 0:
        return 0.0
    done = sum(1 for layer in tree for c in layer["children"] if c["status"] == "completed")
    return done / total


# pending 明细缓冲上限（add-event-delivery-resilience Task 4）：flush 前防御性
# 裁剪——异常路径下缓冲无限膨胀会拖垮内存并推后终态时延；thinking 明细是可丢的
# 进度装饰，丢最旧保最新，终态事件不受影响。
PENDING_TOKEN_MAX = 512


def trim_pending_overflow(
    pending: list[dict],
    session_id: str,
    record_drop: Callable[[str, str], None],
    dropped_total: list[int],
) -> int:
    """flush 前 pending 超限防御：丢最旧至上限并计数，返回本次丢弃条数。

    纯函数（record_drop 注入，便于单测）。丢弃计入 registry.backlog_stats 的
    pending_overflow 桶；日志首条 + 每 100 条节流（对齐 agent_factory._put_event）。
    dropped_total 为跨调用累计单元格（闭包可变状态用单元素 list 承载）。
    """
    overflow = len(pending) - PENDING_TOKEN_MAX
    if overflow <= 0:
        return 0
    del pending[:overflow]
    record_drop(session_id, "pending_overflow")
    prev = dropped_total[0]
    dropped_total[0] = prev + overflow
    if prev == 0 or dropped_total[0] // 100 != prev // 100:
        logger.warning(
            "pending 明细溢出丢最旧 session=%s 本次=%s 累计=%s",
            session_id,
            overflow,
            dropped_total[0],
        )
    return overflow


# ── 后台执行器 ──


class _RunState:
    def __init__(self, thread: threading.Thread):
        self.thread = thread
        self.events: list[str] = []
        self.lock = threading.Lock()
        self.done = False
        # 取消标志：cancel() 置位后 _run 在下一次事件迭代前检测并终止
        self.cancel_event = threading.Event()


class PipelineRunner:
    """管线后台执行：事件累积 + 快照持久化。幂等 start。"""

    _running: dict[str, _RunState] = {}
    _guard = threading.Lock()

    @classmethod
    def is_running(cls, session_id: str) -> bool:
        with cls._guard:
            state = cls._running.get(session_id)
            return state is not None and not state.done

    @classmethod
    def start(
        cls,
        session_id: str,
        event_source: Callable[[], Generator[str, None, None]],
        initial_snapshot: dict,
        loop: Any | None = None,
    ) -> None:
        """启动后台管线线程。已在跑则幂等返回。

        loop 非 None 时走 Fast path 桥接：事件经 stream_registry.publish 写入
        journal，终态由 _run 的 finally 发布，使恢复端点能重放 Fast path 事件；
        loop 为 None 时走原内存队列累积模式（get_events 消费式拉取）。
        """
        with cls._guard:
            if session_id in cls._running and not cls._running[session_id].done:
                return
            thread = threading.Thread(
                target=cls._run,
                args=(session_id, event_source, initial_snapshot, loop),
                daemon=True,
            )
            cls._running[session_id] = _RunState(thread)
            thread.start()

    @classmethod
    def cancel(cls, session_id: str) -> bool:
        """取消运行中的管线任务。设置取消标志并等待线程结束。无运行中任务返回 False。"""
        with cls._guard:
            state = cls._running.get(session_id)
            if not state or state.done:
                return False
            state.cancel_event.set()
        state.thread.join(timeout=5)
        return True

    @classmethod
    def get_events(cls, session_id: str) -> list[str]:
        """取走累积的 SSE 事件（消费式）。done 且取空后清理条目。"""
        with cls._guard:
            state = cls._running.get(session_id)
            if state is None:
                return []
            with state.lock:
                events, state.events = state.events, []
            # 不变量：done 置位后后台线程不再 append 事件，
            # 故 swap 后 events 必为空列表，取空即可安全清理条目
            if state.done:
                cls._running.pop(session_id, None)
            return events

    @classmethod
    def _run(
        cls,
        session_id: str,
        event_source: Callable[[], Generator[str, None, None]],
        snapshot: dict,
        loop: Any | None = None,
    ) -> None:
        state = cls._running.get(session_id)
        tree = snapshot.get("layerTree") or build_layer_tree()
        # 管线节点时序（persist-full-session-timeline）：thinking_token 按 node 分组
        # 持久化到 sessions.pipeline_timelines，写入节奏与 snapshot 一致（每相关事件一次）。
        # fix-timeline-write-amplification：可变累加器 O(1) 摊销（纯函数逐 token 是
        # O(n²)，incident 039 放大器）；语义等价由 tests/test_timeline_accumulator.py 钉死。
        timelineAcc = NodeTimelineAccumulator()
        # 写放大治理：序列化一次并测字节数（自适应中间写间隔输入，design D2）
        _lastPersistPayload = 0  # 首次写入前 0 → 下限 0.5s（现状冷启动行为）

        def _dump_and_persist_timelines() -> int:
            nonlocal _lastPersistPayload
            payload = json.dumps(timelineAcc.materialize(), ensure_ascii=False)
            session_store.update_pipeline_timelines_json(session_id, payload)
            _lastPersistPayload = len(payload.encode("utf-8"))
            return _lastPersistPayload

        # search/tool 事件不带 node 字段，归入「当前运行节点」：
        # node_start 置位、node_complete 清空（用户决策 2026-07-30）
        currentNode = ""
        # 管线全局超时（环境变量可配置，默认 2400s = 40 分钟）
        pipeline_timeout = float(
            os.environ.get("PIPELINE_TIMEOUT_SECONDS", str(PIPELINE_TIMEOUT_DEFAULT_SECONDS))
        )
        start_time = time.time()
        # 终态是否已发布（cancel/超时/异常时为 True，finally 不再发 done）
        terminalPublished = False
        # thinking_token 批量落库（假卡死根因修复）：单条一次 SQLite 事务
        # （fsync 主导）会把消费端限速到事件积压、终态事件迟到
        # （601700 深研会话永远 running）。缓冲后经 publish_many 单事务
        # 批量写入；非 thinking 事件/心跳/缓冲满时冲刷，保持 seq 顺序。
        pending: list[dict] = []
        TOKEN_BATCH_MAX = 32
        # pending 溢出丢弃累计（trim_pending_overflow 的跨调用单元格）
        pendingDropped: list[int] = [0]

        def _flush_pending() -> None:
            trim_pending_overflow(pending, session_id, stream_registry.record_drop, pendingDropped)
            if pending and loop is not None:
                batch = pending[:]
                pending.clear()
                asyncio.run_coroutine_threadsafe(
                    stream_registry.publish_many(session_id, batch), loop
                ).result(timeout=5)

        def _publish_terminal(ev: dict) -> None:
            """终态发布统一出口（后台线程侧）：走 publish_terminal 获得重试兜底。

            - CAS 拒绝（同轮已有终态，返回 0）静默容忍，与原 publish 语义一致；
            - 重试耗尽的 raise 只记日志不外抛：finally 中外抛会跳过 state.done
              置位，is_running 永真、会话永远无法重开（publish_terminal 契约的
              「调用方日志兜底」）；
            - result 超时须覆盖最坏叠加窗口：publish_terminal 5×1s 重试 +
              _get_db 6 次连接重试（≈3.1s 退避）+ busy_timeout 15s 单次写等待，
              故 90s（普通 publish 单发 5s）。仅终态路径一次性行，不构成
              常规写放大。
            """
            if loop is None:  # 仅 loop 桥接模式调用（调用侧均已守卫）；防御式收口
                return
            try:
                asyncio.run_coroutine_threadsafe(
                    stream_registry.publish_terminal(session_id, ev), loop
                ).result(timeout=90)
            except Exception:
                logger.exception("终态发布失败 session=%s type=%s", session_id, ev.get("type"))

        # thinking 高频时序写节流：自适应间隔（fix-timeline-write-amplification，
        # 对齐 agent_factory._background_consume）——间隔随上次序列化字节数伸缩，
        # 写带宽 ≤256KB/s 有界；每 token 全量序列化写库既是 SQLite 锁竞争源也是
        # O(n²) 写放大；节点边界/结束时仍即时冲刷（下方分支）。
        lastTimelinePersist = 0.0
        try:
            for sse_str in event_source():
                # 取消检查：cancel() 置位后在下一次事件迭代前终止
                if state is not None and state.cancel_event.is_set():
                    if loop is not None:
                        _flush_pending()
                        _publish_terminal({"type": "interrupted", "session_id": session_id})
                        terminalPublished = True
                    break
                # 超时检查：事件间检测，长时间无事件时标记 failed
                if time.time() - start_time > pipeline_timeout:
                    session_store.update_session_status(
                        session_id, "failed", failure_reason="管线执行超时"
                    )
                    if loop is not None:
                        _flush_pending()
                        _publish_terminal(
                            {
                                "type": "error",
                                "session_id": session_id,
                                "message": "管线执行超时",
                            }
                        )
                        terminalPublished = True
                    break
                event = cls._parse_event(sse_str)
                # SSE 心跳注释：借机冲刷批量缓冲，空闲期 token 不滞留 journal
                if event is None and loop is not None:
                    _flush_pending()
                # 事件分发：loop 存在时经 publish 桥接到 journal（先落库再 fan-out），
                # 使恢复端点能重放 Fast path 事件；否则累积到内存队列（get_events 消费式拉取）
                if loop is not None:
                    if event is not None:
                        if event.get("type") == "thinking_token":
                            pending.append(event)
                            if len(pending) >= TOKEN_BATCH_MAX:
                                _flush_pending()
                        elif event.get("type") == "report_ready":
                            # report_ready 视同终态关键路径（承载报告产物）：走
                            # publish_terminal 获得重试兜底（非 CAS 集合，必放行）
                            _flush_pending()
                            _publish_terminal(event)
                        else:
                            _flush_pending()
                            asyncio.run_coroutine_threadsafe(
                                stream_registry.publish(session_id, event), loop
                            ).result(timeout=5)
                elif state is not None:
                    with state.lock:
                        state.events.append(sse_str)
                if event is None:
                    continue
                eventType = event.get("type")
                # 管线模式 thinking_token：node 字段缺失/空串归入 '' 键（与前端一致）
                if eventType == "thinking_token":
                    timelineAcc.add_thinking_token(event.get("node") or "", event.get("token", ""))
                    now_p = time.time()
                    if now_p - lastTimelinePersist >= timeline_persist_interval(
                        _lastPersistPayload
                    ):
                        lastTimelinePersist = now_p
                        _dump_and_persist_timelines()
                elif eventType in ("search_start", "search_result", "search_error"):
                    timelineAcc.apply_search_event(currentNode, event)
                    _dump_and_persist_timelines()
                elif eventType in ("tool_call", "tool_result"):
                    timelineAcc.apply_tool_event(currentNode, event)
                    _dump_and_persist_timelines()
                elif eventType in ("node_start", "node_complete", "node_timing"):
                    if eventType == "node_start":
                        currentNode = event.get("node_id", "")
                    elif eventType == "node_complete":
                        timelineAcc.apply_node_complete(event.get("node_id", ""))
                        # 节点边界即时冲刷（spec 同节奏锚点，不经自适应间隔）
                        _dump_and_persist_timelines()
                        # 该节点完成即非当前运行节点；间隙事件归入 '' 键
                        if currentNode == event.get("node_id", ""):
                            currentNode = ""
                    now_ms = int(time.time() * 1000)
                    tree = apply_node_event(tree, event, now_ms)
                    snapshot = {
                        # layerTree 序列化为内嵌 JSON 字符串，对齐前端 deserializeLayerTree 契约
                        "layerTree": json.dumps(tree, ensure_ascii=False),
                        "currentNodeId": _current_node(tree),
                        "progress": _progress(tree),
                        "updatedAt": now_ms,
                        # 管线启动时间戳（毫秒）：前端刷新重建用作「已用时」计时起点
                        "pipeline_start_ts": int(start_time * 1000),
                    }
                    session_store.update_pipeline_snapshot(session_id, snapshot)
        except Exception as e:
            logger.exception("后台管线执行异常 session=%s", session_id)
            session_store.update_session_status(
                session_id, "failed", failure_reason=f"{type(e).__name__}: {e}"
            )
            if loop is not None:
                _flush_pending()
                _publish_terminal(
                    {
                        "type": "error",
                        "session_id": session_id,
                        "message": f"{type(e).__name__}: {e}",
                    }
                )
                terminalPublished = True
        finally:
            # 节流可能跳过末尾 thinking chunk 的时序写，结束时补写完整时序
            # （对齐 agent_factory._background_consume 正常结束分支的 flush）
            if timelineAcc:
                try:
                    _dump_and_persist_timelines()
                except Exception:  # noqa: S110 -- 补写失败不阻断终态发布
                    logger.warning("管线时序补写失败 session=%s", session_id)
            # 顺序不变量：先发布终态 done 并等其落库，再置 state.done（is_running=False）
            # ——反过来会让外部看到「已不在运行」时 journal 里还没有 done（CI 实测
            # flaky；回归用例 test_done_flag_not_set_before_terminal_event_published）
            if loop is not None and not terminalPublished:
                _flush_pending()
                _publish_terminal({"type": "done", "session_id": session_id})
            if state is not None:
                state.done = True
            # drop 计数随运行终结清理（issue #227.1：fast path 无 registry task，
            # 不走 _notify_and_cleanup，须在此显式清）；收尾 flush 的丢弃已计数
            # 并日志，清除不影响事后审计
            stream_registry.clear_drop_counts(session_id)

    @staticmethod
    def _parse_event(sse_str: str) -> dict | None:
        match = _SSE_DATA_RE.search(sse_str)
        if not match:
            return None
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            return None

    @classmethod
    def mark_swept_failed(cls, statuses: tuple[str, ...] = ("running",)) -> int:
        """启动清扫：悬挂 running 会话置 failed（后端重启后 _running 已丢失）。"""
        conn = session_store._get_db()  # noqa: SLF001 - 同模块内部复用连接工厂
        cur = conn.execute(
            f"UPDATE sessions SET status = 'failed', failure_reason = '后端重启，管线无法恢复' "  # noqa: S608
            f"WHERE status IN ({','.join('?' * len(statuses))})",
            tuple(statuses),
        )
        conn.commit()
        conn.close()
        return cur.rowcount
