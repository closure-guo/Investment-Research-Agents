"""记忆消融变体单测（add-decision-memory Task 3；spec causal-ablation）。

覆盖：
- `tech_memory`（分析师层 + 决策记忆注入）/ `tech_no_memory`（无记忆对照臂）可构建，
  两臂唯一差异 = analysts_entry 是否检索记忆（单变量；共享同一 state 快照）；
- judge 维度按变体适用性过滤（分析师层直出 → 只评 report_relevance）；
- 聚合与报告口径：CI 含 0 → 逐字「无显著差异」，报告披露有效 n 与适用口径，
  「无效」类引申被结构守卫拒绝（复用既有措辞守卫）。
"""

from __future__ import annotations

import functools
import inspect
from pathlib import Path

import pytest
from evals.ablation import (
    build_variant_graph,
    conclusion_for_memory_increment,
    render_memory_ablation_report,
)

from evals import ablation
from finance_agent.memory import memory_recall_entry
from finance_agent.outcome.track_record.model import (
    init_track_record_tables,
    insert_prediction,
)


def _unwrap_entry(spec) -> object:  # noqa: ANN001
    """在 LangGraph 节点包装层中找 analysts_entry 的真实函数（同 T1 测试的走法）。"""
    seen: set[int] = set()
    stack = [spec]
    while stack:
        obj = stack.pop()
        if obj is None or id(obj) in seen:
            continue
        seen.add(id(obj))
        if inspect.isfunction(obj) and getattr(obj, "memory_recall_enabled", False):
            return obj
        for attr in ("bound", "func", "afunc", "runnable"):
            stack.append(getattr(obj, attr, None))
        if isinstance(obj, functools.partial):
            stack.extend(obj.args)
    return None


class TestMemoryVariantGraphs:
    def test_memory_variants_buildable(self):
        for variant in ("tech_memory", "tech_no_memory"):
            assert build_variant_graph(variant) is not None

    def test_variant_binding_is_the_only_difference(self):
        """两臂唯一差异 = 记忆召回入口的有无（消融单变量纪律）。"""
        memory_arm = build_variant_graph("tech_memory")
        bare_arm = build_variant_graph("tech_no_memory")
        assert _unwrap_entry(memory_arm.nodes["analysts_entry"]) is memory_recall_entry
        assert _unwrap_entry(bare_arm.nodes["analysts_entry"]) is None
        # 节点集合一致（差异只在入口行为，不在编排）
        assert {n for n in memory_arm.nodes if not n.startswith("__")} == {
            n for n in bare_arm.nodes if not n.startswith("__")
        }

    def test_existing_variants_untouched(self):
        assert ablation._VARIANTS == ("analysts", "plus_debate", "through_trader", "full")
        assert ablation._TECH_VARIANTS == ("tech_bare", "tech_factor_tool")
        assert ablation._MEMORY_VARIANTS == ("tech_no_memory", "tech_memory")
        assert ablation._MEMORY_VARIANT_ENABLED == {
            "tech_memory": True,
            "tech_no_memory": False,
        }

    def test_applicable_dims_analysts_layer_only(self):
        assert ablation._applicable_dims("tech_memory") == ("report_relevance",)
        assert ablation._applicable_dims("tech_no_memory") == ("report_relevance",)

    def test_memory_arm_entry_writes_decision_memory(self, tmp_path: Path, monkeypatch):
        """记忆臂入口真的检索（同标的已结算历史 → decision_memory 键）。"""
        db = tmp_path / "sessions.db"
        init_track_record_tables(db)
        insert_prediction(
            {
                "prediction_id": "p1",
                "source_type": "live",
                "symbol": "300750.SZ",
                "direction": "long",
                "rationale_snapshot": {},
                "created_at": "2026-09-01T09:00:00",
            },
            db_path=db,
            status="resolved_win",
        )
        monkeypatch.setenv("SESSIONS_DB_PATH", str(db))

        out = ablation._variant_analysts_entry("tech_memory")({"stock_code": "300750"})

        assert [e["decision_id"] for e in out["decision_memory"]["episodic"]] == ["p1"]
        assert ablation._variant_analysts_entry("tech_no_memory")({"stock_code": "300750"}) == {}


class TestCiZeroWording:
    def test_ci_containing_zero_is_no_significant_difference(self):
        assert conclusion_for_memory_increment((-0.1, 0.3)) == "无显著差异"
        assert conclusion_for_memory_increment((0.0, 0.0)) == "无显著差异"

    def test_positive_and_negative_directions(self):
        assert conclusion_for_memory_increment((0.1, 0.3)) == "显著改进"
        assert conclusion_for_memory_increment((-0.3, -0.1)) == "显著退步"

    def test_render_report_discloses_n_and_caliber(self):
        report = {
            "pairing_unit": "ticker",
            "pairing_units": 10,
            "n_runs": 20,
            "variants": {
                "tech_no_memory": {"n_runs": 10, "judge_medians": {"report_relevance": 3.0}},
                "tech_memory": {"n_runs": 10, "judge_medians": {"report_relevance": 3.2}},
            },
            "dims": {
                "report_relevance": {
                    "diff_mean": 0.2,
                    "ci": (-0.1, 0.5),
                    "conclusion": "无显著差异",
                }
            },
        }
        text = render_memory_ablation_report(report)
        assert "无显著差异" in text
        assert "[-0.1, 0.5]" in text
        assert "有效 n" in text and "10" in text
        assert "适用口径" in text
        assert "report_relevance" in text

    def test_render_report_forbids_invalidity_wording(self):
        bad = {
            "pairing_unit": "ticker",
            "pairing_units": 10,
            "n_runs": 20,
            "variants": {},
            "dims": {
                "report_relevance": {
                    "diff_mean": 0.2,
                    "ci": (-0.1, 0.5),
                    "conclusion": "记忆无效",
                }
            },
        }
        with pytest.raises(ValueError):
            render_memory_ablation_report(bad)


class TestAggregation:
    @staticmethod
    def _runs(variant: str, by_ticker: dict[str, list[float]]) -> list[dict]:
        return [
            {"variant": variant, "ticker": ticker, "judge": {"report_relevance": value}}
            for ticker, values in by_ticker.items()
            for value in values
        ]

    def test_zero_straddling_increment_reports_no_significant_difference(self):
        runs = self._runs(
            "tech_no_memory", {"t0": [3.0, 3.0], "t1": [3.0, 3.0], "t2": [3.0, 3.0]}
        ) + self._runs("tech_memory", {"t0": [3.1, 3.1], "t1": [3.0, 3.0], "t2": [2.9, 2.9]})
        report = ablation.aggregate_memory_ablation(runs)
        assert report["pairing_unit"] == "ticker"
        assert report["pairing_units"] == 3
        entry = report["dims"]["report_relevance"]
        assert entry["conclusion"] == "无显著差异"
        assert entry["ci"][0] <= 0 <= entry["ci"][1]
        assert "无显著差异" in render_memory_ablation_report(report)

    def test_clear_increment_reports_significant_improvement(self):
        runs = self._runs("tech_no_memory", {"t0": [2.0], "t1": [2.0], "t2": [2.0]}) + self._runs(
            "tech_memory", {"t0": [4.5], "t1": [4.5], "t2": [4.5]}
        )
        report = ablation.aggregate_memory_ablation(runs, B=2000)
        assert report["dims"]["report_relevance"]["conclusion"] == "显著改进"

    def test_no_paired_tickers_yields_empty_dims(self):
        report = ablation.aggregate_memory_ablation(self._runs("tech_no_memory", {"t0": [3.0]}))
        assert report["dims"] == {}
        assert render_memory_ablation_report(report)


class TestMemoryRunner:
    """跑批入口复用库侧唯一判分入口（score_run）与既有快照机制（同 factor-tool 先例）。"""

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
        report = ablation.run_memory_ablation(["600519"], repeats=1, judge_repeats=1)
        assert [variant for kind, variant in calls if kind == "score"] == [
            "tech_no_memory",
            "tech_memory",
        ]
        assert report["snapshot_digests"] == {"600519": "digest"}
        assert report["pairing_units"] == 1
        assert report["report_markdown"]
