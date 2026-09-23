"""结算纯函数(design 决策 2/3/4/5)。全部合成 DataFrame 可测,不调 LLM/网络。

[已废弃] 生产判定已切到 finance_agent.outcome.track_record.judgment.resolve_prediction
(horizon + 区间超额 + ±2% 中性带;delta update-decision-settlement-contract),
本模块仅供两类只读消费方:
  1. GET /api/decisions* 读取存量 decision_log 的历史 settlement 字段;
  2. evals/golden/gates.py 的 settlement_rule 判例复算(语义独立,不在本次切换范围)。
新代码不得再调用 evaluate_decision;evaluate_decision 首次调用打一次 WARN。

结算优先级:止损 > 目标 > 超期(expired);同日触及两者按止损(保守)。
一字板(open==high==low==close)触及 → 递延至打开首日开盘价,hold_days 含等待日。
递延期间不做超期判定(一字板无法成交,先到先结算优先),hold_days 可超过 max_hold_days。
一字板是启发式判定:对流动性极差的单 tick 日(全天仅一笔成交,开高低收自然相等)
可能误判为一字板而递延,属可接受的近似。
停牌无 K 线行 → 行驱动迭代自然顺延,hold_days 只数有数据的交易日。
评估起点:decision_date 之后的行(T+1 起评)。
跳空穿越:非一字板触发日开盘价已越过触发位时,按实际可成交价结算
(stop 取 min(开盘, stop_loss),target 取 max(开盘, target_price)),不虚记成交价。
"""

from __future__ import annotations

import logging
import math
import os
from dataclasses import dataclass
from typing import cast

import pandas as pd

logger = logging.getLogger(__name__)

# 废弃提示只打一次(模块级标志):避免批处理/测试对每次调用重复告警刷屏
_DEPRECATION_WARNED = False

MAX_HOLD_DAYS = int(os.getenv("MAX_HOLD_DAYS", "20"))
STALE_DAYS = int(os.getenv("DECISION_STALE_DAYS", "5"))
BENCHMARK_CODE = os.getenv("BENCHMARK_CODE", "000300")


@dataclass
class Settlement:
    status: str  # hit_stop / hit_target / expired
    settle_date: str
    settle_price: float
    hold_days: int
    decision_return: float
    benchmark_return: float | None
    decision_excess: float | None
    # 0.0% 平局(不盈不亏)按 miss 计:decision_hit = decision_return > 0
    decision_hit: bool


def _direction_sign(action: str) -> float:
    """buy 为正;sell/hold/watch 取负(建议不买/卖出后下跌为正)。"""
    return 1.0 if action == "buy" else -1.0


def _is_one_word_board(row: pd.Series) -> bool:
    """一字板:开=高=低=收(全天未打开)。"""
    return bool(row["开盘"] == row["最高"] == row["最低"] == row["收盘"])


def _price_or_none(value: object) -> float | None:
    """None/NaN → None。NaN 参与比较恒为 False,会静默失效,入口显式归一。"""
    if value is None:
        return None
    price = float(value)  # type: ignore[arg-type]
    return None if math.isnan(price) else price


def _normalize_dates(df: pd.DataFrame) -> pd.DataFrame:
    """日期列归一化为 'YYYY-MM-DD' 字符串(幂等,不改动调用方 DataFrame)。

    akshare stock_zh_a_hist 的日期列是 datetime.date 对象(与 str 比较会
    TypeError);pd.Timestamp 的 str() 带时间分量。astype(str).str[:10]
    对 str / datetime.date / pd.Timestamp 均成立。
    """
    df = df.copy()
    df["日期"] = df["日期"].astype(str).str[:10]
    return df


def _bench_close_on_or_before(benchmark: pd.DataFrame, date: str) -> float | None:
    """基准在 date 或之前最后一个收盘(日期列须已归一化为 str)。"""
    eligible = benchmark[benchmark["日期"] <= date]
    if eligible.empty:
        return None
    return float(eligible.iloc[-1]["收盘"])


def evaluate_decision(
    decision: dict,
    kline: pd.DataFrame,
    benchmark: pd.DataFrame | None,
    max_hold_days: int = MAX_HOLD_DAYS,
) -> Settlement | None:
    """评估单个 open 决策是否结算。未触发返回 None。

    [已废弃] 见模块 docstring:生产判定走 track_record.judgment.resolve_prediction。
    entry_price 非正(数据损坏)→ WARN 并返回 None:保持 open 等待,
    不产出符号错乱的结算;持续无行情时由 job 层 data_stale 机制标记告警。
    """
    global _DEPRECATION_WARNED
    if not _DEPRECATION_WARNED:
        _DEPRECATION_WARNED = True
        logger.warning(
            "outcome.settle.evaluate_decision 已废弃:生产判定走 "
            "track_record.judgment.resolve_prediction(horizon + 超额 + ±2% 带)"
        )
    entry_price = float(decision["entry_price"])
    if entry_price <= 0:
        logger.warning(
            "entry_price 非正(%s),本批跳过结算: %s",
            entry_price,
            decision.get("decision_id"),
        )
        return None
    stop_loss = _price_or_none(decision.get("stop_loss"))
    target_price = _price_or_none(decision.get("target_price"))
    sign = _direction_sign(decision["action"])
    decision_date = str(decision["timestamp"])[:10]

    df = _normalize_dates(kline)
    rows = df[df["日期"] > decision_date].sort_values("日期").reset_index(drop=True)
    if rows.empty:
        return None
    bench_df = _normalize_dates(benchmark) if benchmark is not None else None

    pending_trigger: str | None = None  # 一字板递延中的触发类型
    for i in range(len(rows)):
        row = rows.iloc[i]
        hold_days = i + 1

        if pending_trigger is not None:
            # 一字板递延中:等待打开首日,以开盘价结算(递延期间不做超期判定)
            if _is_one_word_board(row):
                continue
            return _settle(
                decision,
                pending_trigger,
                str(row["日期"]),
                float(row["开盘"]),
                hold_days,
                entry_price,
                sign,
                bench_df,
            )

        hit_stop = stop_loss is not None and float(row["最低"]) <= stop_loss
        hit_target = target_price is not None and float(row["最高"]) >= target_price

        if hit_stop or hit_target:
            trigger = "hit_stop" if hit_stop else "hit_target"  # 同日止损优先
            if _is_one_word_board(row):
                pending_trigger = trigger  # 未成交,递延
                continue
            # 跳空穿越按实际可成交价结算。不变量:hit_stop 为真 ⟹ stop_loss
            # 非 None,hit_target 为真 ⟹ target_price 非 None(上方判定含
            # is not None 短路),cast 仅为 mypy 收窄。
            if trigger == "hit_stop":
                price = min(float(row["开盘"]), cast(float, stop_loss))
            else:
                price = max(float(row["开盘"]), cast(float, target_price))
            return _settle(
                decision, trigger, str(row["日期"]), price, hold_days, entry_price, sign, bench_df
            )

        if hold_days >= max_hold_days:
            return _settle(
                decision,
                "expired",
                str(row["日期"]),
                float(row["收盘"]),
                hold_days,
                entry_price,
                sign,
                bench_df,
            )

    return None  # 行数不足或一字板未打开:继续 open


def _settle(
    decision: dict,
    status: str,
    settle_date: str,
    settle_price: float,
    hold_days: int,
    entry_price: float,
    sign: float,
    benchmark: pd.DataFrame | None,
) -> Settlement:
    decision_return = sign * (settle_price - entry_price) / entry_price
    benchmark_return: float | None = None
    decision_excess: float | None = None
    if benchmark is not None and not benchmark.empty:
        entry_bench = _bench_close_on_or_before(benchmark, str(decision["timestamp"])[:10])
        settle_bench = _bench_close_on_or_before(benchmark, settle_date)
        if entry_bench is not None and settle_bench is not None:
            benchmark_return = sign * (settle_bench - entry_bench) / entry_bench
            decision_excess = decision_return - benchmark_return
    return Settlement(
        status=status,
        settle_date=settle_date,
        settle_price=settle_price,
        hold_days=hold_days,
        decision_return=decision_return,
        benchmark_return=benchmark_return,
        decision_excess=decision_excess,
        decision_hit=decision_return > 0,
    )
