import pytest
from evals.causal_ablation.conclusion import (
    assert_sentence_legal,
    conclude,
)


class TestConclude:
    def test_ci_below_threshold_is_true_negative(self):
        got = conclude((0.0, 1.0), mde=2.0, threshold=3.0, unit="拦截率")
        assert got.form == "true_negative"
        assert "3.0" in got.sentence and "2.0" in got.sentence

    def test_ci_crossing_zero_and_threshold_is_inconclusive(self):
        got = conclude((-0.5, 4.0), mde=2.0, threshold=3.0)
        assert got.form == "inconclusive"
        assert "分辨率不足" in got.sentence

    def test_ci_above_threshold_is_not_a_cut_candidate(self):
        got = conclude((5.0, 9.0), mde=2.0, threshold=3.0)
        assert got.form == "inconclusive"
        assert "扩至" in got.sentence

    def test_mde_always_present_in_sentence(self):
        got = conclude((0.0, 1.0), mde=1.5, threshold=2.0)
        assert "MDE" in got.sentence


class TestLegality:
    def test_bare_no_support_rejected(self):
        with pytest.raises(ValueError, match="不合格"):
            assert_sentence_legal("该层价值未获统计支持", mde=None)

    def test_sentence_with_mde_passes(self):
        assert_sentence_legal("在本实验分辨率（MDE=2.0）下……", mde=2.0)
