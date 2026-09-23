"""add-track-record-stage-c：置信度校准（分桶 + Brier Score）。

置信度分桶 [0.5,0.6)...[0.9,1.0]，每桶输出 {中值, 实际命中率, 样本数}；
Brier Score = 平均 (prob - outcome)^2，neutral 按 0.5 处理（可配置剔除）。
输入为 predictions 行（需 confidence/status），纯函数可离线测试。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# 分桶边界（含左不含右；0.9-1.0 闭右）
CALIBRATION_BUCKETS: tuple[tuple[float, float], ...] = (
    (0.5, 0.6),
    (0.6, 0.7),
    (0.7, 0.8),
    (0.8, 0.9),
    (0.9, 1.0),
)

NEUTRAL_PROB = 0.5  # neutral 观点在概率口径下的命中值（可配剔除时置 None）


def outcome_value(p: dict[str, Any], neutral_prob: float | None = NEUTRAL_PROB) -> float | None:
    """观点结果的概率化取值：win=1 / loss=0 / neutral=neutral_prob / 其余 None。

    neutral 方向回避判定终态（`status == "avoidance"`）按 `avoidance_status` 映射：
    avoidance_win→1.0、avoidance_loss→0.0、avoidance_neutral→neutral_prob；
    `avoidance_status IS NULL`（未判定）→ None 不参与。其余行为不变。
    """
    status = p.get("status")
    if status == "avoidance":
        avoidance = p.get("avoidance_status")
        if avoidance == "avoidance_win":
            return 1.0
        if avoidance == "avoidance_loss":
            return 0.0
        if avoidance == "avoidance_neutral" and neutral_prob is not None:
            return float(neutral_prob)
        return None
    if status == "resolved_win":
        return 1.0
    if status == "resolved_loss":
        return 0.0
    if status == "resolved_neutral" and neutral_prob is not None:
        return float(neutral_prob)
    return None


@dataclass
class CalibrationResult:
    buckets: list[dict[str, Any]] = field(default_factory=list)
    brier: float | None = None
    sample_size: int = 0


def calibration_table(
    predictions: list[dict[str, Any]],
    neutral_prob: float | None = NEUTRAL_PROB,
) -> CalibrationResult:
    """分桶校准表 + Brier Score。未结算/unresolvable 不进桶（outcome None 剔除）。"""
    buckets: dict[tuple[float, float], list[tuple[float, float]]] = {
        b: [] for b in CALIBRATION_BUCKETS
    }
    outcomes: list[tuple[float, float]] = []
    total = 0
    for p in predictions:
        conf = p.get("confidence")
        if conf is None:
            continue
        out = outcome_value(p, neutral_prob)
        if out is None:
            continue
        total += 1
        outcomes.append((float(conf), out))
        for lo, hi in CALIBRATION_BUCKETS:
            if lo <= conf < hi or (hi == 1.0 and conf == 1.0):
                buckets[(lo, hi)].append((float(conf), out))
                break

    rows: list[dict[str, Any]] = []
    for (lo, hi), items in buckets.items():
        n = len(items)
        rows.append(
            {
                "bucket": f"[{lo:.1f},{hi:.1f})",
                "mid": round((lo + hi) / 2, 2),
                "n": n,
                "hit_rate": round(sum(o for _, o in items) / n, 4) if n else None,
            }
        )
    brier = None
    if outcomes:
        brier = round(sum((c - o) ** 2 for c, o in outcomes) / len(outcomes), 4)
    return CalibrationResult(buckets=rows, brier=brier, sample_size=total)
