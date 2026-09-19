"""结论句式纪律：只有两种合法形态，且必带 MDE（spec evaluation 结论句式纪律）。"""

from __future__ import annotations

from dataclasses import dataclass

LEGAL_FORMS: tuple[str, ...] = ("true_negative", "inconclusive")
_BARE_NO_SUPPORT = "未获统计支持"


@dataclass(frozen=True)
class Conclusion:
    form: str
    sentence: str
    mde: float
    threshold: float


def conclude(
    ci: tuple[float, float],
    *,
    mde: float,
    threshold: float,
    unit: str = "增量",
) -> Conclusion:
    """ci 整体低于阈值 → 真阴性；其余（跨 0 或高于阈值）→ 分辨率不足，须扩样。"""
    lo, hi = ci
    if hi < threshold:
        sentence = (
            f"在本实验分辨率（MDE={mde}）下，该对象{unit}低于其成本对应阈值"
            f"（{threshold}），建议降级/裁剪"
        )
        return Conclusion("true_negative", sentence, mde, threshold)
    sentence = (
        f"分辨率不足（MDE={mde}，CI=[{lo}, {hi}] 未整体低于阈值 {threshold}），需扩至 N 只标的"
    )
    return Conclusion("inconclusive", sentence, mde, threshold)


def assert_sentence_legal(sentence: str, mde: float | None) -> None:
    """裸「未获统计支持」不合格：必须附 MDE。"""
    if _BARE_NO_SUPPORT in sentence and mde is None:
        raise ValueError(f"不合格结论句（缺 MDE）：{sentence!r}")
    if mde is not None and "MDE" not in sentence:
        raise ValueError(f"不合格结论句（未写出 MDE={mde}）：{sentence!r}")
