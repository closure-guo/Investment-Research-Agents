"""消融结果报告的状态契约（delta update-ablation-driver-parity-and-report-status §4）。

零成本机器校验，替代「靠人记得加撤回说明」：报告头部状态字段合法，且
`superseded-by` 指向真实文件——**悬空指针与无标记等价**，旧结论该失效却失效不了，
正是 2026-09-15 发现的问题（pilot.md 的被推翻结论原文在线数月）。

`active` 不参与本文件的语义校验；被标记 superseded 的报告 SHALL 带就地撤回说明
（原文保留 + 撤回理由 + 指向权威版本），不得只删数字或只改数字。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_REPORTS_DIR = _ROOT / "evals" / "ablation" / "results"

_STATUS_RE = re.compile(r"^\*\*status\*\*:\s*(?P<value>.+?)\s*$", re.MULTILINE)


def _report_files() -> list[Path]:
    return sorted(_REPORTS_DIR.glob("*.md"))


def _status_value(report: Path) -> str | None:
    match = _STATUS_RE.search(report.read_text(encoding="utf-8"))
    return match.group("value") if match else None


def _superseded_target(report: Path) -> str | None:
    value = _status_value(report) or ""
    if not value.startswith("superseded-by:"):
        return None
    return value.split(":", 1)[1].strip()


@pytest.fixture(params=_report_files(), ids=lambda p: p.name)
def report(request: pytest.FixtureRequest) -> Path:
    return Path(request.param)


class TestAblationReportStatus:
    def test_reports_directory_not_empty(self):
        """防目录改名后本文件静默全绿（契约消失而无人发现）。"""
        assert _report_files(), f"{_REPORTS_DIR} 下应有消融结果报告"

    def test_status_header_valid(self, report: Path):
        value = _status_value(report)
        assert value is not None, (
            f"{report.name} 头部缺 `**status**:`——取值须为 `active` 或 `superseded-by: <路径>`"
        )
        assert value == "active" or value.startswith("superseded-by:"), (
            f"{report.name} 的 status 取值非法：{value!r}"
        )

    def test_superseded_target_resolves(self, report: Path):
        target = _superseded_target(report)
        if target is None:
            pytest.skip("active 报告，无指针需校验")
        assert (_ROOT / target).exists(), (
            f"{report.name} 的 superseded-by 指向不存在的文件：{target}（悬空指针 = 旧结论没失效）"
        )

    def test_superseded_report_carries_retraction_note(self, report: Path):
        if _superseded_target(report) is None:
            pytest.skip("active 报告，无需撤回说明")
        body = report.read_text(encoding="utf-8")
        assert "撤回" in body, (
            f"{report.name} 标了 superseded 但无就地撤回说明——只改数字会让读者以为结论仍成立"
        )
