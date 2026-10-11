"""分层市场状态抽样（spec decision-backtest「分层市场状态抽样」）。

regime 判定：窗口内基准指数总涨跌幅（> +10% bull / < -10% bear / 其间 sideways）。
每 regime ≥ per_regime 只标的；缺 regime 直接抛错（禁止单边行情汇报）。
"""

from __future__ import annotations

import numpy as np
import pandas as pd

UP_THRESHOLD = 0.10
DOWN_THRESHOLD = -0.10


def classify_regime(
    window_kline: pd.DataFrame,
    *,
    up_threshold: float = UP_THRESHOLD,
    down_threshold: float = DOWN_THRESHOLD,
) -> str:
    """窗口首尾收盘总涨跌幅判 regime：bull / bear / sideways。"""
    close = window_kline["收盘"].astype(float)
    if close.empty:
        raise ValueError("空窗口")
    total = close.iloc[-1] / close.iloc[0] - 1.0
    if total > up_threshold:
        return "bull"
    if total < down_threshold:
        return "bear"
    return "sideways"


def stratified_sample(
    index_kline: pd.DataFrame,
    stock_pool: list[str],
    *,
    per_regime: int = 10,
    window_days: int = 120,
    seed: int = 42,
    min_decision_date: str | None = None,
) -> list[dict]:
    """滑窗扫描指数历史找三种 regime 窗口；每 regime 抽 per_regime 只标的 +
    决策日（窗口末日）。样本不足抛 ValueError。

    同 seed 完全可复现（抽样与窗口扫描均确定）。
    min_decision_date（issue #172 深历史执法）：决策日早于该日的窗口跳过，
    cutoff 之后凑不齐三 regime 按既有语义抛 ValueError（禁止单边汇报）。
    """
    dates = index_kline["日期"].astype(str).str[:10]
    n = len(index_kline)
    rng = np.random.default_rng(seed)
    found: dict[str, dict] = {}  # regime → {"end_idx", "decision_date"}
    step = max(1, window_days // 4)
    for end in range(window_days, n + 1, step):
        decision_date = str(dates.iloc[end - 1])
        if min_decision_date and decision_date < min_decision_date:
            continue
        window = index_kline.iloc[end - window_days : end]
        regime = classify_regime(window)
        if regime not in found:
            found[regime] = {"end_idx": end, "decision_date": decision_date}
        if len(found) == 3:
            break
    missing = {"bull", "bear", "sideways"} - set(found)
    if missing:
        raise ValueError(f"指数历史未覆盖 regime: {sorted(missing)}（禁止只在单边行情上汇报）")
    sample: list[dict] = []
    for regime, info in found.items():
        if len(stock_pool) < per_regime:
            raise ValueError(f"regime {regime} 标的池不足 {per_regime}")
        chosen = list(rng.choice(stock_pool, size=per_regime, replace=False))
        for code in chosen:
            sample.append(
                {"code": str(code), "regime": regime, "decision_date": info["decision_date"]}
            )
    return sample
