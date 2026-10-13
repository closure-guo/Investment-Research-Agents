"""因子来源分层考核（add-screening-evaluation Task 3 / 计划 V4）。

spec evaluation「因子来源分层考核」：前向统计按因子来源分层——**仅 builtin 打分** 与
**仅 LLM 挖掘因子打分**两分层各自驱动候选池时的前向表现对照，作为因子挖掘管线的
考核指标；两分层唯一差异 = 因子子集（单变量纪律），IC 统计同源（同一 ``ic_stats``
口径，两分层用各自因子的 IC 均值加权，见 ``ranker.factor_weights``）。

分层映射：``source == "builtin"`` → builtin 层；其余 active 因子（``proposal`` /
``llm`` 等挖掘产物）→ LLM 层。每日对每层经 ``rank_universe``（同面板、同
``passed_codes``、同 ``top_n``）重跑 Top-N → 走 ``forward`` 的撮合口径前向收益 →
配对（池日）均值差 CI（LLM − builtin）。

注意：``builtin`` 层为空或 ``llm`` 层为空时**不伪造对照**——空层记 ``n=0`` 且
结论「样本不足，无法判定」；无 active 因子 → ``ValueError``（fail loud）。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from evals.screening.ablation import DEFAULT_TOP_N
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
from finance_agent.factors.library import list_active_factors
from finance_agent.screening.ranker import rank_universe, recent_ic_stats

SOURCE_BUILTIN = "builtin"
STRATUM_BUILTIN = "builtin"
STRATUM_LLM = "llm"
STRATA: tuple[str, ...] = (STRATUM_BUILTIN, STRATUM_LLM)


def factor_source_strata(factors: Sequence[Mapping[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """active 因子行 → ``{"builtin": [...], "llm": [...]}``（``source=="builtin"`` 划分）。"""
    strata: dict[str, list[dict[str, Any]]] = {STRATUM_BUILTIN: [], STRATUM_LLM: []}
    for row in factors:
        entry = dict(row)
        source = str(entry.get("source") or "")
        key = STRATUM_BUILTIN if source == SOURCE_BUILTIN else STRATUM_LLM
        strata[key].append(entry)
    return strata


def _factor_ref(row: Mapping[str, Any]) -> dict[str, Any]:
    """因子引用固定投影（id/expression/source）——artifact 确定性不含壁钟字段。"""
    return {
        "id": int(row["id"]),
        "expression": str(row.get("expression") or ""),
        "source": str(row.get("source") or ""),
    }


def conclusion_for_source_strata(ci: Sequence[float] | None) -> str:
    """分层结论（diff = LLM − builtin）：CI 整体 ><0 → 显著优/劣；含 0 → 无显著差异。"""
    if ci is None:
        return "样本不足，无法判定"
    lo, hi = ci
    if lo > 0:
        return "LLM 因子分层显著优于 builtin"
    if hi < 0:
        return "LLM 因子分层显著劣于 builtin"
    return "无显著差异"


def _stratum_top_symbols(
    *,
    factors: Sequence[Mapping[str, Any]],
    ic_stats: Mapping[int, tuple[float, float]],
    panel: MarketPanel,
    pool_date: str,
    passed_codes: Sequence[str],
    top_n: int,
) -> list[str]:
    """单池日单分层：``rank_universe`` 重跑 → Top-N symbols（rank 序）。"""
    candidates = rank_universe(
        panel=panel,
        as_of=pool_date,
        passed_codes=passed_codes,
        factors=[dict(row) for row in factors],
        ic_stats=dict(ic_stats),
        top_n=top_n,
    )
    return [candidate.symbol for candidate in candidates]


def stratify_by_factor_source(
    *,
    pool_dates: Sequence[str],
    panel: MarketPanel,
    passed_codes_by_date: Mapping[str, Sequence[str]],
    factors: Sequence[Mapping[str, Any]] | None = None,
    ic_stats: Mapping[int, tuple[float, float]] | None = None,
    library_db_path: str | Path | None = None,
    top_n: int = DEFAULT_TOP_N,
    horizon: int = 20,
    as_of: str | None = None,
    config: MatchingConfig | None = None,
    B: int = BOOTSTRAP_B,  # noqa: N803 - 与 paired_bootstrap_ci 参数名一致
    seed: int = BOOTSTRAP_SEED,
) -> dict[str, Any]:
    """两分层前向收益对照主入口（因子子集从库读取或显式注入；IC 统计同源）。

    ``passed_codes_by_date`` 必须覆盖全部池日（缺 → ``ValueError``）；``factors``
    缺省 ``list_active_factors(library_db_path)``，``ic_stats`` 缺省
    ``recent_ic_stats(全部 active 因子)``（两分层复用同一份，加权口径一致）。
    """
    active = (
        [dict(row) for row in factors]
        if factors is not None
        else list_active_factors(db_path=library_db_path)
    )
    if not active:
        raise ValueError("无 active 因子：拒绝分层打分（fail loud，不产出空对照）")
    stats_map = (
        dict(ic_stats)
        if ic_stats is not None
        else recent_ic_stats(list(active), db_path=library_db_path)
    )
    normalized_dates = sorted({str(pool_date) for pool_date in pool_dates})
    missing = [pool_date for pool_date in normalized_dates if pool_date not in passed_codes_by_date]
    if missing:
        raise ValueError(f"passed_codes_by_date 缺池日覆盖: {missing}")

    strata_rows = factor_source_strata(active)
    days_by_stratum: dict[str, list[DayForwardResult]] = {}
    for name in STRATA:
        rows = strata_rows[name]
        if not rows:
            days_by_stratum[name] = []
            continue
        symbols_by_date = {
            pool_date: _stratum_top_symbols(
                factors=rows,
                ic_stats=stats_map,
                panel=panel,
                pool_date=pool_date,
                passed_codes=passed_codes_by_date[pool_date],
                top_n=top_n,
            )
            for pool_date in normalized_dates
        }
        days_by_stratum[name] = forward_returns_by_day(
            symbols_by_date=symbols_by_date,
            panel=panel,
            horizon=horizon,
            as_of=as_of,
            config=config,
        )

    diff = paired_diff_ci(
        days_by_stratum[STRATUM_LLM], days_by_stratum[STRATUM_BUILTIN], B=B, seed=seed
    )
    conclusion = conclusion_for_source_strata(diff["ci95"])
    return {
        "horizon": horizon,
        "as_of": as_of,
        "pool_dates": normalized_dates,
        "top_n": top_n,
        "strata": {
            name: {
                **group_stats(days_by_stratum[name]),
                # 因子引用固定投影（id/expression/source）：不携带库行 created_at 等
                # 壁钟字段 → artifact 跨次运行逐位可复现
                "factors": [_factor_ref(row) for row in strata_rows[name]],
            }
            for name in STRATA
        },
        "diff": {**diff, "conclusion": conclusion},
        "conclusion": conclusion,
        "methodology": {
            "strata": (
                "builtin = source=='builtin'；llm = 其余 active 挖掘因子（proposal/llm 等）；"
                "两分层同面板、同 L1 通过集、同 top_n，唯一差异 = 因子子集（单变量）"
            ),
            "ic": "两分层复用同一份近 60 日 RankIC 统计（recent_ic_stats，全 active 因子）",
            "entry": "入场 = 池日次一交易日（D+1）开盘经撮合引擎成交（买 1 手，滑点取最保守档）",
            "exit": "出场 = 入场日后第 horizon 个交易日收盘；收益 = exit/entry − 1（费用不折算）",
            "truncation": "时点截断：as_of 给定则只用 ≤ as_of 的面板行；超出截断 → 剔除并计原因码",
            "bootstrap": f"配对单元 = 池日；B={BOOTSTRAP_B}，seed={BOOTSTRAP_SEED}",
        },
    }
