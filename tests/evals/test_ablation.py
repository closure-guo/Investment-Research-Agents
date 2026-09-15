"""消融编排测试：变体输入对齐（同一快照）、聚合与结论措辞。全部 mock 节点，不调 LLM。"""

from evals.ablation import aggregate_results, build_variant_graph, conclusion_for_layer


class TestVariantGraph:
    def test_three_variants_buildable(self):
        for variant in ("analysts", "plus_debate", "full"):
            graph = build_variant_graph(variant)
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

    def test_citation_pass_rate_increment_with_ci(self):
        """citation_pass 率层增量须带配对 bootstrap CI（spec：消融以带 CI 的 pass 率衡量）。"""
        runs = (
            self._runs("analysts", [False] * 6, [2.0] * 6)
            + self._runs("plus_debate", [True] * 6, [4.0] * 6)
            + self._runs("full", [True] * 6, [4.5] * 6)
        )
        report = aggregate_results(runs)
        rate = report["layers"]["debate"]["citation_pass_rate"]
        assert rate["prev"] == 0.0
        assert rate["current"] == 1.0
        assert rate["ci"][0] > 0
        assert rate["conclusion"] == "显著改进"

    def test_citation_pass_rate_without_support(self):
        """逐 ticker pass 率交错（t0 降、t1 升）→ CI 含 0 → 未获统计支持。"""
        runs = self._runs("analysts", [True, False], [3.0, 3.0]) + self._runs(
            "plus_debate", [False, True], [3.0, 3.0]
        )
        report = aggregate_results(runs)
        rate = report["layers"]["debate"]["citation_pass_rate"]
        assert rate["ci"][0] <= 0 <= rate["ci"][1]
        assert rate["conclusion"] == "该层价值未获统计支持"


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
            },
        )
        report = ablation.run_ablation(["600519"], repeats=1, judge_repeats=2)
        assert calls, "库侧未调用均值判分"
        assert all(k == 2 for _, k in calls), f"judge_repeats 未透传: {calls}"
        assert report["variants"]["full"]["n_runs"] == 1


class _FakeGraph:
    def invoke(self, state):
        return {}
