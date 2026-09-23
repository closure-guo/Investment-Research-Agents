"""add-track-record Task 3 补丁:预测落库的共享入口(供 api 与 ReAct 深模式共用)。

背景:落库挂点原先只在旧 /api/analyze SSE 路径(api.py:1169);ADR-0017 统一后
深模式经 ReAct 的 run_deep_analysis 工具走 _stream_graph(agent_factory),
该路径无挂点 → 深度分析完成后 predictions 恒为 0(真实线上事故)。
本模块把「accumulated → predictions 全量记录」抽为中立函数,两条路径共用。
"""

from __future__ import annotations

import logging
from typing import Any

from finance_agent.outcome.track_record.judgment import (
    DEFAULT_HORIZON_DAYS,
    direction_for_action,
)
from finance_agent.outcome.track_record.model import insert_prediction

logger = logging.getLogger("finance_agent.track_record.ingest")


def persist_prediction_from_accumulated(
    accumulated: dict[str, Any], session_id: str, stock_code: str, stock_name: str
) -> None:
    """观点全量落 predictions(旁路:任何失败仅 ERROR,不阻断业务)。

    approve/reject/hold/watch 均记录;action→direction 映射(buy→long,
    sell→short,hold/watch→neutral);horizon_days 显式写 DEFAULT_HORIZON_DAYS
    (不再依赖插入兜底);rationale_snapshot 冻结申报价位(entry/stop/target);
    entry_price 取 quote 优先、kline 收盘兜底——两者均不可得时仅 WARN,
    状态保持 open(判定不依赖参考价,结算入场价届时由行情派生)。
    """
    try:
        decision = accumulated.get("final_trade_decision")
        if decision is None:
            return
        # 真实管线 state 携带 pydantic TradeDecision 对象（非 dict）——归一化访问。
        # 线上事故：decision.get() 在 pydantic 对象上抛 AttributeError 被旁路吞掉，
        # 深度分析完成后 predictions 恒为 0（历史战绩空）。
        if not isinstance(decision, dict):
            decision = decision.model_dump()
        if not decision.get("action"):
            return
        action = decision["action"]
        direction = direction_for_action(action)
        entry_price = (accumulated.get("stock_quote") or {}).get("price")
        if entry_price is None:
            kline = accumulated.get("kline")
            if kline is not None and len(kline) > 0:
                last = kline.iloc[-1] if hasattr(kline, "iloc") else kline[-1]
                entry_price = float(last["收盘"])
        status = "open"
        resolution_rule = None
        if entry_price is None:
            logger.warning(
                "参考价不可得（quote 与 kline 均无）: %s；判定时由行情派生结算入场价", stock_code
            )
        symbol = f"{stock_code}.SH" if str(stock_code).startswith("6") else f"{stock_code}.SZ"
        insert_prediction(
            {
                "source_type": "live",
                "symbol": symbol,
                "symbol_name": stock_name,
                "direction": direction,
                "entry_price": float(entry_price) if entry_price is not None else None,
                "target_price": decision.get("target_price"),
                "horizon_days": DEFAULT_HORIZON_DAYS,
                "confidence": decision.get("confidence"),
                "rationale_snapshot": {
                    "action": action,
                    "fund_manager_decision": accumulated.get("fund_manager_decision"),
                    "fund_manager_decision_reasoning": accumulated.get(
                        "fund_manager_decision_reasoning"
                    ),
                    "declared_prices": {
                        "entry_price": decision.get("entry_price"),
                        "stop_loss": decision.get("stop_loss"),
                        "target_price": decision.get("target_price"),
                    },
                },
                "langfuse_trace_id": accumulated.get("langfuse_trace_id"),
                "timestamp": _now_iso(),
                "resolution_rule": resolution_rule,
            },
            status=status,
        )
        logger.info("prediction 已落库: %s %s status=%s", stock_code, action, status)
    except Exception:  # noqa: BLE001 - 旁路铁律:失败仅 ERROR 不阻断业务
        logger.exception("prediction 落库失败(不阻断业务)")


def _now_iso() -> str:
    from datetime import datetime

    return datetime.now().isoformat()
