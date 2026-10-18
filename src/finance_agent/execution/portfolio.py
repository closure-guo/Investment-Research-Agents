"""组合仓位聚合（add-signal-portfolio-risk-gate G3）：同日 active Signal → 目标持仓。

纯函数、无 IO（落库与门禁分别归 ``store.py`` / ``risk_gate.py``）。口径：

- 仅 ``direction=="long"`` 参与聚合（``flat``= 退出敞口，不进目标持仓，也不计入
  强度加权分母）；
- 两档可配置：``equal`` = 1/n；``strength`` = ``strength/Σstrength``；
- 先单票 clip：> 单票上限 → 上限，记 ``SINGLE_CAP_CLIP``；
- 再总仓缩减：Σ > 总上限 → 全体按 ``total_cap/Σ`` 等比缩，记 ``TOTAL_CAP_SCALE_DOWN``；
- 最后市场状态总仓位系数（add-crowding-regime-positioning）：全体乘
  ``regime_multiplier``（默认 1.0），系数 ≠ 1.0 时逐票记 ``REGIME_SCALE``；
  系数 0 → 全 0 权重（flat 目标）；默认 1.0 行为逐字节不变（含不追加调整码）；
- 上限默认值从 ``screening/constraints.py`` 单一来源引入（调用方可显式覆盖）；
- 输出按权重降序、symbol 升序 tie-break；权重和恒满足 Σ ≤ total_cap + 1e-9
  （勾稽容差，浮点尘埃不触发缩减/调整记录）。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Literal

from finance_agent.execution.signals import Signal
from finance_agent.screening import constraints

# 聚合调整原因码（逐字，跨模块契约；spec「超限部分按比例缩减并记录调整原因」）
ADJUSTMENT_SINGLE_CAP_CLIP = "SINGLE_CAP_CLIP"
ADJUSTMENT_TOTAL_CAP_SCALE_DOWN = "TOTAL_CAP_SCALE_DOWN"
# 市场状态总仓位系数调整码（delta spec trade-execution：系数 ≠ 1.0 逐票留痕）
ADJUSTMENT_REGIME_SCALE = "REGIME_SCALE"

# 权重勾稽容差：Σ ≤ total_cap + SUM_TOLERANCE（计划 G3 明文 1e-9）；单票 clip 同容差，
# 浮点尘埃（≤1e-9）不产生 clip 调整记录
SUM_TOLERANCE = 1e-9


@dataclass(frozen=True)
class TargetPosition:
    """目标持仓：symbol + 目标权重 + 应用过的调整原因码（应用顺序）。"""

    symbol: str
    weight: float
    adjustments: tuple[str, ...] = field(default=())


def aggregate_targets(
    signals: Sequence[Signal],
    *,
    method: Literal["equal", "strength"] = "equal",
    single_cap: float = constraints.MAX_SINGLE_POSITION,
    total_cap: float = constraints.MAX_TOTAL_POSITION,
    regime_multiplier: float = 1.0,
) -> list[TargetPosition]:
    """聚合为单票上限/总仓上限约束内的目标持仓（权重降序、symbol 升序）。

    ``regime_multiplier`` 为市场状态总仓位系数（[0, 1]，默认 1.0）：在总仓缩减
    **之后**对全部权重乘系数；≠ 1.0 时逐票追加 ``REGIME_SCALE``；默认 1.0 时
    输出与不传该参数逐项一致。
    """
    if method not in ("equal", "strength"):
        raise ValueError(f"method 非法值: {method!r}（须 equal/strength）")
    if not 0.0 <= regime_multiplier <= 1.0:
        raise ValueError(f"regime_multiplier 越界（须在 [0, 1]）: {regime_multiplier!r}")
    longs = [signal for signal in signals if signal.direction == "long"]
    if not longs:
        return []

    if method == "equal":
        raw: dict[str, float] = {signal.symbol: 1.0 / len(longs) for signal in longs}
    else:
        total_strength = sum(signal.strength for signal in longs)
        if total_strength <= 0:
            raise ValueError("strength 合计为 0，无法强度加权（改用 equal 或提供正强度信号）")
        raw = {signal.symbol: signal.strength / total_strength for signal in longs}

    # ① 单票 clip（>单票上限 → 上限）
    weights: dict[str, float] = {}
    adjustments: dict[str, list[str]] = {}
    for symbol, weight in raw.items():
        if weight > single_cap + SUM_TOLERANCE:
            weights[symbol] = single_cap
            adjustments.setdefault(symbol, []).append(ADJUSTMENT_SINGLE_CAP_CLIP)
        else:
            weights[symbol] = weight

    # ② 总仓缩减（Σ>总上限 → 全体按 total_cap/Σ 等比缩）
    total = sum(weights.values())
    if total > total_cap + SUM_TOLERANCE:
        scale = total_cap / total
        for symbol in weights:
            weights[symbol] *= scale
            adjustments.setdefault(symbol, []).append(ADJUSTMENT_TOTAL_CAP_SCALE_DOWN)

    # ③ 市场状态总仓位系数（总仓缩减之后应用；系数 ≠ 1.0 逐票留痕）
    if regime_multiplier != 1.0:
        for symbol in weights:
            weights[symbol] *= regime_multiplier
            adjustments.setdefault(symbol, []).append(ADJUSTMENT_REGIME_SCALE)

    positions = [
        TargetPosition(symbol=symbol, weight=weight, adjustments=tuple(adjustments.get(symbol, ())))
        for symbol, weight in weights.items()
    ]
    positions.sort(key=lambda position: (-position.weight, position.symbol))
    return positions
