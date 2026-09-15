import pytest
from evals.causal_ablation.escape import EscapePair
from evals.causal_ablation.pilot_calibration import calibration_verdict


def _pairs(discordant: int, total: int) -> list[EscapePair]:
    out = [EscapePair(f"d{i}", "caught", "escaped") for i in range(discordant)]
    out += [EscapePair(f"s{i}", "caught", "caught") for i in range(total - discordant)]
    return out


class TestCalibration:
    def test_healthy_ratio_is_ok(self):
        got = calibration_verdict(_pairs(4, 10))
        assert got["verdict"] == "ok"

    def test_all_caught_is_too_easy(self):
        got = calibration_verdict(_pairs(0, 10))
        assert got["verdict"] == "too_easy"
        assert "强度" in got["advice"]

    def test_all_escaped_is_too_hard(self):
        got = calibration_verdict(_pairs(10, 10))
        assert got["verdict"] == "too_hard"

    def test_empty_rejected(self):
        with pytest.raises(ValueError):
            calibration_verdict([])
