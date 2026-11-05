"""零买入消融运行器单测（2026-11-06 零买入修复 Task 3）。

只测注入器职责：patch 生效与还原、库路径隔离硬校验、变体文件名→prompt 名映射。
不真跑管线（冒烟由 scripts/run_zero_buy_ablation.py 在隔离库上手工执行）。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from scripts.run_zero_buy_ablation import assert_isolated_db, parse_variant_mapping, patch_prompts


def test_patch_overrides_trader_prompt_then_restores(tmp_path):
    variant = tmp_path / "trader.md"
    variant.write_text("VARIANT-TRADER", encoding="utf-8")
    import finance_agent.nodes.trader as trader_mod

    original = trader_mod.load_prompt_with_meta
    with patch_prompts(tmp_path, {"trader": "trader.md"}):
        assert trader_mod.load_prompt_with_meta("trader").template == "VARIANT-TRADER"
    assert trader_mod.load_prompt_with_meta is original


def test_patch_overrides_risk_judge_prompt_then_restores(tmp_path):
    variant = tmp_path / "risk_judge.md"
    variant.write_text("VARIANT-RISK", encoding="utf-8")
    import finance_agent.nodes.risk as risk_mod

    original = risk_mod.load_prompt_with_meta
    with patch_prompts(tmp_path, {"risk_judge": "risk_judge.md"}):
        assert risk_mod.load_prompt_with_meta("risk_judge").template == "VARIANT-RISK"
    assert risk_mod.load_prompt_with_meta is original


def test_patch_falls_back_to_real_loader_for_unpatched_names(tmp_path):
    variant = tmp_path / "trader.md"
    variant.write_text("VARIANT-TRADER", encoding="utf-8")
    import finance_agent.nodes.trader as trader_mod

    with patch_prompts(tmp_path, {"trader": "trader.md"}):
        info = trader_mod.load_prompt_with_meta("research_manager")
        assert info.prompt_name == "research_manager"


def test_refuses_production_db(tmp_path):
    with pytest.raises(RuntimeError):
        assert_isolated_db(Path("data/sessions.db"))
    assert assert_isolated_db(tmp_path / "x.db") == (tmp_path / "x.db").resolve()


def test_variant_mapping_parses_prefixes():
    assert parse_variant_mapping([Path("evals/variants/zero_buy/trader_no_catalyst.md")]) == {
        "trader": "trader_no_catalyst.md"
    }
    assert parse_variant_mapping(
        [Path("d/trader_no_contract.md"), Path("d/risk_judge_no_contract.md")]
    ) == {"trader": "trader_no_contract.md", "risk_judge": "risk_judge_no_contract.md"}


def test_variant_mapping_rejects_unknown_prefix_and_duplicates():
    with pytest.raises(ValueError, match="trader/risk_judge"):
        parse_variant_mapping([Path("foo.md")])
    with pytest.raises(ValueError, match="重复"):
        parse_variant_mapping([Path("trader_a.md"), Path("trader_b.md")])
