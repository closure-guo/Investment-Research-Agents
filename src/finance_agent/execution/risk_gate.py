"""确定性风控门禁（add-signal-portfolio-risk-gate G4）：纯代码、无 LLM 依赖。

所有订单进入执行前 SHALL 经本模块 ``check_order``（delta spec trade-execution
「确定性风控门禁」）；约束定义 import 自 ``screening/constraints.py``（单一来源，
禁止在执行层复制常量），且**调用期读模块属性**——改约束一处即全链路生效
（与 L1 过滤层同款纪律）。本模块 MUST NOT 发起或依赖任何 LLM 调用；测试以 AST
静态扫描钉死「无 llm/gateway/harness import」。

检查顺序（首个命中即拒——拒绝即停，``GateDecision.detail`` 记检查轨迹）：

① ``is_st`` → ``ST_FLAG``（与 L1 过滤使用同一约束来源）
② ``is_suspended`` → ``SUSPENDED``
③ 一字板（``bar`` 缺失 → 跳过判定，不编造板状态）：买 + ``limit_up_sealed`` →
   ``LIMIT_UP_UNFILLABLE``；卖 + ``limit_down_sealed`` → ``LIMIT_DOWN_UNFILLABLE``
④ 单票仓位上限（``target_weight`` > ``MAX_SINGLE_POSITION``）→ ``SINGLE_POSITION_CAP``
⑤ 组合总仓上限（``projected_total_weight`` > ``MAX_TOTAL_POSITION``）→
   ``TOTAL_POSITION_CAP``
⑥ T+1（卖单 ``qty`` > 可卖数量；``position`` 缺省视作可卖 0）→ ``T1_SELL_BLOCKED``
⑦ 整手（``qty % LOT_SIZE != 0`` → ``ODD_LOT``；卖零股全卖例外同 matching 语义）

``qty <= 0`` 或 side 非 buy/sell → ``ValueError``（调用方编程错误，非业务原因码，
同 matching M3 口径）。顺序差异说明：本门禁 ⑥ 先于 ⑦（准入层先判可卖性再判委托
单位），与 matching M3（撮合层前因裁决：整手 → 涨跌停 → T+1）各自实现，互不复用。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from finance_agent.execution.matching import MarketBar, PositionContext
from finance_agent.screening import constraints

# 原因码常量（spec 逐字，跨模块契约；消费方比较时不得改写）
REASON_SINGLE_POSITION_CAP = "SINGLE_POSITION_CAP"
REASON_TOTAL_POSITION_CAP = "TOTAL_POSITION_CAP"
REASON_T1_SELL_BLOCKED = "T1_SELL_BLOCKED"
REASON_LIMIT_UP_UNFILLABLE = "LIMIT_UP_UNFILLABLE"
REASON_LIMIT_DOWN_UNFILLABLE = "LIMIT_DOWN_UNFILLABLE"
REASON_SUSPENDED = "SUSPENDED"
REASON_ST_FLAG = "ST_FLAG"
REASON_ODD_LOT = "ODD_LOT"

# 上限判定容差：portfolio.aggregate_targets 的勾稽容差同为 1e-9（计划 G3），
# 浮点尘埃不得把组合刚产出的合法权重误拒为超限
_CAP_TOLERANCE = 1e-9


@dataclass(frozen=True)
class OrderIntent:
    """订单意图（纯数据）：``decision_id``/``signal_id`` 为证据链必填字段。

    ``target_weight`` 为该订单执行后的目标持仓权重（单票上限判定输入）。
    """

    decision_id: str
    signal_id: str
    symbol: str
    side: Literal["buy", "sell"]
    qty: int
    target_weight: float


@dataclass(frozen=True)
class GateDecision:
    """门禁结论：通过时 ``reason is None``；拒绝时 ``reason`` 为逐字原因码。

    ``detail`` 含证据链（decision_id/signal_id/symbol/side/qty）与 ``checks``
    检查轨迹（按执行顺序；拒绝即停，命中项后不再记录）。
    """

    approved: bool
    reason: str | None
    detail: dict[str, Any]


def _decision(order: OrderIntent, checks: list[dict[str, Any]], reason: str | None) -> GateDecision:
    return GateDecision(
        approved=reason is None,
        reason=reason,
        detail={
            "decision_id": order.decision_id,
            "signal_id": order.signal_id,
            "symbol": order.symbol,
            "side": order.side,
            "qty": order.qty,
            "checks": checks,
        },
    )


def check_order(
    order: OrderIntent,
    *,
    current_weight: float,
    projected_total_weight: float,
    bar: MarketBar | None,
    position: PositionContext | None,
    is_st: bool = False,
    is_suspended: bool = False,
) -> GateDecision:
    """逐规则判定订单（顺序见模块 docstring）；首个命中即拒，detail 记轨迹。"""
    if order.qty <= 0:
        raise ValueError(f"委托数量必须为正整数: {order.qty!r}")
    if order.side not in ("buy", "sell"):
        raise ValueError(f"委托方向必须为 buy/sell: {order.side!r}")

    checks: list[dict[str, Any]] = []

    def _pass(name: str, **detail: Any) -> None:
        checks.append({"check": name, "passed": True, **detail})

    def _reject(name: str, reason: str, **detail: Any) -> GateDecision:
        checks.append({"check": name, "passed": False, "reason": reason, **detail})
        return _decision(order, checks, reason)

    # ① ST 状态
    if is_st:
        return _reject("st_flag", REASON_ST_FLAG, is_st=True)
    _pass("st_flag", is_st=False)

    # ② 停牌
    if is_suspended:
        return _reject("suspended", REASON_SUSPENDED, is_suspended=True)
    _pass("suspended", is_suspended=False)

    # ③ 一字板（bar 缺失 → 跳过，不编造板状态）
    if bar is None:
        _pass("limit_seal", bar=None, note="bar 缺失，跳过涨跌停判定")
    else:
        if order.side == "buy" and bar.limit_up_sealed:
            return _reject("limit_seal", REASON_LIMIT_UP_UNFILLABLE, limit_up_sealed=True)
        if order.side == "sell" and bar.limit_down_sealed:
            return _reject("limit_seal", REASON_LIMIT_DOWN_UNFILLABLE, limit_down_sealed=True)
        _pass(
            "limit_seal",
            limit_up_sealed=bool(bar.limit_up_sealed),
            limit_down_sealed=bool(bar.limit_down_sealed),
        )

    # ④ 单票上限（调用期读 constraints 单一来源）
    single_cap = constraints.MAX_SINGLE_POSITION
    if order.target_weight > single_cap + _CAP_TOLERANCE:
        return _reject(
            "single_position_cap",
            REASON_SINGLE_POSITION_CAP,
            target_weight=order.target_weight,
            cap=single_cap,
        )
    _pass(
        "single_position_cap",
        target_weight=order.target_weight,
        cap=single_cap,
        current_weight=current_weight,
    )

    # ⑤ 组合总仓上限
    total_cap = constraints.MAX_TOTAL_POSITION
    if projected_total_weight > total_cap + _CAP_TOLERANCE:
        return _reject(
            "total_position_cap",
            REASON_TOTAL_POSITION_CAP,
            projected_total_weight=projected_total_weight,
            cap=total_cap,
        )
    _pass(
        "total_position_cap",
        projected_total_weight=projected_total_weight,
        cap=total_cap,
        current_weight=current_weight,
    )

    # ⑥ T+1（position 缺省 = 可卖 0，保守拒绝无持仓卖出）
    if order.side == "sell":
        sellable_qty = position.sellable_qty if position is not None else 0
        if order.qty > sellable_qty:
            return _reject(
                "t1_sellable", REASON_T1_SELL_BLOCKED, qty=order.qty, sellable_qty=sellable_qty
            )
        _pass("t1_sellable", qty=order.qty, sellable_qty=sellable_qty)
    else:
        _pass("t1_sellable", note="买单不受 T+1 约束")

    # ⑦ 整手（卖零股全卖例外同 matching 语义；LOT_SIZE 同源 constraints）
    lot_size = int(constraints.LOT_SIZE)
    if order.qty % lot_size != 0:
        full_odd_lot_sell = (
            order.side == "sell"
            and position is not None
            and order.qty == position.total_qty
            and position.total_qty % lot_size != 0
        )
        if not full_odd_lot_sell:
            return _reject("odd_lot", REASON_ODD_LOT, qty=order.qty, lot_size=lot_size)
        _pass("odd_lot", qty=order.qty, lot_size=lot_size, note="卖零股全卖例外")
    else:
        _pass("odd_lot", qty=order.qty, lot_size=lot_size)

    return _decision(order, checks, None)
