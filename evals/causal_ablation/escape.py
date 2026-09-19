"""逃逸率统计：只认真逃逸（人工终裁），校验器误报走四桶拆报（spec causal-ablation）。"""

from __future__ import annotations

from dataclasses import dataclass

VERIFIER_BUCKETS: tuple[str, ...] = (
    "blocked",
    "analyst_true_fail",
    "surgical_repaired",
    "verifier_normalized",
    # 值槽类型错填（eval-driven-contract-fixes 任务 2）：claim 契约病（变化量入水平槽），
    # 校验器侧不计逃逸分母、不算分析师幻觉
    "claim_contract_error",
)


@dataclass(frozen=True)
class EscapePair:
    case_id: str
    on_state: str  # caught | escaped
    off_state: str


def classify_state(*, polluted_present: bool, flagged: bool) -> str:
    """逃逸 = 污染值出现在终态产物且未被告警；其余为 caught。"""
    return "escaped" if (polluted_present and not flagged) else "caught"


def mcnemar_table(pairs: list[EscapePair]) -> dict[str, float]:
    """不一致对子表：b = 开拦下/关逃逸，c = 反向；一致对子不携带信息。"""
    b = sum(1 for p in pairs if p.on_state == "caught" and p.off_state == "escaped")
    c = sum(1 for p in pairs if p.on_state == "escaped" and p.off_state == "caught")
    total = len(pairs)
    return {
        "b": float(b),
        "c": float(c),
        "discordant_ratio": ((b + c) / total) if total else 0.0,
    }


def escape_rate(pairs: list[EscapePair], *, adjudicated: set[str]) -> dict[str, float | int | None]:
    """分母 = 已终裁单元数；未终裁单列 pending，既不进分子也不进分母。

    无任何终裁时 rate 为 None（「0 逃逸」与「没判定过」不得混为一谈）。
    """
    escaped = [p for p in pairs if p.off_state == "escaped"]
    adjudicated_pairs = [p for p in pairs if p.case_id in adjudicated]
    adjudicated_escapes = [p for p in escaped if p.case_id in adjudicated]
    pending = [p for p in escaped if p.case_id not in adjudicated]
    denom = len(adjudicated_pairs)
    return {
        "escapes": len(adjudicated_escapes),
        "pending": len(pending),
        "denominator": denom,
        "rate": (len(adjudicated_escapes) / denom) if denom else None,
    }


def split_verifier_buckets(counts: dict[str, int]) -> dict[str, int]:
    """四桶拆报：校验器误报（verifier_normalized 等）不进逃逸率分母。"""
    unknown = set(counts) - set(VERIFIER_BUCKETS)
    if unknown:
        raise ValueError(f"未知桶: {sorted(unknown)}（合法桶 {VERIFIER_BUCKETS}）")
    out = {b: int(counts.get(b, 0)) for b in VERIFIER_BUCKETS}
    out["total"] = sum(out.values())
    return out
