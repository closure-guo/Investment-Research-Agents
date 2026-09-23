"""§1.8 口径登记护栏：口径条目是评估协议的唯一权威引用锚，防误删/漂移（spec evaluation 口径与预登记）。"""

from pathlib import Path

_ROOT = Path(__file__).resolve().parents[3]  # tests/evals/outcome/ → repo root


def _metrics_text() -> str:
    return (_ROOT / "docs/evals/metrics.md").read_text(encoding="utf-8")


def test_section_1_8_registered_with_core_caliber():
    text = _metrics_text()
    assert "### 1.8 Outcome 收益指标" in text
    for anchor in ("T+20", "000300.SH", "回避正确率", "settled", "1.4008", "0.2802", "runs.jsonl"):
        assert anchor in text, f"§1.8 缺口径锚点: {anchor}"


def test_timeline_has_outcome_switchpoint():
    text = _metrics_text()
    assert "Outcome 收益口径切点（2026-09-23" in text
    assert "add-outcome-profitability-protocol" in text


def test_no_report_status_literal_marker():
    """docs/evals 非报告文档不得复现 status 头字面标记（README 扫描器警告）。"""
    assert "**status**:" not in _metrics_text()
