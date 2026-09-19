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
    assert "只能引用输入数据段" in text  # data 型锚点限输入数据段内联的 state 键路径
    assert "禁止" in text and "伪造" in text  # 禁止为推断伪造锚点
    assert "inference" in text  # 拿不准标 inference


@pytest.mark.parametrize("name", PROMPTS)
def test_assertion_level_anchor_rule_present(name):
    """eval-driven-contract-fixes 任务 4：data 论点逐断言锚定 + 推断冒充数据反例判例。

    依据 grounding 扫描（2026-09-18 owner 终裁 1.000）：8/64 条 kind=data 论点含摘要
    无源断言（推断冒充数据）——一条论点多个事实断言时，无法逐断言锚定的部分必须拆条
    或整条标 inference。"""
    text = (ROOT / name).read_text(encoding="utf-8")
    assert "每个事实断言" in text, f"{name} 缺逐断言锚定要求"
    assert "拆分" in text or "拆条" in text, f"{name} 缺多断言拆条指引"
    assert "资金" in text and "inference" in text, f"{name} 缺推断冒充数据反例（资金流类断言）"
