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
        assert out["anchor_coverage"]["value"] == 0.5
        assert out["anchor_coverage"]["unanchored_inference"] == 1
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

    def test_run_ablation_run_records_carry_anchor_value(self, monkeypatch):
        """run 记录携带 argument_anchor_coverage = 指标 value（analysts → None）。"""
        import evals.ablation as ablation

        captured: dict[str, list[dict]] = {}

        def fake_aggregate(runs, **kwargs):
            captured["runs"] = runs
            return {}

        def fake_run_once(variant, snapshot, query):
            coverage = None if variant == "analysts" else {"value": 0.25, "total": 4, "anchored": 1}
            return {
                "final_report": "r",
                "citation_pass": True,
                "judge_vars": {},
                "anchor_coverage": coverage,
            }

        monkeypatch.setattr(ablation, "aggregate_results", fake_aggregate)
        monkeypatch.setattr(ablation, "run_judge_mean", lambda dim, v, *, repeats=3: {"score": 4.0})
        monkeypatch.setattr(ablation, "build_snapshot", lambda ticker: {"stock_code": ticker})
        monkeypatch.setattr(ablation, "snapshot_digest", lambda state: "d")
        monkeypatch.setattr(ablation, "build_variant_graph", lambda variant: _FakeGraph())
        monkeypatch.setattr(ablation, "run_variant_once", fake_run_once)
        ablation.run_ablation(["600519"], repeats=2, judge_repeats=1)
        records = {r["variant"]: r for r in captured["runs"] if r["ticker"] == "600519"}
        assert len(captured["runs"]) == 6
        assert records["analysts"]["argument_anchor_coverage"] is None, "无论点不得伪造 0"
        assert records["plus_debate"]["argument_anchor_coverage"] == 0.25
        assert records["full"]["argument_anchor_coverage"] == 0.25


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


class TestJudgeOutputsUnchangedGuard:
    """4.4 纯增量回归网：固定输入下 judge / citation 各切片逐字段不变（锚点键除外）。

    golden 为改动前基线（未变更代码时逐字捕获）；任何 judge 口径改动都会在此转红。
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
            "citation_pass_rate": {
                "prev": 1.0,
                "current": 1.0,
                "ci": (0.0, 0.0),
                "conclusion": "该层价值未获统计支持",
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
            "citation_pass_rate": {
                "prev": 1.0,
                "current": 0.5,
                "ci": (-0.5, -0.5),
                "conclusion": "显著退步",
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
            assert actual == golden, f"{name} 层 judge/citation 切片必须逐字段不变（纯增量）"
        for variant, golden in self._GOLDEN_VARIANTS.items():
            actual = {
                k: v
                for k, v in report["variants"][variant].items()
                if k != "anchor_coverage_median"
            }
            assert actual == golden, f"{variant} 变体切片必须逐字段不变（纯增量）"


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
                "anchor_coverage": None,  # 假件须对齐 run_variant_once 真实返回协议（4.4）
            },
        )
        report = ablation.run_ablation(["600519"], repeats=1, judge_repeats=2)
        assert calls, "库侧未调用均值判分"
        assert all(k == 2 for _, k in calls), f"judge_repeats 未透传: {calls}"
        assert report["variants"]["full"]["n_runs"] == 1


class _FakeGraph:
    def invoke(self, state):
        return {}
