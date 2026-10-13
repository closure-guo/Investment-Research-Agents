"""标准化 Signal 契约与信号生成入口（add-signal-portfolio-risk-gate G1/G2）。

审批通过（fund_manager approve）的 TradeDecision → Signal 落库；缺字段、非法枚举、
未审批一律拒收（``SignalContractError``，不落库）。映射口径（计划 G1）：

- ``action=buy`` → ``long``；``hold``/``watch`` → ``flat``；
- ``action=sell``：``sell_type=="short"``（做空建仓）→ 非法 direction——A 股信号路径
  不建模做空，SHALL 拒收，消息逐字「direction 非法值: short」；``exit``/未申报 →
  ``flat``（持有者减仓/退出敞口 = 平掉敞口）；
- 显式 ``direction`` 键（spec 场景「一条 direction 为 short 的转换输入」）声明优先：
  枚举内即采用，枚举外拒收（同一消息）；
- ``strength = confidence``（0-1 数值校验）；``horizon_days = DEFAULT_HORIZON_DAYS``
  （口径同源 import，禁止本地复制）；
- ``signal_id = f"{symbol}:{trade_date}"``（确定性 → 同日同标的重跑幂等 upsert 不翻倍）。

信号生成路径入口 ``generate_signal``：契约转换 → 落库（滞回应用见 Task 4 扩展）。
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, cast

from finance_agent.execution.store import record_signal
from finance_agent.outcome.track_record.judgment import DEFAULT_HORIZON_DAYS

SignalDirection = Literal["long", "flat"]

# direction 合法枚举（delta spec trade-execution：限 long/flat，无做空）
SIGNAL_DIRECTIONS: tuple[str, ...] = ("long", "flat")

# 决策动作 → 信号方向（sell 分型语义见模块 docstring；不做空的信号语义）
_ACTION_DIRECTION: dict[str, str] = {"buy": "long", "hold": "flat", "watch": "flat"}


class SignalContractError(ValueError):
    """Signal 契约违规（未审批/缺字段/非法枚举）：转换拒收，不落库。"""


@dataclass(frozen=True)
class Signal:
    """标准化信号（落库单元，delta spec trade-execution 字段逐字）。

    ``direction`` 限 ``long``/``flat``（枚举不变量由 ``signal_from_decision``
    保证；直接构造属调用方编程错误）。
    """

    signal_id: str
    symbol: str
    direction: SignalDirection
    strength: float
    horizon_days: int
    source: str
    decision_id: str
    created_at: str


def _coerce_strength(value: object) -> float:
    """strength = confidence：须 0-1 有限数值（bool 视为非法，防 True 当 1）。"""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise SignalContractError(f"strength 非法值: {value!r}（须 0-1 数值）")
    strength = float(value)
    if not math.isfinite(strength) or not 0.0 <= strength <= 1.0:
        raise SignalContractError(f"strength 非法值: {value!r}（须 0-1 数值）")
    return strength


def _direction_for_action(decision: Mapping[str, Any]) -> str:
    """action（+sell_type 分型）→ direction；未知 action 以其原值代 direction 报错。"""
    action = str(decision.get("action") or "").strip()
    if not action:
        raise SignalContractError("缺少必填字段: action")
    if action == "sell":
        return "short" if str(decision.get("sell_type") or "").strip() == "short" else "flat"
    mapped = _ACTION_DIRECTION.get(action)
    return action if mapped is None else mapped


def signal_from_decision(
    decision: Mapping[str, Any],
    *,
    decision_id: str,
    source: str,
    trade_date: str,
    created_at: str | None = None,
    approved: bool | None = None,
) -> Signal:
    """审批通过的决策 → Signal（纯转换，不落库）。

    审批门禁：``approved is True`` 或 ``decision["fund_manager_verdict"] == "approve"``
    才可转换（delta spec decision-outcome：被拒绝/退回/未审批的决策 SHALL NOT 生成
    Signal）；显式 ``approved=False`` 一律拒收（调用方申报未审批，保守优先）。
    """
    if not isinstance(decision, Mapping):
        raise SignalContractError("决策输入须为映射（dict），拒收")
    verdict_approved = str(decision.get("fund_manager_verdict") or "").strip() == "approve"
    if approved is False or (approved is not True and not verdict_approved):
        raise SignalContractError("未审批通过的决策不得生成信号")

    symbol = str(decision.get("symbol") or "").strip()
    if not symbol:
        raise SignalContractError("缺少必填字段: symbol（标的，含交易所后缀）")
    decision_id_text = str(decision_id or "").strip()
    if not decision_id_text:
        raise SignalContractError("缺少必填字段: decision_id（决策外键）")
    trade_date_text = str(trade_date or "").strip()
    if not trade_date_text:
        raise SignalContractError("缺少必填字段: trade_date")
    strength = _coerce_strength(decision.get("confidence"))

    # 显式 direction 声明优先（spec 场景逐字）；否则由 action/sell_type 推导
    declared = decision.get("direction")
    direction = str(declared).strip() if isinstance(declared, str) and declared.strip() else None
    if direction is None:
        direction = _direction_for_action(decision)
    if direction not in SIGNAL_DIRECTIONS:
        raise SignalContractError(f"direction 非法值: {direction}")

    return Signal(
        signal_id=f"{symbol}:{trade_date_text}",
        symbol=symbol,
        direction=cast(SignalDirection, direction),
        strength=strength,
        horizon_days=DEFAULT_HORIZON_DAYS,
        source=str(source),
        decision_id=decision_id_text,
        created_at=str(created_at).strip() if created_at else trade_date_text,
    )


def generate_signal(
    decision: Mapping[str, Any],
    *,
    decision_id: str,
    source: str,
    trade_date: str,
    created_at: str | None = None,
    approved: bool | None = None,
    db_path: str | Path | None = None,
) -> Signal:
    """信号生成路径入口：契约转换 → 落库（幂等 upsert by signal_id）。"""
    signal = signal_from_decision(
        decision,
        decision_id=decision_id,
        source=source,
        trade_date=trade_date,
        created_at=created_at,
        approved=approved,
    )
    record_signal(signal, db_path=db_path)
    return signal
