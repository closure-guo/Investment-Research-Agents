"""离线回放：截断快照 → 完整编排 → TradeDecision → 结算。

结算语义与生产 track-record 判定同源（`outcome.track_record.judgment.resolve_prediction`：
horizon 到点 + 区间超额 + ±2% 中性带），不另造一套（spec「绩效指标与基线对比」）。
入场基准由 K 线派生（决策日收盘，非存档参考价）；neutral 方向（hold/watch）判定
输出 avoidance_*，回测聚合仍按既有 action 符号口径（见 run_backtest 报告口径说明）。
口径护栏：结算只用 hfq 全量 K 线——快照 kline 是管线 qfq 口径，不得混用。
"""

from __future__ import annotations

from collections import Counter
from typing import Any

import pandas as pd

from evals.ablation import build_variant_graph
from evals.backtest.data_snapshot import SnapshotResult, build_snapshot
from finance_agent.nodes.compute import compute_metrics
from finance_agent.outcome.track_record.judgment import (
    DEFAULT_HORIZON_DAYS,
    Resolution,
    resolve_prediction,
)

# action → 判定方向（hold/watch 及其余非买卖动作 → neutral → 回避判定）
_ACTION_DIRECTION = {"buy": "long", "sell": "short"}


def direction_agreement(actions: list[str]) -> float:
    """n 次重复的多数方向占比（spec：一致率 < 2/3 剔除或单独标注）。"""
    if not actions:
        return 0.0
    top = Counter(actions).most_common(1)[0][1]
    return top / len(actions)


def _settlement_kline(
    full_kline: pd.DataFrame | None, state: dict[str, Any]
) -> pd.DataFrame | None:
    """结算 K 线：只接受 hfq 全量；回落快照 kline（管线 qfq）违反同源口径 → ValueError。"""
    if full_kline is not None:
        return full_kline
    snapshot_kline = state.get("kline")
    if snapshot_kline is None or snapshot_kline.empty:
        return None
    raise ValueError("回测结算需 hfq 全量 K 线；快照 kline 为管线口径，不得混用")


def _excess_split(
    resolution: Resolution, benchmark: pd.DataFrame | None
) -> tuple[float | None, float | None]:
    """(benchmark_return, decision_excess)；基准不可得 → (None, None)。

    与旧 outcome.settle 契约一致：基准缺失（未传或未覆盖入场/出场日）时超额记 None，
    不当作 0（下游 store AVG 与 Langfuse Score 均按 null 剔除）。
    """
    if resolution.excess_return is None:
        return None, None
    if benchmark is None or benchmark.empty:
        return None, None
    dates = benchmark["日期"].astype(str).str[:10]
    covered = (dates <= resolution.entry_date).any() and (dates <= resolution.exit_date).any()
    if not covered:
        # 基准未覆盖入场/出场日：judgment 回落到 raw_return，此处超额不可得
        return None, None
    return resolution.raw_return - resolution.excess_return, resolution.excess_return


def _settlement_from_resolution(
    resolution: Resolution, benchmark: pd.DataFrame | None
) -> dict[str, Any]:
    """Resolution → 下游既有契约键（run_backtest._trade_daily_returns 与基线对齐依赖）。"""
    benchmark_return, decision_excess = _excess_split(resolution, benchmark)
    return {
        "status": resolution.status,
        "settle_date": resolution.exit_date,
        "settle_price": resolution.exit_price,
        "hold_days": resolution.hold_days,
        "decision_return": resolution.raw_return,
        "benchmark_return": benchmark_return,
        "decision_excess": decision_excess,
        "decision_hit": resolution.raw_return > 0,
    }


def replay_decision(
    code: str,
    decision_date: str,
    *,
    snapshot: SnapshotResult | None = None,
    client: Any = None,
    full_kline: pd.DataFrame | None = None,
    full_benchmark: pd.DataFrame | None = None,
) -> dict:
    """单次回放：快照（截断）→ compute_metrics → 完整 5 层 → 结算（全量 K 线/基准）。"""
    snap = snapshot or build_snapshot(code, decision_date, client=client)
    state = {**snap.state}
    state.update(compute_metrics(state))  # type: ignore[arg-type]
    graph = build_variant_graph("full")
    final = graph.invoke({**state, "focus": f"{code} 综合评估投资价值"})
    # 编排 state 的 final_trade_decision 是 TradeDecision pydantic 对象
    # （[backtest-pilot] 缺陷修复：按 dict 用 .get 会 AttributeError）
    raw_decision = final.get("final_trade_decision")
    if raw_decision is not None and hasattr(raw_decision, "model_dump"):
        decision: dict = raw_decision.model_dump()
    else:
        decision = dict(raw_decision or {})
    raw_action = decision.get("action")
    # 口径统一：action 缺失记 "unknown"（与 replay_with_consistency 计数一致）
    action = str(raw_action or "unknown")
    base: dict[str, Any] = {
        "decision": decision,
        "action": action,
        "decision_date": decision_date,
        "snapshot_metadata": snap.metadata,
        "final_report": final.get("final_report"),
    }
    # 无有效 action（图未产出决策）→ 不进入结算，也不触碰 K 线口径护栏
    if not raw_action:
        return {**base, "settlement": None, "entry_price": None}
    kline = _settlement_kline(full_kline, state)
    if kline is None:
        return {**base, "settlement": None, "entry_price": None}
    benchmark = full_benchmark if full_benchmark is not None else state.get("benchmark_kline")
    # 纯日期 created_at → before_close → 归属日收盘入场（与旧 _close_on_or_before 同值）
    prediction = {
        "created_at": decision_date,
        "direction": _ACTION_DIRECTION.get(action, "neutral"),
        "horizon_days": DEFAULT_HORIZON_DAYS,
        "target_price": decision.get("target_price"),
    }
    resolution = resolve_prediction(prediction, kline, benchmark)
    if resolution is None:
        return {**base, "settlement": None, "entry_price": None}
    return {
        **base,
        "settlement": _settlement_from_resolution(resolution, benchmark),
        "entry_price": resolution.entry_price,
    }


def replay_with_consistency(
    code: str,
    decision_date: str,
    *,
    n: int = 3,
    snapshot: SnapshotResult | None = None,
    client: Any = None,
    full_kline: pd.DataFrame | None = None,
    full_benchmark: pd.DataFrame | None = None,
) -> dict:
    """同一快照重复回放 n 次：决策方向一致率 + 首次结算结果（一致性独立维度披露）。

    结算上下文（settlement/entry_price/action/decision_date）取首轮回放透传，
    供回测编排把单笔结算摊成持有期日收益（Task 10 _trade_daily_returns）。
    """
    snap = snapshot or build_snapshot(code, decision_date, client=client)
    actions: list[str] = []
    first: dict | None = None
    for _ in range(n):
        result = replay_decision(
            code, decision_date, snapshot=snap, full_kline=full_kline, full_benchmark=full_benchmark
        )
        # 口径统一：action 缺失记 "unknown"（与 replay_decision 返回一致）
        actions.append(str(result.get("action") or "unknown"))
        if first is None:
            first = result
    return {
        "code": code,
        "decision_date": decision_date,
        "actions": actions,
        "agreement": round(direction_agreement(actions), 4),
        "settlement": (first or {}).get("settlement"),
        "entry_price": (first or {}).get("entry_price"),
        "action": (first or {}).get("action"),
        "snapshot_metadata": snap.metadata,
    }
