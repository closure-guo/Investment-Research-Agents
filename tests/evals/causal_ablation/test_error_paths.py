"""错误路径覆盖：这些分支是契约的一部分（拒绝非法输入），不是可省略的防御代码。"""

import pandas as pd
import pytest
from evals.causal_ablation.aggregate import cluster_mean_diff_ci, effective_n
from evals.causal_ablation.claims import (
    CausalClaim,
    UnregisteredTargetError,
    assert_admissible,
    validate_registry,
)
from evals.causal_ablation.conclusion import assert_sentence_legal
from evals.causal_ablation.injection import POLLUTION_TYPES, build_injection_cases


class TestAggregateErrors:
    def test_unpaired_clusters_rejected(self):
        with pytest.raises(ValueError, match="簇不配对"):
            cluster_mean_diff_ci({"a": [1.0]}, {"a": [1.0], "b": [2.0]})

    @pytest.mark.parametrize("deff", [0.0, -1.0])
    def test_non_positive_design_effect_rejected(self, deff):
        with pytest.raises(ValueError, match="设计效应"):
            effective_n(100, deff)


class TestClaimsErrors:
    def test_missing_metric_reported(self):
        bad = CausalClaim(
            id="Y1",
            family="A",
            target="t",
            claim="c",
            failure_mode="f",
            primary_metric="",
            method="code",
            effect_expectation="e",
        )
        assert any("缺主指标" in i for i in validate_registry((bad,)))

    def test_missing_claim_reported(self):
        bad = CausalClaim(
            id="Y2",
            family="A",
            target="t",
            claim="",
            failure_mode="f",
            primary_metric="m",
            method="code",
            effect_expectation="e",
        )
        assert any("缺因果主张" in i for i in validate_registry((bad,)))

    def test_admissible_uses_passed_registry(self):
        custom = CausalClaim(
            id="C9",
            family="B",
            target="t",
            claim="c",
            failure_mode="f",
            primary_metric="m",
            method="code",
            effect_expectation="e",
        )
        assert assert_admissible("C9", (custom,)).id == "C9"
        with pytest.raises(UnregisteredTargetError):
            assert_admissible("A1", (custom,))


class TestConclusionErrors:
    def test_sentence_missing_mde_text_rejected(self):
        with pytest.raises(ValueError, match="未写出 MDE"):
            assert_sentence_legal("该层增量低于阈值", mde=2.0)


class TestInjectionPayloads:
    @pytest.mark.parametrize("pollution", POLLUTION_TYPES)
    def test_every_pollution_builds_payload(self, pollution):
        base = {
            "price": 10.0,
            "revenue": 1.0e9,
            "growth": 0.15,
            "macro_as_of": "2026-09-01",
            "news": [],
            "entry": 10.0,
            "stop_loss": 9.0,
            "close_series": [1.0, 2.0, 3.0],
        }
        cases = build_injection_cases(pollution, ticker="T", base_values=base, n=1)
        assert cases[0].payload, f"{pollution} 未构造 payload"

    def test_mirror_narrative_marked_as_indicator_series_reversal(self):
        case = build_injection_cases("mirror_narrative", ticker="T", base_values={}, n=1)[0]
        assert case.payload["op"] == "reverse_series"
        assert case.payload["target"] == "technical_indicators"
        assert case.payload["path"] == ["MA", "5"]
        assert case.payload["fallback"]["target"] == "kline"

    def test_stale_macro_sets_old_as_of(self):
        case = build_injection_cases("stale_macro", ticker="T", base_values={}, n=1)[0]
        assert case.payload["as_of_date"] < "2026-09-01"
        assert case.payload["freshness"] == "stale"

    def test_mirror_narrative_falls_back_to_kline_rows_without_indicators(self):
        from evals.causal_ablation.injection import apply_injection

        base = {"kline": pd.DataFrame({"日期": ["d1", "d2", "d3"], "收盘": [1.0, 2.0, 3.0]})}
        case = build_injection_cases("mirror_narrative", ticker="T", base_values={}, n=1)[0]
        out, effect = apply_injection(base, case)
        assert out["kline"]["收盘"].tolist() == [3.0, 2.0, 1.0]
        assert effect.applied is True
        assert effect.target == "kline"

    def test_mirror_narrative_without_kline_records_not_applied(self):
        from evals.causal_ablation.injection import apply_injection

        base = {"price": 10.0}
        case = build_injection_cases("mirror_narrative", ticker="T", base_values={}, n=1)[0]
        out, effect = apply_injection(dict(base), case)
        assert out == base
        assert effect.applied is False and "kline" in effect.reason


class TestPreregisterLookupEdge:
    def test_dir_without_markdown_returns_none(self, tmp_path):
        from evals.causal_ablation.preregister import find_latest_preregister

        (tmp_path / "notes.txt").write_text("not a preregister doc", encoding="utf-8")
        assert find_latest_preregister(tmp_path) is None

    def test_missing_dir_returns_none(self, tmp_path):
        from evals.causal_ablation.preregister import find_latest_preregister

        assert find_latest_preregister(tmp_path / "does-not-exist") is None
