from pathlib import Path

import pytest
from evals.causal_ablation.claims import (
    DEFAULT_REGISTRY,
    CausalClaim,
    UnregisteredTargetError,
    assert_admissible,
    load_registry,
    validate_registry,
)


class TestRegistryShape:
    def test_covers_family_a_mechanisms(self):
        ids = {c.id for c in DEFAULT_REGISTRY if c.family == "A"}
        assert ids == {"A1", "A2", "A3", "A4", "A5", "A6", "A7"}

    def test_covers_family_b_layers(self):
        ids = {c.id for c in DEFAULT_REGISTRY if c.family == "B"}
        assert ids == {"B1", "B2", "B3", "B4", "B5", "B6"}

    def test_every_claim_has_metric_and_method(self):
        for c in DEFAULT_REGISTRY:
            assert c.claim and c.failure_mode and c.primary_metric
            assert c.method in {"code", "nli", "judge"}

    def test_a5_effect_expectation_is_pending_not_proven(self):
        a5 = next(c for c in DEFAULT_REGISTRY if c.id == "A5")
        assert "待" in a5.effect_expectation  # incident 027 修正：不得声称已实证


class TestValidation:
    def test_valid_registry_has_no_issues(self):
        assert validate_registry(DEFAULT_REGISTRY) == []

    def test_duplicate_id_reported(self):
        dup = (DEFAULT_REGISTRY[0], DEFAULT_REGISTRY[0])
        assert any("重复" in issue for issue in validate_registry(dup))

    def test_unknown_method_reported(self):
        bad = CausalClaim(
            id="X1",
            family="A",
            target="t",
            claim="c",
            failure_mode="f",
            primary_metric="m",
            method="vibes",
            effect_expectation="e",
        )
        assert any("method" in issue for issue in validate_registry((bad,)))


class TestAdmission:
    def test_registered_target_passes(self):
        assert assert_admissible("A3").id == "A3"

    def test_unregistered_target_rejected(self):
        with pytest.raises(UnregisteredTargetError):
            assert_admissible("Z9")


class TestJsonMerge:
    def test_json_override_adds_target(self, tmp_path: Path):
        p = tmp_path / "registry.json"
        p.write_text(
            '[{"id":"B7","family":"B","target":"新层","claim":"c","failure_mode":"f",'
            '"primary_metric":"m","method":"code","effect_expectation":"e"}]',
            encoding="utf-8",
        )
        merged = load_registry(p)
        assert "B7" in {c.id for c in merged}
        assert len(merged) == len(DEFAULT_REGISTRY) + 1

    def test_missing_file_falls_back_to_default(self, tmp_path: Path):
        assert load_registry(tmp_path / "nope.json") == DEFAULT_REGISTRY
