"""决策契约解析器单测（enforce-decision-contract-at-execution Task 1）。"""

from finance_agent.execution.contracts import (
    POSITION_TIER_WEIGHTS,
    parse_first_batch_ratio,
    parse_position_tier,
)


def test_tier_anchors_match_trader_prompt_lower_bounds():
    """锚值与 trader.md:117 档位表下沿逐字对齐（light 10-20/moderate 30-50/heavy 50+）。"""
    assert POSITION_TIER_WEIGHTS == {"light": 0.10, "moderate": 0.30, "heavy": 0.50}


def test_parse_position_tier_known_and_normalized():
    assert parse_position_tier("light") == "light"
    assert parse_position_tier(" Light ") == "light"
    assert parse_position_tier("MODERATE") == "moderate"


def test_parse_position_tier_unknown_returns_none():
    assert parse_position_tier("30%") is None  # 百分比串非档位名（落库 REAL 口径）
    assert parse_position_tier("unknown") is None
    assert parse_position_tier(None) is None
    assert parse_position_tier(123) is None


def test_parse_first_batch_ratio_single_match():
    # 601066 2026-11-12 真实方案文本形态（首批…70%，中间含其他数字）
    assert parse_first_batch_ratio("首批在现价 21.72 附近即减持有敞口的 70%（较原方案 50% 上调）") == 0.7
    assert parse_first_batch_ratio("首批减仓 30%，余下等反弹") == 0.3


def test_parse_first_batch_ratio_ambiguous_returns_none():
    assert parse_first_batch_ratio("首批 70%，另一方案首批 50%") is None


def test_parse_first_batch_ratio_invalid_inputs_return_none():
    assert parse_first_batch_ratio(None) is None
    assert parse_first_batch_ratio("") is None
    assert parse_first_batch_ratio("一次性清仓") is None  # 无首批表述
    assert parse_first_batch_ratio("首批 150%") is None  # 越界
    assert parse_first_batch_ratio("首批 0%") is None  # 越界


def test_parse_first_batch_ratio_clamps_to_ratio():
    assert parse_first_batch_ratio("首批 100% 清仓") == 1.0


from finance_agent.outcome.track_record.ingest import _declared_contract


def test_declared_contract_freezes_all_fields_for_buy():
    decision = {
        "sell_type": None,
        "position_size": "light",
        "exit_schedule": None,
        "trigger_high": 22.35,
        "trigger_low": None,
    }
    contract = _declared_contract(decision, action="buy")
    assert contract == {
        "sell_type": None,
        "position_size": "light",
        "exit_schedule": None,
        "trigger_high": 22.35,
        "trigger_low": None,
    }


def test_declared_contract_triggers_only_for_buy():
    decision = {"sell_type": "exit", "position_size": None,
                "exit_schedule": "首批减 70%", "trigger_high": 22.35, "trigger_low": 20.0}
    assert _declared_contract(decision, action="sell")["trigger_high"] is None
    assert _declared_contract(decision, action="sell")["exit_schedule"] == "首批减 70%"
    assert _declared_contract(decision, action="watch")["trigger_high"] is None
