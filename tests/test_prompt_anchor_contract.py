"""add-debate-argument-anchors Task 4：辩手 prompt 锚点申报纪律契约。"""

from pathlib import Path

import pytest

PROMPTS = ["bull_debater.md", "bear_debater.md", "risk_debater.md"]
ROOT = Path(__file__).resolve().parent.parent / "src" / "finance_agent" / "prompts"


@pytest.mark.parametrize("name", PROMPTS)
def test_schema_is_structured(name):
    text = (ROOT / name).read_text(encoding="utf-8")
    assert '"kind"' in text and '"anchors"' in text, f"{name} 的 key_arguments 示例未结构化"


@pytest.mark.parametrize("name", PROMPTS)
def test_anchor_discipline_present(name):
    text = (ROOT / name).read_text(encoding="utf-8")
    assert "data 型" in text and "field_ref" in text  # data 型必须附 field_ref
    assert "禁止" in text and "伪造" in text  # 禁止为推断伪造锚点
    assert "inference" in text  # 拿不准标 inference
