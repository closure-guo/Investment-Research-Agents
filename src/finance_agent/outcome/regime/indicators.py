"""拥挤度四指标与历史分位（纯函数；delta add-crowding-regime-positioning T1）。

口径（metrics.md §1.14 / design.md「指标口径」，实施裁决见计划 Task 2）：

- **参与集** = 快照行中「非停牌 且成交额有限且 > 0」的行（停牌与零成交剔除）。
  裁决：停牌标的剔除于**全部四个指标**（含涨跌家数）——故全市场成交额的求和域
  同为参与集；
- **小市值组** = 参与集按当日流通市值升序的前 ``ceil(n × SMALL_CAP_QUANTILE)`` 只
  （市值并列按 code 升序 tie-break，保证同输入同输出）；占比 = 组内成交额之和
  ÷ 参与集成交额之和；
- **涨跌家数** 按 ``pct_chg`` 符号（``== 0`` 计「平」）；**涨停/跌停家数** 按
  ``limit_up_sealed`` / ``limit_down_sealed`` 计数（NULL 视为未封板）；
- **不可计算 → None**（universe 空 / 参与集空 / 参与集任一标的缺 cap / 任一参与
  标的 ``amount`` 或 ``pct_chg`` 非有限值）——MUST NOT 折算 0 或估算值冒充；
  原因由调用方记 ``cap_data_missing``。零成交标的**不**要求 cap（已被剔除）。

流通市值与快照任务同源（``AKShareClient.fetch_market_spot`` 的 ``流通市值`` 列），
MUST NOT 以成交额等替代口径。
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Any

# 小市值分组分位（按流通市值升序取前 30%；可经 RegimeConfig 覆盖调用侧口径）
SMALL_CAP_QUANTILE: float = 0.3
# 历史分位窗口（交易日；含当日）
HISTORY_WINDOW: int = 250
# 分位最小样本（少于即 null + insufficient_history）
MIN_HISTORY: int = 60

# 指标键（落库列名/判定行键/载荷键共用同一序列，跨模块契约）
INDICATOR_KEYS: tuple[str, ...] = (
    "small_cap_share",
    "up_count",
    "down_count",
    "flat_count",
    "limit_up_count",
    "limit_down_count",
    "total_amount",
)


def _finite(value: Any) -> float | None:
    """数值强转；None/NaN/inf/不可解析 → None（不编造数值，同 snapshot._num）。"""
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _flag(value: Any) -> bool:
    """快照状态位（INTEGER 0/1/NULL）；NULL/NaN → False（同 driver._flag 口径）。"""
    number = _finite(value)
    return number is not None and number != 0.0


def compute_indicators(
    universe: Sequence[Mapping[str, Any]], caps_by_code: Mapping[str, float]
) -> dict[str, float] | None:
    """四指标计算；口径见模块 docstring，不可计算 → None（不产伪值）。

    ``caps_by_code``：``{6 位裸码: 流通市值}``（与快照 ``code`` 同口径）；
    参与集任一标的缺失（含 NaN/非正）→ None。返回键覆盖 ``INDICATOR_KEYS``。
    """
    active: list[tuple[str, float, float, Mapping[str, Any]]] = []  # (code, amount, pct_chg, row)
    for row in universe:
        if _flag(row.get("is_suspended")):
            continue  # 停牌剔除（含涨跌家数、封板家数、全市场成交额）
        amount = _finite(row.get("amount"))
        if amount is None:
            return None  # 非停牌行成交额缺失：不可计算，不按 0 折算
        if amount <= 0:
            continue  # 零成交剔除
        pct_chg = _finite(row.get("pct_chg"))
        if pct_chg is None:
            return None  # 无法分类涨跌平：不静默归入任一类
        active.append((str(row.get("code") or ""), amount, pct_chg, row))
    if not active:
        return None  # 空 universe / 全部剔除：无样本，不产伪指标

    caps: dict[str, float] = {}
    for code, _, _, _ in active:
        cap = _finite(caps_by_code.get(code))
        if cap is None:
            return None  # caps 覆盖不全：不缩样本冒充
        caps[code] = cap

    group_size = math.ceil(len(active) * SMALL_CAP_QUANTILE)
    ordered = sorted(active, key=lambda item: (caps[item[0]], item[0]))
    small_amount = sum(item[1] for item in ordered[:group_size])
    total_amount = sum(item[1] for item in active)
    up_count = sum(1 for _, _, pct_chg, _ in active if pct_chg > 0)
    down_count = sum(1 for _, _, pct_chg, _ in active if pct_chg < 0)
    return {
        "small_cap_share": small_amount / total_amount,
        "up_count": up_count,
        "down_count": down_count,
        "flat_count": len(active) - up_count - down_count,
        "limit_up_count": sum(1 for *_, row in active if _flag(row.get("limit_up_sealed"))),
        "limit_down_count": sum(1 for *_, row in active if _flag(row.get("limit_down_sealed"))),
        "total_amount": total_amount,
    }


def percentile_rank(
    values: Sequence[float], value: float, *, min_history: int = MIN_HISTORY
) -> float | None:
    """``value`` 在 ``values`` 窗口内的百分位（``#{x≤v}/n``）；样本不足 → None。

    口径（metrics.md §1.14）：窗口 = 最近 ``HISTORY_WINDOW`` 个交易日（含当日），
    分母为窗口样本数 n；``n < min_history`` → None（调用方注明
    ``insufficient_history``，不折算）。
    """
    count = len(values)
    if count == 0 or count < min_history:
        return None
    return sum(1 for item in values if item <= value) / count
