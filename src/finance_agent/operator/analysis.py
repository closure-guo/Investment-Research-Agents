"""快速模式分析派单工具（P1）。"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Awaitable, Callable
from typing import Any

from finance_agent.llm.config import LLMConfig

_logger = logging.getLogger("finance_agent.operator.analysis")

_SYMBOL_RE = re.compile(r"^\d{6}$")

Dispatcher = Callable[[str, str, str | None, LLMConfig | None], Awaitable[dict[str, Any]]]


def _error(message: str) -> str:
    return json.dumps({"ok": False, "error": message}, ensure_ascii=False)


def _receipt(receipt: dict[str, Any]) -> str:
    return json.dumps(
        {
            "ok": True,
            "session_id": receipt["session_id"],
            "display_name": receipt["display_name"],
            "status": receipt["status"],
            "symbol": receipt["symbol"],
        },
        ensure_ascii=False,
    )


def make_start_stock_analysis(
    *,
    api_key: str | None = None,
    llm_config: LLMConfig | None = None,
    dispatcher: Dispatcher | None = None,
):
    """创建 ``start_stock_analysis`` 工具（派单到独立 analysis 会话）。

    dispatcher 注入点用于测试与解耦；生产缺省从 api 模块函数内导入共享派单服务。
    """
    dispatched: dict[str, str] = {}

    async def start_stock_analysis(stock_code: str, stock_name: str = "") -> str:
        """启动一只 A 股的深度分析（派单到独立分析会话，立即返回回执）

        只负责派单，不等待分析完成。调用前须先用 search_stock 确认 6 位代码；
        用户尚未确认标的或存在多个候选时不要调用本工具。

        Args:
            stock_code: 6 位 A 股代码，如 "600519"
            stock_name: 股票名称，如 "贵州茅台"
        """
        symbol = (stock_code or "").strip()
        if not _SYMBOL_RE.fullmatch(symbol):
            return _error(
                f"股票代码必须是 6 位数字，收到 {stock_code!r}；请先调用 search_stock 确认标的"
            )
        cached = dispatched.get(symbol)
        if cached is not None:
            return cached

        try:
            if dispatcher is None:
                from finance_agent.api import start_analysis_session_for_operator

                receipt = await start_analysis_session_for_operator(
                    symbol,
                    (stock_name or "").strip(),
                    api_key=api_key,
                    llm_config=llm_config,
                )
            else:
                receipt = await dispatcher(symbol, (stock_name or "").strip(), api_key, llm_config)
            payload = _receipt(receipt)
        except Exception as exc:  # noqa: BLE001 -- 工具边界必须把失败转成可读结果
            _logger.exception("operator 派单失败 symbol=%s", symbol)
            return _error(f"派单失败：{type(exc).__name__}: {exc}")

        dispatched[symbol] = payload
        _logger.info(
            "operator_dispatch session_id=%s symbol=%s status=%s",
            receipt.get("session_id"),
            symbol,
            receipt.get("status"),
        )
        return payload

    return start_stock_analysis
