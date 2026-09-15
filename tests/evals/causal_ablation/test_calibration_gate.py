import pytest
from evals.causal_ablation.calibration_gate import (
    CalibrationBlockedError,
    agreement_rate,
    assert_calibrated,
    gate_dimension,
)


class TestAgreement:
    def test_perfect_agreement(self):
        assert agreement_rate(["a", "b"], ["a", "b"]) == pytest.approx(1.0)

    def test_partial_agreement(self):
        assert agreement_rate(["a", "b", "b", "a"], ["a", "b", "a", "a"]) == pytest.approx(0.75)

    def test_length_mismatch_rejected(self):
        with pytest.raises(ValueError, match="等长"):
            agreement_rate(["a"], ["a", "b"])

    def test_empty_rejected(self):
        with pytest.raises(ValueError):
            agreement_rate([], [])


class TestGate:
    def test_code_method_not_gated(self):
        got = gate_dimension("code", [], [])
        assert got["gated"] is False
        assert got["passed"] is True

    def test_nli_below_threshold_blocked(self):
        got = gate_dimension("nli", ["a"] * 7 + ["b"] * 3, ["a"] * 10)
        assert got["gated"] is True
        assert got["passed"] is False
        assert got["agreement"] == pytest.approx(0.7)

    def test_judge_at_threshold_passes(self):
        got = gate_dimension("judge", ["a"] * 8 + ["b"] * 2, ["a"] * 10)
        assert got["passed"] is True

    def test_assert_raises_for_uncalibrated(self):
        with pytest.raises(CalibrationBlockedError, match="nli"):
            assert_calibrated("nli", ["a", "b"], ["a", "a"])

    def test_assert_passes_for_calibrated(self):
        assert_calibrated("judge", ["a"] * 9 + ["b"], ["a"] * 10)

    def test_unknown_method_rejected(self):
        with pytest.raises(ValueError, match="method"):
            gate_dimension("vibes", ["a"], ["a"])
