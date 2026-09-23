"""Outcome 结论句式纪律（spec evaluation「Outcome 读数收口纪律」）。

合法结论仅两句式：显著方向（带效应量与 CI）｜分辨率不足（带 MDE 与扩样方向）。
裸「未获统计支持」非法。与 causal_ablation/conclusion.py 同款纪律，判定对象不同：
causal 侧对成本阈值判定（裁剪方向），outcome 侧对 0 判定（是否存在超越基准的能力）。
"""

from __future__ import annotations

from dataclasses import dataclass

LEGAL_FORMS: tuple[str, ...] = ("positive", "negative", "inconclusive")
_BARE_NO_SUPPORT = "未获统计支持"


@dataclass(frozen=True)
class OutcomeConclusion:
    form: str
    sentence: str
    mde: float
    n: int


def conclude_outcome(
    ci: tuple[float, float],
    *,
    mde: float,
    n: int,
    unit: str = "可执行决策",
) -> OutcomeConclusion:
    """ci = 均值超额收益 95% 置信区间；跨 0 → 分辨率不足（须带 MDE）。"""
    lo, hi = ci
    if lo > 0:
        sentence = (
            f"显著为正：均值超额 {lo:+.2%} 至 {hi:+.2%}（95% CI 下限 > 0，n={n} {unit}），"
            f"赚钱能力主张成立（MDE={mde:.2%}）"
        )
        return OutcomeConclusion("positive", sentence, mde, n)
    if hi < 0:
        sentence = (
            f"显著为负：均值超额 {lo:+.2%} 至 {hi:+.2%}（95% CI 上限 < 0，n={n} {unit}），"
            f"处置前须先分桶归因（MDE={mde:.2%}）"
        )
        return OutcomeConclusion("negative", sentence, mde, n)
    sentence = (
        f"分辨率不足（MDE={mde:.2%}，CI=[{lo:+.2%}, {hi:+.2%}] 跨越 0，n={n} {unit}），"
        f"需扩样；扩样方向见预登记 §4"
    )
    return OutcomeConclusion("inconclusive", sentence, mde, n)


def assert_outcome_sentence_legal(sentence: str) -> None:
    """显著句必须写出 CI；非显著句（分辨率不足）必须写出 MDE；裸「未获统计支持」非法。"""
    if _BARE_NO_SUPPORT in sentence and "MDE" not in sentence:
        raise ValueError(f"不合格结论句（裸「未获统计支持」且缺 MDE）：{sentence!r}")
    if "显著" in sentence:
        if "CI" not in sentence:
            raise ValueError(f"不合格显著句（缺 CI）：{sentence!r}")
    elif "MDE" not in sentence:
        raise ValueError(f"不合格非显著句（分辨率不足须写出 MDE）：{sentence!r}")
