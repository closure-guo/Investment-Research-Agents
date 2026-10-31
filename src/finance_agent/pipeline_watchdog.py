"""管线停滞看门狗共享语义（update-pipeline-stall-watchdog，issue #521）。

两执行路径——agent_factory `_background_consume`（asyncio 消费）与
pipeline_runner fast path（阻塞生成器 + 独立看门狗线程）——共用的阈值读取、
等待计算、过期分类与 failure_reason 文案，保证 spec pipeline-events
「双执行路径语义一致」。

与总墙钟（PIPELINE_TIMEOUT_SECONDS，语义不变）互补：
- 停滞看门狗抓「零事件挂死」（provider 断流后 SSL 读永久阻塞，
  10-28 opencode 宕机 16 管线吊死的形态）；
- 总预算抓「有事件进度但超出总时长」（601700 类 runaway 生成）。

阈值证据（2026-10-31 生产库实测，60 个完成会话 / 43 万个管线内事件间隔）：
p99.99=17s、最大 124.4s，默认 600s ≈ 5× 余量。
"""

from __future__ import annotations

import os
import threading

# 与总墙钟文案（"管线执行超时"）可区分（spec：先触发者生效、文案 MUST 可区分）
TOTAL_TIMEOUT_FAILURE_REASON = "管线执行超时"
STALL_TIMEOUT_DEFAULT_SECONDS = 600.0

# 迟到完成守卫登记表：看门狗判停滞的会话在管线线程真正解除阻塞前，
# 图完成路径（_run_graph_streaming）不得把状态改回 completed。
_STALLED: set[str] = set()
_STALLED_LOCK = threading.Lock()


class PipelineStalledError(TimeoutError):
    """管线停滞（零事件超停滞阈值）——区别于总预算超时（TimeoutError 原语义）。

    继承 TimeoutError 使既有「超时=协作终止生产端」处置结构可复用；
    agent_factory 的异常分支按先 PipelineStalledError 后 TimeoutError 排序分流。
    """


def stall_timeout_seconds() -> float:
    """停滞阈值；调用期读 env（E2E 注入纪律，导入期冻结会让注入失效）。"""
    return float(
        os.environ.get("PIPELINE_STALL_TIMEOUT_SECONDS", str(STALL_TIMEOUT_DEFAULT_SECONDS))
    )


def stall_failure_reason(stall_seconds: float, last_event_type: str, last_node: str = "") -> str:
    """停滞 failure_reason：携带停滞时长与最后事件类型/节点（issue #521 要求）。"""
    last_desc = last_event_type or "无"
    node_desc = f"，节点: {last_node}" if last_node else ""
    return (
        f"管线停滞：{stall_seconds:.0f}s 无任何事件"
        f"（最后事件: {last_desc}{node_desc}），疑似 provider 断流或执行层挂死"
    )


def mark_stalled(session_id: str) -> None:
    """登记停滞终态（迟到完成守卫依据）。"""
    with _STALLED_LOCK:
        _STALLED.add(session_id)


def clear_stalled(session_id: str) -> None:
    """清除停滞标记（PipelineRunner.start 重跑时调用）。"""
    with _STALLED_LOCK:
        _STALLED.discard(session_id)


def is_stalled(session_id: str) -> bool:
    with _STALLED_LOCK:
        return session_id in _STALLED


def completion_status_or_skip(session_id: str) -> str | None:
    """迟到完成守卫：停滞终态已发布 → 返回 None（跳过 completed 回写），
    否则返回 "completed"。图完成路径据此决定是否写完成状态——终态以先到者为准。"""
    return None if is_stalled(session_id) else "completed"
