import copy

import pytest
from evals.causal_ablation.injection import (
    COST_CLASS,
    INJECTION_POINTS,
    MECHANISM_SWITCHES,
    POLLUTION_TYPES,
    apply_injection,
    build_injection_cases,
)

BASE = {
    "price": 10.0,
    "revenue": 1.0e9,
    "growth": 0.15,
    "macro_as_of": "2026-09-01",
    "news": ["真新闻A"],
    "stop_loss": 9.0,
    "entry": 10.0,
}


class TestMatrix:
    def test_all_eight_pollutions_registered(self):
        assert len(POLLUTION_TYPES) == 8
        assert set(POLLUTION_TYPES) == {
            "value_error",
            "direction_error",
            "unit_error",
            "period_shift",
            "mirror_narrative",
            "fabricated_event",
            "stale_macro",
            "illegal_price",
        }

    def test_three_injection_points(self):
        assert set(INJECTION_POINTS) == {"context", "analyst_output", "decision"}

    def test_every_pollution_has_cost_class(self):
        assert set(COST_CLASS) == set(POLLUTION_TYPES)
        assert set(COST_CLASS.values()) <= {"offline_replay", "real_run"}

    def test_verifier_side_pollutions_are_offline(self):
        for pt in (
            "direction_error",
            "unit_error",
            "period_shift",
            "fabricated_event",
            "stale_macro",
        ):
            assert COST_CLASS[pt] == "offline_replay"

    def test_input_side_pollutions_are_real_run(self):
        for pt in ("value_error", "mirror_narrative", "illegal_price"):
            assert COST_CLASS[pt] == "real_run"

    def test_every_switch_maps_to_registered_mechanism(self):
        from evals.causal_ablation.claims import DEFAULT_REGISTRY

        ids = {c.id for c in DEFAULT_REGISTRY}
        assert set(MECHANISM_SWITCHES) <= ids

    def test_switch_declares_on_and_off(self):
        for sw in MECHANISM_SWITCHES.values():
            assert sw.off_value != sw.on_value


class TestDeterminism:
    def test_same_seed_same_payload(self):
        a = build_injection_cases("value_error", ticker="002412", base_values=BASE, seed=7)
        b = build_injection_cases("value_error", ticker="002412", base_values=BASE, seed=7)
        assert a == b

    def test_different_seed_differs(self):
        a = build_injection_cases("value_error", ticker="002412", base_values=BASE, seed=1)
        b = build_injection_cases("value_error", ticker="002412", base_values=BASE, seed=2)
        assert a != b

    def test_n_cases_respected(self):
        assert len(build_injection_cases("value_error", ticker="T", base_values=BASE, n=4)) == 4

    def test_unknown_pollution_rejected(self):
        with pytest.raises(ValueError, match="污染类型"):
            build_injection_cases("meltdown", ticker="T", base_values=BASE)


class TestPayloadSemantics:
    def test_value_error_changes_numeric_by_5_to_50_pct(self):
        cases = build_injection_cases("value_error", ticker="T", base_values=BASE, n=4, seed=3)
        for c in cases:
            field, polluted = next(iter(c.payload["set"].items()))
            original = float(BASE[field])
            ratio = abs(polluted - original) / abs(original)
            assert 0.05 <= ratio <= 0.50

    def test_direction_error_flips_sign(self):
        case = build_injection_cases("direction_error", ticker="T", base_values=BASE, n=1)[0]
        assert case.payload["set"]["growth"] == pytest.approx(-0.15)

    def test_unit_error_is_1e8_scale(self):
        case = build_injection_cases("unit_error", ticker="T", base_values=BASE, n=1)[0]
        assert case.payload["set"]["revenue"] == pytest.approx(1.0e9 * 1.0e8)

    def test_fabricated_event_appends_title(self):
        case = build_injection_cases("fabricated_event", ticker="T", base_values=BASE, n=1)[0]
        assert len(case.payload["news_append"]) == 1

    def test_illegal_price_breaks_long_stop_rule(self):
        case = build_injection_cases("illegal_price", ticker="T", base_values=BASE, n=1)[0]
        assert case.payload["set"]["stop_loss"] > case.payload["set"]["entry"]


class TestApply:
    def test_apply_does_not_mutate_input(self):
        snapshot = copy.deepcopy(BASE)
        case = build_injection_cases("value_error", ticker="T", base_values=BASE, n=1)[0]
        apply_injection(snapshot, case)
        assert snapshot == BASE

    def test_apply_writes_payload(self):
        case = build_injection_cases("direction_error", ticker="T", base_values=BASE, n=1)[0]
        out = apply_injection(dict(BASE), case)
        assert out["growth"] == pytest.approx(-0.15)

    def test_news_append_extends_list(self):
        case = build_injection_cases("fabricated_event", ticker="T", base_values=BASE, n=1)[0]
        out = apply_injection(dict(BASE), case)
        assert len(out["news"]) == len(BASE["news"]) + 1
