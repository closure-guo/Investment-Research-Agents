import pytest
from evals.causal_ablation.family_b import (
    GroundingParam,
    absorption_rate,
    grounding_rate,
    majority_verdict,
    pairwise_assign,
    risk_point_diff,
)


class TestDiff:
    def test_new_points_extracted(self):
        prev = ["估值偏高", "行业景气下行"]
        cur = ["估值偏高", "行业景气下行", "应收周转恶化"]
        assert risk_point_diff(prev, cur) == ["应收周转恶化"]

    def test_whitespace_normalized(self):
        assert risk_point_diff(["A 风险"], ["A风险"]) == []

    def test_empty_prev_returns_all(self):
        assert risk_point_diff([], ["x", "y"]) == ["x", "y"]

    def test_order_stable(self):
        assert risk_point_diff([], ["b", "a"]) == ["b", "a"]


class TestAbsorption:
    def test_rate_over_new_points(self):
        assert absorption_rate(["a", "b"], lambda p: p == "a") == pytest.approx(0.5)

    def test_no_new_points_returns_none(self):
        assert absorption_rate([], lambda p: True) is None


class TestGrounding:
    def test_rate_counts_sourced_params(self):
        params = [
            GroundingParam("止损", 9.0, "trader:stop_loss"),
            GroundingParam("仓位", 0.3, None),
        ]
        assert grounding_rate(params) == pytest.approx(0.5)

    def test_empty_returns_none(self):
        assert grounding_rate([]) is None


class TestPairwise:
    def test_assignment_is_deterministic(self):
        assert pairwise_assign("full", "plus_debate", pair_key="002412#1") == pairwise_assign(
            "full", "plus_debate", pair_key="002412#1"
        )

    def test_assignment_returns_both_ids_once(self):
        got = pairwise_assign("full", "plus_debate", pair_key="600519#2")
        assert set(got) == {"full", "plus_debate"}

    def test_position_varies_across_keys(self):
        keys = [f"t#{i}" for i in range(30)]
        firsts = {pairwise_assign("full", "plus_debate", pair_key=k)[0] for k in keys}
        assert firsts == {"full", "plus_debate"}


class TestMajority:
    def test_majority_wins(self):
        assert majority_verdict(["A", "A", "B"]) == "A"

    def test_tie_returns_tie(self):
        assert majority_verdict(["A", "B"]) == "tie"

    def test_empty_is_tie(self):
        assert majority_verdict([]) == "tie"
