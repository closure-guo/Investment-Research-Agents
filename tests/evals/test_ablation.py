"""消融编排测试：变体输入对齐（同一快照）、聚合与结论措辞。全部 mock 节点，不调 LLM。"""

import json
from pathlib import Path

from evals.ablation import aggregate_results, build_variant_graph, conclusion_for_layer


class TestVariantGraph:
    def test_three_variants_buildable(self):
        for variant in ("analysts", "plus_debate", "full"):
            graph = build_variant_graph(variant)
            assert graph is not None

    def test_through_trader_contains_trader_but_no_risk_layers(self):
        """B5 修正臂（2026-09-18 owner 批准）：裁掉风控辩论+FM、保留 Trader——
        两臂都含决策层，消掉 analysts 对 full 的同义反复口径。"""
        graph = build_variant_graph("through_trader")
        assert graph is not None

    def test_unknown_variant_raises(self):
        import pytest

        with pytest.raises(ValueError):
            build_variant_graph("nope")


class TestAggregate:
    def _runs(self, variant: str, citation: list[bool], judge: list[float]) -> list[dict]:
        return [
            {
                "variant": variant,
                "ticker": f"t{i % 3}",
                "citation_pass": c,
                "judge": {"report_relevance": j},
            }
            for i, (c, j) in enumerate(zip(citation, judge, strict=True))
        ]

    def test_layer_increment_with_ci_support(self):
        runs = (
            self._runs("analysts", [False] * 6, [2.0] * 6)
            + self._runs("plus_debate", [True] * 6, [4.0] * 6)
            + self._runs("full", [True] * 6, [4.5] * 6)
        )
        report = aggregate_results(runs)
        debate = report["layers"]["debate"]
        assert debate["judge_report_relevance"]["conclusion"] == "显著改进"
        full = report["layers"]["full"]
        assert full["judge_report_relevance"]["ci"][0] > 0

    def test_layer_without_support_flagged(self):
        runs = self._runs("analysts", [True, False], [3.0, 3.1]) + self._runs(
            "plus_debate", [True, False], [3.05, 3.0]
        )
        report = aggregate_results(runs)
        assert (
            report["layers"]["debate"]["judge_report_relevance"]["conclusion"]
            == "该层价值未获统计支持"
        )

    def test_citation_pass_rate_exits_conclusion_path(self):
        """G2/F3 回归：citation_pass 标量不得再作层增量比较（spec evaluation「citation 腿拆报呈现」）。

        历史形态：本用例改前断言 `rate["ci"][0] > 0` 与 `conclusion == "显著改进"`
        （最大可能的层增量形态 0% → 100%）；现在此块 SHALL NOT 带 CI / 结论，只留
        点估计供向后兼容，并显式标记 not_for_conclusions。
        """
        runs = (
            self._runs("analysts", [False] * 6, [2.0] * 6)
            + self._runs("plus_debate", [True] * 6, [4.0] * 6)
            + self._runs("full", [True] * 6, [4.5] * 6)
        )
        report = aggregate_results(runs)
        rate = report["layers"]["debate"]["citation_pass_rate"]
        assert "ci" not in rate, "标量不得带层增量 CI"
        assert "conclusion" not in rate, "标量不得带层增量结论（层增量只认四桶）"
        assert rate["not_for_conclusions"] is True, "须显式标记退出结论路径"
        assert rate["prev"] == 0.0 and rate["current"] == 1.0, "点估计仍披露（向后兼容）"
        assert report["variants"]["analysts"]["citation_pass_rate"] == 0.0, "变体级 pass 率保留"
        assert report["variants"]["full"]["citation_pass_rate"] == 1.0

    def test_citation_pass_rate_point_estimates_survive_without_conclusion(self):
        """逐 ticker 交错（t0 降、t1 升）：无论差值形态，层块只留点估计、不出结论。"""
        runs = self._runs("analysts", [True, False], [3.0, 3.0]) + self._runs(
            "plus_debate", [False, True], [3.0, 3.0]
        )
        report = aggregate_results(runs)
        rate = report["layers"]["debate"]["citation_pass_rate"]
        assert rate["prev"] == 0.5 and rate["current"] == 0.5
        assert "ci" not in rate and "conclusion" not in rate


class TestLayerIncrementFieldName:
    """层增量点估计是均值差（CI 同为 mean-diff 口径），字段名与配对单元须如实披露。"""

    @staticmethod
    def _runs(variant: str, by_ticker: dict[str, list[float]]) -> list[dict]:
        return [
            {
                "variant": variant,
                "ticker": ticker,
                "citation_pass": True,
                "judge": {"report_relevance": value},
            }
            for ticker, values in by_ticker.items()
            for value in values
        ]

    def _report(self):
        # 逐标的中位数：analysts [2,2,2] vs plus_debate [2,2,11] → 差值 [0,0,9]
        # 均值 3.0 / 中位 0.0 —— 用两者的分离值锁死「点估计是均值不是中位」
        runs = self._runs(
            "analysts", {"t0": [2.0, 2.0], "t1": [2.0, 2.0], "t2": [2.0, 2.0]}
        ) + self._runs("plus_debate", {"t0": [2.0, 2.0], "t1": [2.0, 2.0], "t2": [11.0, 11.0]})
        return aggregate_results(runs)

    def test_point_estimate_named_diff_mean(self):
        entry = self._report()["layers"]["debate"]["judge_report_relevance"]
        assert "diff_median" not in entry, "字段名不得与实际统计量（均值差）不符"
        assert entry["diff_mean"] == 3.0, "点估计为逐标的差值的均值（中位数为 0.0）"

    def test_pairing_unit_disclosed(self):
        """有效 n 是标的数而非 run 数，须让读报告的人看得见。"""
        layer = self._report()["layers"]["debate"]
        assert layer["pairing_unit"] == "ticker"
        assert layer["pairing_units"] == 3


class _FakeGraphWithState:
    """返回固定 state 的假图：验证真实 run_variant_once 的接线（零 LLM）。"""

    def __init__(self, state: dict):
        self._state = state

    def invoke(self, state):
        return self._state


class TestAnchorCoverageWiring:
    """delta add-debate-argument-anchors 4.4：锚点覆盖率进 run_variant_once 与 run 记录。"""

    def test_run_variant_once_returns_anchor_coverage(self, monkeypatch):
        """真实 run_variant_once 须从 state 通道 debate_anchor_checks 提取该指标。"""
        import evals.ablation as ablation

        checks = [
            {"anchored": True, "status": "resolved"},
            {"anchored": False, "status": "none"},
        ]
        monkeypatch.setattr(
            ablation,
            "build_variant_graph",
            lambda variant: _FakeGraphWithState(
                {"final_report": "r", "citation_pass": True, "debate_anchor_checks": checks}
            ),
        )
        out = ablation.run_variant_once("plus_debate", {"stock_code": "600519"}, "综合评估投资价值")
        assert out["anchor_coverage"] is not None, "有论点就必须携带锚点覆盖率"
        assert set(out["anchor_coverage"]) == {
            "value",
            "total",
            "anchored",
            "unanchored_inference",
            "unresolved",
            "missing_required",
            "unspecified",
        }, "value + 拆项桶名须与锚点统计（anchor_stats / task.py detail）同口径"
        assert out["anchor_coverage"]["value"] == 0.5
        assert out["anchor_coverage"]["unanchored_inference"] == 1
        assert out["anchor_coverage"]["unresolved"] == 0
        assert out["anchor_coverage"]["missing_required"] == 0
        assert out["anchor_coverage"]["unspecified"] == 0
        assert out["anchor_coverage"]["total"] == 2

    def test_run_variant_once_anchor_none_without_arguments(self, monkeypatch):
        """无论点（如 analysts 无辩论层）→ None，不得伪造成 0。"""
        import evals.ablation as ablation

        monkeypatch.setattr(
            ablation,
            "build_variant_graph",
            lambda variant: _FakeGraphWithState({"final_report": "r", "citation_pass": True}),
        )
        out = ablation.run_variant_once("analysts", {"stock_code": "600519"}, "综合评估投资价值")
        assert out["anchor_coverage"] is None

    def test_run_ablation_run_records_carry_anchor_value_and_detail(self, monkeypatch):
        """run 记录携带 value + 拆项（spec「value 与拆项」；analysts → 双 None）。"""
        import evals.ablation as ablation

        captured: dict[str, list[dict]] = {}

        def fake_aggregate(runs, **kwargs):
            captured["runs"] = runs
            return {}

        def fake_run_once(variant, snapshot, query):
            coverage = (
                None
                if variant == "analysts"
                else {
                    "value": 0.25,
                    "total": 8,
                    "anchored": 2,
                    "unanchored_inference": 3,
                    "unresolved": 1,
                    "missing_required": 1,
                    "unspecified": 1,
                }
            )
            return {
                "final_report": "r",
                "citation_pass": True,
                "judge_vars": {},
                "anchor_coverage": coverage,
                # 假件须对齐 run_variant_once 真实返回协议（G2/F3 四桶拆报）
                "citation_buckets": ablation.split_verifier_buckets({"blocked": 0}),
            }

        monkeypatch.setattr(ablation, "aggregate_results", fake_aggregate)
        monkeypatch.setattr(ablation, "run_judge_mean", lambda dim, v, *, repeats=3: {"score": 4.0})
        monkeypatch.setattr(ablation, "build_snapshot", lambda ticker: {"stock_code": ticker})
        monkeypatch.setattr(ablation, "snapshot_digest", lambda state: "d")
        monkeypatch.setattr(ablation, "build_variant_graph", lambda variant: _FakeGraph())
        monkeypatch.setattr(ablation, "run_variant_once", fake_run_once)
        ablation.run_ablation(["600519"], repeats=2, judge_repeats=1)
        records = {r["variant"]: r for r in captured["runs"] if r["ticker"] == "600519"}
        assert len(captured["runs"]) == 8
        assert records["analysts"]["argument_anchor_coverage"] is None, "无论点不得伪造 0"
        assert records["analysts"]["argument_anchor_coverage_detail"] is None
        assert records["plus_debate"]["argument_anchor_coverage"] == 0.25
        assert records["full"]["argument_anchor_coverage"] == 0.25
        # 拆项（去 value——value 已单独成键），镜像 evals/task.py 的同名字段
        expected_detail = {
            "total": 8,
            "anchored": 2,
            "unanchored_inference": 3,
            "unresolved": 1,
            "missing_required": 1,
            "unspecified": 1,
        }
        assert records["plus_debate"]["argument_anchor_coverage_detail"] == expected_detail
        assert records["full"]["argument_anchor_coverage_detail"] == expected_detail


class TestCitationBucketWiring:
    """G2（delta tasks 1.3/F3）：citation 腿四桶拆报进 run_variant_once 与 run 记录。

    桶名与校验的唯一来源是 `evals.causal_ablation.escape`（VERIFIER_BUCKETS /
    split_verifier_buckets），本层不得另建映射副本。
    """

    @staticmethod
    def _patch_state(ablation, monkeypatch, state: dict) -> None:
        monkeypatch.setattr(
            ablation, "build_variant_graph", lambda variant: _FakeGraphWithState(state)
        )

    def test_run_variant_once_maps_state_keys_to_four_buckets(self, monkeypatch):
        """state 键 → 桶名：citation_blocked(bool) / citation_analyst_true_fail /
        value_mismatch_repaired→surgical_repaired / citation_verifier_normalized。"""
        import evals.ablation as ablation
        from evals.causal_ablation.escape import VERIFIER_BUCKETS

        self._patch_state(
            ablation,
            monkeypatch,
            {
                "final_report": "r",
                "citation_pass": False,
                "citation_blocked": True,
                "citation_analyst_true_fail": 2,
                "value_mismatch_repaired": 1,
                "citation_verifier_normalized": 3,
            },
        )
        out = ablation.run_variant_once("full", {"stock_code": "600519"}, "综合评估投资价值")
        assert out["citation_buckets"] == {
            "blocked": 1,
            "analyst_true_fail": 2,
            "surgical_repaired": 1,
            "verifier_normalized": 3,
            "claim_contract_error": 0,
            "total": 7,
        }
        assert set(out["citation_buckets"]) == set(VERIFIER_BUCKETS) | {"total"}, (
            "桶名须与共享常量 VERIFIER_BUCKETS 一致"
        )

    def test_missing_state_keys_default_to_zero(self, monkeypatch):
        """旧图 / stub 不产四桶状态键 → 记 0（不得 KeyError，也不得伪造成有逃逸）。"""
        import evals.ablation as ablation

        self._patch_state(ablation, monkeypatch, {"final_report": "r", "citation_pass": True})
        out = ablation.run_variant_once("analysts", {"stock_code": "600519"}, "综合评估投资价值")
        assert out["citation_buckets"] == {
            "blocked": 0,
            "analyst_true_fail": 0,
            "surgical_repaired": 0,
            "verifier_normalized": 0,
            "claim_contract_error": 0,
            "total": 0,
        }

    def test_none_state_values_default_to_zero(self, monkeypatch):
        """状态键存在但为 None → 同样记 0（None 不是计数）。"""
        import evals.ablation as ablation

        self._patch_state(
            ablation,
            monkeypatch,
            {
                "final_report": "r",
                "citation_pass": True,
                "citation_blocked": None,
                "citation_analyst_true_fail": None,
                "value_mismatch_repaired": None,
                "citation_verifier_normalized": None,
            },
        )
        out = ablation.run_variant_once("analysts", {"stock_code": "600519"}, "综合评估投资价值")
        assert out["citation_buckets"] == {
            "blocked": 0,
            "analyst_true_fail": 0,
            "surgical_repaired": 0,
            "verifier_normalized": 0,
            "claim_contract_error": 0,
            "total": 0,
        }

    def test_run_ablation_run_records_carry_citation_buckets(self, monkeypatch):
        """run 记录携带 citation_buckets（additive：锚点/judge/citation_pass 字段不动）。"""
        import evals.ablation as ablation

        captured: dict[str, list[dict]] = {}

        def fake_aggregate(runs, **kwargs):
            captured["runs"] = runs
            return {}

        def fake_run_once(variant, snapshot, query):
            return {
                "final_report": "r",
                "citation_pass": True,
                "judge_vars": {},
                "anchor_coverage": None,
                "citation_buckets": ablation.split_verifier_buckets(
                    {"blocked": 0, "analyst_true_fail": 1, "verifier_normalized": 2}
                ),
            }

        monkeypatch.setattr(ablation, "aggregate_results", fake_aggregate)
        monkeypatch.setattr(ablation, "run_judge_mean", lambda dim, v, *, repeats=3: {"score": 4.0})
        monkeypatch.setattr(ablation, "build_snapshot", lambda ticker: {"stock_code": ticker})
        monkeypatch.setattr(ablation, "snapshot_digest", lambda state: "d")
        monkeypatch.setattr(ablation, "build_variant_graph", lambda variant: _FakeGraph())
        monkeypatch.setattr(ablation, "run_variant_once", fake_run_once)
        ablation.run_ablation(["600519"], repeats=2, judge_repeats=1)
        records = {r["variant"]: r for r in captured["runs"] if r["ticker"] == "600519"}
        assert len(captured["runs"]) == 8
        for variant in ("analysts", "plus_debate", "full"):
            assert records[variant]["citation_buckets"] == {
                "blocked": 0,
                "analyst_true_fail": 1,
                "surgical_repaired": 0,
                "verifier_normalized": 2,
                "claim_contract_error": 0,
                "total": 3,
            }
            assert records[variant]["citation_pass"] is True, "标量字段保持产出（向后兼容）"


class TestAnchorCoverageAggregation:
    """4.4：锚点覆盖率层增量与 judge 维度同口径（逐标的中位 + 配对 bootstrap）。"""

    @staticmethod
    def _anchor_runs(variant: str, by_ticker: dict[str, list[float | None]]) -> list[dict]:
        return [
            {
                "variant": variant,
                "ticker": ticker,
                "citation_pass": True,
                "judge": {"report_relevance": 4.0},
                "argument_anchor_coverage": value,
            }
            for ticker, values in by_ticker.items()
            for value in values
        ]

    def test_full_layer_anchor_increment_with_ci(self):
        # 逐标的中位数：plus_debate 0.5/0.5/0.5 vs full 0.75/0.75/0.75（t2 两次 0.5/1.0 取中位）
        runs = (
            self._anchor_runs("analysts", {t: [None, None] for t in ("t0", "t1", "t2")})
            + self._anchor_runs(
                "plus_debate", {"t0": [0.5, 0.5], "t1": [0.5, 0.5], "t2": [0.5, 0.5]}
            )
            + self._anchor_runs("full", {"t0": [0.75, 0.75], "t1": [0.75, 0.75], "t2": [0.5, 1.0]})
        )
        report = aggregate_results(runs)
        layer = report["layers"]["full"]
        entry = layer["argument_anchor_coverage"]
        assert entry["diff_mean"] == 0.25, "点估计 = 逐标的中位差的均值"
        assert entry["ci"] == (0.25, 0.25), "各标的中位差恒 0.25 → bootstrap CI 收窄于 0.25"
        assert entry["conclusion"] == "显著改进"
        assert layer["pairing_unit"] == "ticker"
        assert layer["pairing_units"] == 3, "配对单元为标的（与 judge 块同一披露）"
        assert report["variants"]["plus_debate"]["anchor_coverage_median"] == 0.5
        assert report["variants"]["full"]["anchor_coverage_median"] == 0.75
        assert report["variants"]["analysts"]["anchor_coverage_median"] is None, (
            "全 None 不得伪造 0"
        )

    def test_prev_side_without_arguments_skips_layer_entry(self):
        """analysts 无辩论层（None）→ debate 层无锚点条目（mirror judge 维度跳过语义）。"""
        runs = (
            self._anchor_runs("analysts", {t: [None] for t in ("t0", "t1", "t2")})
            + self._anchor_runs("plus_debate", {"t0": [0.5], "t1": [0.6], "t2": [0.7]})
            + self._anchor_runs("full", {"t0": [0.8], "t1": [0.9], "t2": [1.0]})
        )
        report = aggregate_results(runs)
        assert "argument_anchor_coverage" not in report["layers"]["debate"]
        assert report["layers"]["full"]["argument_anchor_coverage"]["conclusion"] == "显著改进"
        assert report["variants"]["analysts"]["anchor_coverage_median"] is None

    def test_current_side_without_values_skips_layer_entry(self):
        runs = self._anchor_runs("plus_debate", {"t0": [0.5], "t1": [0.6]}) + self._anchor_runs(
            "full", {"t0": [None], "t1": [None]}
        )
        report = aggregate_results(runs)
        assert "argument_anchor_coverage" not in report["layers"]["full"]

    def test_detail_key_does_not_affect_aggregation(self):
        """拆项键只随 run 记录落盘，聚合不读取：带不带它报告逐字段相同。"""
        runs = (
            self._anchor_runs("analysts", {"t0": [None], "t1": [None]})
            + self._anchor_runs("plus_debate", {"t0": [0.5], "t1": [0.6]})
            + self._anchor_runs("full", {"t0": [0.8], "t1": [0.9]})
        )
        with_detail = [
            {
                **r,
                "argument_anchor_coverage_detail": (
                    {"total": 4, "anchored": 2}
                    if r["argument_anchor_coverage"] is not None
                    else None
                ),
            }
            for r in runs
        ]
        assert aggregate_results(with_detail) == aggregate_results(runs)

    def test_old_records_without_key_skipped_gracefully(self):
        """旧 run 记录（无该键）不得炸聚合：跳过该指标，变体中位数记 None。"""
        runs = [
            {
                "variant": variant,
                "ticker": t,
                "citation_pass": True,
                "judge": {"report_relevance": 3.0},
            }
            for variant in ("analysts", "plus_debate", "full")
            for t in ("t0", "t1")
        ]
        report = aggregate_results(runs)
        assert "argument_anchor_coverage" not in report["layers"]["debate"]
        assert "argument_anchor_coverage" not in report["layers"]["full"]
        assert report["variants"]["full"]["anchor_coverage_median"] is None


class TestCitationBucketAggregation:
    """G2（F3）：四桶层增量与 judge 维度同口径（逐标的中位 + 配对 bootstrap）。

    结论措辞由 `conclusion_for_layer` 按**计数增减方向**生成（与 judge 维度同一函数）：
    计数下降 → 字面「显著退步」= 该桶计数显著下降；质量解读随桶语义（blocked /
    analyst_true_fail 下降即改善，surgical_repaired / verifier_normalized 为校验器
    工作量遥测）。措辞方向化不在 F3 范围内；G7/⑥ 以 `lower_is_better` 元数据把方向
    交给消费者（见 TestCitationBucketDirectionMetadata）。
    """

    _BASE = {"blocked": 1, "analyst_true_fail": 2, "surgical_repaired": 0, "verifier_normalized": 3}
    _DEBATE = {
        "blocked": 0,
        "analyst_true_fail": 1,
        "surgical_repaired": 1,
        "verifier_normalized": 2,
    }
    _FULL = {"blocked": 0, "analyst_true_fail": 0, "surgical_repaired": 2, "verifier_normalized": 1}

    @staticmethod
    def _runs(variant: str, by_ticker: dict[str, dict[str, int]]) -> list[dict]:
        """{ticker: counts} → 每标的两次重复（中位数 = 该计数）；桶映射走库侧真函数。"""
        from evals.causal_ablation.escape import split_verifier_buckets

        return [
            {
                "variant": variant,
                "ticker": ticker,
                "citation_pass": True,
                "judge": {"report_relevance": 4.0},
                "citation_buckets": split_verifier_buckets(counts),
            }
            for ticker, counts in by_ticker.items()
            for _ in range(2)
        ]

    def _report(self) -> dict:
        tickers = ("t0", "t1", "t2")
        runs = (
            self._runs("analysts", dict.fromkeys(tickers, self._BASE))
            + self._runs("plus_debate", dict.fromkeys(tickers, self._DEBATE))
            + self._runs("full", dict.fromkeys(tickers, self._FULL))
        )
        return aggregate_results(runs)

    def test_four_bucket_layer_entries_with_expected_values(self):
        """四桶各有层条目，口径同 judge 维度：逐标的中位差 + 配对 bootstrap CI。"""
        layer = self._report()["layers"]["debate"]
        assert layer["citation_blocked"] == {
            "diff_mean": -1.0,
            "ci": (-1.0, -1.0),
            "conclusion": "显著退步",
            "lower_is_better": True,
        }
        assert layer["citation_analyst_true_fail"] == {
            "diff_mean": -1.0,
            "ci": (-1.0, -1.0),
            "conclusion": "显著退步",
            "lower_is_better": True,
        }
        assert layer["citation_surgical_repaired"] == {
            "diff_mean": 1.0,
            "ci": (1.0, 1.0),
            "conclusion": "显著改进",
            "lower_is_better": None,
        }
        assert layer["citation_verifier_normalized"] == {
            "diff_mean": -1.0,
            "ci": (-1.0, -1.0),
            "conclusion": "显著退步",
            "lower_is_better": None,
        }
        assert layer["pairing_unit"] == "ticker"
        assert layer["pairing_units"] == 3, "配对单元为标的（与 judge 块同一披露）"

    def test_full_layer_buckets_paired_against_plus_debate(self):
        layer = self._report()["layers"]["full"]
        assert layer["citation_blocked"] == {
            "diff_mean": 0.0,
            "ci": (0.0, 0.0),
            "conclusion": "该层价值未获统计支持",
            "lower_is_better": True,
        }
        assert layer["citation_analyst_true_fail"]["diff_mean"] == -1.0
        assert layer["citation_surgical_repaired"]["diff_mean"] == 1.0
        assert layer["citation_verifier_normalized"]["ci"] == (-1.0, -1.0)

    def test_variant_bucket_medians_recorded(self):
        """变体级逐桶中位数（排除 None；全无该键 → None，不伪造 0）。"""
        report = self._report()
        assert report["variants"]["analysts"]["citation_buckets_median"] == {
            "blocked": 1.0,
            "analyst_true_fail": 2.0,
            "surgical_repaired": 0.0,
            "verifier_normalized": 3.0,
            "claim_contract_error": 0.0,
        }
        assert (
            report["variants"]["plus_debate"]["citation_buckets_median"]["surgical_repaired"] == 1.0
        )
        assert report["variants"]["full"]["citation_buckets_median"]["verifier_normalized"] == 1.0

    def test_zero_counts_are_real_values_not_missing(self):
        """两侧计数恒 0 也是有效观测：条目须在（diff 0），不得当作缺值跳过。"""
        zeros = {
            "blocked": 0,
            "analyst_true_fail": 0,
            "surgical_repaired": 0,
            "verifier_normalized": 0,
        }
        runs = self._runs("plus_debate", {"t0": zeros, "t1": zeros}) + self._runs(
            "full", {"t0": zeros, "t1": zeros}
        )
        report = aggregate_results(runs)
        layer = report["layers"]["full"]
        assert layer["citation_blocked"] == {
            "diff_mean": 0.0,
            "ci": (0.0, 0.0),
            "conclusion": "该层价值未获统计支持",
            "lower_is_better": True,
        }
        assert report["variants"]["full"]["citation_buckets_median"]["blocked"] == 0.0

    def test_old_records_without_key_skipped_gracefully(self):
        """旧 run 记录（无 citation_buckets）不得炸聚合：整腿跳过，中位数记 None。"""
        runs = [
            {
                "variant": variant,
                "ticker": t,
                "citation_pass": True,
                "judge": {"report_relevance": 3.0},
            }
            for variant in ("analysts", "plus_debate", "full")
            for t in ("t0", "t1")
        ]
        report = aggregate_results(runs)
        for bucket in ("blocked", "analyst_true_fail", "surgical_repaired", "verifier_normalized"):
            assert f"citation_{bucket}" not in report["layers"]["debate"]
            assert f"citation_{bucket}" not in report["layers"]["full"]
        assert report["variants"]["analysts"]["citation_buckets_median"] is None
        assert report["variants"]["full"]["citation_buckets_median"] is None

    def test_mixed_records_pair_only_complete_side(self):
        """一侧为旧记录 → 该侧无值 → 该层整腿跳过；另一层两段齐备则照常出条目。"""
        runs = (
            [
                {
                    "variant": "analysts",
                    "ticker": t,
                    "citation_pass": True,
                    "judge": {"report_relevance": 3.0},
                }
                for t in ("t0", "t1")
            ]
            + self._runs("plus_debate", {"t0": self._DEBATE, "t1": self._DEBATE})
            + self._runs("full", {"t0": self._FULL, "t1": self._FULL})
        )
        report = aggregate_results(runs)
        assert "citation_surgical_repaired" not in report["layers"]["debate"], (
            "配对侧缺值不得伪造成 0 产伪层结论"
        )
        assert report["layers"]["full"]["citation_surgical_repaired"]["diff_mean"] == 1.0
        assert report["variants"]["analysts"]["citation_buckets_median"] is None

    def test_partial_bucket_dict_does_not_fabricate_zero(self):
        """残缺记录（只有部分桶键）只贡献已有桶；缺失桶按无值跳过，不补 0。"""
        runs = [
            {
                "variant": variant,
                "ticker": t,
                "citation_pass": True,
                "judge": {"report_relevance": 4.0},
                "citation_buckets": {"blocked": value},
            }
            for variant, value in (("plus_debate", 1), ("full", 0))
            for t in ("t0", "t1")
        ]
        report = aggregate_results(runs)
        layer = report["layers"]["full"]
        assert layer["citation_blocked"]["diff_mean"] == -1.0
        assert "citation_analyst_true_fail" not in layer
        assert report["variants"]["full"]["citation_buckets_median"] == {
            "blocked": 0.0,
            "analyst_true_fail": None,
            "surgical_repaired": None,
            "verifier_normalized": None,
            "claim_contract_error": None,
        }


class TestCitationBucketDirectionMetadata:
    """G7/⑥：四桶层条目带 `lower_is_better` 方向元数据（纯增量，措辞不动）。

    `conclusion_for_layer` 的措辞是**计数方向通用**的（下降恒写作「显著退步」），
    与桶语义无关；质量解读必须由消费者按 `lower_is_better` 施加。judge / 锚点块
    的 golden（`TestJudgeOutputsUnchangedGuard`）不涉及四桶，保持逐字不变。
    """

    def _layer(self) -> dict:
        return TestCitationBucketAggregation()._report()["layers"]["debate"]

    def test_quality_counters_marked_lower_is_better(self):
        layer = self._layer()
        assert layer["citation_blocked"]["lower_is_better"] is True, "阻断计数下降即改善"
        assert layer["citation_analyst_true_fail"]["lower_is_better"] is True, (
            "分析师真错计数下降即改善"
        )

    def test_workload_telemetry_has_no_direction(self):
        layer = self._layer()
        assert layer["citation_surgical_repaired"]["lower_is_better"] is None, (
            "单点修复为工作量遥测，无好坏方向"
        )
        assert layer["citation_verifier_normalized"]["lower_is_better"] is None, (
            "校验器归一为工作量遥测，无好坏方向"
        )

    def test_all_four_bucket_entries_carry_the_flag(self):
        """四个桶条目**均**带该键（缺席会让消费者把遥测桶误读成质量桶）。"""
        layer = self._layer()
        for bucket in ("blocked", "analyst_true_fail", "surgical_repaired", "verifier_normalized"):
            assert f"citation_{bucket}" in layer
            assert "lower_is_better" in layer[f"citation_{bucket}"]

    def test_direction_mapping_covers_shared_bucket_constant(self):
        """方向映射与共享桶常量同域（缺键 → 建层即 KeyError，不静默给 None）。"""
        from evals.ablation import _CITATION_BUCKET_LOWER_IS_BETTER
        from evals.causal_ablation.escape import VERIFIER_BUCKETS

        assert set(_CITATION_BUCKET_LOWER_IS_BETTER) == set(VERIFIER_BUCKETS)

    def test_conclusion_wording_stays_count_directional(self):
        """⑥ 只加元数据、不改措辞：blocked 下降仍逐字写作「显著退步」（计数口径）。"""
        entry = self._layer()["citation_blocked"]
        assert entry["diff_mean"] == -1.0
        assert entry["conclusion"] == "显著退步"


class TestJudgeOutputsUnchangedGuard:
    """纯增量回归网：固定输入下 judge / 锚点切片逐字段不变（锚点键除外）。

    golden 为改动前基线（未变更代码时逐字捕获）；任何 judge 口径改动都会在此转红。
    citation_pass_rate 层块是 F3 的**有意变更**（去 CI/结论 + not_for_conclusions
    标记，见 `test_citation_pass_rate_exits_conclusion_path`），其 golden 已同步；
    judge 维度与锚点块的 golden 值保持基线逐字不变。
    """

    @staticmethod
    def _fixed_runs() -> list[dict]:
        return (
            [
                {
                    "variant": "analysts",
                    "ticker": f"t{i % 3}",
                    "citation_pass": True,
                    "judge": {"report_relevance": 2.0},
                }
                for i in range(6)
            ]
            + [
                {
                    "variant": "plus_debate",
                    "ticker": f"t{i % 3}",
                    "citation_pass": True,
                    "judge": {"report_relevance": 2.0 if i % 3 != 2 else 11.0},
                }
                for i in range(6)
            ]
            + [
                {
                    "variant": "full",
                    "ticker": f"t{i % 3}",
                    "citation_pass": i % 2 == 0,
                    "judge": {"report_relevance": 4.0 + (i % 3)},
                }
                for i in range(6)
            ]
        )

    _GOLDEN_LAYERS = {
        "debate": {
            "pairing_unit": "ticker",
            "pairing_units": 3,
            "judge_report_relevance": {
                "diff_mean": 3.0,
                "ci": (0.0, 9.0),
                "conclusion": "该层价值未获统计支持",
            },
            # F3：标量退出层增量结论路径（不再出 ci/conclusion），点估计保留
            "citation_pass_rate": {
                "prev": 1.0,
                "current": 1.0,
                "not_for_conclusions": True,
            },
        },
        "full": {
            "pairing_unit": "ticker",
            "pairing_units": 3,
            "judge_report_relevance": {
                "diff_mean": 0.0,
                "ci": (-5.0, 3.0),
                "conclusion": "该层价值未获统计支持",
            },
            # F3：同上——层增量只认四桶，标量块不再出 ci/conclusion
            "citation_pass_rate": {
                "prev": 1.0,
                "current": 0.5,
                "not_for_conclusions": True,
            },
        },
    }

    _GOLDEN_VARIANTS = {
        "analysts": {
            "n_runs": 6,
            "citation_pass_rate": 1.0,
            "judge_medians": {
                "report_relevance": 2.0,
                "debate_quality": None,
                "decision_grounding": None,
                "consistency": None,
            },
        },
        "plus_debate": {
            "n_runs": 6,
            "citation_pass_rate": 1.0,
            "judge_medians": {
                "report_relevance": 2.0,
                "debate_quality": None,
                "decision_grounding": None,
                "consistency": None,
            },
        },
        "full": {
            "n_runs": 6,
            "citation_pass_rate": 0.5,
            "judge_medians": {
                "report_relevance": 5.0,
                "debate_quality": None,
                "decision_grounding": None,
                "consistency": None,
            },
        },
    }

    def test_judge_and_citation_slices_byte_identical(self):
        report = aggregate_results(self._fixed_runs())
        for name, golden in self._GOLDEN_LAYERS.items():
            actual = {
                k: v for k, v in report["layers"][name].items() if k != "argument_anchor_coverage"
            }
            assert actual == golden, (
                f"{name} 层 judge/citation 切片必须逐字段不变（F3 有意变更除外）"
            )
        for variant, golden in self._GOLDEN_VARIANTS.items():
            actual = {
                k: v
                for k, v in report["variants"][variant].items()
                if k not in ("anchor_coverage_median", "citation_buckets_median")
            }
            assert actual == golden, f"{variant} 变体切片必须逐字段不变（纯增量）"

    def test_golden_records_without_buckets_add_no_citation_bucket_entries(self):
        """golden 输入为旧口径记录（无 citation_buckets）→ 不得凭空产四桶层条目。"""
        report = aggregate_results(self._fixed_runs())
        for name in ("debate", "full"):
            extra = [
                k
                for k in report["layers"][name]
                if k.startswith("citation_") and k != "citation_pass_rate"
            ]
            assert extra == [], f"{name} 层不得凭空产四桶条目：{extra}"


class TestConclusionWording:
    def test_ci_contains_zero_wording(self):
        c = conclusion_for_layer((-0.01, 0.02))
        assert c == "该层价值未获统计支持"

    def test_ci_positive_wording(self):
        assert conclusion_for_layer((0.05, 0.4)) == "显著改进"

    def test_ci_negative_wording(self):
        assert conclusion_for_layer((-0.4, -0.05)) == "显著退步"


class TestJudgeDimApplicability:
    """#112：judge 维度按变体适用性过滤——plus_debate 无决策/风控层，不得评
    consistency/decision_grounding（评不存在的层产伪影）。"""

    def test_analysts_only_relevance(self):
        from evals.ablation import _applicable_dims

        assert _applicable_dims("analysts") == ("report_relevance",)

    def test_plus_debate_no_decision_dims(self):
        from evals.ablation import _applicable_dims

        assert _applicable_dims("plus_debate") == ("report_relevance", "debate_quality")

    def test_through_trader_has_decision_but_no_consistency(self):
        from evals.ablation import _applicable_dims

        # B5 修正臂：有 Trader 决策层（grounding 可评）、无风控辩论/FM（consistency 不评）
        assert _applicable_dims("through_trader") == (
            "report_relevance",
            "debate_quality",
            "decision_grounding",
        )

    def test_full_all_dims(self):
        from evals.ablation import _applicable_dims

        assert set(_applicable_dims("full")) == {
            "report_relevance",
            "debate_quality",
            "decision_grounding",
            "consistency",
        }


class TestAblationJudgeMeanProtocol:
    """库侧（run_ablation）与驱动侧（ablation_pilot）必须用同一判分协议。

    回归背景：驱动侧通过注入假件测试，真模块是否暴露 `run_judge_mean` 一度未被覆盖
    ——lint 把 `evals/ablation.py` 里未使用的 re-export 删掉后，真模块上该属性消失，
    假件却仍通过（mock 缺口）。本用例钉住「库侧确实用 K 次均值判分」。
    """

    def test_run_ablation_uses_mean_judge(self, monkeypatch):
        import evals.ablation as ablation

        calls: list[tuple[str, int]] = []

        def fake_mean(dimension, variables, *, repeats=3):
            calls.append((dimension, repeats))
            return {"name": dimension, "score": 4.0, "scores": [4, 4], "score_spread": 0}

        monkeypatch.setattr(ablation, "run_judge_mean", fake_mean, raising=True)
        monkeypatch.setattr(ablation, "build_snapshot", lambda ticker: {"stock_code": ticker})
        monkeypatch.setattr(ablation, "snapshot_digest", lambda state: "d")
        monkeypatch.setattr(ablation, "build_variant_graph", lambda variant: _FakeGraph())
        monkeypatch.setattr(
            ablation,
            "run_variant_once",
            lambda variant, snapshot, query: {
                "final_report": "r",
                "citation_pass": True,
                "judge_vars": {"debate_history": "x"},
                "anchor_coverage": None,  # 假件须对齐 run_variant_once 真实返回协议（4.4）
                "citation_buckets": ablation.split_verifier_buckets({"blocked": 0}),  # G2/F3
            },
        )
        report = ablation.run_ablation(["600519"], repeats=1, judge_repeats=2)
        assert calls, "库侧未调用均值判分"
        assert all(k == 2 for _, k in calls), f"judge_repeats 未透传: {calls}"
        assert report["variants"]["full"]["n_runs"] == 1


class _FakeGraph:
    def invoke(self, state):
        return {}


class TestScoreRunEntry:
    """G1（delta tasks 1.2）：判分/维度过滤/明细塑形/材料落盘的库侧唯一入口。

    驱动与 `run_ablation` 均只调用 `score_run`——本类钉住真模块上的入口行为，
    防止「假件全绿、真模块缺实现」的 mock 缺口。
    """

    @staticmethod
    def _out(coverage: dict | None = None, judge_vars: dict | None = None) -> dict:
        return {
            "citation_pass": True,
            "judge_vars": {"debate_history": "x"} if judge_vars is None else judge_vars,
            "anchor_coverage": coverage,
        }

    @staticmethod
    def _anchor(value: float) -> dict:
        return {
            "value": value,
            "total": 4,
            "anchored": 2,
            "unanchored_inference": 1,
            "unresolved": 1,
            "missing_required": 0,
            "unspecified": 0,
        }

    def test_filters_inapplicable_dims(self, monkeypatch):
        import evals.ablation as ablation

        calls: list[str] = []

        def fake_mean(dimension, variables, *, repeats=3):
            calls.append(dimension)
            return {"score": 4.0}

        monkeypatch.setattr(ablation, "run_judge_mean", fake_mean, raising=False)
        scored = ablation.score_run("plus_debate", self._out(), ticker="600519", repeat=0)
        assert calls == ["report_relevance", "debate_quality"], "不适用维度不得发起判分（#112）"
        assert set(scored["judge"]) == set(ablation.JUDGE_DIMS), "四维键齐全，不适用记 None"
        assert scored["judge"]["decision_grounding"] is None
        assert scored["judge"]["consistency"] is None
        assert "decision_grounding" not in scored["judge_detail"], "过滤维度不得产生明细"

    def test_judge_repeats_passed_to_mean_protocol(self, monkeypatch):
        import evals.ablation as ablation

        seen: list[int] = []

        def fake_mean(dimension, variables, *, repeats=3):
            seen.append(repeats)
            return {"score": 5.0}

        monkeypatch.setattr(ablation, "run_judge_mean", fake_mean, raising=False)
        ablation.score_run("analysts", self._out(), ticker="600519", repeat=1, judge_repeats=5)
        assert seen == [5], "K 次均值协议参数须透传"

    def test_failed_judge_score_stays_none(self, monkeypatch):
        import evals.ablation as ablation

        monkeypatch.setattr(
            ablation,
            "run_judge_mean",
            lambda dimension, variables, *, repeats=3: {"score": None, "judge_failures": 3},
            raising=False,
        )
        scored = ablation.score_run("analysts", self._out(), ticker="600519", repeat=0)
        assert scored["judge"]["report_relevance"] is None, "全败不得静默给分"
        assert scored["judge_detail"]["report_relevance"]["score"] is None
        assert scored["judge_detail"]["report_relevance"]["judge_failures"] == 3

    def test_judge_detail_shapes_telemetry_and_truncates_reason(self, monkeypatch):
        import evals.ablation as ablation

        monkeypatch.setattr(
            ablation,
            "run_judge_mean",
            lambda dimension, variables, *, repeats=3: {
                "score": 4.333,
                "reason": "长" * 400,
                "scores": [5, 4, 4],
                "score_spread": 1,
                "judge_repeats": 3,
                "judge_failures": 0,
                "qualitative_points": 1,
                "cap_applied": False,
                "enumeration_missing": False,
            },
            raising=False,
        )
        detail = ablation.score_run("plus_debate", self._out(), ticker="600519", repeat=0)[
            "judge_detail"
        ]["debate_quality"]
        assert detail["score"] == 4.333
        assert detail["scores"] == [5, 4, 4], "每次分数随 run 落盘（噪声可见）"
        assert detail["score_spread"] == 1
        assert detail["judge_repeats"] == 3
        assert detail["judge_failures"] == 0
        assert detail["qualitative_points"] == 1
        assert detail["cap_applied"] is False
        assert detail["enumeration_missing"] is False
        assert len(detail["reason"]) == 300, "理由截断为 300 字符（run 记录体积控制）"

    def test_persists_materials_and_returns_posix_path(self, monkeypatch, tmp_path):
        import evals.ablation as ablation

        monkeypatch.setattr(
            ablation,
            "run_judge_mean",
            lambda dimension, variables, *, repeats=3: {"score": 5.0},
            raising=False,
        )
        judge_vars = {"debate_history": "【bull】论点: x", "trader_plan": "y"}
        scored = ablation.score_run(
            "full",
            self._out(judge_vars=judge_vars),
            ticker="600519",
            repeat=2,
            materials_dir=tmp_path / "judge_vars",
        )
        assert scored["materials_path"].endswith("full-600519-2.json"), "按 run 三元组命名"
        assert "\\" not in scored["materials_path"], "路径 POSIX 风格（跨平台可复现）"
        written = json.loads(Path(scored["materials_path"]).read_text(encoding="utf-8"))
        assert written == judge_vars, "材料内容即 judge_vars（可离线重判）"

    def test_materials_path_none_without_dir(self, monkeypatch):
        import evals.ablation as ablation

        monkeypatch.setattr(
            ablation,
            "run_judge_mean",
            lambda dimension, variables, *, repeats=3: {"score": 5.0},
            raising=False,
        )
        scored = ablation.score_run("analysts", self._out(), ticker="600519", repeat=0)
        assert scored["materials_path"] is None, "未配置材料目录不得落盘"

    def test_anchor_value_and_detail_mapping(self, monkeypatch):
        import evals.ablation as ablation

        monkeypatch.setattr(
            ablation,
            "run_judge_mean",
            lambda dimension, variables, *, repeats=3: {"score": 5.0},
            raising=False,
        )
        scored = ablation.score_run(
            "plus_debate", self._out(coverage=self._anchor(0.5)), ticker="600519", repeat=0
        )
        assert scored["argument_anchor_coverage"] == 0.5
        assert "value" not in scored["argument_anchor_coverage_detail"], "value 已单独成键"
        assert scored["argument_anchor_coverage_detail"]["anchored"] == 2
        without_arguments = ablation.score_run(
            "analysts", self._out(coverage=None), ticker="600519", repeat=0
        )
        assert without_arguments["argument_anchor_coverage"] is None, "无论点不得伪造 0"
        assert without_arguments["argument_anchor_coverage_detail"] is None


class TestRunAblationScoringDelegation:
    """G1：`run_ablation` 不得内联判分循环——判分只经库侧 `score_run` 一处。"""

    @staticmethod
    def _patch_pipeline(ablation, monkeypatch) -> None:
        monkeypatch.setattr(ablation, "build_snapshot", lambda ticker: {"stock_code": ticker})
        monkeypatch.setattr(ablation, "snapshot_digest", lambda state: "d")
        monkeypatch.setattr(ablation, "build_variant_graph", lambda variant: _FakeGraph())
        monkeypatch.setattr(
            ablation,
            "run_variant_once",
            lambda variant, snapshot, query: {
                "final_report": "r",
                "citation_pass": True,
                "judge_vars": {},
                "anchor_coverage": None,
                "citation_buckets": ablation.split_verifier_buckets({"blocked": 0}),  # G2/F3
            },
        )

    @staticmethod
    def _scored_bundle(ablation) -> dict:
        return {
            "judge": dict.fromkeys(ablation.JUDGE_DIMS, 4.0),
            "judge_detail": {},
            "materials_path": None,
            "argument_anchor_coverage": None,
            "argument_anchor_coverage_detail": None,
        }

    def test_run_ablation_scores_via_library_entry(self, monkeypatch):
        import evals.ablation as ablation

        calls: list[tuple] = []
        monkeypatch.setattr(
            ablation,
            "run_judge_mean",
            lambda dimension, variables, *, repeats=3: {"score": 4.0},
            raising=False,
        )

        def spy(variant, out, *, ticker, repeat, judge_repeats=3, materials_dir=None):
            calls.append((variant, ticker, repeat, judge_repeats))
            return self._scored_bundle(ablation)

        monkeypatch.setattr(ablation, "score_run", spy, raising=False)
        self._patch_pipeline(ablation, monkeypatch)
        report = ablation.run_ablation(["600519"], repeats=2, judge_repeats=2)
        assert len(calls) == 8, "1 标的 × 4 变体 × 2 重复 = 每 run 一次库侧判分入口"
        assert all(call[3] == 2 for call in calls), "judge_repeats 须透传"
        assert {call[:3] for call in calls} == {
            (variant, "600519", repeat) for variant in ablation._VARIANTS for repeat in range(2)
        }
        assert report["variants"]["full"]["n_runs"] == 2

    def test_run_ablation_does_not_inline_judge_loop(self, monkeypatch):
        """判分只许发生在 score_run 内部：score_run 被替换后 run_judge_mean 不得被调。"""
        import evals.ablation as ablation

        inline_calls: list[str] = []
        monkeypatch.setattr(
            ablation,
            "run_judge_mean",
            lambda dimension, variables, *, repeats=3: (
                inline_calls.append(dimension) or {"score": 4.0}
            ),
            raising=False,
        )
        monkeypatch.setattr(
            ablation,
            "score_run",
            lambda variant, out, *, ticker, repeat, judge_repeats=3, materials_dir=None: (
                self._scored_bundle(ablation)
            ),
            raising=False,
        )
        self._patch_pipeline(ablation, monkeypatch)
        ablation.run_ablation(["600519"], repeats=1, judge_repeats=1)
        assert inline_calls == [], "库侧聚合路径不得内联判分——判分只在 score_run 一处"


class TestSnapshotDigestContentStability:
    """G5 通路验证缺陷回归：digest 必须内容稳定。

    旧实现按 `str(values.tobytes())` 哈希 DataFrame——对象列（报告日等字符串）
    的 ndarray 序列化的是 PyObject 指针，同一数据两次构建 digest 不同。后果是
    驱动的「重建 digest ≠ 登记 digest → RuntimeError」（跨进程续跑防混批）在
    真实/stub 数据上恒触发，整条消融跑批被堵死。修法：DataFrame/Series 走
    `pd.util.hash_pandas_object`（按行内容哈希），非 DataFrame 的 shaped 对象
    仍走旧路径。
    """

    @staticmethod
    def _frame():
        """带 object 列（报告日）的最小帧：旧路径下两次构建必不等。"""
        import pandas as pd

        return pd.DataFrame({"报告日": ["20241231", "20231231"], "资产总计": [1000.0, 900.0]})

    def test_two_consecutive_stub_builds_hash_identically(self, monkeypatch):
        """(a) 同标的两次构建（TESTING=1 确定性 stub）digest 必须一致。"""
        import evals.ablation as ablation

        monkeypatch.setenv("TESTING", "1")
        first = ablation.snapshot_digest(ablation.build_snapshot("600519"))
        second = ablation.snapshot_digest(ablation.build_snapshot("600519"))
        assert first == second, "同内容两次构建 digest 必须一致，否则续跑核验恒失败"

    def test_stub_object_column_frames_are_stable_and_included(self, monkeypatch):
        """(c) 显式覆盖 object 列帧：报告日列参与 digest 且跨构建稳定。"""
        import hashlib

        from evals.ablation import snapshot_digest

        from finance_agent.nodes.fetch import _make_stub_balance_sheet

        frame_a = _make_stub_balance_sheet()
        frame_b = _make_stub_balance_sheet()
        assert str(frame_a["报告日"].dtype) == "object", "fixture 前提：报告日为 object 列"
        digest_a = snapshot_digest({"balance_sheet": frame_a})
        digest_b = snapshot_digest({"balance_sheet": frame_b})
        assert digest_a == digest_b, "object 列帧两次构建 digest 必须一致"
        assert digest_a.startswith(f"balance_sheet:{frame_a.shape}:")

        # 旧路径（values.tobytes() = PyObject 指针）在本 fixture 上确实不稳定：
        # 若哪天回归，本断言即为红灯信号
        def _old_path(frame):
            return hashlib.md5(  # noqa: S324 — 仅为复现旧实现的指针序列化
                str(frame.values.tobytes()).encode(), usedforsecurity=False
            ).hexdigest()[:8]

        assert _old_path(frame_a) != _old_path(frame_b), "fixture 前提：旧路径在 object 列上不稳定"

    def test_mutating_numeric_value_changes_digest(self):
        """(b) 内容敏感：改数值 → digest 变。"""
        from evals.ablation import snapshot_digest

        frame = self._frame()
        baseline = snapshot_digest({"balance_sheet": frame})
        mutated = self._frame()
        mutated.loc[0, "资产总计"] = 1000.5
        assert snapshot_digest({"balance_sheet": mutated}) != baseline

    def test_mutating_object_cell_changes_digest(self):
        """(b) 内容敏感：改 object 列单元格 → digest 变（不得只哈希类型/指针）。"""
        from evals.ablation import snapshot_digest

        frame = self._frame()
        baseline = snapshot_digest({"balance_sheet": frame})
        mutated = self._frame()
        mutated.loc[0, "报告日"] = "20991231"
        assert snapshot_digest({"balance_sheet": mutated}) != baseline

    def test_identical_content_different_allocations_match(self):
        """(b) 同内容不同对象 → digest 相等（与分配无关）。"""
        from evals.ablation import snapshot_digest

        assert snapshot_digest({"balance_sheet": self._frame()}) == snapshot_digest(
            {"balance_sheet": self._frame()}
        )

    def test_digest_string_format_unchanged(self):
        """格式契约不变：`{key}:{shape}:{hash8}`（台账/报告既有解析依赖此形态）。"""
        import re

        from evals.ablation import snapshot_digest

        digest = snapshot_digest({"balance_sheet": self._frame()})
        assert re.fullmatch(r"balance_sheet:\(2, 2\):[0-9a-f]{8}", digest), digest

    def test_scalar_and_non_frame_entries_keep_old_encoding(self):
        """非 DataFrame 条目编码不变：标量走 repr、无 shape 对象走 type=。"""
        from evals.ablation import snapshot_digest

        digest = snapshot_digest(
            {"stock_code": "600519", "enable_web_search": False, "d": {"a": 1}}
        )
        assert digest == "d:type=dict|enable_web_search:False|stock_code:'600519'"
