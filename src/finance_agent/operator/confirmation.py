"""写操作的两步确认令牌（进程内，单 worker 前提同 StreamRegistry）。

第一次调用只生成预览与 token；第二次调用必须同时满足：预览未过期、session/action/
params 一致、**当前用户消息**含明确确认词。token 可显式携带，也可省略——省略时按
session+action+params 匹配最近一条未过期预览（token 不随 chat_history 注入，跨 run
时模型通常拿不到它）。
"""

from __future__ import annotations

import secrets
import threading
import time
from dataclasses import dataclass
from typing import Any

TOKEN_TTL_SECONDS = 300.0
CONFIRM_WORDS = ("确认", "确定", "执行", "同意", "可以", "好的")


@dataclass
class PendingAction:
    token: str
    session_id: str
    action: str
    params: dict[str, Any]
    summary: str
    created_at: float
    expires_at: float


_PENDING: dict[str, PendingAction] = {}
_LOCK = threading.Lock()


def _prune(now: float) -> None:
    for token in [token for token, item in _PENDING.items() if item.expires_at <= now]:
        _PENDING.pop(token, None)


def issue_confirmation(
    *,
    session_id: str,
    action: str,
    params: dict[str, Any],
    summary: str,
    now: float | None = None,
) -> tuple[str, float]:
    """生成预览 token，返回 ``(token, ttl_seconds)``。"""
    current = time.monotonic() if now is None else now
    token = secrets.token_urlsafe(12)
    with _LOCK:
        _prune(current)
        _PENDING[token] = PendingAction(
            token=token,
            session_id=session_id,
            action=action,
            params=dict(params),
            summary=summary,
            created_at=current,
            expires_at=current + TOKEN_TTL_SECONDS,
        )
    return token, TOKEN_TTL_SECONDS


def _find_pending(
    *, session_id: str, token: str, action: str, params: dict[str, Any], now: float
) -> PendingAction | None:
    if token:
        item = _PENDING.get(token)
        if item is None:
            return None
        return item
    candidates = [
        item
        for item in _PENDING.values()
        if item.session_id == session_id
        and item.action == action
        and item.params == dict(params)
        and item.expires_at > now
    ]
    return max(candidates, key=lambda item: item.created_at) if candidates else None


def consume_confirmation(
    *,
    session_id: str,
    token: str,
    action: str,
    params: dict[str, Any],
    user_message: str,
    now: float | None = None,
) -> tuple[bool, str]:
    """校验并消费一条预览；成功返回 ``(True, "")``。"""
    current = time.monotonic() if now is None else now
    with _LOCK:
        _prune(current)
        pending = _find_pending(
            session_id=session_id, token=token, action=action, params=params, now=current
        )
        if pending is None:
            if token and token not in _PENDING:
                return False, "确认令牌不存在或已过期，请重新预览"
            return False, "未找到匹配的预览记录，请重新预览"
        if pending.expires_at <= current:
            _PENDING.pop(pending.token, None)
            return False, "确认令牌已过期，请重新预览"
        if pending.session_id != session_id:
            return False, "确认令牌不属于当前会话"
        if pending.action != action or pending.params != dict(params):
            return False, "确认参数与预览不一致，请重新预览"
        if not any(word in (user_message or "") for word in CONFIRM_WORDS):
            return False, "未检测到用户确认词；请让用户明确回复「确认执行」"
        _PENDING.pop(pending.token, None)
    return True, ""


def _reset_for_tests() -> None:
    """清空 pending 表（测试隔离用）。"""
    with _LOCK:
        _PENDING.clear()
