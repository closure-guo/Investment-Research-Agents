"""快速模式只读系统查询工具（P2）。

只做“读 + 紧凑整形”：业务口径全部复用既有端点函数，不在这里写第二套查询逻辑。
列表一律截断，缺数据/陈旧/样本不足状态原样保留。
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from typing import Any

_MAX_POOL = 20
_MAX_QUEUE = 10
_MAX_POSITIONS = 20
_MAX_CLAUSES = 20
_MAX_FACTORS = 20
_TEXT_LIMIT = 300
_EXPR_LIMIT = 200
_SUMMARY_KEYS = (
    "run_id",
    "status",
    "started_at",
    "finished_at",
    "error",
    "summary",
)


def _ok(**payload: Any) -> str:
    return json.dumps({"ok": True, **payload}, ensure_ascii=False)


def _err(message: str) -> str:
    return json.dumps({"ok": False, "error": message}, ensure_ascii=False)


def _truncate(value: Any, limit: int = _TEXT_LIMIT) -> Any:
    if not isinstance(value, str):
        return value
    return value if len(value) <= limit else value[:limit] + "…"


def _compact_screening(payload: dict[str, Any]) -> dict[str, Any]:
    pool = payload.get("pool") or []
    queue = payload.get("queue") or []
    rows = [
        {
            "symbol": row.get("symbol"),
            "composite_score": row.get("composite_score"),
            "rank": row.get("rank"),
            "consecutive_days": row.get("consecutive_days"),
            "l3_verdict": row.get("l3_verdict"),
            "l3_reason": _truncate(row.get("l3_reason")),
            "pool": row.get("pool"),
        }
        for row in pool[:_MAX_POOL]
    ]
    return {
        "batch_date": payload.get("batch_date"),
        "batch_status": payload.get("batch_status"),
        "stale": payload.get("stale"),
        "selection_mode": payload.get("selection_mode"),
        "notice": payload.get("notice"),
        "pool_size": len(pool),
        "pool": rows,
        "queue_size": len(queue),
        "queue": queue[:_MAX_QUEUE],
    }


def _compact_track(payload: dict[str, Any]) -> dict[str, Any]:
    paper = payload.get("paper") or {}
    return {
        "as_of": payload.get("as_of"),
        "total": payload.get("total"),
        "open": payload.get("open"),
        "settled": payload.get("settled"),
        "win_rate": payload.get("win_rate"),
        "avg_excess": payload.get("avg_excess"),
        "insufficient_sample": payload.get("insufficient_sample"),
        "status_counts": payload.get("status_counts"),
        "portfolio": payload.get("portfolio"),
        "avoidance": payload.get("avoidance"),
        "paper_loss_pct": paper.get("loss_pct"),
        "paper_note": paper.get("note"),
        "paper_base_date": paper.get("base_date"),
        "caliber_horizon": payload.get("caliber_horizon"),
        "legacy_settled": payload.get("legacy_settled"),
        "disclaimer": payload.get("disclaimer"),
    }


def _compact_broker(
    overview: dict[str, Any], positions_payload: dict[str, Any]
) -> dict[str, Any]:
    positions = positions_payload.get("positions") or []
    rows = [
        {
            "symbol": row.get("symbol"),
            "name": row.get("name"),
            "qty": row.get("qty"),
            "avg_cost": row.get("avg_cost"),
            "close": row.get("close"),
            "market_value": row.get("market_value"),
            "unrealized_pnl": row.get("unrealized_pnl"),
        }
        for row in positions[:_MAX_POSITIONS]
    ]
    return {
        **overview,
        "positions_total": overview.get("positions_count", len(positions)),
        "positions": rows,
    }


def _compact_ops(payload: dict[str, Any]) -> dict[str, Any]:
    jobs = payload.get("jobs") or []
    rows = []
    for row in jobs:
        last = row.get("last_run") or {}
        rows.append(
            {
                "job_id": row.get("job_id"),
                "label": row.get("label"),
                "next_fire_time": row.get("next_fire_time"),
                "last_run": (
                    {key: last.get(key) for key in _SUMMARY_KEYS} if last else None
                ),
            }
        )
    return {
        "scheduler_running": payload.get("scheduler_running"),
        "jobs": rows,
        "cohort": payload.get("cohort"),
        "error": payload.get("error"),
    }


def _compact_memory(payload: dict[str, Any]) -> dict[str, Any]:
    clauses = payload.get("clauses") or []
    rows = [
        {
            "id": row.get("id"),
            "text": _truncate(row.get("text")),
            "status": row.get("status"),
            "source": row.get("source"),
            "created_at": row.get("created_at"),
        }
        for row in clauses[:_MAX_CLAUSES]
    ]
    return {"status": payload.get("status"), "count": len(clauses), "clauses": rows}


def _compact_factors(payload: dict[str, Any]) -> dict[str, Any]:
    factors = payload.get("factors") or []
    rows = [
        {
            "id": row.get("id"),
            "name": row.get("name"),
            "expression": _truncate(row.get("expression"), _EXPR_LIMIT),
            "status": row.get("status"),
            "source": row.get("source"),
            "ic_mean_60d": row.get("ic_mean_60d"),
            "icir_60d": row.get("icir_60d"),
            "ic_points": row.get("ic_points"),
            "decayed": row.get("decayed"),
            "last_event": row.get("last_event"),
        }
        for row in factors[:_MAX_FACTORS]
    ]
    return {"count": len(factors), "factors": rows}


def _compact_factor_research(payload: dict[str, Any]) -> dict[str, Any]:
    supervisor = payload.get("supervisor") or {}
    session = payload.get("current_session") or {}
    control = payload.get("control") or {}
    agent = payload.get("agent") or {}
    launcher = payload.get("launcher") or {}
    factors = session.get("factors") or []
    return {
        "available": payload.get("available"),
        "stale": payload.get("stale"),
        "age_seconds": payload.get("age_seconds"),
        "collected_at": payload.get("collected_at"),
        "supervisor": {
            "running": supervisor.get("running"),
            "pid": supervisor.get("pid"),
            "started_at": supervisor.get("started_at"),
        },
        "current_session": {
            "alive": session.get("alive"),
            "loop_index": session.get("loop_index"),
            "step_index": session.get("step_index"),
            "step_name": session.get("step_name"),
            "steps_total": session.get("steps_total"),
            "step_eta_s": session.get("step_eta_s"),
            "factors": factors[:5],
        },
        "control": {
            "desired_running": control.get("desired_running"),
            "pending": control.get("pending"),
            "honor": control.get("honor"),
            "honor_eta_seconds": control.get("honor_eta_seconds"),
            "requested_at": control.get("requested_at"),
        },
        "agent": {
            "last_action": agent.get("last_action"),
            "last_action_at": agent.get("last_action_at"),
            "last_error": agent.get("last_error"),
        },
        "launcher": {
            "available": launcher.get("available"),
            "fresh": launcher.get("fresh"),
            "agent_ok": launcher.get("agent_ok"),
        },
        "totals": payload.get("totals"),
    }


def make_read_tools() -> dict[str, Callable[..., Awaitable[str]]]:
    """创建全部只读工具（名称与 tool_registry 常量一致）。"""

    async def get_screening_pool() -> str:
        """查询最近成功选股池、批次状态与分析队列

        只读。池内最多返回 20 只，队列最多 10 条；无成功批次时如实返回空池。
        """
        try:
            from finance_agent.api import screening_pool

            return _ok(**_compact_screening(await screening_pool()))
        except Exception as exc:  # noqa: BLE001 - 工具边界转可读错误
            return _err(f"{type(exc).__name__}: {exc}")

    async def get_track_record_overview() -> str:
        """查询战绩总览（胜率/超额/组合指标/回避正确率/paper 执行损耗）

        只读。样本不足时统计字段为 null 并带 insufficient_sample 标注；不返回净值曲线。
        """
        try:
            from finance_agent.api import track_record_overview

            return _ok(**_compact_track(await track_record_overview()))
        except Exception as exc:  # noqa: BLE001
            return _err(f"{type(exc).__name__}: {exc}")

    async def get_broker_overview() -> str:
        """查询执行台账（可用资金/持仓市值/总资产/浮盈 + 持仓明细）

        只读。快照缺失时市值/浮盈为 null 并附 market_value_note；持仓最多 20 行。
        """
        try:
            from finance_agent.api import broker_overview, broker_positions

            overview = await broker_overview()
            positions = await broker_positions()
            return _ok(**_compact_broker(overview, positions))
        except Exception as exc:  # noqa: BLE001
            return _err(f"{type(exc).__name__}: {exc}")

    async def get_daily_ops_status() -> str:
        """查询日批调度器状态与各任务的下次触发/最近运行

        只读。返回全部任务行，但不返回历史运行列表；调度器未运行时如实披露。
        """
        try:
            from finance_agent.ops_api import _jobs_payload

            payload = await asyncio.to_thread(_jobs_payload)
            return _ok(**_compact_ops(payload))
        except Exception as exc:  # noqa: BLE001
            return _err(f"{type(exc).__name__}: {exc}")

    async def get_memory_clauses(status: str = "pending") -> str:
        """查询语义记忆条款（默认 pending 审批队列）

        只读。status 可选 pending/confirmed/rejected；条款最多 20 条，文本截断 300 字。
        """
        try:
            from finance_agent.api import memory_clause_list

            return _ok(**_compact_memory(await memory_clause_list(status)))
        except Exception as exc:  # noqa: BLE001
            return _err(f"{type(exc).__name__}: {exc}")

    async def get_factor_library() -> str:
        """查询因子库列表（名称/表达式/状态/ICIR/最近闸门结论）

        只读。按 ICIR 降序返回最多 20 条；不返回 IC 序列与完整事件历史。
        """
        try:
            from finance_agent.api import factor_library_list

            return _ok(**_compact_factors(await factor_library_list()))
        except Exception as exc:  # noqa: BLE001
            return _err(f"{type(exc).__name__}: {exc}")

    async def get_factor_research_status() -> str:
        """查询因子研发看护状态（运行/陈旧/当前进度/控制期望态）

        只读。包含 supervisor、当前 session 进度、control 块与最近调和动作；
        不返回日志尾。快照缺失/陈旧时 available/stale 如实披露。
        """
        try:
            from finance_agent.api import factor_research_status

            return _ok(**_compact_factor_research(await factor_research_status()))
        except Exception as exc:  # noqa: BLE001
            return _err(f"{type(exc).__name__}: {exc}")

    return {
        "get_screening_pool": get_screening_pool,
        "get_track_record_overview": get_track_record_overview,
        "get_broker_overview": get_broker_overview,
        "get_daily_ops_status": get_daily_ops_status,
        "get_memory_clauses": get_memory_clauses,
        "get_factor_library": get_factor_library,
        "get_factor_research_status": get_factor_research_status,
    }
