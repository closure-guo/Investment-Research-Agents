"""会话状态流转唯一 owner（C5：refactor-session-lifecycle-owner）。

openspec session-streaming「状态流转单一 owner」：clarifying→running→终态的
流转 SHALL 经本模块执行，api/pipeline 层 SHALL NOT 直接调用 session_store
状态 setter 手写 transition（stream_registry 读状态做订阅恢复语义除外）。

- ``begin_analysis``：create→chat→anchor→clarify→running→session_created 一段式
  启动（screening_analyze / events_watchlist_analyze 双入口收编，消除 30 行逐行重复）
- ``mark_running / mark_complete / mark_failed / mark_interrupted``：终态流转统一入口
  （落库/事件/重试语义全部由 session_store 与 publish_terminal 承载，本模块不新增）
- ``update_clarify``：clarify 对话环（focus/代码解析回写）的 owner 透传

前端消费的事件流零变化；SSE 事件类型不增不减。
"""

from __future__ import annotations

from typing import Any

from finance_agent.session_store import (
    append_chat,
    append_session_event,
    create_session,
    set_pipeline_anchor,
    update_session_for_clarify,
    update_session_status,
)


def _now_iso() -> str:
    from datetime import datetime

    return datetime.now().isoformat()


def begin_analysis(
    *,
    display_name: str,
    user_message: str,
    stock_code: str = "",
    stock_name: str = "",
    session_type: str = "analysis",
) -> str:
    """分析启动一段式：create(clarifying)→chat→anchor→clarify(running)→session_created。

    双自动入口（screening_analyze / events_watchlist_analyze）的原逐行序列收编；
    落库语义与事件形状不变。返回 session_id。
    """
    session_id = create_session(
        stock_code="",
        stock_name="",
        display_name=display_name,
        status="clarifying",
        session_type=session_type,
    )
    append_chat(session_id, "user", user_message)
    set_pipeline_anchor(session_id)
    update_session_for_clarify(
        session_id,
        stock_code=stock_code,
        stock_name=stock_name,
        status="running",
    )
    append_session_event(
        session_id,
        {
            "type": "session_created",
            "session_id": session_id,
            "display_name": display_name,
            "timestamp": _now_iso(),
        },
    )
    return session_id


def mark_running(session_id: str) -> None:
    update_session_status(session_id, "running")


def mark_complete(session_id: str) -> None:
    update_session_status(session_id, "completed")


def mark_failed(session_id: str, failure_reason: str | None = None, **extra: Any) -> None:
    fields: dict[str, Any] = {"failure_reason": failure_reason, **extra}
    update_session_status(session_id, "failed", **fields)


def mark_interrupted(session_id: str) -> None:
    update_session_status(session_id, "interrupted")


def update_clarify(session_id: str, **fields: Any) -> None:
    """clarify 对话环（focus / 代码解析回写 / clarify→running 带 stock 元数据）透传。"""
    update_session_for_clarify(session_id, **fields)
