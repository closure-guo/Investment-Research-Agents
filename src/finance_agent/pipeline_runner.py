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
    apply_pipeline_node_complete,
    apply_pipeline_search_event,
    apply_pipeline_thinking_token,
    apply_pipeline_tool_event,
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
        # 持久化到 sessions.pipeline_timelines，写入节奏与 snapshot 一致（每相关事件一次）
        nodeTimelines: dict[str, list[dict]] = {}
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

        def _flush_pending() -> None:
            if pending and loop is not None:
                batch = pending[:]
                pending.clear()
                asyncio.run_coroutine_threadsafe(
                    stream_registry.publish_many(session_id, batch), loop
                ).result(timeout=5)

        # thinking 高频时序写节流（对齐 agent_factory._background_consume 的
        # TIMELINE_PERSIST_INTERVAL）：每 token 全量序列化写库既是 SQLite
        # 锁竞争源也是 O(n²) 写放大；节点边界/结束时仍即时冲刷（下方分支）。
        lastTimelinePersist = 0.0
        TIMELINE_PERSIST_INTERVAL = 0.5
        try:
            for sse_str in event_source():
                # 取消检查：cancel() 置位后在下一次事件迭代前终止
                if state is not None and state.cancel_event.is_set():
                    if loop is not None:
                        _flush_pending()
                        asyncio.run_coroutine_threadsafe(
                            stream_registry.publish(
                                session_id, {"type": "interrupted", "session_id": session_id}
                            ),
                            loop,
                        ).result(timeout=5)
                        terminalPublished = True
                    break
                # 超时检查：事件间检测，长时间无事件时标记 failed
                if time.time() - start_time > pipeline_timeout:
                    session_store.update_session_status(
                        session_id, "failed", failure_reason="管线执行超时"
                    )
                    if loop is not None:
                        _flush_pending()
                        asyncio.run_coroutine_threadsafe(
                            stream_registry.publish(
                                session_id,
                                {
                                    "type": "error",
                                    "session_id": session_id,
                                    "message": "管线执行超时",
                                },
                            ),
                            loop,
                        ).result(timeout=5)
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
                    nodeTimelines = apply_pipeline_thinking_token(
                        nodeTimelines, event.get("node") or "", event.get("token", "")
                    )
                    now_p = time.time()
                    if now_p - lastTimelinePersist >= TIMELINE_PERSIST_INTERVAL:
                        lastTimelinePersist = now_p
                        session_store.update_pipeline_timelines(session_id, nodeTimelines)
                elif eventType in ("search_start", "search_result", "search_error"):
                    nodeTimelines = apply_pipeline_search_event(nodeTimelines, currentNode, event)
                    session_store.update_pipeline_timelines(session_id, nodeTimelines)
                elif eventType in ("tool_call", "tool_result"):
                    nodeTimelines = apply_pipeline_tool_event(nodeTimelines, currentNode, event)
                    session_store.update_pipeline_timelines(session_id, nodeTimelines)
                elif eventType in ("node_start", "node_complete", "node_timing"):
                    if eventType == "node_start":
                        currentNode = event.get("node_id", "")
                    elif eventType == "node_complete":
                        nodeTimelines = apply_pipeline_node_complete(
                            nodeTimelines, event.get("node_id", "")
                        )
                        session_store.update_pipeline_timelines(session_id, nodeTimelines)
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
                asyncio.run_coroutine_threadsafe(
                    stream_registry.publish(
                        session_id,
                        {
                            "type": "error",
                            "session_id": session_id,
                            "message": f"{type(e).__name__}: {e}",
                        },
                    ),
                    loop,
                ).result(timeout=5)
                terminalPublished = True
        finally:
            # 节流可能跳过末尾 thinking chunk 的时序写，结束时补写完整时序
            # （对齐 agent_factory._background_consume 正常结束分支的 flush）
            if nodeTimelines:
                try:
                    session_store.update_pipeline_timelines(session_id, nodeTimelines)
                except Exception:  # noqa: S110 -- 补写失败不阻断终态发布
                    logger.warning("管线时序补写失败 session=%s", session_id)
            # 顺序不变量：先发布终态 done 并等其落库，再置 state.done（is_running=False）
            # ——反过来会让外部看到「已不在运行」时 journal 里还没有 done（CI 实测
            # flaky；回归用例 test_done_flag_not_set_before_terminal_event_published）
            if loop is not None and not terminalPublished:
                _flush_pending()
                asyncio.run_coroutine_threadsafe(
                    stream_registry.publish(session_id, {"type": "done", "session_id": session_id}),
                    loop,
                ).result(timeout=5)
            if state is not None:
                state.done = True

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
