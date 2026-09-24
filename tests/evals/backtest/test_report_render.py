"""回测报告 md 渲染测试（delta add-backtest-leakage-controls，Δ4 Task 4）。

契约：渲染头部必须能被 `evals.outcome.report.assert_outcome_report` 通过；
固定六段（干净窗口 / 泄漏探针三态 / regime 覆盖 / 绩效表 / 结论逐字 / 口径）；
缺数据如实写「未提供/不适用」，不静默省略。
"""

from __future__ import annotations

import pytest
from evals.backtest.report import build_conclusion, render_backtest_report_md
from evals.outcome.report import assert_outcome_report


def _probe(state: str, *, rate: float | None, downgraded: bool, window: list[str]) -> dict:
    return {
        "state": state,
        "probe_n": 10,
        "questions_per_ticker": 3,
        "direction_hit_rate": rate,
        "magnitude_hit_rate": 0.4,
        "event_hit_rate": 0.5,
        "unknown_ratio": 0.125,
        "threshold": 0.60,
        "downgraded": downgraded,
        "probe_window": window,
    }


def _report(**over) -> dict:
    base: dict = {
        "generated_at": "2026-09-23T12:00:00",
        "batch_kind": "formal",
        "preregister": {"path": "evals/ablation/preregister/outcome-x.md", "valid": True},
        "clean_window": {
            "passed": True,
            "reason": "全部 3 个决策日距 as_of(2026-09-23) ≥ 20 个交易日（最少 25）",
        },
        "leakage_probe": _probe("measurable", rate=0.3, downgraded=False, window=["2023-01-05"]),
        "regime_coverage": {
            "covered": ["bear", "bull", "sideways"],
            "limited": False,
            "all_regimes": ["bull", "bear", "sideways"],
        },
        "perf_table": {
            "system": {"CR": 0.10, "ARR": 0.20, "Sharpe": 1.00, "MDD": 0.30},
            "buy_hold": {"CR": 0.05, "ARR": 0.10, "Sharpe": 0.50, "MDD": 0.40},
            "macd": {"CR": 0.04, "ARR": 0.08, "Sharpe": 0.40, "MDD": 0.35},
            "kdj": {"CR": 0.03, "ARR": 0.06, "Sharpe": 0.30, "MDD": 0.32},
            "rsi": {"CR": 0.02, "ARR": 0.04, "Sharpe": 0.20, "MDD": 0.31},
        },
        "best_baseline": "buy_hold",
        "conclusion": "通路验证定位（batch_kind=pathway）：本批不产出 skill / 赚钱能力结论句",
        "methodology": {
            "entry": "结算语义与生产 track-record 判定同源",
            "daily_returns": "单笔结算收益摊到持有期逐日",
            "adjust": "结算/回测区间收益走后复权（hfq，SETTLEMENT_ADJUST）",
            "benchmark": "BENCHMARK_CODE 默认 000300",
            "system_population": "system 收益仅计 buy/sell 决策；hold/watch 整条排除",
        },
    }
    base.update(over)
    return base


def _render(**over) -> str:
    return render_backtest_report_md(_report(**over), name="formal-20260923-120000")


class TestHeaderContract:
    def test_rendered_header_passes_outcome_contract(self):
        text = _render()
        status, target = assert_outcome_report(text)
        assert status == "active"
        assert target is None

    def test_header_carries_name_date_batch_kind_and_preregister(self):
        text = _render()
        assert text.startswith("# 回测报告：formal-20260923-120000")
        assert "**日期**: 2026-09-23T12:00:00" in text
        assert "**批次类型**: formal" in text
        assert "evals/ablation/preregister/outcome-x.md" in text

    def test_missing_status_header_raises(self):
        text = _render().replace("**status**: active", "**状态**: active")
        with pytest.raises(ValueError, match="status"):
            assert_outcome_report(text)

    def test_unbound_preregister_written_as_unbound(self):
        text = _render(preregister=None)
        assert "**预登记**: 未绑定" in text

    def test_invalid_preregister_disclosed(self):
        text = _render(preregister={"path": "p.md", "valid": False})
        assert "无效" in text


class TestProbeStates:
    def test_measurable_renders_reading_and_threshold(self):
        text = _render(
            leakage_probe=_probe("measurable", rate=0.3, downgraded=False, window=["2023-01-05"])
        )
        assert "可测（低于阈值）" in text
        assert "30%" in text
        assert "60%" in text

    def test_downgraded_renders_upper_bound_wording(self):
        text = _render(
            leakage_probe=_probe("downgraded", rate=0.9, downgraded=True, window=["2023-01-05"])
        )
        assert "超阈（降级：泄漏污染下的上界证据）" in text

    def test_unmeasurable_renders_unmeasurable_wording(self):
        text = _render(
            leakage_probe=_probe("unmeasurable", rate=None, downgraded=False, window=["2023-01-05"])
        )
        assert "不可测（探针不可测）" in text

    def test_missing_probe_renders_not_executed(self):
        text = _render(leakage_probe=None)
        assert "未执行（无探针读数）" in text
        # 审查③：无探针时不得出现自相矛盾的「probe_window：未提供（…仅覆盖该窗口）」句。
        assert "仅覆盖该窗口" not in text

    def test_probe_section_discloses_unknown_ratio_and_window(self):
        text = _render(
            leakage_probe=_probe(
                "measurable", rate=0.3, downgraded=False, window=["2023-01-05", "2023-06-01"]
            )
        )
        assert "unknown_ratio" in text
        assert "12.5%" in text
        assert "2023-01-05" in text and "2023-06-01" in text

    def test_per_window_readings_rendered_as_table(self):
        """审查①：逐窗口读数进入 md（每项带自己的窗口），汇总不掩盖单窗口异常。"""
        probe = _probe("downgraded", rate=0.9, downgraded=True, window=["2023-01-05", "2023-06-01"])
        probe["per_window"] = [
            {
                "probe_window": ["2023-01-05"],
                "state": "measurable",
                "direction_hit_rate": 0.3,
                "probe_n": 10,
                "unknown_ratio": 0.0,
                "downgraded": False,
            },
            {
                "probe_window": ["2023-06-01"],
                "state": "downgraded",
                "direction_hit_rate": 0.9,
                "probe_n": 10,
                "unknown_ratio": 0.2,
                "downgraded": True,
            },
        ]
        text = _render(leakage_probe=probe)
        assert "逐窗口读数" in text
        assert "聚合规则：不可测 > 超阈 > 可测" in text
        # 逐窗口行的窗口与其自身读数并存（不是把全部窗口塞进每行）
        assert "| 2023-01-05 |" in text
        assert "| 2023-06-01 |" in text

    def test_probes_missing_count_disclosed(self):
        probe = _probe("measurable", rate=0.3, downgraded=False, window=["2023-01-05"])
        probe["probes_missing"] = 2
        text = _render(leakage_probe=probe)
        assert "未返回读数的窗口条目" in text
        assert "2 条" in text


class TestCleanWindowAndRegime:
    def test_failed_clean_window_reason_verbatim(self):
        text = _render(
            clean_window={"passed": False, "reason": "决策日 2024-01-25 不足 20 个交易日"}
        )
        assert "不通过" in text
        assert "决策日 2024-01-25 不足 20 个交易日" in text

    def test_passed_clean_window_rendered(self):
        text = _render()
        assert "通过" in text

    def test_missing_clean_window_rendered_not_provided(self):
        text = _render(clean_window=None)
        assert "未提供" in text

    def test_regime_limited_disclosed(self):
        text = _render(
            regime_coverage={
                "covered": ["bull", "bear"],
                "limited": True,
                "all_regimes": ["bull", "bear", "sideways"],
            }
        )
        assert "受限" in text
        assert "bull" in text and "bear" in text

    def test_regime_full_coverage_not_limited(self):
        text = _render()
        assert "全覆盖" in text


class TestPerfTableAndConclusion:
    def test_perf_table_lists_system_and_all_baselines(self):
        text = _render()
        for label in ("system", "buy_hold", "macd", "kdj", "rsi"):
            assert label in text
        assert "CR" in text and "ARR" in text and "Sharpe" in text and "MDD" in text

    def test_conclusion_verbatim(self):
        """审查⑤：与 `build_conclusion(...)` 的真实输出相等（不再用硬编码句只钉透传）。"""
        expected = build_conclusion(
            "显著优于基线",
            batch_kind="pathway",
        )
        text = _render(conclusion=expected)
        assert f"## 5. 结论\n\n{expected}\n" in text

    def test_conclusion_is_the_verbatim_build_conclusion_output(self):
        expected = build_conclusion(
            "无显著差异",
            batch_kind="formal",
            clean_window={"passed": True, "reason": "ok"},
            probe={"direction_hit_rate": None, "downgraded": False},
        )
        assert "通路验证" in expected  # 前置：确实走了非透传分支
        assert f"## 5. 结论\n\n{expected}\n" in _render(conclusion=expected)

    def test_missing_conclusion_rendered_not_provided(self):
        text = _render(conclusion=None)
        assert "未提供" in text

    def test_methodology_discloses_hfq_and_non_executable_exclusion(self):
        text = _render()
        assert "hfq" in text
        assert "非可执行" in text or "hold/watch" in text


class TestProbeCompositionDisclosure:
    """spec「探针执行与披露」要求报告含「抽样标的数 / **题目构成** / 记忆命中率 /
    未知占比」。md 是落 `evals/backtest/results/` 的报告正文，故四者都须在 md 内。
    变异实证：`_probe_lines` 去掉题目构成/幅度率/事件率两行 → 本用例 RED。"""

    def test_md_discloses_composition_and_all_three_rates(self):
        text = _render(
            leakage_probe=_probe("measurable", rate=0.3, downgraded=False, window=["2023-01-05"])
        )
        assert "抽样标的数" in text and "10" in text
        assert "题目构成" in text and "3" in text
        assert "方向命中率" in text and "幅度桶命中率" in text and "事件题命中率" in text
        assert "40%" in text  # magnitude_hit_rate
        assert "50%" in text  # event_hit_rate


class TestRenderSelfCheckWiring:
    """渲染后自校是接线（不是装饰）：契约守卫抛错必须传出渲染函数。

    变异实证：删除 `render_backtest_report_md` 末尾的 `assert_outcome_report(text)`
    调用 → 本用例 RED（守卫被 patch 成抛错却无人调用）。"""

    def test_self_check_failure_propagates(self, monkeypatch):
        def _boom(text: str) -> None:
            raise ValueError("契约守卫被触发")

        monkeypatch.setattr("evals.backtest.report.assert_outcome_report", _boom)
        with pytest.raises(ValueError, match="契约守卫被触发"):
            _render()
