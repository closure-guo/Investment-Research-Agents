"""判定引擎(add-track-record):horizon + 中性带 + superseded。纯函数,合成 DataFrame 可测。

与 settle.py(止损/目标/超期)语义不同——入场基准由 K 线派生(derive_entry,非存档参考价),
horizon 到点按区间超额收益判定:long/short → resolved_win/loss/neutral;neutral 方向
→ avoidance_win/loss/neutral(回避正确/错失上涨/无差别,不进胜率)。停牌/退市由 job 层
按连续无行情标记 unresolvable。
"""

from __future__ import annotations

import math
import os
import statistics
from dataclasses import dataclass

import pandas as pd

from finance_agent.data.market_caliber import (
    benchmark_close_on_or_before as _bench_close_on_or_before,
)

MAX_HORIZON_DAYS = 252
DEFAULT_NEUTRAL_BAND = 0.02
# 波动率自适应中性带（update-outcome-band-volatility-scaled，2026-11-28 拍板）：
# 辅助分类带 = max(1.5%, 0.5 × 决策归属日起 20 个交易日窗口波动)。窗口波动 =
# 归属日前 20 个交易日日收益标准差 × sqrt(20)（"年化波动率折算的窗口波动"）。
# 波动不可得（K 线不足 21 点/数据异常）→ 回落既有全局 2%，行为与切点前一致。
BAND_FLOOR = 0.015
BAND_VOL_MULTIPLIER = 0.5
BAND_VOL_WINDOW_DAYS = 20
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
    # update-outcome-band-volatility-scaled：本次判定实际使用的中性带（波动率自适应
    # 或回落 2%）。落库列 neutral_band_used，供「新口径从切点起算」分段与审计。
    neutral_band_used: float = DEFAULT_NEUTRAL_BAND


def realized_daily_vol(
    kline: pd.DataFrame, as_of: str, *, days: int = BAND_VOL_WINDOW_DAYS
) -> float | None:
    """``as_of``（含）之前 ``days`` 个交易日日收益的样本标准差（ddof=1）。

    不足 ``days + 1`` 个收盘点 / 非正值 / 非有限 → ``None``（调用方回落固定带）。
    """
    df = _norm(kline)
    rows = df[df["日期"] <= as_of].tail(days + 1)
    if len(rows) < days + 1:
        return None
    closes = [float(v) for v in rows["收盘"].tolist()]
    if any(v <= 0 for v in closes):
        return None
    rets = [closes[i] / closes[i - 1] - 1.0 for i in range(1, len(closes))]
    if len(rets) < 2:
        return None
    sd = statistics.stdev(rets)
    if not math.isfinite(sd) or sd <= 0:
        return None
    return sd


def scaled_neutral_band(daily_vol: float | None) -> float:
    """波动率自适应中性带：``max(1.5%, 0.5 × daily_vol × sqrt(20))``。

    ``daily_vol`` 不可得（None/非正/非有限）→ 回落 ``DEFAULT_NEUTRAL_BAND``（2%），
    与切点前行为一致；结果保留 6 位小数（与落库精度对齐）。
    """
    if daily_vol is None or not math.isfinite(daily_vol) or daily_vol <= 0:
        return DEFAULT_NEUTRAL_BAND
    window_vol = daily_vol * math.sqrt(BAND_VOL_WINDOW_DAYS)
    return round(max(BAND_FLOOR, BAND_VOL_MULTIPLIER * window_vol), 6)


def _effective_horizon(prediction: dict) -> int:
    return max(1, min(int(prediction.get("horizon_days", MAX_HORIZON_DAYS)), MAX_HORIZON_DAYS))


def direction_for_action(action: str, sell_type: str | None = None) -> str:
    """action（+sell_type 分型）→ direction：buy→long、sell+exit→neutral（回避语义）、
    sell+short→short、sell 未申报→short（回测腿/历史形态）、其余→neutral。

    单一来源：ingest（落库）/ model（decision_log 迁移）/ evals.backtest.replay
    （回测预测量构造）三处共用，禁止各自再写三目拷贝。

    update-exit-avoidance-semantics（#606 影响 3，2026-11-22 拍板）：此前「exit 与
    short 结算数学一致」的论断被 #606 影响 3 推翻——exit 叙事的反弹减仓带在
    entry 上方（价格上涨获益），与做空（价格下跌获益）方向相反；horizon 做空
    结算对 exit 系统性反向（深南电路实证：反弹减仓最优执行被记 short −13.6%）。
    exit 改落 neutral 走回避判定（离场后标的跑输 = 离场正确，与 watch/hold 数学
    同构），复用回避统计全套；未申报分型维持 short（回测腿语义不变、存量读数
    不追溯——§1.9 预登记纪律，历史读数按产出时版本解释）。
    """
    action = str(action or "")
    if action == "buy":
        return "long"
    if action == "sell":
        if sell_type == "exit":
            return "neutral"
        return "short"  # short 型 + 未申报（历史/回测形态）
    return "neutral"


def should_supersede(old: dict, new: dict) -> bool:
    """同标的**方向翻转**才 supersede（update-view-min-survival，2026-11-04）。

    同向重复（含目标价调整）不再结算旧观点：目标价变化是同一方向观点的跟踪更新，
    不是方向改变。旧实现把目标价差异也当 supersede 条件，叠加「同标的几乎每日
    重新分析」，一条连续看空观点被切成 1~2 天的碎片——生产实测方向性观点中位存活
    2 天、活过 20 天者为 **0%**，T+20 主指标因此永远产不出读数。

    目标价仍随各自观点独立落库与判定，不因本条规则丢失。
    """
    if old.get("symbol") != new.get("symbol"):
        return False
    return old.get("direction") != new.get("direction")


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


def settle_at_exit(
    *,
    direction: str,
    entry_date: str,
    entry_price: float,
    exit_date: str,
    exit_price: float,
    benchmark: pd.DataFrame | None,
    resolution_rule: str,
    hold_days: int,
    neutral_band: float = DEFAULT_NEUTRAL_BAND,
) -> Resolution:
    """按给定出场结算：收益 / 超额 / ±2% 中性带分类的**唯一实现**。

    horizon 到点（`expiry`）与提前结算（`superseded`）共用同一套数学
    （update-superseded-excess）：同一条观点不应因为「被新观点覆盖」还是
    「走到窗口」而得到不同的超额与分类。基准不可得时超额退化为 raw_return
    （与既有降级口径一致，不写 NULL）。
    """
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
        exit_price=round(exit_price, 6),
        raw_return=round(raw_return, 6),
        excess_return=excess,
        hold_days=hold_days,
        resolution_rule=resolution_rule,
        neutral_band_used=round(float(band), 6),
    )


def resolve_prediction(
    prediction: dict,
    kline: pd.DataFrame,
    benchmark: pd.DataFrame | None = None,
    neutral_band: float | None = None,
    *,
    entry_override: tuple[str, float] | None = None,
) -> Resolution | None:
    """horizon 到点判定。入场基准为派生 settle_entry_price（非参考价）。

    ``neutral_band``（update-outcome-band-volatility-scaled）：缺省 ``None`` →
    按入场日之前 20 个交易日波动率自适应计算（``scaled_neutral_band``）；显式传值
    则用该值（回测/测试可复现固定带）。

    ``entry_override``（add-matching-engine T4）：回放侧经撮合引擎得到真实成交价时传
    ``(执行日, 成交价)`` 覆盖 derive_entry 的派生入场；给定时跳过派生，其余判定数学
    （horizon / 中性带 / 超额 / 回避语义）零改动——「决策日收盘派生价」与「D+1 撮合价」
    两种口径共用同一套判定，不另造一份（spec「单一撮合实现」）。``entry_price <= 0``
    与既有派生入场同口径返回 None。

    未到点返回 None(保持 open)。区间收益:入场日之后第 horizon 个交易日的收盘价计算;
    short 方向取负(neutral 按 long 口径),判定输出 avoidance_*。
    超额 = raw_return - 同期基准收益(符号同样按方向取负)。
    停牌/一字板递延与数据缺失由 job 层处理,本函数只做到点判定。
    """
    derived = entry_override if entry_override is not None else derive_entry(prediction, kline)
    if derived is None:
        return None
    entry_date, entry_price = derived
    if entry_price <= 0:
        return None
    band = (
        float(neutral_band)
        if neutral_band is not None
        else scaled_neutral_band(realized_daily_vol(kline, entry_date))
    )
    horizon = _effective_horizon(prediction)
    direction = prediction.get("direction", "long")
    df = _norm(kline)
    rows = df[df["日期"] > entry_date].reset_index(drop=True)
    if len(rows) < horizon:
        return None
    exit_row = rows.iloc[horizon - 1]
    exit_price = float(exit_row["收盘"])
    exit_date = str(exit_row["日期"])
    return settle_at_exit(
        direction=str(direction),
        entry_date=entry_date,
        entry_price=entry_price,
        exit_date=exit_date,
        exit_price=exit_price,
        benchmark=benchmark,
        resolution_rule="expiry",
        hold_days=horizon,
        neutral_band=band,
    )
