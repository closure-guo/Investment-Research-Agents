"""±L3 粗筛增量变体（add-screening-evaluation Task 3 / 计划 V3）。

spec causal-ablation「L3 粗筛增量变体」：比较粗筛前候选池与粗筛后精选池的前向收益，
**仅当 drop 组后续 horizon 日收益显著低于 keep 组时，报告方可陈述粗筛具有排雷价值**。

分组口径（store 读取契约）：

- **keep 组** = ``triage_decisions.verdict == "keep"`` **且入池**（``selection_pool``
  当日行）——只考核「粗筛保留且实际进入精选池」的标的，未达在榜门槛的 keep 不入组；
- **drop 组** = ``triage_decisions.verdict == "drop"`` 全部。

统计：两组 ``{n, mean, win_rate, 踩雷率}`` + 配对（池日）均值差（keep − drop）CI；
措辞判定 ``l3_value_conclusion``：CI 整体 > 0 → 具有排雷价值；CI 整体 < 0 →
显著反向（如实陈述，不冒充无差异）；含 0 → 无显著差异；样本不足 → 无法判定。

踩雷定义（本模块钉死，报告披露）：持有窗口收益 ≤ ``BLOWUP_THRESHOLD``（-10%，约一个
跌停板幅度）的样本占比——「排雷」= 把后续大跌的标的剔除在池外。
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any

from evals.screening.forward import (
    BOOTSTRAP_B,
    BOOTSTRAP_SEED,
    DayForwardResult,
    forward_returns_by_day,
    group_stats,
    paired_diff_ci,
)
from finance_agent.execution.matching import MatchingConfig
from finance_agent.factors.eval_engine import MarketPanel
from finance_agent.screening.store import list_selection_pool, list_triage_decisions

# 踩雷阈值（20 日窗口损失达约一个跌停板幅度）
BLOWUP_THRESHOLD = -0.10


def build_l3_keep_drop_groups(
    pool_dates: Sequence[str], *, db_path: str | Path | None = None
) -> dict[str, dict[str, list[str]]]:
    """读库 → ``{"keep": {池日: [symbols]}, "drop": {池日: [symbols]}}``（口径见模块 docstring）。"""
    keep: dict[str, list[str]] = {}
    drop: dict[str, list[str]] = {}
    for pool_date in sorted(pool_dates):
        pool_symbols = {
            str(row["symbol"]) for row in list_selection_pool(pool_date, db_path=db_path)
        }
        decisions = list_triage_decisions(trade_date=pool_date, db_path=db_path)
        keep[pool_date] = sorted(
            str(row["symbol"])
            for row in decisions
            if row["verdict"] == "keep" and str(row["symbol"]) in pool_symbols
        )
        drop[pool_date] = sorted(
            str(row["symbol"]) for row in decisions if row["verdict"] == "drop"
        )
    return {"keep": keep, "drop": drop}


def blowup_rate(
    days: Sequence[DayForwardResult], *, threshold: float = BLOWUP_THRESHOLD
) -> float | None:
    """踩雷率：可结算样本中持有窗口收益 ≤ ``threshold`` 的占比；无样本 → None。"""
    rets = [sample.ret for day in days for sample in day.samples]
    if not rets:
        return None
    return sum(1 for ret in rets if ret <= threshold) / len(rets)


def l3_value_conclusion(ci: Sequence[float] | None) -> str:
    """粗筛价值措辞（diff = keep − drop）：

    - CI 整体 > 0（drop 显著低于 keep）→ **具有排雷价值**（spec 允许陈述的唯一情形）；
    - CI 整体 < 0 → **显著反向（keep 组低于 drop 组）**（如实陈述显著性，不冒充无差异）；
    - CI 含 0 → **无显著差异**（不得引申为「无效/无价值」）；
    - CI 不可得（配对日 < 2）→ 样本不足，无法判定。
    """
    if ci is None:
        return "样本不足，无法判定"
    lo, hi = ci
    if lo > 0:
        return "具有排雷价值"
    if hi < 0:
        return "显著反向（keep 组低于 drop 组）"
    return "无显著差异"


def l3_keep_drop_compare(
    *,
    pool_dates: Sequence[str],
    panel: MarketPanel,
    db_path: str | Path | None = None,
    horizon: int = 20,
    as_of: str | None = None,
    config: MatchingConfig | None = None,
    B: int = BOOTSTRAP_B,  # noqa: N803 - 与 paired_bootstrap_ci 参数名一致
    seed: int = BOOTSTRAP_SEED,
    blowup_threshold: float = BLOWUP_THRESHOLD,
) -> dict[str, Any]:
    """keep/drop 两组前向收益 + 踩雷率 + 配对 CI + 措辞判定（spec 场景口径）。"""
    groups = build_l3_keep_drop_groups(pool_dates, db_path=db_path)
    keep_days = forward_returns_by_day(
        symbols_by_date=groups["keep"], panel=panel, horizon=horizon, as_of=as_of, config=config
    )
    drop_days = forward_returns_by_day(
        symbols_by_date=groups["drop"], panel=panel, horizon=horizon, as_of=as_of, config=config
    )
    diff = paired_diff_ci(keep_days, drop_days, B=B, seed=seed)
    conclusion = l3_value_conclusion(diff["ci95"])
    return {
        "horizon": horizon,
        "as_of": as_of,
        "pool_dates": sorted(groups["keep"]),
        "blowup_threshold": blowup_threshold,
        "keep": {
            **group_stats(keep_days),
            "blowup_rate": blowup_rate(keep_days, threshold=blowup_threshold),
        },
        "drop": {
            **group_stats(drop_days),
            "blowup_rate": blowup_rate(drop_days, threshold=blowup_threshold),
        },
        "diff": {**diff, "conclusion": conclusion},
        "conclusion": conclusion,
        "methodology": {
            "entry": (
                "入场 = 池日次一交易日（D+1）开盘经撮合引擎成交（买 1 手，滑点取最保守档）；"
                "一字涨停不成交 → 记原因码剔除"
            ),
            "exit": "出场 = 入场日后第 horizon 个交易日收盘；收益 = exit/entry − 1（费用不折算）",
            "truncation": "时点截断：as_of 给定则只用 ≤ as_of 的面板行；超出截断 → 剔除并计原因码",
            "groups": "keep 组 = L3 keep ∩ 入池；drop 组 = L3 drop（未入池的 keep 不计入任何组）",
            "blowup": f"踩雷率 = 持有窗口收益 ≤ {blowup_threshold:.0%} 的样本占比",
            "bootstrap": f"配对单元 = 池日；B={BOOTSTRAP_B}，seed={BOOTSTRAP_SEED}",
            "wording": "仅 CI 整体 > 0（drop 显著低于 keep）方可陈述具有排雷价值",
        },
    }
