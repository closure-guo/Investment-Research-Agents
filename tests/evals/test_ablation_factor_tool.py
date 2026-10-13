"""因子工具消融变体 + 报告口径守卫单测（add-analyst-factor-tools Task 3）。

覆盖：
- 变体构建 `tech_factor_tool`（技术分析师带因子库工具）/ `tech_bare`（裸对照臂）
  可构建，且两臂唯一差异 = 技术分析师节点的工具绑定（单变量）；
- judge 维度按变体适用性过滤（无辩论/决策层 → 只评 report_relevance）；
- 报告口径（spec 场景）：CI 含 0 → 结论逐字「无显著差异」，报告文本不含
  「无效」类引申表述（模板渲染断言 + 结构守卫，均不跑真 LLM）。
"""

from __future__ import annotations

import pytest
from evals.ablation import (
    build_variant_graph,
    conclusion_for_factor_tool_increment,
    render_factor_tool_ablation_report,
)

from evals import ablation


class TestTechVariantGraphs:
    def test_tech_variants_buildable(self):
        for variant in ("tech_factor_tool", "tech_bare"):
            assert build_variant_graph(variant) is not None

    def test_unknown_variant_still_raises(self):
        with pytest.raises(ValueError):
            build_variant_graph("nope")

    def test_variant_binding_is_the_only_difference(self):
        """两臂唯一差异 = 技术分析师工具绑定（消融单变量纪律）。"""
        tool_arm = ablation._variant_technical_analyst("tech_factor_tool")
        bare_arm = ablation._variant_technical_analyst("tech_bare")
        assert tool_arm.factor_tool_enabled is True
        assert bare_arm.factor_tool_enabled is False
        # 分层臂（analysts/plus_debate/...）继续用生产入口（带工具，声明同源）
        assert ablation._variant_technical_analyst("analysts").factor_tool_enabled is True

    def test_toolset_declared_only_for_technical(self):
        """工具集差异来自 agent_factory 权威声明（技术分析师唯一含该工具）。"""
        from finance_agent.agent_factory import TOOL_QUERY_FACTOR_LIBRARY, analyst_tool_names

        assert TOOL_QUERY_FACTOR_LIBRARY in analyst_tool_names("technical")
        assert ablation._TECH_VARIANT_FACTOR_TOOL == {
            "tech_factor_tool": True,
            "tech_bare": False,
        }

    def test_applicable_dims_analysts_layer_only(self):
        """无辩论/决策层 → 只评 report_relevance（#112 适用性过滤）。"""
        assert ablation._applicable_dims("tech_bare") == ("report_relevance",)
        assert ablation._applicable_dims("tech_factor_tool") == ("report_relevance",)

    def test_existing_layer_variants_untouched(self):
        assert ablation._VARIANTS == ("analysts", "plus_debate", "through_trader", "full")


class TestCiZeroWording:
    """spec 场景：CI 含 0 → 只写「无显著差异」，禁止「无效」类引申。"""

    def test_ci_containing_zero_is_no_significant_difference(self):
        assert conclusion_for_factor_tool_increment((-0.1, 0.3)) == "无显著差异"
        assert conclusion_for_factor_tool_increment((0.0, 0.0)) == "无显著差异"

    def test_positive_and_negative_directions(self):
        assert conclusion_for_factor_tool_increment((0.1, 0.3)) == "显著改进"
        assert conclusion_for_factor_tool_increment((-0.3, -0.1)) == "显著退步"

    def test_render_report_uses_verbatim_wording(self):
        report = {
            "pairing_unit": "ticker",
            "pairing_units": 3,
            "n_runs": 6,
            "dims": {
                "report_relevance": {
                    "diff_mean": 0.1,
                    "ci": (-0.1, 0.3),
                    "conclusion": "无显著差异",
                }
            },
        }
        text = render_factor_tool_ablation_report(report)
        assert "无显著差异" in text
        assert "[-0.1, 0.3]" in text  # CI 随文案披露（读数可复核）

    def test_render_report_forbids_invalidity_wording(self):
        """结构守卫：报告不得出现「无效」类引申（含结论字段被灌入坏措辞时拒绝渲染）。"""
        good = {
            "pairing_unit": "ticker",
            "pairing_units": 3,
            "n_runs": 6,
            "dims": {
                "report_relevance": {
                    "diff_mean": 0.1,
                    "ci": (-0.1, 0.3),
                    "conclusion": "无显著差异",
                }
            },
        }
        text = render_factor_tool_ablation_report(good)
        for forbidden in ("无效", "无价值", "无用"):
            assert forbidden not in text

        # 坏结论（如手改/上游措辞漂移）→ 渲染失败（fail loud，不让坏措辞进报告）
        bad = {
            "pairing_unit": "ticker",
            "pairing_units": 3,
            "n_runs": 6,
            "dims": {
                "report_relevance": {
                    "diff_mean": 0.1,
                    "ci": (-0.1, 0.3),
                    "conclusion": "因子工具无效",
                }
            },
        }
        with pytest.raises(ValueError):
            render_factor_tool_ablation_report(bad)


class TestAggregation:
    @staticmethod
    def _runs(variant: str, by_ticker: dict[str, list[float]]) -> list[dict]:
        return [
            {
                "variant": variant,
                "ticker": ticker,
                "judge": {"report_relevance": value},
            }
            for ticker, values in by_ticker.items()
            for value in values
        ]

    def test_zero_straddling_increment_reports_no_significant_difference(self):
        runs = self._runs(
            "tech_bare", {"t0": [3.0, 3.0], "t1": [3.0, 3.0], "t2": [3.0, 3.0]}
        ) + self._runs("tech_factor_tool", {"t0": [3.1, 3.1], "t1": [3.0, 3.0], "t2": [2.9, 2.9]})
        report = ablation.aggregate_factor_tool_ablation(runs)
        assert report["pairing_unit"] == "ticker"
        assert report["pairing_units"] == 3
        entry = report["dims"]["report_relevance"]
        assert entry["conclusion"] == "无显著差异"
        assert entry["ci"][0] <= 0 <= entry["ci"][1]
        assert "无显著差异" in render_factor_tool_ablation_report(report)

    def test_clear_increment_reports_significant_improvement(self):
        runs = self._runs("tech_bare", {"t0": [2.0], "t1": [2.0], "t2": [2.0]}) + self._runs(
            "tech_factor_tool", {"t0": [4.5], "t1": [4.5], "t2": [4.5]}
        )
        report = ablation.aggregate_factor_tool_ablation(runs, B=2000)
        entry = report["dims"]["report_relevance"]
        assert entry["conclusion"] == "显著改进"
        assert entry["ci"][0] > 0

    def test_no_paired_tickers_yields_empty_dims(self):
        report = ablation.aggregate_factor_tool_ablation(self._runs("tech_bare", {"t0": [3.0]}))
        assert report["dims"] == {}
        assert render_factor_tool_ablation_report(report)  # 空报告仍可渲染（如实披露）


class TestFactorToolRunner:
    """跑批入口复用库侧唯一判分入口（score_run）与既有快照机制。"""

    def test_runner_uses_score_entry_and_shared_snapshot(self, monkeypatch):
        calls: list[tuple[str, str]] = []

        def fake_variant_once(variant, snapshot, query):
            calls.append(("variant", variant))
            return {
                "final_report": "r",
                "citation_pass": True,
                "judge_vars": {"analysts": "x"},
                "anchor_coverage": None,
                "citation_buckets": {"blocked": 0, "analyst_true_fail": 0},
            }

        def fake_score(variant, out, *, ticker, repeat, judge_repeats=3, materials_dir=None):
            calls.append(("score", variant))
            return {
                "judge": {"report_relevance": 3.0},
                "judge_detail": {},
                "materials_path": None,
                "argument_anchor_coverage": None,
                "argument_anchor_coverage_detail": None,
            }

        monkeypatch.setattr(ablation, "build_snapshot", lambda ticker: {"stock_code": ticker})
        monkeypatch.setattr(ablation, "snapshot_digest", lambda state: "digest")
        monkeypatch.setattr(ablation, "run_variant_once", fake_variant_once)
        monkeypatch.setattr(ablation, "score_run", fake_score)
        report = ablation.run_factor_tool_ablation(["600519"], repeats=1, judge_repeats=1)
        assert [variant for kind, variant in calls if kind == "score"] == [
            "tech_bare",
            "tech_factor_tool",
        ]
        assert report["snapshot_digests"] == {"600519": "digest"}
        assert report["variants"]["tech_bare"]["n_runs"] == 1
        assert report["pairing_units"] == 1
