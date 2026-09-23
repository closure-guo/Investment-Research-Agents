"""outcome 收口报告 status 头契约（spec evaluation「Outcome 读数收口纪律」第④步）。"""

from pathlib import Path

import pytest
from evals.outcome.report import assert_outcome_report


def test_active_report_passes():
    assert assert_outcome_report("# 报告\n\n**status**: active\n") == ("active", None)


def test_missing_header_raises():
    with pytest.raises(ValueError, match="status"):
        assert_outcome_report("# 报告，无头\n")


def test_superseded_without_target_raises():
    with pytest.raises(ValueError, match="目标"):
        assert_outcome_report("**status**: superseded-by\n")


def test_superseded_pointer_must_exist(tmp_path: Path):
    text = "**status**: superseded-by: docs/evals/不存在.md\n"
    with pytest.raises(ValueError, match="不存在"):
        assert_outcome_report(text, root=tmp_path)


def test_superseded_pointer_exists_passes(tmp_path: Path):
    target = tmp_path / "docs/evals"
    target.mkdir(parents=True)
    (target / "v2.md").write_text("# v2", encoding="utf-8")
    text = "**status**: superseded-by: docs/evals/v2.md\n"
    assert assert_outcome_report(text, root=tmp_path) == (
        "superseded-by",
        "docs/evals/v2.md",
    )
