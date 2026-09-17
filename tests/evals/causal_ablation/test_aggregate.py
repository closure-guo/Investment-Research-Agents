import math
from statistics import mean

import pytest
from evals.causal_ablation.aggregate import (
    _z,
    cluster_mean_diff_ci,
    design_effect,
    effective_n,
    mde_paired_binary,
    mde_table,
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


class TestMdePairedBinary:
    """预登记 MDE 换算（McNemar 口径）：数字必须可复算，无分辨率必须报 None。"""

    def test_closed_form_matches_formula(self):
        got = mde_paired_binary(20, discordance=0.2)
        assert got == pytest.approx(2.8016 * (0.2 / 20) ** 0.5, rel=1e-3)

    def test_mde_shrinks_with_n(self):
        small = mde_paired_binary(10)
        large = mde_paired_binary(40)
        assert small is not None and large is not None and small > large

    def test_looser_discordance_shrinks_mde(self):
        tight = mde_paired_binary(20, discordance=0.2)
        loose = mde_paired_binary(20, discordance=0.5)
        assert tight is not None and loose is not None and tight < loose

    def test_underpowered_n_returns_none_not_small_number(self):
        """n=5（B3/B4 的有效分母）即使不一致率很低也测不出 → None（不得报小数假装能测）。"""
        assert mde_paired_binary(5) is None
        assert mde_paired_binary(5, discordance=0.2) is None

    def test_mde_never_exceeds_one(self):
        for n in (2, 3, 4, 6, 8, 12):
            value = mde_paired_binary(n)
            assert value is None or 0.0 < value <= 1.0

    def test_zero_or_negative_n_rejected(self):
        with pytest.raises(ValueError, match="配对单元数"):
            mde_paired_binary(0)

    def test_discordance_bounds(self):
        with pytest.raises(ValueError, match="discordance"):
            mde_paired_binary(20, discordance=0.0)
        with pytest.raises(ValueError, match="discordance"):
            mde_paired_binary(20, discordance=1.5)

    def test_table_reports_none_rows(self):
        rows = mde_table([5, 20], discordance=0.5)
        assert rows[0]["mde"] is None
        assert rows[1]["mde"] is not None

    def test_z_quantiles(self):
        assert _z(0.975) == pytest.approx(1.96, abs=0.01)
        assert _z(0.8) == pytest.approx(0.8416, abs=0.01)
