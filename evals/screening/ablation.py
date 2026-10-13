"""选股消融三变体与配对 bootstrap（add-screening-evaluation Task 2 / 计划 V2）。

三变体（spec causal-ablation「选股消融三变体」）：**共享同一时段与持有窗口
（horizon 对齐）**，配对 bootstrap（B=10000）报告增量：

- ``factor``：因子打分选股 = 当日 ``l2_candidates`` 按 composite 降序（同分 symbol
  升序）取 Top-N——即系统 L2 打分榜的选取口径；
- ``random``：固定种子随机选股（``random.Random(seed)`` 逐日抽样，确定性可复现；
  候选先去重排序 → 输入序无关；spec 场景「种子 42 两次执行标的序列完全一致」）；
- ``equal_weight``：等权指数基线 = 该时段**全部 L2 名单标的**等权组合的同期收益。

增量裁决：``paired_diff_ci`` 以池日为配对单元（沿用 ``forward`` 的结算口径：入场走
撮合引擎、出场 horizon 日收盘、时点截断）；结论措辞由 ``conclusion_for_variant_increment``
生成——**CI 含 0 只写「无显著差异」**，禁止「无效/无价值」类引申（报告渲染器结构守卫）。
"""

from __future__ import annotations

import math
import random
from collections.abc import Mapping, Sequence
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
from finance_agent.screening.pipeline import L2_TOP_N
from finance_agent.screening.store import list_l2_candidates

VARIANT_FACTOR = "factor"
VARIANT_RANDOM = "random"
VARIANT_EQUAL_WEIGHT = "equal_weight"
# 三变体命名契约（报告/聚合按此键读取；缺任一 → fail loud）
ABLATION_VARIANTS: tuple[str, ...] = (VARIANT_FACTOR, VARIANT_RANDOM, VARIANT_EQUAL_WEIGHT)
# 随机臂默认种子（spec 场景固定 42）
DEFAULT_RANDOM_SEED = 42
DEFAULT_TOP_N = L2_TOP_N


def _canonical_pool_dates(pool_dates: Sequence[str]) -> list[str]:
    """池日规范化：严格 ``YYYY-MM-DD``、去重后升序（输出确定性）。"""
    from datetime import date as _date

    normalized: list[str] = []
    for value in pool_dates:
        try:
            parsed = _date.fromisoformat(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"pool_date 须为规范 YYYY-MM-DD: {value!r}") from exc
        if parsed.isoformat() != value:
            raise ValueError(f"pool_date 须为规范 YYYY-MM-DD: {value!r}")
        normalized.append(value)
    if len(set(normalized)) != len(normalized):
        raise ValueError(f"pool_dates 存在重复: {normalized}")
    return sorted(normalized)


def _positive_top_n(top_n: int) -> int:
    if isinstance(top_n, bool) or not isinstance(top_n, int) or top_n < 1:
        raise ValueError(f"top_n 必须是正整数: {top_n!r}")
    return top_n


def random_selection(
    pool_dates: Sequence[str],
    universe: Mapping[str, Sequence[str]],
    *,
    seed: int = DEFAULT_RANDOM_SEED,
    top_n: int = DEFAULT_TOP_N,
) -> dict[str, list[str]]:
    """固定种子随机选股：逐池日从 ``universe[池日]`` 无放回抽 ``min(top_n, n)`` 只。

    确定性口径（spec 场景）：候选先去重升序（输入序无关）→ ``random.Random(seed)``
    按**规范化后的池日升序**逐日抽样 → 同 seed 同输入两次执行的标的序列逐位一致。
    ``universe`` 缺某池日覆盖 → ``ValueError``（fail loud，不静默当空榜）。
    """
    normalized = _canonical_pool_dates(pool_dates)
    k = _positive_top_n(top_n)
    missing = [pool_date for pool_date in normalized if pool_date not in universe]
    if missing:
        raise ValueError(f"universe 缺池日覆盖: {missing}（拒绝把缺失日当空榜抽样）")
    # spec 口径：固定种子随机臂（可复现性优先，非密码学用途）
    rng = random.Random(seed)  # noqa: S311
    selected: dict[str, list[str]] = {}
    for pool_date in normalized:
        pool = sorted({str(symbol) for symbol in universe[pool_date]})
        selected[pool_date] = rng.sample(pool, min(k, len(pool)))
    return selected


def factor_top_selection(
    candidates_by_date: Mapping[str, Sequence[Mapping[str, Any]]],
    *,
    top_n: int = DEFAULT_TOP_N,
) -> dict[str, list[str]]:
    """因子打分选股：逐池日按 ``(-composite_score, symbol)`` 排序取 Top-N（rank 序输出）。

    行须含 ``symbol``/``composite_score``；分值非有限 → ``ValueError``（fail loud）；
    重复 symbol → ``ValueError``（每标的至多一条候选）。
    """
    k = _positive_top_n(top_n)
    selected: dict[str, list[str]] = {}
    for pool_date in sorted(candidates_by_date):
        scored: list[tuple[float, str]] = []
        seen: set[str] = set()
        for row in candidates_by_date[pool_date]:
            symbol = str(row["symbol"])
            if symbol in seen:
                raise ValueError(f"池日 {pool_date!r} 候选 symbol 重复: {symbol!r}")
            seen.add(symbol)
            score = float(row["composite_score"])
            if not math.isfinite(score):
                raise ValueError(f"池日 {pool_date!r} 候选 {symbol!r} 的 composite_score 非有限")
            scored.append((score, symbol))
        scored.sort(key=lambda item: (-item[0], item[1]))
        selected[pool_date] = [symbol for _score, symbol in scored[:k]]
    return selected


def build_ablation_variants(
    pool_dates: Sequence[str],
    *,
    db_path: str | Path | None = None,
    seed: int = DEFAULT_RANDOM_SEED,
    top_n: int = DEFAULT_TOP_N,
) -> dict[str, dict[str, list[str]]]:
    """读 screening 库 → 三变体逐日标的集合（同一时段；``factor``/``random`` 同 top_n）。

    ``equal_weight`` = 当日全部 L2 名单标的（等权指数，不截断）；``random`` 从同一
    L2 名单抽样（``random_selection``，seed 固定）→ 三臂资产池同源，可比。
    """
    normalized = _canonical_pool_dates(pool_dates)
    rows_by_date = {
        pool_date: list_l2_candidates(pool_date, db_path=db_path) for pool_date in normalized
    }
    universe = {
        pool_date: [str(row["symbol"]) for row in rows] for pool_date, rows in rows_by_date.items()
    }
    return {
        VARIANT_FACTOR: factor_top_selection(rows_by_date, top_n=top_n),
        VARIANT_RANDOM: random_selection(normalized, universe, seed=seed, top_n=top_n),
        VARIANT_EQUAL_WEIGHT: {
            pool_date: sorted(set(symbols)) for pool_date, symbols in universe.items()
        },
    }


def conclusion_for_variant_increment(ci: Sequence[float] | None) -> str:
    """变体增量结论（措辞逐字）：CI 整体 >0 → 显著改进；整体 <0 → 显著退步；

    **含 0 → 无显著差异**（不得引申为「无效」——该措辞被报告渲染器守卫拒绝）；
    CI 不可得 → 样本不足，无法判定。
    """
    if ci is None:
        return "样本不足，无法判定"
    lo, hi = ci
    if lo > 0:
        return "显著改进"
    if hi < 0:
        return "显著退步"
    return "无显著差异"


def _variant_date_sets(variants: Mapping[str, Mapping[str, Sequence[str]]]) -> set[frozenset[str]]:
    return {frozenset(variant) for variant in variants.values()}


def compare_ablation_variants(
    *,
    variants: Mapping[str, Mapping[str, Sequence[str]]],
    panel: MarketPanel,
    horizon: int = 20,
    as_of: str | None = None,
    config: MatchingConfig | None = None,
    B: int = BOOTSTRAP_B,  # noqa: N803 - 与 paired_bootstrap_ci 参数名一致
    seed: int = BOOTSTRAP_SEED,
) -> dict[str, Any]:
    """三变体对照：逐变体前向统计 + 因子臂两条增量（vs 随机 / vs 等权指数）。

    强制「共享同一时段」：三变体的池日键集合必须完全一致（否则 ``ValueError``）；
    horizon 单一入参 → 持有窗口对齐（spec「共享同一时段与持有窗口」）。
    """
    missing = [name for name in ABLATION_VARIANTS if name not in variants]
    if missing:
        raise ValueError(f"缺变体: {missing}（三变体契约 {list(ABLATION_VARIANTS)}）")
    if len(_variant_date_sets(variants)) != 1:
        raise ValueError("三变体池日键集合不一致：不接受跨窗口对照（horizon/时段必须对齐）")
    pool_dates = sorted(next(iter(variants.values())))

    days_by_variant: dict[str, list[DayForwardResult]] = {}
    for name in ABLATION_VARIANTS:
        days_by_variant[name] = forward_returns_by_day(
            symbols_by_date=variants[name],
            panel=panel,
            horizon=horizon,
            as_of=as_of,
            config=config,
        )
    diffs: dict[str, dict[str, Any]] = {}
    for name in (VARIANT_RANDOM, VARIANT_EQUAL_WEIGHT):
        key = f"{VARIANT_FACTOR}_minus_{name}"
        diff = paired_diff_ci(
            days_by_variant[VARIANT_FACTOR], days_by_variant[name], B=B, seed=seed
        )
        diffs[key] = {
            **diff,
            "conclusion": conclusion_for_variant_increment(diff["ci95"]),
        }
    return {
        "horizon": horizon,
        "as_of": as_of,
        "pool_dates": pool_dates,
        "seed": int(seed),
        "variants": {name: group_stats(days_by_variant[name]) for name in ABLATION_VARIANTS},
        "diffs": diffs,
        "methodology": {
            "entry": (
                "入场 = 池日次一交易日（D+1）开盘经撮合引擎成交（买 1 手，滑点取最保守档）；"
                "一字涨停不成交 → 记原因码剔除"
            ),
            "exit": "出场 = 入场日后第 horizon 个交易日收盘；收益 = exit/entry − 1（费用不折算）",
            "truncation": "时点截断：as_of 给定则只用 ≤ as_of 的面板行；超出截断 → 剔除并计原因码",
            "variants": (
                "factor = L2 composite Top-N；random = 同名单固定种子抽样（输入序无关，"
                "两次执行逐位一致）；equal_weight = 当期全部 L2 名单标的等权"
            ),
            "bootstrap": f"配对单元 = 池日；B={BOOTSTRAP_B}，seed={BOOTSTRAP_SEED}",
        },
    }
