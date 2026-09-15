"""G7/⑦ 回归：`tests/scripts/ablation_aggregate_90.py` 的人读版渲染不得漏掉 citation 腿。

背景：G2 后层块 `citation_pass_rate` 不再带 `ci`，脚本里 `if "ci" in cpr:` 恒假——
四桶层增量与标量监控行**双双静默消失**，人读报告缺了 citation 腿且无任何报错。
本文件只测渲染纯函数（零 IO，脚本仍可整只导入）；脚本的路径/阈值/参数化不在此范围。
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

from evals.ablation import aggregate_results

_ROOT = Path(__file__).resolve().parents[2]
_SCRIPT = _ROOT / "tests" / "scripts" / "ablation_aggregate_90.py"


def _load_module():
    """以模块方式加载脚本（tests/scripts 非包，走 importlib）。"""
    spec = importlib.util.spec_from_file_location("ablation_aggregate_90_under_test", _SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


agg90 = _load_module()

# 逐变体四桶（每标的两次重复 → 中位数即该计数）+ 标量 citation_pass
_BUCKETS = {
    "analysts": {
        "blocked": 2,
        "analyst_true_fail": 3,
        "surgical_repaired": 0,
        "verifier_normalized": 4,
    },
    "plus_debate": {
        "blocked": 0,
        "analyst_true_fail": 1,
        "surgical_repaired": 1,
        "verifier_normalized": 3,
    },
    "full": {
        "blocked": 0,
        "analyst_true_fail": 0,
        "surgical_repaired": 2,
        "verifier_normalized": 1,
    },
}
_PASS = {"analysts": False, "plus_debate": True, "full": False}
_TICKERS = ("600519", "000001")


def _runs(*, with_buckets: bool = True) -> list[dict]:
    from evals.causal_ablation.escape import split_verifier_buckets

    rows: list[dict] = []
    for variant, counts in _BUCKETS.items():
        for ticker in _TICKERS:
            for _ in range(2):
                row = {
                    "variant": variant,
                    "ticker": ticker,
                    "citation_pass": _PASS[variant],
                    "judge": {"report_relevance": 4.0},
                    "llm_calls": 10,
                    "prompt_tokens": 100,
                    "completion_tokens": 50,
                    "citation_coverage": 0.95,
                }
                if with_buckets:
                    row["citation_buckets"] = split_verifier_buckets(counts)
                rows.append(row)
    return rows


def _render(*, with_buckets: bool = True) -> str:
    runs = _runs(with_buckets=with_buckets)
    return "\n".join(agg90.render_report(aggregate_results(runs), runs))


def test_script_is_import_safe_and_exposes_renderer():
    assert callable(agg90.render_report), "渲染须为可测纯函数（导入不触发文件读取）"
    assert callable(agg90.main)


class TestCitationBucketsRendered:
    def test_four_bucket_lines_present_for_debate_layer(self):
        """四桶各出一行 `{中文标签} Δ{diff} CI[{lo},{hi}]`（此前因死判据整段消失）。"""
        text = _render()
        assert "阻断 Δ-2.0 CI[-2.0,-2.0]" in text
        assert "分析师真错 Δ-2.0 CI[-2.0,-2.0]" in text
        assert "单点修复 Δ1.0 CI[1.0,1.0]" in text
        assert "校验器归一 Δ-1.0 CI[-1.0,-1.0]" in text

    def test_four_bucket_lines_present_for_full_layer(self):
        """full 层（对 plus_debate 配对）同样渲染四桶，数值随该层。"""
        text = _render()
        assert "阻断 Δ0.0 CI[0.0,0.0]" in text
        assert "分析师真错 Δ-1.0 CI[-1.0,-1.0]" in text
        assert "校验器归一 Δ-2.0 CI[-2.0,-2.0]" in text

    def test_scalar_pass_rate_line_kept_and_marked_not_for_conclusions(self):
        """标量块仍在（prev→current 监控），但显式标注不入层增量结论。"""
        text = _render()
        scalar_lines = [line for line in text.splitlines() if "citation_pass 率" in line]
        assert scalar_lines, "标量监控行不得消失"
        assert any(
            "0.0% → 100.0%" in line and "（不入层增量结论）" in line for line in scalar_lines
        ), scalar_lines
        assert any("100.0% → 0.0%" in line for line in scalar_lines), "full 层标量行缺失"

    def test_missing_bucket_renders_no_line_for_that_bucket(self):
        """旧记录/缺桶 → 该桶不产行（不得伪造 0 或空行）。"""
        runs = _runs(with_buckets=False)
        agg = aggregate_results(runs)
        assert "citation_blocked" not in agg["layers"]["debate"], "前提：无桶 → 无层条目"
        text = "\n".join(agg90.render_report(agg, runs))
        assert "阻断 Δ" not in text
        assert "citation_pass 率" in text, "标量监控行与四桶互不依赖"

    def test_legacy_entry_without_direction_metadata_does_not_claim_direction(self):
        """合成 agg（旧产物：层条目无 `lower_is_better`）→ 不冒充「无方向」。"""
        runs = _runs()
        agg = aggregate_results(runs)
        agg["layers"]["debate"]["citation_blocked"].pop("lower_is_better")
        text = "\n".join(agg90.render_report(agg, runs))
        line = next(line for line in text.splitlines() if "citation_blocked（阻断）" in line)
        assert "lower_is_better" not in line, line
