from pathlib import Path

import pytest
from evals.causal_ablation.status_index import (
    ReportEntry,
    collect_status_index,
    render_status_index,
)


def _write(dir_path: Path, name: str, body: str) -> None:
    (dir_path / name).write_text(body, encoding="utf-8")


class TestCollect:
    def test_active_and_superseded_collected(self, tmp_path: Path):
        _write(tmp_path, "a.md", "# A\n\n**status**: active\n")
        _write(tmp_path, "b.md", "# B\n\n**status**: superseded-by: docs/a.md\n")
        entries, unstamped = collect_status_index(tmp_path)
        assert [e.status for e in entries] == ["active", "superseded-by"]
        assert entries[1].target == "docs/a.md"
        assert unstamped == []

    def test_doc_without_status_is_unstamped_not_dropped(self, tmp_path: Path):
        _write(tmp_path, "legacy.md", "# 存量报告\n\n没有 status 头\n")
        entries, unstamped = collect_status_index(tmp_path)
        assert entries == []
        assert unstamped == [(tmp_path / "legacy.md").as_posix()]

    def test_missing_dir_returns_empty(self, tmp_path: Path):
        assert collect_status_index(tmp_path / "nope") == ([], [])

    def test_superseded_without_target_raises(self, tmp_path: Path):
        _write(tmp_path, "bad.md", "**status**: superseded-by:\n")
        with pytest.raises(ValueError, match="缺目标"):
            collect_status_index(tmp_path)

    def test_recursive_opt_in(self, tmp_path: Path):
        sub = tmp_path / "sub"
        sub.mkdir()
        _write(sub, "nested.md", "**status**: active\n")
        assert collect_status_index(tmp_path)[0] == []
        assert len(collect_status_index(tmp_path, recursive=True)[0]) == 1


class TestRender:
    def test_index_lists_entries_and_targets(self):
        out = render_status_index(
            [ReportEntry("docs/evals/x.md", "superseded-by", "docs/evals/y.md")], []
        )
        assert "docs/evals/x.md" in out
        assert "docs/evals/y.md" in out

    def test_unstamped_section_visible(self):
        out = render_status_index([], ["docs/evals/legacy.md"])
        assert "未标注生命周期" in out
        assert "docs/evals/legacy.md" in out

    def test_empty_entries_render_placeholder(self):
        out = render_status_index([], [])
        assert "暂无带 status 头的报告" in out

    def test_entries_sorted_by_path(self):
        out = render_status_index(
            [ReportEntry("b.md", "active", None), ReportEntry("a.md", "active", None)], []
        )
        assert out.index("a.md") < out.index("b.md")


class TestBadgeCarriesTarget:
    """G7/⑤b 回归：徽章必须拿到目标路径（历史形态：`status_badge(e.status)` 未传 target
    → superseded 行恒显 `<缺目标>`，真实取代者只存在于同行的「取代者」列）。"""

    _TARGET = "docs/evals/2026-09-03-消融n10权威结果.md"

    def test_superseded_row_badge_points_at_real_target(self):
        out = render_status_index(
            [ReportEntry("evals/ablation/results/pilot.md", "superseded-by", self._TARGET)], []
        )
        line = next(line for line in out.splitlines() if "pilot.md" in line)
        status_cell = line.split("|")[2].strip()
        assert status_cell == f"![superseded](badge:superseded) → {self._TARGET}", (
            "徽章后须跟随真实取代者路径，而非占位文案"
        )
        assert "<缺目标>" not in out

    def test_active_row_has_no_missing_target_placeholder(self):
        out = render_status_index([ReportEntry("docs/evals/a.md", "active", None)], [])
        assert "<缺目标>" not in out, "active 行不得出现缺目标占位"
