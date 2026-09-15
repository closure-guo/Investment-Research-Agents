"""add-debate-argument-anchors Task 3：节点接入（不触网）。"""

from unittest.mock import patch

from finance_agent.nodes.debate import bull_debater
from finance_agent.nodes.risk import aggressive_debater


def _fake_llm(data):
    def _call(ctx, **kwargs):
        return data

    return _call


class TestNodeWiring:
    def test_bull_returns_anchor_checks(self):
        payload = {
            "role": "bull",
            "round": 1,
            "content": "x",
            "key_arguments": [
                {"text": "MA5 上穿", "kind": "data", "anchors": ["technical_indicators.MA.5.-1"]},
                {"text": "龙头受益", "kind": "inference", "anchors": []},
            ],
        }
        state = {"technical_indicators": {"MA": {"5": [1.0, 2.0]}}}
        with patch("finance_agent.nodes.debate.call_llm_for_json", _fake_llm(payload)):
            out = bull_debater(state)
        checks = out["debate_anchor_checks"]
        assert [c["status"] for c in checks] == ["resolved", "none"]
        assert out["debate_history"][0].key_arguments[1].text == "龙头受益"

    def test_risk_debater_returns_anchor_checks(self):
        payload = {
            "role": "aggressive",
            "round": 1,
            "content": "x",
            "key_arguments": [{"text": "超卖反弹", "kind": "inference", "anchors": []}],
        }
        with patch("finance_agent.nodes.risk.call_llm_for_json", _fake_llm(payload)):
            out = aggressive_debater({"risk_metrics": {"max_drawdown": -0.2}})
        assert "risk_debate_history" in out and len(out["debate_anchor_checks"]) == 1
