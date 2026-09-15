from pathlib import Path

import pytest
from evals.causal_ablation.preregister import (
    MissingPreregistrationError,
    assert_preregistered,
    find_latest_preregister,
    parse_preregister,
)

GOOD = """# P1 预登记
- 主指标: 逃逸率
- MDE: 6pp
- 决策阈值: 拦截率 < 50% 建议降级；依据：成本-效用换算见附录 A
- 样本量依据: 20 标的 × 32 单元，不一致对子按 20% 估
- 停止规则: 不一致对子 < 10% 则收窄污染矩阵
- rubric 版本: judge-v8 / round7
"""


class TestParse:
    def test_good_document_is_valid(self):
        p = parse_preregister(GOOD)
        assert p.valid is True
        assert p.issues == []
        assert p.fields["MDE"] == "6pp"

    def test_missing_field_reported(self):
        text = GOOD.replace("- 停止规则: 不一致对子 < 10% 则收窄污染矩阵\n", "")
        p = parse_preregister(text)
        assert p.valid is False
        assert any("停止规则" in i for i in p.issues)

    def test_bare_threshold_without_rationale_rejected(self):
        text = GOOD.replace(
            "- 决策阈值: 拦截率 < 50% 建议降级；依据：成本-效用换算见附录 A",
            "- 决策阈值: 60%",
        )
        p = parse_preregister(text)
        assert p.valid is False
        assert any("换算依据" in i for i in p.issues)


class TestLookup:
    def test_latest_by_filename(self, tmp_path: Path):
        (tmp_path / "2026-09-10-x.md").write_text(GOOD, encoding="utf-8")
        (tmp_path / "2026-09-16-y.md").write_text(GOOD, encoding="utf-8")
        assert find_latest_preregister(tmp_path).path.name == "2026-09-16-y.md"

    def test_empty_dir_returns_none(self, tmp_path: Path):
        assert find_latest_preregister(tmp_path) is None

    def test_assert_raises_when_absent(self, tmp_path: Path):
        with pytest.raises(MissingPreregistrationError):
            assert_preregistered(tmp_path)

    def test_assert_raises_when_invalid(self, tmp_path: Path):
        (tmp_path / "2026-09-16-bad.md").write_text("# 空文档\n", encoding="utf-8")
        with pytest.raises(MissingPreregistrationError):
            assert_preregistered(tmp_path)

    def test_assert_passes_when_valid(self, tmp_path: Path):
        (tmp_path / "2026-09-16-good.md").write_text(GOOD, encoding="utf-8")
        assert assert_preregistered(tmp_path).valid is True
