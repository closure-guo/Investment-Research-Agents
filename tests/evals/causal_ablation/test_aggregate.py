import math
from statistics import mean

import pytest
from evals.causal_ablation.aggregate import (
    cluster_mean_diff_ci,
    design_effect,
    effective_n,
)


class TestClusterCi:
    def test_null_cluster_contains_zero(self):
        prev = {"a": [5.0, 5.0], "b": [4.0, 4.0], "c": [4.5, 4.5]}
        cur = {"a": [5.0, 5.0], "b": [4.0, 4.0], "c": [4.5, 4.5]}
        lo, hi = cluster_mean_diff_ci(prev, cur)
        assert lo <= 0 <= hi

    def test_strong_positive_shift_excludes_zero(self):
        prev = {t: [3.0] * 5 for t in ("a", "b", "c", "d", "e", "f")}
        cur = {t: [5.0] * 5 for t in ("a", "b", "c", "d", "e", "f")}
        lo, hi = cluster_mean_diff_ci(prev, cur)
        assert lo > 0

    def test_point_estimate_is_unit_mean_diff(self):
        prev = {"a": [1.0, 3.0], "b": [2.0, 2.0]}
        cur = {"a": [3.0, 5.0], "b": [4.0, 4.0]}
        lo, hi = cluster_mean_diff_ci(prev, cur)
        assert lo <= mean([3.0, 5.0, 4.0, 4.0]) - mean([1.0, 3.0, 2.0, 2.0]) <= hi

    def test_cluster_mismatch_rejected(self):
        with pytest.raises(ValueError, match="簇"):
            cluster_mean_diff_ci({"a": [1.0]}, {"b": [1.0]})

    def test_resampling_respects_cluster_boundary(self):
        # 簇间方向相反：按单元重采样会得到窄 CI；按簇重采样必须给出覆盖 [-10, 10] 的宽 CI
        prev = {"a": [0.0] * 50, "b": [10.0] * 50}
        cur = {"a": [10.0] * 50, "b": [0.0] * 50}
        lo, hi = cluster_mean_diff_ci(prev, cur, B=2000)
        assert hi - lo > 1.0


class TestDesignEffect:
    def test_icc_zero_is_one(self):
        assert design_effect([30, 30, 30], 0.0) == pytest.approx(1.0)

    def test_formula(self):
        # 1 + (m-1)ρ，m=30, ρ=0.1 → 3.9
        assert design_effect([30] * 20, 0.1) == pytest.approx(3.9)

    def test_effective_n_20x30_icc01(self):
        n = effective_n(600, design_effect([30] * 20, 0.1))
        assert math.isclose(n, 600 / 3.9, rel_tol=1e-6)
        assert 150 < n < 160

    def test_empty_sizes_rejected(self):
        with pytest.raises(ValueError):
            design_effect([], 0.1)
