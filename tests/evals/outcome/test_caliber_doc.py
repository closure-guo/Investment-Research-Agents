"""§1.8 口径登记护栏：口径条目是评估协议的唯一权威引用锚，防误删/漂移（spec evaluation 口径与预登记）。"""

from pathlib import Path

_ROOT = Path(__file__).resolve().parents[3]  # tests/evals/outcome/ → repo root


def _metrics_text() -> str:
    return (_ROOT / "docs/evals/metrics.md").read_text(encoding="utf-8")


def test_section_1_8_registered_with_core_caliber():
    text = _metrics_text()
    assert "### 1.8 Outcome 收益指标" in text, "§1.8 小节缺失"
    assert text.index("### 1.8 ") < text.index("## 2. 时间线"), "§1.8 须登记在 §2 时间线之前"
    # 锚点须落在 §1.8 小节内：全文断言会被他处同名锚点满足（如 `runs.jsonl` 在文档头部亦出现），
    # 整段被删测试仍绿。
    sec = text.split("### 1.8 ", 1)[1].split("\n---", 1)[0]
    for anchor in (
        "T+20",
        "000300.SH",
        "回避正确率",
        "settled",
        "1.4008",
        "0.2802",
        "runs.jsonl",
        # ③⑤⑥ 三行锚点（终审 I3）：辅助观测窗 / 两项健康阈值 / 泄漏阈值与通路验证定位
        "T+5",
        "0.90",
        "0.10",
        "0.60",
        "通路验证",
        "记账完整率",
    ):
        assert anchor in sec, f"§1.8 缺口径锚点: {anchor}"


def test_timeline_has_outcome_switchpoint():
    switchpoints = [
        ln for ln in _metrics_text().splitlines() if "Outcome 收益口径切点（2026-09-23" in ln
    ]
    assert switchpoints, "§2 时间线缺 outcome 收益口径切点行"
    line = switchpoints[0]
    assert "add-outcome-profitability-protocol" in line, "切点行未钉死 delta 名"
    assert "未跑批" in line, "切点行未标「未跑批」"


def test_no_report_status_literal_marker():
    """本文件（metrics.md）不得复现 status 头字面标记（status_index 扫描器以 `**status**` 为键，全宽冒号变体同拦）。"""
    assert "**status**" not in _metrics_text()
