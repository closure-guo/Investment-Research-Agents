"""buy/sell 终稿 reeval_triggers 必填化（agent-node-contracts 2026-09 扩展，601818 实证）。"""
import json
from unittest.mock import patch

from finance_agent.models import TradeDecision
from finance_agent.nodes.validate import exec_reeval_missing


def _decision(action="sell", reeval=None):
    return TradeDecision(
        action=action,
        confidence=0.55,
        reasoning="r",
        entry_price=2.99 if action in ("buy", "sell") else None,
        stop_loss=3.12 if action in ("buy", "sell") else None,
        target_price=2.9 if action in ("buy", "sell") else None,
        reeval_triggers=reeval if reeval is not None else [],
    )


def test_exec_decision_with_empty_reeval_is_missing():
    missing = exec_reeval_missing(_decision(reeval=[]))
    assert missing == ["reeval_triggers"]


def test_exec_decision_with_triggers_passes():
    assert exec_reeval_missing(_decision(reeval=["跌破 22.94 则止损离场"])) == []


def test_watch_decision_not_constrained_by_exec_check():
    # watch/hold 的 reeval 由既有 inaction 检查管，本检查不重复约束
    assert exec_reeval_missing(_decision(action="watch")) == []


def test_noise_shapes_cleaned_to_empty_then_missing():
    d = TradeDecision(
        action="buy",
        confidence=0.6,
        reasoning="r",
        entry_price=1.0,
        stop_loss=0.9,
        target_price=1.2,
        reeval_triggers=[None, 123],  # 全部非法条目 → 清洗后 [] → 缺失
    )
    assert exec_reeval_missing(d) == ["reeval_triggers"]


class TestFinalReevalCheckWiring:
    """risk_judge 终稿检查链：buy/sell 缺 reeval 打回一次，仍缺放行 + note 标注。"""

    def _run_node(self, llm_outputs):
        import finance_agent.nodes.risk as risk_mod

        # call_llm_for_json 契约：返回解析后的 dict（risk.py 拿它直接
        # TradeDecision.model_validate，不吃 JSON 字符串），fixture 在 fake 内解析
        outputs = list(llm_outputs)

        def fake_llm(context, **kwargs):
            return json.loads(outputs.pop(0))

        base = {
            "risk_debate_history": [{"role": "conservative", "round": 1, "content": "c"}],
            "llm_config": None,
            "stock_code": "601818",
        }
        with patch.object(risk_mod, "call_llm_for_json", side_effect=fake_llm):
            return risk_mod.risk_judge(dict(base))

    def test_buy_sell_missing_reeval_bounces_once_then_releases_with_note(self):
        first = {
            "action": "sell",
            "confidence": 0.55,
            "reasoning": "r1",
            "position_size": "light",
            "entry_price": 2.99,
            "stop_loss": 3.12,
            "target_price": 2.9,
            "reeval_triggers": [],
        }
        second = dict(first, reasoning="r2", reeval_triggers=["反抽至均线加仓"])
        result = self._run_node([json.dumps(first), json.dumps(second)])
        check = result["final_reeval_check"]
        assert check["result"] == "pass"
        assert "打回" in check["note"]
        assert result["final_trade_decision"].reeval_triggers == ["反抽至均线加仓"]

    def test_buy_sell_missing_reeval_after_retry_releases_with_note(self):
        out = {
            "action": "sell",
            "confidence": 0.55,
            "reasoning": "r",
            "position_size": "light",
            "entry_price": 2.99,
            "stop_loss": 3.12,
            "target_price": 2.9,
            "reeval_triggers": [],
        }
        result = self._run_node([json.dumps(out), json.dumps(out)])
        check = result["final_reeval_check"]
        assert check["result"] == "released"
        assert "已打回仍未申报" in check["note"]
        assert result["final_trade_decision"].reeval_triggers == []

    def test_buy_sell_with_reeval_passes_without_bounce(self):
        out = {
            "action": "sell",
            "confidence": 0.55,
            "reasoning": "r",
            "position_size": "light",
            "entry_price": 2.99,
            "stop_loss": 3.12,
            "target_price": 2.9,
            "reeval_triggers": ["跌破 2.94 转空评估"],
        }
        result = self._run_node([json.dumps(out)])
        assert result["final_reeval_check"] == {"result": "pass", "note": ""}
