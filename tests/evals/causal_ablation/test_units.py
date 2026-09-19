from pathlib import Path

import pytest
from evals.causal_ablation.units import (
    UnitJudgment,
    incomplete_reasons,
    read_units,
    write_units,
)


def _unit(**kw):
    base = {
        "unit_id": "u1",
        "ticker": "002412",
        "run": "r1",
        "variant": "full",
        "unit_type": "claim",
        "judgment": "pass",
        "method": "code",
        "confidence": 0.9,
    }
    base.update(kw)
    return UnitJudgment(**base)


class TestValidation:
    def test_valid_unit_constructs(self):
        assert _unit().unit_type == "claim"

    @pytest.mark.parametrize("method", ["code", "nli", "judge"])
    def test_allowed_methods(self, method):
        assert _unit(method=method).method == method

    def test_bad_method_rejected(self):
        with pytest.raises(ValueError, match="method"):
            _unit(method="guess")

    def test_bad_unit_type_rejected(self):
        with pytest.raises(ValueError, match="unit_type"):
            _unit(unit_type="啥")

    @pytest.mark.parametrize("conf", [-0.1, 1.5])
    def test_confidence_out_of_range_rejected(self, conf):
        with pytest.raises(ValueError, match="confidence"):
            _unit(confidence=conf)

    def test_empty_judgment_rejected(self):
        with pytest.raises(ValueError, match="judgment"):
            _unit(judgment="")


class TestRoundTrip:
    def test_jsonl_round_trip(self, tmp_path: Path):
        p = tmp_path / "units.jsonl"
        units = [_unit(unit_id="u1"), _unit(unit_id="u2", method="judge", unit_type="risk_point")]
        write_units(p, units)
        assert read_units(p) == units

    def test_read_missing_file_returns_empty(self, tmp_path: Path):
        assert read_units(tmp_path / "nope.jsonl") == []


class TestCompleteness:
    def test_complete_set_has_no_reasons(self):
        assert incomplete_reasons([_unit()]) == []

    def test_missing_field_reported(self):
        reasons = incomplete_reasons([_unit(ticker="")])
        assert any("ticker" in r for r in reasons)
