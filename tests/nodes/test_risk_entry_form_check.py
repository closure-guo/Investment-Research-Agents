"""buy 入场形态软标注（update-conditional-entry-gate-waiver）。

601066（2026-11-11）实证：技术逆势（收盘 < MA20 且 < MA60）+ 窗口错配场景输出
立即市价 buy 且 trigger 字段均为 null。软标注（I-1 模式）：不打回、不阻断，
`final_entry_form_check` 如实标注进 FM 审批上下文。
"""

import json
from unittest.mock import patch

from finance_agent.nodes.risk import risk_judge


def _buy_resp(**extra: object) -> str:
    return json.dumps(
        {
            "action": "buy",
            "confidence": 0.6,
            "reasoning": "方向成立",
            "position_size": "light",
            "entry_price": 21.89,
            "stop_loss": 21.0,
            "target_price": 23.0,
            # 触发条件一并申报，避免误入再评估触发条件打回回路（既有测试同款）
            "reeval_triggers": ["跌破 21.0 止损离场"],
            **extra,
        },
        ensure_ascii=False,
    )


def _ma_indicators(ma20: float, ma60: float) -> dict:
    """calc_technical() 形态的最小 MA 载体（序列末值即最新值）。"""
    return {
        "MA": {
            "20": [None, ma20],
            "60": [None, ma60],
        }
    }


def _state(ma20: float | None = None, ma60: float | None = None) -> dict:
    """最小 state：收盘价经 price_levels.entry_ref（_latest_close_for_price_check 回落级）。

    stop_band_long 覆盖 fixture 触发词里的 21.0，避免误入价位门禁打回回路。
    """
    state: dict = {
        "trader_plan": {},
        "risk_debate_history": [],
        "price_levels": {
            "available": True,
            "entry_ref": 21.89,
            "stop_band_long": {"low": 20.5, "high": 21.5},
        },
    }
    if ma20 is not None and ma60 is not None:
        state["technical_indicators"] = _ma_indicators(ma20=ma20, ma60=ma60)
    return state


class TestBuyEntryFormCheck:
    @patch("finance_agent.nodes._llm_utils.call_llm_streaming")
    def test_adverse_immediate_entry_annotated(self, mock_llm):
        """逆势（收盘 < MA20 且 < MA60）+ 无触发位 → 软标注，不打回。"""
        mock_llm.return_value = _buy_resp()
        result = risk_judge(_state(ma20=22.5, ma60=23.0))
        check = result["final_entry_form_check"]
        assert check["result"] == "pass"
        assert "逆势立即入场" in check["note"]
        assert "MA20" in check["note"]
        # I-1：不打回（单次 LLM 调用）、不虚构触发位
        mock_llm.assert_called_once()

    @patch("finance_agent.nodes._llm_utils.call_llm_streaming")
    def test_conditional_entry_not_annotated(self, mock_llm):
        """已申报条件触发位（回踩承接 trigger_low）→ 不标注。"""
        mock_llm.return_value = _buy_resp(trigger_low=21.4)
        result = risk_judge(_state(ma20=22.5, ma60=23.0))
        assert result["final_entry_form_check"] == {"result": "pass", "note": ""}

    @patch("finance_agent.nodes._llm_utils.call_llm_streaming")
    def test_not_adverse_not_annotated(self, mock_llm):
        """非逆势（收盘高于 MA20）→ 不标注。"""
        mock_llm.return_value = _buy_resp()
        result = risk_judge(_state(ma20=21.0, ma60=22.0))
        assert result["final_entry_form_check"] == {"result": "pass", "note": ""}

    @patch("finance_agent.nodes._llm_utils.call_llm_streaming")
    def test_missing_indicators_skipped(self, mock_llm):
        """技术指标不可用 → 跳过（不凭空报注）。"""
        mock_llm.return_value = _buy_resp()
        result = risk_judge(_state())
        assert result["final_entry_form_check"] == {"result": "pass", "note": ""}

    @patch("finance_agent.nodes._llm_utils.call_llm_streaming")
    def test_non_buy_actions_skipped(self, mock_llm):
        """sell/watch/hold → 不适用（回踩承接语义仅 buy）。"""
        mock_llm.return_value = _buy_resp(action="watch", trigger_high=22.5)
        result = risk_judge(_state(ma20=22.5, ma60=23.0))
        assert result["final_entry_form_check"] == {"result": "pass", "note": ""}


def test_state_annotation_declared():
    """state.py 注解声明（未声明会被图合并静默丢弃——add-watch-trigger-tracking 先例）。"""
    from finance_agent.state import AnalysisState

    hints = AnalysisState.__annotations__
    assert "final_entry_form_check" in hints
