import pytest
from evals.causal_ablation.escape import (
    EscapePair,
    classify_state,
    escape_rate,
    mcnemar_table,
    split_verifier_buckets,
)


class TestClassify:
    def test_flagged_is_caught(self):
        assert classify_state(polluted_present=True, flagged=True) == "caught"

    def test_present_and_unflagged_is_escaped(self):
        assert classify_state(polluted_present=True, flagged=False) == "escaped"

    def test_absent_is_caught(self):
        assert classify_state(polluted_present=False, flagged=False) == "caught"


class TestMcNemar:
    def test_counts_discordant_pairs(self):
        pairs = [
            EscapePair("a", "caught", "escaped"),  # b
            EscapePair("b", "escaped", "caught"),  # c
            EscapePair("c", "caught", "caught"),  # 一致
            EscapePair("d", "escaped", "escaped"),  # 一致
        ]
        table = mcnemar_table(pairs)
        assert table["b"] == 1
        assert table["c"] == 1
        assert table["discordant_ratio"] == pytest.approx(0.5)

    def test_no_discordant_pairs_ratio_zero(self):
        table = mcnemar_table([EscapePair("a", "caught", "caught")])
        assert table["discordant_ratio"] == 0.0


class TestEscapeRate:
    def test_rate_denominator_is_adjudicated_units(self):
        pairs = [
            EscapePair("a", "caught", "escaped"),
            EscapePair("c", "caught", "caught"),
        ]
        result = escape_rate(pairs, adjudicated={"a", "c"})
        assert result["escapes"] == 1
        assert result["denominator"] == 2
        assert result["rate"] == pytest.approx(0.5)

    def test_unadjudicated_escapes_are_pending_not_counted(self):
        pairs = [
            EscapePair("a", "caught", "escaped"),
            EscapePair("b", "caught", "escaped"),
        ]
        result = escape_rate(pairs, adjudicated={"a"})
        assert result["escapes"] == 1
        assert result["pending"] == 1
        assert result["rate"] == pytest.approx(1.0)

    def test_no_adjudication_yields_none_not_zero(self):
        """未终裁时不得报 0%——「0 逃逸」与「没判定过」是两回事。"""
        result = escape_rate([EscapePair("a", "caught", "escaped")], adjudicated=set())
        assert result["escapes"] == 0
        assert result["pending"] == 1
        assert result["rate"] is None

    def test_empty_pairs_rate_none(self):
        assert escape_rate([], adjudicated=set())["rate"] is None


class TestFalsePositiveSplit:
    def test_four_buckets_total(self):
        got = split_verifier_buckets(
            {"blocked": 3, "analyst_true_fail": 1, "surgical_repaired": 2, "verifier_normalized": 4}
        )
        assert got["total"] == 10
        assert got["verifier_normalized"] == 4

    def test_unknown_bucket_rejected(self):
        with pytest.raises(ValueError, match="未知桶"):
            split_verifier_buckets({"误报": 1})


class TestClaimContractErrorBucket:
    """claim_contract_error 桶（任务 2）：校验器侧契约病，进拆报不计逃逸分母。"""

    def test_contract_error_bucket_accepted_in_split(self):
        from evals.causal_ablation.escape import split_verifier_buckets

        got = split_verifier_buckets({"claim_contract_error": 2, "analyst_true_fail": 1})
        assert got["claim_contract_error"] == 2
        assert got["total"] == 3
