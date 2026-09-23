"""outcome 两句式结论纪律（spec evaluation「Outcome 读数收口纪律」第③步）。"""

import pytest
from evals.outcome.conclusion import (
    LEGAL_FORMS,
    assert_outcome_sentence_legal,
    conclude_outcome,
)


class TestConclude:
    def test_positive_when_ci_lower_above_zero(self):
        c = conclude_outcome((0.021, 0.068), mde=0.051, n=30)
        assert c.form in LEGAL_FORMS
        assert c.form == "positive"
        assert "显著为正" in c.sentence and "CI" in c.sentence
        assert "5.10%" in c.sentence  # MDE 格式化披露
        assert_outcome_sentence_legal(c.sentence)

    def test_negative_when_ci_upper_below_zero(self):
        c = conclude_outcome((-0.08, -0.012), mde=0.051, n=30)
        assert c.form in LEGAL_FORMS
        assert c.form == "negative"
        assert "显著为负" in c.sentence and "归因" in c.sentence  # 先归因后处置
        assert_outcome_sentence_legal(c.sentence)

    def test_inconclusive_when_ci_straddles_zero(self):
        c = conclude_outcome((-0.02, 0.03), mde=0.051, n=42)
        assert c.form in LEGAL_FORMS
        assert c.form == "inconclusive"
        assert "分辨率不足" in c.sentence and "MDE" in c.sentence
        assert_outcome_sentence_legal(c.sentence)

    def test_below_red_line_raises(self):
        with pytest.raises(ValueError, match="红线"):
            conclude_outcome((0.0001, 0.02), mde=0.051, n=3)

    def test_forms_are_legal_set(self):
        assert set(LEGAL_FORMS) == {"positive", "negative", "inconclusive"}


class TestSentenceLegality:
    def test_bare_no_support_illegal(self):
        with pytest.raises(ValueError, match="裸「未获统计支持」"):
            assert_outcome_sentence_legal("本批未获统计支持")

    def test_significant_with_ci_but_bare_no_support_illegal(self):
        with pytest.raises(ValueError, match="裸「未获统计支持」"):
            assert_outcome_sentence_legal("显著为正但本批未获统计支持（95% CI 下限 > 0）")

    def test_significant_without_ci_illegal(self):
        with pytest.raises(ValueError, match="CI"):
            assert_outcome_sentence_legal("显著为正，赚钱能力主张成立")

    def test_inconclusive_without_mde_illegal(self):
        with pytest.raises(ValueError, match="MDE"):
            assert_outcome_sentence_legal("分辨率不足，需扩样")
