"""会话 failure_reason 文案格式化（issue #490）。

空 message 异常（典型：`asyncio.wait_for` / `run_coroutine_threadsafe(...).result(timeout=)`
超时抛出的内置 `TimeoutError()`）经 `f"{type(e).__name__}: {e}"` 落库后只剩
`"TimeoutError: "`——一个类型名加空尾巴，运维无法判断是哪一层在等什么。
统一入口：message 为空时保留类型名并显式标注「无错误消息」，可附带等待点上下文。
"""

from __future__ import annotations


def failure_reason_text(exc: BaseException, *, context: str | None = None) -> str:
    """返回可读的会话失败文案：`Type: message`；空 message 给显式占位（含可选上下文）。"""
    name = type(exc).__name__
    message = str(exc).strip()
    if message:
        return f"{name}: {message}"
    suffix = f"，{context}" if context else ""
    return f"{name}（无错误消息{suffix}）"
