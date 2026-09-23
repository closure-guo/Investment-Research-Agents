from pathlib import Path

import pytest
from evals.causal_ablation.preregister import (
    OUTCOME_REQUIRED_FIELDS,
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


class TestOutcomeGate:
    """outcome 收益评估门禁字段（metrics.md §1.8）：无 rubric、增成本分型与泄漏控制。"""

    OUTCOME_GOOD = """# Outcome 预登记
- 主指标: T+20 相对 000300 超额收益
- MDE: 均值 5.1pp@n=30
- 决策阈值: CI 判定，下限>0 显著为正；依据：单样本 CI 换算见 §4
- 样本量依据: n>=30 可执行 settled
- 停止规则: 健康检查不过作废
- 成本分型: forward 166k tokens/标的
- 泄漏控制: 探针阈值 60%
"""

    def test_outcome_fields_valid(self):
        p = parse_preregister(self.OUTCOME_GOOD, required_fields=OUTCOME_REQUIRED_FIELDS)
        assert p.valid is True, p.issues

    def test_outcome_missing_leakage_field_reported(self):
        text = self.OUTCOME_GOOD.replace("- 泄漏控制: 探针阈值 60%\n", "")
        p = parse_preregister(text, required_fields=OUTCOME_REQUIRED_FIELDS)
        assert any("泄漏控制" in i for i in p.issues)

    def test_default_required_fields_unchanged(self):
        """因果消融默认门禁不变：outcome 文档按默认口径应报缺 rubric 版本。"""
        p = parse_preregister(self.OUTCOME_GOOD)
        assert any("rubric 版本" in i for i in p.issues)

    def test_real_outcome_preregister_document_valid(self):
        root = Path(__file__).resolve().parents[3]
        doc = root / "evals/ablation/preregister/2026-09-23-outcome-forward-and-backtest.md"
        p = parse_preregister(
            doc.read_text(encoding="utf-8"), required_fields=OUTCOME_REQUIRED_FIELDS
        )
        assert p.valid is True, p.issues


class TestOutcomeLookup:
    def test_name_contains_isolates_outcome_docs(self, tmp_path: Path):
        (tmp_path / "2026-09-17-p2-family-b.md").write_text(
            "# P2\n- 主指标: B1\n", encoding="utf-8"
        )
        (tmp_path / "2026-09-23-outcome-forward-and-backtest.md").write_text(
            TestOutcomeGate.OUTCOME_GOOD, encoding="utf-8"
        )
        found = find_latest_preregister(
            tmp_path, name_contains="outcome", required_fields=OUTCOME_REQUIRED_FIELDS
        )
        assert found is not None
        assert found.path.name == "2026-09-23-outcome-forward-and-backtest.md"
        assert found.valid is True
