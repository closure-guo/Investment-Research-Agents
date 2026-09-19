"""注入强度校准：太易全拦/太难全穿都浪费样本（spec causal-ablation 风险对策）。"""

from __future__ import annotations

from evals.causal_ablation.escape import EscapePair, mcnemar_table


def calibration_verdict(
    pairs: list[EscapePair], *, min_ratio: float = 0.10, max_ratio: float = 0.90
) -> dict[str, object]:
    if not pairs:
        raise ValueError("pilot 校准要求非空配对集合")
    ratio = mcnemar_table(pairs)["discordant_ratio"]
    if ratio < min_ratio:
        verdict, advice = "too_easy", "污染太易被拦：提高注入强度或换注入点，不硬扩样本"
    elif ratio > max_ratio:
        verdict, advice = "too_hard", "污染太难被拦：降低注入强度或校验收紧，不硬扩样本"
    else:
        verdict, advice = "ok", "注入强度落在健康区间，可扩到正式批次"
    return {"verdict": verdict, "ratio": ratio, "advice": advice}
