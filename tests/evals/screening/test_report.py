"""报告渲染单测（add-screening-evaluation Task 2/3；措辞守卫 + 有效 n / 口径披露）。

spec 口径：CI 含 0 的报告只写「无显著差异」，禁止引申为否定性结论；渲染器是
**结构守卫**（fail loud，坏措辞不进报告）。合成数据声明必须随报告渲染（不冒充真实读数）。
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from evals.screening import ablation, forward, report
from finance_agent.execution.config import default_matching_config
from finance_agent.factors.eval_engine import MarketPanel
from finance_agent.screening.ranker import Candidate
from finance_agent.screening.store import record_l2_candidates, record_screening_batch

DATES = tuple(f"2026-03-{day:02d}" for day in range(1, 7))  # d0..d5
D0, D1 = DATES[0], DATES[1]
P0 = DATES[0]


def _panel() -> MarketPanel:
    closes = {"A": [10.0] * 6, "B": [10.0, 10.0, 12.0, 13.0, 14.0, 15.0]}
    close_frame = pd.DataFrame(closes, index=list(DATES)).sort_index().sort_index(axis=1)
    return MarketPanel(
        fields={
            "close": close_frame,
            "open": close_frame.copy(),
            "high": close_frame * 1.02,
            "low": close_frame * 0.98,
            "amount": pd.DataFrame(
                {symbol: [1000.0] * 6 for symbol in closes}, index=list(DATES)
            ).sort_index(axis=1),
        }
    )


def _ref_payload(tmp_path: Path) -> dict:
    """真实 ``forward_pool_returns`` 输出（键形状与上游一致，防止渲染器漂移）。"""
    db_path = tmp_path / "screening.db"
    record_l2_candidates(
        P0,
        [
            Candidate(symbol="A", trade_date=P0, composite_score=1.0, factor_contribs=(), rank=1),
            Candidate(symbol="B", trade_date=P0, composite_score=0.5, factor_contribs=(), rank=2),
        ],
        db_path=db_path,
    )
    record_screening_batch(
        P0,
        status="ok",
        started_at="2026-03-01T00:00:00",
        finished_at="2026-03-01T00:01:00",
        l2_top=2,
        l3_keep=1,
        pool_rows=[
            {
                "symbol": "A",
                "composite_score": 1.0,
                "rank": 1,
                "consecutive_days": 3,
                "l3_verdict": "keep",
                "l3_reason": "合成",
                "factor_contribs": [],
            }
        ],
        db_path=db_path,
    )
    return forward.forward_pool_returns(
        pool_dates=(P0,),
        panel=_panel(),
        db_path=db_path,
        horizons=(3,),
        config=default_matching_config(),
    )


def _stats(
    *,
    n: int = 3,
    mean: float | None = 0.01,
    win_rate: float | None = 0.5,
    days: int = 1,
    excluded: dict[str, int] | None = None,
    unfilled: dict[str, int] | None = None,
) -> dict:
    return {
        "n": n,
        "mean": mean,
        "win_rate": win_rate,
        "days": days,
        "excluded_counts": excluded or {},
        "unfilled_counts": unfilled or {},
    }


def _diff(*, ci: list[float] | None, conclusion: str, diff_mean: float = 0.01) -> dict:
    return {
        "paired_days": 2,
        "diff_mean": diff_mean,
        "ci95": ci,
        "bootstrap_B": 10000,
        "seed": 42,
        "conclusion": conclusion,
    }


def _ablation_payload(*, ci: list[float] | None, conclusion: str) -> dict:
    return {
        "data_source": "synthetic",
        "title": "选股前向跟踪与消融报告（样本）",
        "generated_at": "2026-10-13T20:00:00",
        "ablation": {
            "horizon": 3,
            "pool_dates": [D0, D1],
            "as_of": None,
            "seed": 42,
            "variants": {
                "factor": _stats(n=2, mean=0.02),
                "random": _stats(n=2, mean=0.01),
                "equal_weight": _stats(n=4, mean=-0.005),
            },
            "diffs": {
                "factor_minus_random": _diff(ci=ci, conclusion=conclusion),
                "factor_minus_equal_weight": _diff(ci=[0.01, 0.03], conclusion="显著改进"),
            },
            "methodology": {
                "entry": "入场 = 池日次一交易日开盘经撮合引擎成交（买 1 手）",
                "exit": "出场 = 入场日后第 horizon 个交易日收盘",
                "truncation": "时点截断：只用 ≤ as_of 的面板行",
                "bootstrap": "配对单元 = 池日；B=10000，seed=42",
            },
        },
    }


def test_render_forward_section_discloses_valid_n_and_caliber(tmp_path: Path):
    payload = {
        "data_source": "synthetic",
        "title": "选股前向跟踪报告",
        "forward": _ref_payload(tmp_path),
    }
    text = report.render_screening_report_md(payload)
    assert "有效 n" in text
    assert "Top-N 入选组" in text
    assert "落选对照组" in text
    assert "配对（池日）" in text
    assert "配对日数" in text
    # 口径披露：入场走撮合引擎 + 时点截断 + bootstrap 规格
    assert "撮合" in text
    assert "时点截断" in text
    assert "B=10000" in text
    assert "seed=42" in text
    # 合成数据声明（不冒充真实读数）
    assert "合成数据" in text


def test_render_ablation_section_real_payload():
    result = ablation.compare_ablation_variants(
        variants={
            "factor": {D0: ["B"], D1: ["B"]},
            "random": {D0: ["A"], D1: ["A"]},
            "equal_weight": {D0: ["A", "B"], D1: ["A", "B"]},
        },
        panel=_panel(),
        horizon=3,
        config=default_matching_config(),
    )
    text = report.render_screening_report_md({"ablation": result, "data_source": "synthetic"})
    assert "因子打分" in text
    assert "固定种子随机" in text
    assert "等权指数" in text
    assert "配对日数" in text
    assert "显著改进" in text  # 恒定正差 → CI 不含 0
    assert "seed=42" in text
    assert "有效 n" in text


def test_ci_containing_zero_renders_no_significant_difference():
    """spec 场景：CI 含 0 → 逐字「无显著差异」，且不出现「无效/无价值/无用」类引申。"""
    payload = _ablation_payload(ci=[-0.01, 0.02], conclusion="无显著差异")
    text = report.render_screening_report_md(payload)
    assert "无显著差异" in text
    assert "[-1.00%, 2.00%]" in text  # CI 随文案披露（读数可复核）
    for forbidden in ("无效", "无价值", "无用", "没有价值"):
        assert forbidden not in text


def test_render_rejects_forbidden_wording():
    """坏结论（手改/上游措辞漂移）→ 渲染 fail loud，坏措辞不进报告。"""
    payload = _ablation_payload(ci=[-0.01, 0.02], conclusion="随机选股无效")
    with pytest.raises(ValueError):
        report.render_screening_report_md(payload)


def test_render_without_sections_is_still_valid():
    text = report.render_screening_report_md({"data_source": "live"})
    assert "选股前向跟踪" in text
    assert "合成数据" not in text


def _l3_payload(*, conclusion: str, ci: list[float] | None) -> dict:
    """spec 场景 payload：keep 20 日均值 +2.1%、drop -3.4%（Task 3 测试清单）。"""
    return {
        "data_source": "synthetic",
        "l3": {
            "horizon": 20,
            "as_of": "2026-10-13",
            "pool_dates": [f"2026-09-{day:02d}" for day in range(1, 31, 2)],  # 15 个池日
            "blowup_threshold": -0.10,
            "keep": {
                **_stats(n=12, mean=0.021, win_rate=0.58, days=12),
                "blowup_rate": 0.0,
            },
            "drop": {
                **_stats(n=9, mean=-0.034, win_rate=0.22, days=9),
                "blowup_rate": 1 / 3,
            },
            "diff": {
                "paired_days": 15,
                "diff_mean": 0.055,
                "ci95": ci,
                "bootstrap_B": 10000,
                "seed": 42,
                "conclusion": conclusion,
            },
            "conclusion": conclusion,
            "methodology": {
                "entry": "入场 = 池日次一交易日开盘经撮合引擎成交（买 1 手）",
                "exit": "出场 = 入场日后第 20 个交易日收盘",
                "truncation": "时点截断：只用 ≤ as_of 的面板行",
                "bootstrap": "配对单元 = 池日；B=10000，seed=42",
            },
        },
    }


def test_render_l3_section_spec_scenario_discloses_window_and_n():
    """spec 场景：keep +2.1% / drop -3.4% / CI 不含 0 → 「具有排雷价值」+ 窗口与 n 披露。"""
    text = report.render_screening_report_md(
        _l3_payload(conclusion="具有排雷价值", ci=[0.012, 0.098])
    )
    assert "具有排雷价值" in text
    assert "有效 n" in text
    assert "池日窗口" in text and "15 个池日" in text  # 窗口范围披露
    assert "+2.10%" in text and "-3.40%" in text  # 两组均值逐字可复核
    assert "踩雷率" in text
    assert "0.0%" in text and "33.3%" in text  # 踩雷率两行随表披露
    assert "[1.20%, 9.80%]" in text  # 配对 CI 随文案披露


def test_render_l3_section_ci_containing_zero_says_no_significant_difference():
    text = report.render_screening_report_md(_l3_payload(conclusion="无显著差异", ci=[-0.02, 0.04]))
    assert "无显著差异" in text
    for forbidden in ("无效", "无价值", "无用", "没有价值"):
        assert forbidden not in text


def _stratify_payload(*, conclusion: str) -> dict:
    return {
        "data_source": "synthetic",
        "stratify": {
            "horizon": 20,
            "as_of": None,
            "pool_dates": [D0, D1],
            "top_n": 3,
            "strata": {
                "builtin": {**_stats(n=6, mean=0.011), "factors": [{"id": 1}]},
                "llm": {**_stats(n=6, mean=0.004), "factors": [{"id": 2}]},
            },
            "diff": {
                "paired_days": 2,
                "diff_mean": -0.007,
                "ci95": [-0.02, 0.006],
                "bootstrap_B": 10000,
                "seed": 42,
                "conclusion": conclusion,
            },
            "conclusion": conclusion,
            "methodology": {
                "entry": "入场 = 池日次一交易日开盘经撮合引擎成交（买 1 手）",
                "exit": "出场 = 入场日后第 horizon 个交易日收盘",
                "truncation": "时点截断：只用 ≤ as_of 的面板行",
                "bootstrap": "配对单元 = 池日；B=10000，seed=42",
            },
        },
    }


def test_render_stratify_section_two_strata_rows():
    text = report.render_screening_report_md(_stratify_payload(conclusion="无显著差异"))
    assert "仅 builtin 打分" in text
    assert "仅 LLM 因子打分" in text
    assert "无显著差异" in text
    assert "有效 n" in text
    assert "[-2.00%, 0.60%]" in text
