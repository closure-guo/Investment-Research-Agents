"""判定引擎(add-track-record):horizon + 中性带 + superseded。纯函数,合成 DataFrame 可测。

与 settle.py(止损/目标/超期)语义不同——horizon 到点按区间超额收益判定 win/loss/neutral;
short 方向对称;neutral 不进胜率。停牌/退市由 job 层按连续无行情标记 unresolvable。
"""

from __future__ import annotations

import os
from dataclasses import dataclass

import pandas as pd

MAX_HORIZON_DAYS = 252
DEFAULT_NEUTRAL_BAND = 0.02
# outcome 评估默认判定窗口（delta update-decision-settlement-contract；口径 metrics.md §1.8）
DEFAULT_HORIZON_DAYS = int(os.getenv("OUTCOME_DEFAULT_HORIZON_DAYS", "20"))
CLOSE_TIME_CUTOFF = "15:00"  # 归属日收盘切分：created_at 时间部分 < 此值 → 归属日收盘入场


@dataclass
class Resolution:
    status: str  # resolved_win / resolved_loss / resolved_neutral
    exit_price: float
    raw_return: float
    excess_return: float | None
    resolution_rule: str  # expiry / superseded


def _effective_horizon(prediction: dict) -> int:
    return max(1, min(int(prediction.get("horizon_days", MAX_HORIZON_DAYS)), MAX_HORIZON_DAYS))


def _bench_close_on_or_before(benchmark: pd.DataFrame, date: str) -> float | None:
    eligible = benchmark[benchmark["日期"] <= date]
    if eligible.empty:
        return None
    return float(eligible.iloc[-1]["收盘"])


def should_supersede(old: dict, new: dict) -> bool:
    """同标的方向相反或目标价不同 → 旧观点立即结算。"""
    if old.get("symbol") != new.get("symbol"):
        return False
    if old.get("direction") != new.get("direction"):
        return True
    old_t = old.get("target_price")
    new_t = new.get("target_price")
    return old_t is not None and new_t is not None and abs(float(old_t) - float(new_t)) > 1e-9


def resolve_prediction(
    prediction: dict,
    kline: pd.DataFrame,
    benchmark: pd.DataFrame | None = None,
    neutral_band: float = DEFAULT_NEUTRAL_BAND,
) -> Resolution | None:
    """horizon 到点判定。未到点返回 None(保持 open)。

    区间收益:创建日之后第 horizon 个交易日的收盘价计算;short 方向取负。
    超额 = raw_return - 同期基准收益(符号同样按方向取负)。
    停牌/一字板递延与数据缺失由 job 层处理,本函数只做到点判定。
    """
    entry_price = float(prediction["entry_price"])
    if entry_price <= 0:
        return None
    horizon = _effective_horizon(prediction)
    direction = prediction.get("direction", "long")
    created = str(prediction["created_at"])[:10]
    df = kline.copy()
    df["日期"] = df["日期"].astype(str).str[:10]
    rows = df[df["日期"] > created].sort_values("日期").reset_index(drop=True)
    if len(rows) < horizon:
        return None
    exit_row = rows.iloc[horizon - 1]
    exit_price = float(exit_row["收盘"])
    sign = 1.0 if direction == "long" else -1.0
    raw_return = sign * (exit_price / entry_price - 1.0)
    excess = raw_return
    if benchmark is not None and not benchmark.empty:
        bench_df = benchmark.copy()
        bench_df["日期"] = bench_df["日期"].astype(str).str[:10]
        entry_bench = _bench_close_on_or_before(bench_df, created)
        exit_bench = _bench_close_on_or_before(bench_df, str(exit_row["日期"]))
        if entry_bench is not None and exit_bench is not None:
            bench_ret = sign * (exit_bench / entry_bench - 1.0)
            excess = raw_return - bench_ret
    band = float(neutral_band)
    if excess > band:
        status = "resolved_win"
    elif excess < -band:
        status = "resolved_loss"
    else:
        status = "resolved_neutral"
    return Resolution(
        status=status,
        exit_price=exit_price,
        raw_return=round(raw_return, 6),
        excess_return=round(excess, 6),
        resolution_rule="expiry",
    )
