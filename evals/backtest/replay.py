"""离线回放：截断快照 → 完整编排 → TradeDecision → 撮合入场 → 结算。

入场语义（add-matching-engine T5，替换旧「决策日收盘成交」简化假设）：决策在 D 收盘后
产出 → 最早可执行价是次一交易日（D+1）开盘；成交判定走系统唯一撮合引擎
（`execution.matching.match_order`）：整手/涨跌停/T+1 约束 + 滑点取档（回放无全市场成交额
样本 → 取最保守档 = 最大滑点，默认 0.002），成交价 = D+1 开盘 ×(1 ± 滑点)。fail-closed：
D+1 一字涨停买单、一字跌停卖单**不成交**——记 `settlement=None` + `unfilled_reason=<原因码>`，
回测聚合按原因码披露且不计入收益序列（绝不假设按涨停价成交）；缺「开盘」列时回落该行收盘
作价基并披露 `fill.reference="close_fallback"`。

结算语义与生产 track-record 判定同源（`outcome.track_record.judgment.resolve_prediction`：
horizon 到点 + 区间超额 + ±2% 中性带），不另造一套（spec「绩效指标与基线对比」）；成交价经
`resolve_prediction(entry_override=(D+1, 成交价))` 结算，判定数学零改动。neutral 方向
（hold/watch）不触引擎，仍走派生入场路径，判定输出 avoidance_*，回测聚合**整条排除**
（system 收益仅计 buy/sell，§1.9②）——口径说明见 run_backtest 报告的
`methodology.system_population`。
口径护栏：结算/撮合 bar 只用 hfq 全量 K 线（与结算同源）——快照 kline 是管线 qfq 口径，
不得混用。
"""

from __future__ import annotations

import math
from collections import Counter
from typing import Any

import pandas as pd

from evals.ablation import build_variant_graph
from evals.backtest.data_snapshot import SnapshotResult, build_snapshot
from finance_agent.execution.config import load_matching_config
from finance_agent.execution.matching import (
    MarketBar,
    Order,
    PositionContext,
    match_order,
    sealed_flags_from_ohlc,
)
from finance_agent.nodes.compute import compute_metrics
from finance_agent.outcome.track_record.judgment import (
    DEFAULT_HORIZON_DAYS,
    Resolution,
    direction_for_action,
    resolve_prediction,
)

_KLINE_REQUIRED_COLUMNS = ("日期", "收盘")

# 回放委托数量：单笔决策级（不建持仓账本），按 1 手计；成交可行性与滑点不依赖数量，
# 费用（佣金最低 5 元）依赖数量 → 结果披露 fee，收益序列暂不折算费用（报告披露）。
_REPLAY_ORDER_QTY = 100

# 成交价基来源（fill.reference）：正常取「开盘」；缺列/不可用回落该行「收盘」
_FILL_REFERENCE_OPEN = "open"
_FILL_REFERENCE_CLOSE_FALLBACK = "close_fallback"


def direction_agreement(actions: list[str]) -> float:
    """n 次重复的多数方向占比（spec：一致率 < 2/3 剔除或单独标注）。"""
    if not actions:
        return 0.0
    top = Counter(actions).most_common(1)[0][1]
    return top / len(actions)


def _usable_kline(kline: pd.DataFrame | None) -> bool:
    """可结算 K 线：非空且含 日期/收盘（缺列的空表曾以 KeyError 逃逸）。"""
    return (
        kline is not None
        and not kline.empty
        and all(col in kline.columns for col in _KLINE_REQUIRED_COLUMNS)
    )


def _settlement_kline(
    full_kline: pd.DataFrame | None, state: dict[str, Any]
) -> pd.DataFrame | None:
    """结算 K 线：只接受 hfq 全量；回落快照 kline（管线 qfq）违反同源口径 → ValueError。

    形状不全（空表/缺 日期·收盘 列）→ 返回 None 走「无结算」优雅返回，不以 KeyError 逃逸。
    """
    if full_kline is not None:
        return full_kline if _usable_kline(full_kline) else None
    snapshot_kline = state.get("kline")
    if _usable_kline(snapshot_kline):
        raise ValueError("回测结算需 hfq 全量 K 线；快照 kline 为管线口径，不得混用")
    return None


def _excess_split(
    resolution: Resolution, benchmark: pd.DataFrame | None
) -> tuple[float | None, float | None]:
    """(benchmark_return, decision_excess)；基准不可得 → (None, None)。

    与旧 outcome.settle 契约一致：基准缺失（未传或未覆盖入场/出场日）时超额记 None，
    不当作 0（下游 store AVG 与 Langfuse Score 均按 null 剔除）。
    """
    # 保留理由：excess_return 类型为 float | None，此守卫同时是 mypy 的收窄
    # （expiry 路径恒非 None，superseded 才可能 None）；删掉则 raw - None 无法通过类型检查。
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


def _finite_price(value: Any) -> float | None:
    """价格强转：None/NaN/inf/不可解析 → None（不编造价格，与 `execution.modes` 同哲学）。"""
    if value is None:
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def _entry_bar(kline: pd.DataFrame, decision_date: str) -> tuple[MarketBar, str] | None:
    """取撮合执行 bar：决策日之后的次一交易日（D+1），按 M1 语义供引擎定价。

    返回 ``(bar, fill_reference)``；无 D+1 行或该行价基不可用 → None（走既有「无结算」返回，
    不是业务性未成交、无原因码）：
    - ``open`` 列缺失/不可用 → 回落该行「收盘」作价基，``reference="close_fallback"``；
    - ``prev_close`` = 升序后上一交易日收盘（决策日在该序列内时即决策日收盘），供
      ``sealed_flags_from_ohlc`` 判一字板；
    - ``最高/最低`` 缺失/不可用 → 回落该行收盘（OHLC 同值 + 涨跌幅 ≥ 4.8% 会被判一字板 →
      fail-closed 判不成交，宁可少成交不美化收益）。
    """
    df = kline.copy()
    df["日期"] = df["日期"].astype(str).str[:10]
    df = df.sort_values("日期").reset_index(drop=True)
    after = df[df["日期"] > str(decision_date)[:10]]
    if after.empty:
        return None
    idx = int(after.index[0])
    row = df.iloc[idx]
    close = _finite_price(row.get("收盘"))
    if close is None or close <= 0:
        return None
    open_ = _finite_price(row.get("开盘")) if "开盘" in df.columns else None
    reference = _FILL_REFERENCE_OPEN
    if open_ is None or open_ <= 0:
        open_, reference = close, _FILL_REFERENCE_CLOSE_FALLBACK
    high = _finite_price(row.get("最高")) if "最高" in df.columns else None
    low = _finite_price(row.get("最低")) if "最低" in df.columns else None
    high = high if high is not None and high > 0 else close
    low = low if low is not None and low > 0 else close
    prev_close = _finite_price(df.iloc[idx - 1].get("收盘")) if idx > 0 else None
    limit_up_sealed, limit_down_sealed = sealed_flags_from_ohlc(open_, high, low, close, prev_close)
    bar = MarketBar(
        trade_date=str(row["日期"]),
        open=open_,
        high=high,
        low=low,
        close=close,
        prev_close=prev_close,
        amount=None,  # 回放无全市场成交额样本 → 滑点取最保守档（M4）
        limit_up_sealed=limit_up_sealed,
        limit_down_sealed=limit_down_sealed,
    )
    return bar, reference


def _execute_and_settle(
    *,
    code: str,
    decision_date: str,
    action: str,
    prediction: dict,
    kline: pd.DataFrame,
    benchmark: pd.DataFrame | None,
    base: dict[str, Any],
) -> dict:
    """buy/sell 决策：D+1 bar → 撮合引擎 → 未成交短路 / 成交价结算（T5 行为 1–5）。"""
    entry = _entry_bar(kline, decision_date)
    if entry is None:
        return {**base, "settlement": None, "entry_price": None}
    bar, reference = entry
    # M6：回放为单笔决策级、不建持仓账本 —— 卖单视作已有隔日仓位（可卖 = 总持仓 = 委托量），
    # 买单不需持仓（position=None）
    if action == "sell":
        order = Order(code=code, side="sell", qty=_REPLAY_ORDER_QTY)
        position: PositionContext | None = PositionContext(
            sellable_qty=_REPLAY_ORDER_QTY, total_qty=_REPLAY_ORDER_QTY
        )
    else:
        order = Order(code=code, side="buy", qty=_REPLAY_ORDER_QTY)
        position = None
    fill = match_order(order, bar, config=load_matching_config(), position=position)
    if fill.reason is not None:
        # 未成交（一字板等撮合约束）：M7 —— settlement/entry_price 置空 + 原因码；绝不按
        # 涨停价假设成交（fail-closed），聚合侧天然不计收益
        return {**base, "settlement": None, "entry_price": None, "unfilled_reason": fill.reason}
    fill_price = fill.fill_price
    if fill_price is None:  # 引擎契约：reason is None ⇒ 已成交有价；防御不静默出 None
        raise ValueError("撮合引擎返回已成交但无成交价")
    fill_detail: dict[str, Any] = {
        "price": fill_price,
        "fee": fill.fee,
        "slippage_rate": fill.slippage_rate,
        "reference": reference,
    }
    resolution = resolve_prediction(
        prediction, kline, benchmark, entry_override=(bar.trade_date, fill_price)
    )
    if resolution is None:
        # 已成交但 horizon 未到点：保持既有「无结算」形态，成交明细仍披露
        return {**base, "settlement": None, "entry_price": None, "fill": fill_detail}
    return {
        **base,
        "settlement": _settlement_from_resolution(resolution, benchmark),
        "entry_price": resolution.entry_price,
        "fill": fill_detail,
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
    # 纯日期 created_at → before_close → 归属日收盘派生入场（与旧 _close_on_or_before 同值）；
    # buy/sell 的**执行价**不再是派生入场，而是 D+1 撮合成交价（见 _execute_and_settle）
    prediction = {
        "created_at": decision_date,
        "direction": direction_for_action(action),
        "horizon_days": DEFAULT_HORIZON_DAYS,
        "target_price": decision.get("target_price"),
    }
    if action in ("buy", "sell"):
        return _execute_and_settle(
            code=code,
            decision_date=decision_date,
            action=action,
            prediction=prediction,
            kline=kline,
            benchmark=benchmark,
            base=base,
        )
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

    结算上下文（settlement/entry_price/action/decision_date）取首轮回放透传；
    成交明细 ``fill`` 与未成交原因码 ``unfilled_reason`` 同样透传（T6 聚合按原因码
    披露未成交笔数并排除出收益序列，不透传则聚合侧看不到），供回测编排把单笔结算
    摊成持有期日收益（Task 10 _trade_daily_returns）。
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
        # add-matching-engine T5：成交明细/未成交原因码透传（聚合侧按原因码披露、不计收益）
        "unfilled_reason": (first or {}).get("unfilled_reason"),
        "fill": (first or {}).get("fill"),
        "snapshot_metadata": snap.metadata,
    }
