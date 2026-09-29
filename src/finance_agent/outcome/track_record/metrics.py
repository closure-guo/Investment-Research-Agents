"""add-track-record-stage-b：组合净值与风险收益指标引擎（P4 收益与风险成对）。

输入为 daily_marks（每观点每日累计收益 cum_return + 基准价），产出：
- 组合日收益：等权 1/N（当日有盯市的 N 条观点日收益均值；空仓记 0；
  缺数据观点当日不计入 N —— 停牌/无行情不按 0 惩罚）
- 净值曲线：agent 净值（自 1.0 累积）与基准净值（同日期序列）
- 指标：年化收益/波动率/夏普（无风险利率默认 2%，env TRACK_RISK_FREE_RATE 可配）/
  最大回撤/风险分 clip(round(0.6*dd% + 0.4*vol%), 1, 10)（映射表配置化）
"""

from __future__ import annotations

import math
import os
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

RISK_FREE_RATE = float(os.getenv("TRACK_RISK_FREE_RATE", "0.02"))

# 风险分 → 标签 映射表（配置化：改此处即可调整分档）
RISK_LABELS: tuple[tuple[tuple[int, int], str], ...] = (
    ((1, 3), "低"),
    ((4, 6), "中"),
    ((7, 8), "高"),
    ((9, 10), "极高"),
)

_TRADING_DAYS = 252


def risk_score_from(
    max_drawdown: float | None,
    volatility: float | None,
) -> int | None:
    """风险分 = clip(round(0.6*d% + 0.4*v%), 1, 10)。输入为小数。"""
    if max_drawdown is None or volatility is None:
        return None
    score = round(0.6 * max_drawdown * 100 + 0.4 * volatility * 100)
    return max(1, min(10, score))


def risk_label_from(score: int | None) -> str | None:
    if score is None:
        return None
    for (lo, hi), label in RISK_LABELS:
        if lo <= score <= hi:
            return label
    return None


@dataclass
class PortfolioMetrics:
    """指标计算结果；缺数据字段为 None。"""

    annual_return: float | None = None
    volatility: float | None = None
    sharpe: float | None = None
    max_drawdown: float | None = None
    risk_score: int | None = None
    risk_label: str | None = None
    nav_points: list[dict[str, Any]] = field(default_factory=list)


def daily_portfolio_returns(
    marks: list[dict[str, Any]],
    calendar_dates: list[str] | None = None,
    exclude_prediction_ids: set[str] | None = None,
) -> dict[str, float]:
    """按日聚合组合收益：每观点日收益 = 当日 cum_return - 前一盯市日 cum_return。

    首盯市日贡献 0（现金口径）：入场→首盯市日的漂移不进组合净值（incident 032
    根因 C），组合日收益纯盯市差分、对坏参考价免疫。等权平均当日有盯市的各观点。
    传入 calendar_dates（基准交易日历）时序列覆盖首个盯市日以来全部交易日，无盯市
    交易日（空仓/整体缺数据）记 0；缺省维持 marks-only 口径。
    exclude_prediction_ids 把 neutral 方向观点排除出组合聚合（根因 A）。
    """
    excl = exclude_prediction_ids or set()
    by_pred: dict[str, list[tuple[str, float]]] = defaultdict(list)
    for m in marks:
        if m.get("cum_return") is None:
            continue
        if m["prediction_id"] in excl:
            continue
        by_pred[m["prediction_id"]].append((str(m["mark_date"]), float(m["cum_return"])))
    for pts in by_pred.values():
        pts.sort()

    per_pred: dict[str, dict[str, float]] = {}
    for pid, pts in by_pred.items():
        daily: dict[str, float] = {}
        prev: float | None = None
        for d, cum in pts:
            daily[d] = cum - prev if prev is not None else 0.0
            prev = cum
        per_pred[pid] = daily

    if calendar_dates:
        all_marked = [d for dd in per_pred.values() for d in dd]
        if not all_marked:
            return {}
        first_mark = min(all_marked)
        # 日历骨架之外保留 mark 日期：基准日 K 滞后/截断时个股新鲜 mark 不得静默丢弃
        dates = sorted({d for d in calendar_dates if d >= first_mark} | set(all_marked))
    else:
        dates = sorted({d for dd in per_pred.values() for d in dd})
    out: dict[str, float] = {}
    for d in dates:
        rets = [per_pred[pid][d] for pid in per_pred if d in per_pred[pid]]
        out[d] = sum(rets) / len(rets) if rets else 0.0
    return out


def _benchmark_returns(marks: list[dict[str, Any]]) -> dict[str, float]:
    """基准日收益：同日期序列上 benchmark_price 的逐日变化（首日为 0 基准）。"""
    by_date: dict[str, float] = {}
    for m in marks:
        if m.get("benchmark_price") is None:
            continue
        d = str(m["mark_date"])
        by_date.setdefault(d, float(m["benchmark_price"]))
    dates = sorted(by_date)
    out: dict[str, float] = {}
    prev: float | None = None
    for d in dates:
        out[d] = by_date[d] / prev - 1.0 if prev else 0.0
        prev = by_date[d]
    return out


def _cum_nav(daily_returns: dict[str, float]) -> dict[str, float]:
    nav = 1.0
    out: dict[str, float] = {}
    for d in sorted(daily_returns):
        nav *= 1.0 + daily_returns[d]
        out[d] = nav
    return out


def compute_metrics_from_marks(
    marks: list[dict[str, Any]],
    risk_free_rate: float = RISK_FREE_RATE,
    calendar_dates: list[str] | None = None,
    benchmark_by_date: dict[str, float] | None = None,
    exclude_prediction_ids: set[str] | None = None,
) -> PortfolioMetrics:
    """由 daily_marks 计算组合指标与双净值曲线。

    净值口径（incident 032 后）：agent 与 benchmark 双线以首个盯市日为基日归一
    1.0；观点首盯市日贡献 0，组合日收益纯盯市差分（入场参考价不进净值）。
    传入 calendar_dates（基准交易日历）时序列覆盖首个盯市日以来全部交易日，
    空仓日记 0，年化/波动 n = 交易日数；基准净值取 benchmark_by_date 按日历推进
    （缺失日沿用前值持平）；缺省退化为 marks-only 口径（基准取 marks 内
    benchmark_price）。
    """
    excl = exclude_prediction_ids or set()
    if excl:
        marks = [m for m in marks if m["prediction_id"] not in excl]
    rets = daily_portfolio_returns(marks, calendar_dates=calendar_dates)
    if not rets:
        return PortfolioMetrics()

    agent_cum = _cum_nav(rets)
    # 双线统一基日：agent 首个盯市日归一 1.0（真实累积/首日累积）
    dates = sorted(agent_cum)
    first_agent = agent_cum[dates[0]]
    agent_nav = {d: agent_cum[d] / first_agent for d in dates}

    bench_cum: dict[str, float] = {}
    if benchmark_by_date:
        base: float | None = None
        last: float | None = None
        for d in dates:
            close = benchmark_by_date.get(d)
            if close is not None and close > 0:
                if base is None:
                    base = close
                    last = 1.0
                else:
                    last = close / base
            # 基准收盘缺失的交易日沿用前值持平（净值不倒退、图线不断点）
            if last is not None:
                bench_cum[d] = last
    else:
        bench_ret = _benchmark_returns(marks)
        bench_cum = _cum_nav(bench_ret) if bench_ret else {}

    n = len(dates)
    final_nav = agent_cum[dates[-1]]
    annual = (final_nav ** (_TRADING_DAYS / n)) - 1.0 if n > 0 and final_nav > 0 else None

    ret_values = [rets[d] for d in dates]
    vol = (
        (sum((r - sum(ret_values) / n) ** 2 for r in ret_values) / n) ** 0.5
        * math.sqrt(_TRADING_DAYS)
        if n > 1
        else None
    )

    sharpe = None
    if annual is not None and vol:
        sharpe = (annual - risk_free_rate) / vol

    drawdown = 0.0
    peak = 0.0
    for d in dates:
        peak = max(peak, agent_cum[d])
        if peak > 0:
            drawdown = min(drawdown, agent_cum[d] / peak - 1.0)
    max_dd = abs(drawdown) if n > 0 else None

    risk_score = risk_score_from(max_dd, vol)

    nav_points = [
        {
            "date": d,
            "agent_nav": round(agent_nav[d], 6),
            "benchmark_nav": round(bench_cum[d], 6) if d in bench_cum else None,
        }
        for d in dates
    ]
    return PortfolioMetrics(
        annual_return=round(annual, 6) if annual is not None else None,
        volatility=round(vol, 6) if vol is not None else None,
        sharpe=round(sharpe, 6) if sharpe is not None else None,
        max_drawdown=round(max_dd, 6) if max_dd is not None else None,
        risk_score=risk_score,
        risk_label=risk_label_from(risk_score),
        nav_points=nav_points,
    )


def build_equity_curve_points(
    db_path: Any = None,
    calendar_dates: list[str] | None = None,
    benchmark_by_date: dict[str, float] | None = None,
) -> list[dict[str, Any]]:
    """读库内全部 daily_marks → 净值点序列（供 equity_curve 表与 API 共用）。

    calendar_dates/benchmark_by_date 由日批透传（基准交易日历与收盘）；缺省
    退化为 marks-only 口径。neutral 方向观点一律排除（incident 032 根因 A）。
    """
    from finance_agent.outcome.track_record.model import (
        list_daily_marks,
        prediction_ids_by_direction,
    )

    marks = list_daily_marks(db_path=db_path)
    excl = prediction_ids_by_direction("neutral", db_path=db_path)
    return compute_metrics_from_marks(
        marks,
        calendar_dates=calendar_dates,
        benchmark_by_date=benchmark_by_date,
        exclude_prediction_ids=excl,
    ).nav_points


def compute_metrics_snapshot(
    db_path: Any = None,
    calendar_dates: list[str] | None = None,
    benchmark_by_date: dict[str, float] | None = None,
) -> dict[str, Any]:
    """聚合 predictions 统计 + 组合指标 → agent_metrics_daily 行内容。

    头条口径按 `horizon_days=DEFAULT_HORIZON_DAYS` 过滤（与 overview 同口径），
    避免日批快照把跨切点的 252 存量行混入同一读数（口径切点分段，metrics.md §1.9）。
    组合指标聚合排除 neutral 方向观点（incident 032 根因 A）。
    """
    from finance_agent.outcome.track_record.judgment import DEFAULT_HORIZON_DAYS
    from finance_agent.outcome.track_record.model import (
        list_daily_marks,
        prediction_ids_by_direction,
        prediction_stats,
    )

    stats = prediction_stats(source_type=None, db_path=db_path, horizon_days=DEFAULT_HORIZON_DAYS)
    excl = prediction_ids_by_direction("neutral", db_path=db_path)
    pm = compute_metrics_from_marks(
        list_daily_marks(db_path=db_path),
        calendar_dates=calendar_dates,
        benchmark_by_date=benchmark_by_date,
        exclude_prediction_ids=excl,
    )
    return {
        "sample_size": stats["total"],
        "settled": stats["settled"],
        "win_rate": stats["win_rate"],
        "avg_excess": stats["avg_excess"],
        "annual_return": pm.annual_return,
        "volatility": pm.volatility,
        "sharpe": pm.sharpe,
        "max_drawdown": pm.max_drawdown,
        "risk_score": pm.risk_score,
        "risk_label": pm.risk_label,
        "segment_json": "{}",
    }
