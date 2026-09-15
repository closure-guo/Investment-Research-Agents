"""校准门控全覆盖：凡 method ∈ {nli, judge} 的判定须人工一致率 ≥ 0.80（spec causal-ablation）。"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

MIN_AGREEMENT = 0.80
MIN_REVIEW_FRACTION = 0.20
_GATED_METHODS = ("nli", "judge")


class CalibrationBlockedError(RuntimeError):
    """判定未过校准门控，不得进入消融结论。"""


def agreement_rate(judge_labels: Sequence[str], human_labels: Sequence[str]) -> float:
    if not judge_labels or not human_labels:
        raise ValueError("校准比对要求标签非空")
    if len(judge_labels) != len(human_labels):
        raise ValueError("校准比对要求两侧等长")
    same = sum(1 for j, h in zip(judge_labels, human_labels, strict=True) if j == h)
    return same / len(judge_labels)


def gate_dimension(
    method: str,
    judge_labels: Sequence[str],
    human_labels: Sequence[str],
    *,
    min_agreement: float = MIN_AGREEMENT,
) -> dict[str, Any]:
    """返回 {gated, passed, agreement, reason}；code 判定不适用校准门控。"""
    if method == "code":
        return {
            "gated": False,
            "passed": True,
            "agreement": None,
            "reason": "code 判定不适用校准门控",
        }
    if method not in _GATED_METHODS:
        raise ValueError(f"未知 method: {method!r}（须为 code/nli/judge 之一）")
    rate = agreement_rate(judge_labels, human_labels)
    passed = rate >= min_agreement
    reason = (
        "一致率达标" if passed else f"一致率 {rate:.2f} < {min_agreement:.2f}：该批判定作废并重校准"
    )
    return {"gated": True, "passed": passed, "agreement": rate, "reason": reason}


def assert_calibrated(
    method: str,
    judge_labels: Sequence[str],
    human_labels: Sequence[str],
    *,
    min_agreement: float = MIN_AGREEMENT,
) -> None:
    result = gate_dimension(method, judge_labels, human_labels, min_agreement=min_agreement)
    if not result["passed"]:
        raise CalibrationBlockedError(f"{method} 判定未过校准门控：{result['reason']}")
