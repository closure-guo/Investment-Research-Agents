"""聚类 bootstrap 与设计效应：推断单元是标的，不是 run/单元（spec causal-ablation）。"""

from __future__ import annotations

import math
from collections.abc import Sequence
from statistics import mean

import numpy as np


def cluster_mean_diff_ci(
    prev_by_cluster: dict[str, list[float]],
    cur_by_cluster: dict[str, list[float]],
    *,
    B: int = 10_000,  # noqa: N803 — 与 evals.stats 同口径
    seed: int = 42,
    alpha: float = 0.05,
) -> tuple[float, float]:
    """以簇（标的）为重采样单元的配对 bootstrap CI（mean(cur) - mean(prev)）。

    点估计是单元级均值差；CI 反映的是簇层不确定性——两个簇各 50 个单元也会得到宽 CI。
    """
    clusters = sorted(set(prev_by_cluster) & set(cur_by_cluster))
    if not clusters:
        raise ValueError("无共同簇：prev/cur 的簇集合必须一致")
    only_prev = set(prev_by_cluster) - set(cur_by_cluster)
    only_cur = set(cur_by_cluster) - set(prev_by_cluster)
    if only_prev or only_cur:
        raise ValueError(f"簇不配对: 仅 prev={sorted(only_prev)} 仅 cur={sorted(only_cur)}")

    def _mean(values: Sequence[float]) -> float:
        return mean(values) if values else 0.0

    point = _mean([v for t in clusters for v in cur_by_cluster[t]]) - _mean(
        [v for t in clusters for v in prev_by_cluster[t]]
    )
    rng = np.random.default_rng(seed)
    n = len(clusters)
    diffs = np.empty(B, dtype=float)
    for i in range(B):
        picks = rng.integers(0, n, size=n)
        cur_vals = [v for p in picks for v in cur_by_cluster[clusters[p]]]
        prev_vals = [v for p in picks for v in prev_by_cluster[clusters[p]]]
        diffs[i] = _mean(cur_vals) - _mean(prev_vals)
    lo = float(np.percentile(diffs, 100 * alpha / 2))
    hi = float(np.percentile(diffs, 100 * (1 - alpha / 2)))
    # 簇数很少时重采样可能退化到只抽中同一簇，点估计会落在分位区间之外；
    # 并入点估计保证区间始终可解释（不静默产出误导性窄区间）
    return (min(lo, point), max(hi, point))


def design_effect(cluster_sizes: Sequence[int], icc: float) -> float:
    """设计效应 1 + (m-1)ρ；m 取簇大小均值。"""
    if not cluster_sizes:
        raise ValueError("cluster_sizes 不得为空")
    m = mean(cluster_sizes)
    return 1.0 + (m - 1.0) * icc


def effective_n(total_units: int, deff: float) -> float:
    """有效样本量 = 总单元数 / 设计效应。"""
    if deff <= 0:
        raise ValueError(f"设计效应必须为正: {deff}")
    return total_units / deff


# ── 功效反算（预登记 MDE 的换算依据，不得为裸数字） ──


def mde_paired_binary(
    n_pairs: float,
    *,
    discordance: float = 0.5,
    alpha: float = 0.05,
    power: float = 0.8,
) -> float | None:
    """配对二值设计的最小可检出差异（McNemar 口径，量纲 = 比例差）。

    n_pairs 为**有效配对单元数**（本项目 = 标的，或按设计效应折算后的有效标的数）；
    discordance ψ = P(两态判定不一致)——未知时取 0.5（最大方差、保守）。换算依据（标准近似）：

        power(δ) ≈ Φ( δ·√(n/ψ) − z_{α/2} )        ⇒  δ_MDE = (z_{α/2} + z_β)·√(ψ/n)

    两条下界约束：δ ≤ 1（比例差不可能超过 1）且 δ² ≤ ψ（不一致率是差异的上界）。
    违反任一 → 返回 None（**该样本量下无分辨率**，不得报一个小数字假装能测）。
    """
    if n_pairs < 1:
        raise ValueError(f"配对单元数必须 ≥1: {n_pairs}")
    if not 0.0 < discordance <= 1.0:
        raise ValueError(f"discordance 须落 (0, 1]: {discordance}")
    if not 0.0 < alpha < 1.0 or not 0.0 < power < 1.0:
        raise ValueError(f"alpha/power 须落 (0,1): alpha={alpha}, power={power}")
    delta = (_z(1.0 - alpha / 2.0) + _z(power)) * math.sqrt(discordance / n_pairs)
    if delta > 1.0 or delta * delta > discordance:
        return None
    return delta


def _z(p: float) -> float:
    """标准正态分位数（二分反解，精度足够功效换算）。"""
    if not 0.0 < p < 1.0:
        raise ValueError(f"概率须落 (0,1): {p}")
    lo, hi = -10.0, 10.0
    for _ in range(200):
        mid = (lo + hi) / 2.0
        if _normal_cdf(mid) < p:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2.0


def _normal_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def mde_table(
    ns: Sequence[float], *, discordance: float = 0.5, alpha: float = 0.05, power: float = 0.8
) -> list[dict]:
    """MDE 随有效配对单元数的变化（预登记文档直接引用；None = 该 n 无分辨率）。"""
    return [
        {
            "n_pairs": n,
            "mde": mde_paired_binary(n, discordance=discordance, alpha=alpha, power=power),
            "discordance": discordance,
        }
        for n in ns
    ]
