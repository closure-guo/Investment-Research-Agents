import pytest
from evals.causal_ablation.report_status import (
    assert_status_valid,
    parse_status,
    status_badge,
)


class TestParse:
    def test_active(self):
        assert parse_status("# R\n\n**status**: active\n") == ("active", None)

    def test_superseded(self):
        status, target = parse_status("# R\n\n**status**: superseded-by: docs/x.md\n")
        assert status == "superseded-by"
        assert target == "docs/x.md"

    def test_missing_raises(self):
        with pytest.raises(ValueError, match="status"):
            parse_status("# R\n")

    def test_unknown_status_raises(self):
        with pytest.raises(ValueError, match="未知 status"):
            parse_status("**status**: maybe\n")


class TestValidate:
    def test_valid_passes(self):
        assert_status_valid("**status**: active\n")

    def test_superseded_requires_target(self):
        with pytest.raises(ValueError, match="目标"):
            assert_status_valid("**status**: superseded-by:\n")


class TestBadge:
    def test_active_badge(self):
        assert "active" in status_badge("active")

    def test_superseded_badge_points_to_target(self):
        assert "docs/x.md" in status_badge("superseded-by", "docs/x.md")
