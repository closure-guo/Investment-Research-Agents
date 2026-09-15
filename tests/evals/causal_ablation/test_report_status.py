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


class TestLineBoundedTarget:
    """G7/⑤a 回归：target 捕获必须限于 status 头**同一行**。

    历史形态：正则尾部的 `\\s*` 会吃掉换行，于是 `**status**: superseded-by`（本行无
    路径）把下一行的首个 token（`**日期**:`）当作 target，`assert_status_valid` 放行——
    恰好削弱了规格依赖的「superseded-by 必须带目标」校验。
    """

    _MALFORMED = "**status**: superseded-by\n**日期**: 2026-09-03 21:18\n"
    _WELL_FORMED = "# R\n\n**status**: superseded-by: docs/evals/x.md\n**日期**: 2026-09-03 21:18\n"

    def test_next_line_token_not_captured_as_target(self):
        with pytest.raises(ValueError, match="目标"):
            assert_status_valid(self._MALFORMED)
        assert parse_status(self._MALFORMED) == ("superseded-by", None), (
            "下一行 token 不得冒充取代者路径"
        )

    def test_well_formed_superseded_still_parses_exact_target(self):
        assert parse_status(self._WELL_FORMED) == ("superseded-by", "docs/evals/x.md")
        assert_status_valid(self._WELL_FORMED)

    def test_active_line_with_following_metadata_has_no_target(self):
        """`active` 行同样受行界约束：下一行的 `**日期**:` 不得进 target（此前 n10 索引中招）。"""
        assert parse_status("**status**: active\n**日期**: 2026-09-03 21:18\n") == ("active", None)


class TestBadge:
    def test_active_badge(self):
        assert "active" in status_badge("active")

    def test_superseded_badge_points_to_target(self):
        assert "docs/x.md" in status_badge("superseded-by", "docs/x.md")
