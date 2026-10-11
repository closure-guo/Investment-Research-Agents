"""判定引擎(add-track-record):horizon + 中性带 + superseded。纯函数,合成 DataFrame 可测。

与 settle.py(止损/目标/超期)语义不同——入场基准由 K 线派生(derive_entry,非存档参考价),
horizon 到点按区间超额收益判定:long/short → resolved_win/loss/neutral;neutral 方向
→ avoidance_win/loss/neutral(回避正确/错失上涨/无差别,不进胜率)。停牌/退市由 job 层
按连续无行情标记 unresolvable。
"""

from __future__ import annotations

import os
from dataclasses import dataclass

import pandas as pd

MAX_HORIZON_DAYS = 252
DEFAULT_NEUTRAL_BAND = 0.02
# outcome 评估默认判定窗口（delta update-decision-settlement-contract；口径 metrics.md §1.9）
DEFAULT_HORIZON_DAYS = int(os.getenv("OUTCOME_DEFAULT_HORIZON_DAYS", "20"))
CLOSE_TIME_CUTOFF = "15:00"  # 归属日收盘切分：created_at 时间部分 < 此值 → 归属日收盘入场

# neutral 回避判定的结果域（写 avoidance_status 列）
AVOIDANCE_STATUSES: tuple[str, ...] = ("avoidance_win", "avoidance_loss", "avoidance_neutral")
# 回避判定的生命周期终态（写 status 列）：离开 open 池的标记，避免日批无限重结算；
# 结果本身只入 avoidance_status，故与 AVOIDANCE_STATUSES 是两个域。
TERMINAL_AVOIDANCE_STATUS = "avoidance"


@dataclass
class Resolution:
    status: str  # resolved_win/loss/neutral 或 avoidance_win/loss/neutral
    entry_date: str
    entry_price: float
    exit_date: str
    exit_price: float
    raw_return: float
    excess_return: float | None
    hold_days: int
    resolution_rule: str  # expiry / superseded


def _effective_horizon(prediction: dict) -> int:
    return max(1, min(int(prediction.get("horizon_days", MAX_HORIZON_DAYS)), MAX_HORIZON_DAYS))


def _bench_close_on_or_before(benchmark: pd.DataFrame, date: str) -> float | None:
    # #57:入口排序防御——不隐式依赖调用方升序,乱序输入按日期取最后收盘
    eligible = benchmark[benchmark["日期"] <= date].sort_values("日期")
    if eligible.empty:
        return None
    return float(eligible.iloc[-1]["收盘"])


def direction_for_action(action: str) -> str:
    """action → direction：buy→long、sell→short、其余（含 hold/watch/空）→neutral。

    单一来源：ingest（落库）/ model（decision_log 迁移）/ evals.backtest.replay
    （回测预测量构造）三处共用，禁止各自再写三目拷贝。
    update-sell-action-typing（#188）：sell 分 exit/short 两型后本映射零改动——
    exit（持有者减仓）与 short（做空建仓）的结算数学一致（均「价格下跌获益」，
    direction=short 的收益语义对两型同样成立），差异仅在执行结构（渲染/校验层
    已分型），结算层不感知 sell_type。
    """
    return {"buy": "long", "sell": "short"}.get(str(action or ""), "neutral")


def should_supersede(old: dict, new: dict) -> bool:
    """同标的方向相反或目标价不同 → 旧观点立即结算。"""
    if old.get("symbol") != new.get("symbol"):
        return False
    if old.get("direction") != new.get("direction"):
        return True
    old_t = old.get("target_price")
    new_t = new.get("target_price")
    return old_t is not None and new_t is not None and abs(float(old_t) - float(new_t)) > 1e-9


def _norm(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out["日期"] = out["日期"].astype(str).str[:10]
    return out.sort_values("日期").reset_index(drop=True)


def derive_entry(prediction: dict, kline: pd.DataFrame) -> tuple[str, float] | None:
    """派生结算入场价（date, close）：归属日收盘；收盘后决策 → 次一交易日；非交易日 → 其后首个交易日。"""
    created = str(prediction["created_at"])
    day, _, time_part = created.partition("T")
    before_close = (time_part or "00:00:00")[:5] < CLOSE_TIME_CUTOFF
    df = _norm(kline)
    rows = df[df["日期"] >= day] if before_close else df[df["日期"] > day]
    if rows.empty:
        return None
    row = rows.iloc[0]
    return str(row["日期"]), float(row["收盘"])


def derive_attribution_date(created_at: str, calendar: list[str]) -> str:
    """决策归属日：收盘前产出→当日（或其后首个交易日）；收盘后/非交易日→次一/首个交易日。

    与 derive_entry 同源（同用 CLOSE_TIME_CUTOFF，口径 §1.9-v2）；calendar 为升序
    交易日字符串列表（日批判定传基准指数交易日）。晚于全部交易日或空日历 → 返回
    产出日本身（降级：同日去重仍按自然日成立）。
    """
    day, _, time_part = str(created_at).partition("T")
    before_close = (time_part or "00:00:00")[:5] < CLOSE_TIME_CUTOFF
    if before_close:
        for d in calendar:
            if d >= day:
                return d
    else:
        for d in calendar:
            if d > day:
                return d
    return day


def day_master_ids(predictions: list[dict], calendar: list[str]) -> set[str]:
    """日主观点 id 集合：同 (symbol, 归属日) 取 created_at 最晚者（§1.9-v2 样本口径）。

    同 created_at 并列时取 prediction_id 最大者（uuid hex，确定性 tiebreak）。
    """
    groups: dict[tuple[str, str], list[dict]] = {}
    for p in predictions:
        day = derive_attribution_date(str(p["created_at"]), calendar)
        groups.setdefault((str(p["symbol"]), day), []).append(p)
    masters: set[str] = set()
    for group in groups.values():
        winner = max(group, key=lambda p: (str(p["created_at"]), str(p["prediction_id"])))
        masters.add(str(winner["prediction_id"]))
    return masters


def resolve_prediction(
    prediction: dict,
    kline: pd.DataFrame,
    benchmark: pd.DataFrame | None = None,
    neutral_band: float = DEFAULT_NEUTRAL_BAND,
) -> Resolution | None:
    """horizon 到点判定。入场基准为派生 settle_entry_price（非参考价）。

    未到点返回 None(保持 open)。区间收益:入场日之后第 horizon 个交易日的收盘价计算;
    short 方向取负(neutral 按 long 口径),判定输出 avoidance_*。
    超额 = raw_return - 同期基准收益(符号同样按方向取负)。
    停牌/一字板递延与数据缺失由 job 层处理,本函数只做到点判定。
    """
    derived = derive_entry(prediction, kline)
    if derived is None:
        return None
    entry_date, entry_price = derived
    if entry_price <= 0:
        return None
    horizon = _effective_horizon(prediction)
    direction = prediction.get("direction", "long")
    df = _norm(kline)
    rows = df[df["日期"] > entry_date].reset_index(drop=True)
    if len(rows) < horizon:
        return None
    exit_row = rows.iloc[horizon - 1]
    exit_price = float(exit_row["收盘"])
    exit_date = str(exit_row["日期"])
    sign = -1.0 if direction == "short" else 1.0  # neutral 按 long 口径
    raw_return = sign * (exit_price / entry_price - 1.0)
    excess = raw_return
    if benchmark is not None and not benchmark.empty:
        bench_df = _norm(benchmark)
        entry_bench = _bench_close_on_or_before(bench_df, entry_date)
        exit_bench = _bench_close_on_or_before(bench_df, exit_date)
        if entry_bench is not None and exit_bench is not None:
            excess = raw_return - sign * (exit_bench / entry_bench - 1.0)
    # 分类前先消浮点噪声:102/100−1 = 0.020000000000000018 会被误判越出 ±2% 中性带
    excess = round(excess, 6)
    band = float(neutral_band)
    if direction == "neutral":
        status = (
            "avoidance_win"
            if excess < -band
            else ("avoidance_loss" if excess > band else "avoidance_neutral")
        )
    else:
        status = (
            "resolved_win"
            if excess > band
            else ("resolved_loss" if excess < -band else "resolved_neutral")
        )
    return Resolution(
        status=status,
        entry_date=entry_date,
        entry_price=round(entry_price, 6),
        exit_date=exit_date,
        exit_price=exit_price,
        raw_return=round(raw_return, 6),
        excess_return=round(excess, 6),
        hold_days=horizon,
        resolution_rule="expiry",
    )
