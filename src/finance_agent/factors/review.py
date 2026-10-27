"""入库后业绩复核（add-factor-post-admission-review）：月批尾部运行期处置。

对 active 且入库后 IC 样本 ≥ ``POST_ADMISSION_MIN_SAMPLES`` 的因子复核两项
（detail 同记）：
1. 水位复核：入库后全样本 |ICIR|（mean/std，std=0 → 0，与闸②同口径）
   < ``MIN_ABS_ICIR`` → 命中；
2. 全局多重检验复核：账本全部非空 p 值作单一家族 BH 校正（与
   ``cumulative_fdr_preview`` 同一算法单一真相源），该因子 ledger 行
   ``p_adj > BH_FDR_Q`` → 命中。

任一命中 → ``set_status(active → degraded, reason=POST_ADMISSION_REVIEW)``。
账本只读消费（MUST NOT 写 ledger）；异常上抛（fail loud）。
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

from finance_agent.factors.constraints import (
    BH_FDR_Q,
    MIN_ABS_ICIR,
    POST_ADMISSION_MIN_SAMPLES,
)
from finance_agent.factors.library import (
    list_active_factors,
    list_ic_series,
    list_ledger_entries,
    set_status,
)
from finance_agent.factors.validate import benjamini_hochberg_adjust


def _icir(values: list[float]) -> float:
    """mean/std（ddof=1）；样本 < 2 或 std=0 → 0.0（与闸② ``_ic_stats`` 同口径）。"""
    n = len(values)
    if n < 2:
        return 0.0
    mean = sum(values) / n
    var = sum((v - mean) ** 2 for v in values) / (n - 1)
    std = math.sqrt(var)
    if std == 0.0:
        return 0.0
    return mean / std


def _global_p_adj_by_factor(db_path: str | Path | None) -> dict[int, float]:
    """账本全局 BH：全部非空 p 值作单一家族 → {factor_id: p_adj}（同 factor 多行取最小）。"""
    entries = list_ledger_entries(db_path=db_path)
    tested = {str(e["id"]): float(e["p_value"]) for e in entries if e.get("p_value") is not None}
    if not tested:
        return {}
    adjusted = benjamini_hochberg_adjust(tested)
    best: dict[int, float] = {}
    for e in entries:
        if e.get("p_value") is None or e.get("factor_id") is None:
            continue
        p_adj = adjusted.get(str(e["id"]))
        if p_adj is None:
            continue
        fid = int(e["factor_id"])
        if fid not in best or p_adj < best[fid]:
            best[fid] = float(p_adj)
    return best


def run_post_admission_review(*, db_path: str | Path | None = None) -> dict[str, Any]:
    """月度复核入口：返回 ``{"reviewed": int, "degraded": [id], "skipped": [id]}``。"""
    reviewed = 0
    degraded: list[int] = []
    skipped: list[int] = []
    global_p_adj = _global_p_adj_by_factor(db_path)
    for row in list_active_factors(db_path=db_path):
        fid = int(row["id"])
        values = [float(v) for _d, v in list_ic_series(fid, db_path=db_path)]
        if len(values) < POST_ADMISSION_MIN_SAMPLES:
            skipped.append(fid)
            continue
        reviewed += 1
        icir = _icir(values)
        p_adj = global_p_adj.get(fid)
        watermark_hit = abs(icir) < MIN_ABS_ICIR
        counterfactual_hit = p_adj is not None and p_adj > BH_FDR_Q
        if not (watermark_hit or counterfactual_hit):
            continue
        set_status(
            fid,
            "degraded",
            detail={
                "reason": "POST_ADMISSION_REVIEW",
                "post_admission": {
                    "icir": icir,
                    "global_p_adj": p_adj,
                    "q": BH_FDR_Q,
                    "watermark_hit": watermark_hit,
                    "counterfactual_hit": counterfactual_hit,
                    "samples": len(values),
                },
            },
            db_path=db_path,
        )
        degraded.append(fid)
    return {"reviewed": reviewed, "degraded": degraded, "skipped": skipped}
