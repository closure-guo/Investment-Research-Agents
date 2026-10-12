"""TDD tests for nodes/risk.py — Layer IV Risk Management 辩论 + Judge。"""

import json
from unittest.mock import patch

import pytest
from pydantic import ValidationError

from finance_agent.nodes.report import _fm_incomplete_integrity_block
from finance_agent.nodes.risk import (
    aggressive_debater,
    conservative_debater,
    neutral_debater,
    risk_judge,
)


def _mock_risk_msg(role: str) -> str:
    return json.dumps(
        {
            "role": role,
            "round": 1,
            "content": f"{role} 视角的风险分析",
            "key_arguments": ["论点1"],
        },
        ensure_ascii=False,
    )


def _mock_judge_response() -> str:
    return json.dumps(
        {
            "action": "buy",
            "confidence": 0.6,
            "reasoning": "风险可控，建议轻仓买入",
            "position_size": "light",
        },
        ensure_ascii=False,
    )


class TestRiskDebaters:
    """Layer IV 风险辩论 Agent。"""

    @patch("finance_agent.nodes._llm_utils.call_llm_streaming")
    def test_aggressive_debater(self, mock_llm):
        mock_llm.return_value = _mock_risk_msg("aggressive")
        result = aggressive_debater({"risk_debate_history": [], "trader_plan": {}})
        msg = result["risk_debate_history"][0]
        assert msg.role == "aggressive"

    @patch("finance_agent.nodes._llm_utils.call_llm_streaming")
    def test_conservative_debater(self, mock_llm):
        mock_llm.return_value = _mock_risk_msg("conservative")
        result = conservative_debater({"risk_debate_history": [], "trader_plan": {}})
        msg = result["risk_debate_history"][0]
        assert msg.role == "conservative"

    @patch("finance_agent.nodes._llm_utils.call_llm_streaming")
    def test_neutral_debater(self, mock_llm):
        mock_llm.return_value = _mock_risk_msg("neutral")
        result = neutral_debater({"risk_debate_history": [], "trader_plan": {}})
        msg = result["risk_debate_history"][0]
        assert msg.role == "neutral"


class TestRiskJudge:
    """Layer IV Risk Judge — 最终交易决策。"""

    @patch("finance_agent.nodes._llm_utils.call_llm_streaming")
    def test_produces_final_decision(self, mock_llm):
        mock_llm.return_value = _mock_judge_response()
        state = {
            "trader_plan": {"action": "buy", "confidence": 0.75},
            "risk_debate_history": [],
        }
        result = risk_judge(state)
        assert "final_trade_decision" in result
        decision = result["final_trade_decision"]
        assert decision.action == "buy"
        assert decision.confidence == 0.6


class TestRiskFieldValidation:
    """role 与 confidence 约束（harden-llm-output-validation）。"""

    @patch("finance_agent.nodes._llm_utils.call_llm_streaming")
    def test_invalid_role_raises(self, mock_llm):
        """风控辩论者输出非法 role 时抛 ValidationError。"""
        mock_llm.return_value = _mock_risk_msg("激进派")  # 非法：中文角色名
        with pytest.raises(ValidationError):
            aggressive_debater({"risk_debate_history": [], "trader_plan": {}})

    @patch("finance_agent.nodes._llm_utils.call_llm_streaming")
    def test_judge_confidence_out_of_range_raises(self, mock_llm):
        """Risk Judge 返回百分数置信度时抛 ValidationError。"""
        mock_llm.return_value = json.dumps(
            {"action": "buy", "confidence": 95, "reasoning": "按百分数返回"},
            ensure_ascii=False,
        )
        with pytest.raises(ValidationError):
            risk_judge({"trader_plan": {}, "risk_debate_history": []})


class TestRiskContextCarriesTraderPlan:
    """_build_risk_context 必须携带 trader 方案（含 evidence_refs）——final review F1。"""

    def test_risk_judge_context_contains_trader_plan_when_pydantic(self):
        from finance_agent.models import TradeDecision
        from finance_agent.nodes.risk import _build_risk_context

        plan = TradeDecision.model_validate(
            {
                "action": "buy",
                "confidence": 0.75,
                "reasoning": "理由",
                "evidence_refs": [{"claim": "ROE 3.4%", "source": "fundamental"}],
            }
        )
        context = _build_risk_context({"trader_plan": plan})
        assert "交易方案" in context
        assert "evidence_refs" in context
        assert "fundamental" in context


class TestDerivedMetricsInjection:
    """deterministic-derived-metrics：派生指标行注入风险辩论/裁决 context。

    代码计算的止损距离与赔率随 context 下发，辩论方直接引用不重算；
    指标缺失（None）或 state 无该键时不注入（watch/hold、参数缺失形态）。
    """

    def _plan(self):
        from finance_agent.models import TradeDecision

        return TradeDecision.model_validate(
            {"action": "buy", "confidence": 0.75, "reasoning": "理由"}
        )

    def test_context_contains_derived_metrics_line(self):
        from finance_agent.nodes.risk import _build_risk_context

        state = {
            "trader_plan": self._plan(),
            "derived_metrics": {
                "stop_distance_pct": 0.05,
                "risk_reward_ratio": 1.6,
                "missing_reason": None,
            },
        }
        ctx = _build_risk_context(state)
        assert "派生指标（代码计算）" in ctx
        assert "止损距离 5.0%" in ctx
        assert "赔率 1.60:1" in ctx
        assert "MUST NOT 自行重算或改写" in ctx

    def test_missing_values_not_injected(self):
        from finance_agent.nodes.risk import _build_risk_context

        state = {
            "trader_plan": self._plan(),
            "derived_metrics": {
                "stop_distance_pct": None,
                "risk_reward_ratio": None,
                "missing_reason": "stop 缺失",
            },
        }
        assert "派生指标" not in _build_risk_context(state)

    def test_no_key_not_injected(self):
        from finance_agent.nodes.risk import _build_risk_context

        assert "派生指标" not in _build_risk_context({"trader_plan": self._plan()})


class TestFinalPriceIntegrity:
    """终稿价位完整性（extend-payout-self-check-coverage，601888 实证：buy 价位
    全 None 直通管线）：buy/sell 缺失首次打回重试一次；仍缺放行 + 如实标注。"""

    @staticmethod
    def _resp(action: str = "buy", **prices: object) -> str:
        return json.dumps(
            {
                "action": action,
                "confidence": 0.6,
                "reasoning": "风险可控",
                "position_size": "light",
                **prices,
            },
            ensure_ascii=False,
        )

    @patch("finance_agent.nodes._llm_utils.call_llm_streaming")
    def test_buy_missing_price_first_retry_fills(self, mock_llm):
        """首次缺价位 → 打回重试一次（第二次补齐）。"""
        mock_llm.side_effect = [
            self._resp(),  # 第一次：无价位
            # 第二次：补齐价位；触发条件一并申报，避免误入再评估触发条件打回回路
            self._resp(
                entry_price=51.05,
                stop_loss=49.5,
                target_price=54.5,
                reeval_triggers=["跌破 49.5 元止损离场"],
            ),
        ]
        result = risk_judge({"trader_plan": {}, "risk_debate_history": []})
        assert mock_llm.call_count == 2
        # 打回反馈拼进重试（第二次）调用的 context
        assert "价位完整性打回" in mock_llm.call_args_list[1].args[0]
        decision = result["final_trade_decision"]
        assert decision.entry_price == 51.05
        assert result["final_price_check"] == {"result": "pass", "note": "打回后已申报"}

    @patch("finance_agent.nodes._llm_utils.call_llm_streaming")
    def test_retry_exhausted_passes_with_note(self, mock_llm):
        """重试仍缺 → 放行（不虚构价位）+ 如实标注。"""
        # 触发条件申报齐备，隔离价位回路（再评估触发条件打回由 TestFinalReevalCheck 覆盖）
        mock_llm.return_value = self._resp(reeval_triggers=["跌破止损位离场"])
        result = risk_judge({"trader_plan": {}, "risk_debate_history": []})
        assert mock_llm.call_count == 2
        assert result["final_trade_decision"].entry_price is None
        assert result["final_price_check"]["result"] == "pass"
        assert "已打回仍未申报" in result["final_price_check"]["note"]
        assert "entry_price" in result["final_price_check"]["note"]

    @patch("finance_agent.nodes._llm_utils.call_llm_streaming")
    def test_complete_prices_no_extra_call(self, mock_llm):
        mock_llm.return_value = self._resp(
            entry_price=26.35,
            stop_loss=25.3,
            target_price=28.0,
            reeval_triggers=["跌破止损位离场"],
        )
        result = risk_judge({"trader_plan": {}, "risk_debate_history": []})
        assert mock_llm.call_count == 1
        assert result["final_price_check"] == {"result": "pass", "note": ""}

    @patch("finance_agent.nodes._llm_utils.call_llm_streaming")
    def test_watch_no_price_requirement(self, mock_llm):
        # watch 无价位要求，但 require-watch-hold-rationale 要求理由字段齐备
        # （否则理由回路会追加一次重试，call_count 变 2）；触发位申报齐备，
        # 隔离触发位申报打回回路（add-watch-trigger-tracking）
        mock_llm.return_value = self._resp(
            action="watch",
            inaction_reason="多因素均衡，等待信号",
            reeval_triggers=["关键指标显著变化"],
            trigger_high=28.5,
            trigger_low=24.0,
        )
        result = risk_judge({"trader_plan": {}, "risk_debate_history": []})
        assert mock_llm.call_count == 1
        assert result["final_price_check"] == {"result": "pass", "note": ""}
        assert result["final_inaction_check"] == {"result": "pass", "note": ""}

    def test_final_price_missing_helper(self):
        from finance_agent.models import TradeDecision
        from finance_agent.nodes.validate import final_price_missing

        buy = TradeDecision.model_validate({"action": "buy", "confidence": 0.6, "reasoning": "r"})
        assert final_price_missing(buy) == ["entry_price", "stop_loss", "target_price"]
        full = TradeDecision.model_validate(
            {
                "action": "buy",
                "confidence": 0.6,
                "reasoning": "r",
                "entry_price": 10.0,
                "stop_loss": 9.0,
                "target_price": 12.0,
            }
        )
        assert final_price_missing(full) == []
        watch = TradeDecision.model_validate(
            {"action": "watch", "confidence": 0.6, "reasoning": "r"}
        )
        assert final_price_missing(watch) == []


class TestFinalInactionRationale:
    """require-watch-hold-rationale：终稿 watch/hold 理由完整性回路。

    复用 TestFinalPriceIntegrity._resp（staticmethod，字段透传），不复制副本：
    两条终稿回路共用同一响应构造器，字段语义保持单点定义。
    """

    @patch("finance_agent.nodes._llm_utils.call_llm_streaming")
    def test_watch_with_rationale_no_extra_call(self, mock_llm):
        mock_llm.return_value = TestFinalPriceIntegrity._resp(
            action="watch",
            inaction_reason="估值分位偏高且缺催化剂",
            reeval_triggers=["价格回落至 1500 以下"],
            # 触发位申报齐备，隔离触发位申报打回回路（add-watch-trigger-tracking）
            trigger_high=28.5,
            trigger_low=24.0,
        )
        result = risk_judge({"trader_plan": {}, "risk_debate_history": []})
        assert mock_llm.call_count == 1
        assert result["final_inaction_check"] == {"result": "pass", "note": ""}

    @patch("finance_agent.nodes._llm_utils.call_llm_streaming")
    def test_watch_missing_rationale_retries_then_completes(self, mock_llm):
        mock_llm.side_effect = [
            TestFinalPriceIntegrity._resp(action="watch"),
            TestFinalPriceIntegrity._resp(
                action="watch",
                inaction_reason="等待趋势确认",
                reeval_triggers=["价格站稳 60 日均线"],
                # 触发位申报齐备，隔离触发位申报打回回路（add-watch-trigger-tracking）
                trigger_high=28.5,
                trigger_low=24.0,
            ),
        ]
        result = risk_judge({"trader_plan": {}, "risk_debate_history": []})
        assert mock_llm.call_count == 2
        # 打回反馈拼进重试（第二次）调用的 context
        assert "非执行动作理由打回" in mock_llm.call_args_list[1].args[0]
        assert result["final_inaction_check"] == {"result": "pass", "note": "打回后已申报"}

    @patch("finance_agent.nodes._llm_utils.call_llm_streaming")
    def test_retry_exhausted_passes_with_note(self, mock_llm):
        # 理由持续缺失（本回路被测形态）；触发位申报齐备，隔离触发位申报打回回路
        # （add-watch-trigger-tracking），call_count 停在理由回路的 2 次
        mock_llm.return_value = TestFinalPriceIntegrity._resp(
            action="watch", trigger_high=28.5, trigger_low=24.0
        )
        result = risk_judge({"trader_plan": {}, "risk_debate_history": []})
        assert mock_llm.call_count == 2
        assert result["final_inaction_check"]["result"] == "pass"
        assert "已打回仍未申报" in result["final_inaction_check"]["note"]
        assert "inaction_reason" in result["final_inaction_check"]["note"]

    @patch("finance_agent.nodes._llm_utils.call_llm_streaming")
    def test_buy_unaffected(self, mock_llm):
        mock_llm.return_value = TestFinalPriceIntegrity._resp(
            entry_price=26.35,
            stop_loss=25.3,
            target_price=28.0,
            reeval_triggers=["跌破止损位离场"],
        )
        result = risk_judge({"trader_plan": {}, "risk_debate_history": []})
        assert mock_llm.call_count == 1
        assert result["final_inaction_check"] == {"result": "pass", "note": ""}

    @patch("finance_agent.nodes._llm_utils.call_llm_streaming")
    def test_inaction_retry_to_buy_rechecks_price_conclusion(self, mock_llm):
        """终审 I-1 主场景：理由重试使终稿换代（watch→buy），价位结论作废必须复核改注。

        两段式最小复现：第 1 次 watch 缺理由（价位块不触发，价位 note 为空 pass），
        理由重试返回无价位的 buy——若不复核，终稿是无价位 buy 却仍报 pass 空注（假阳性）。
        """
        mock_llm.side_effect = [
            TestFinalPriceIntegrity._resp(action="watch"),
            # 理由重试换代为 buy：价位缺失；触发条件申报齐备，隔离再评估触发条件打回回路
            TestFinalPriceIntegrity._resp(action="buy", reeval_triggers=["跌破止损位离场"]),
        ]
        result = risk_judge({"trader_plan": {}, "risk_debate_history": []})
        assert mock_llm.call_count == 2
        assert result["final_trade_decision"].action == "buy"
        note = result["final_price_check"]["note"]
        assert "理由重试后终稿价位缺失" in note
        assert "entry_price" in note

    @patch("finance_agent.nodes._llm_utils.call_llm_streaming")
    def test_price_retry_to_watch_annotated_as_not_applicable(self, mock_llm):
        """终审 I-1 反向：价位重试输出 watch（带完整理由）→ 如实标注非执行动作。

        不得沿用「打回后已申报」——该文案对非执行动作是错话；理由块此时不应再打回
        （watch 理由齐备），故 call_count 停在第 2 次。
        """
        mock_llm.side_effect = [
            TestFinalPriceIntegrity._resp(action="buy"),
            TestFinalPriceIntegrity._resp(
                action="watch",
                inaction_reason="等待趋势确认",
                reeval_triggers=["价格站稳 60 日均线"],
                # 触发位申报齐备，隔离触发位申报打回回路（add-watch-trigger-tracking）
                trigger_high=28.5,
                trigger_low=24.0,
            ),
        ]
        result = risk_judge({"trader_plan": {}, "risk_debate_history": []})
        assert mock_llm.call_count == 2
        assert result["final_trade_decision"].action == "watch"
        assert result["final_price_check"]["note"] == "打回后改为非执行动作（价位不适用）"

    @patch("finance_agent.nodes._llm_utils.call_llm_streaming")
    def test_double_retry_to_watch_annotates_price_note_accurately(self, mock_llm):
        """双翻转终态为非执行动作：价位结论注记须精确（评审 Minor 收口）。

        序：buy 缺价位 →（价位重试）watch 缺理由 →（理由重试）watch 带理由。
        终稿为 watch 时不得声称「价位齐备」（对非执行动作是错话）——如实标注改为非执行。
        """
        mock_llm.side_effect = [
            TestFinalPriceIntegrity._resp(action="buy"),
            TestFinalPriceIntegrity._resp(action="watch"),
            TestFinalPriceIntegrity._resp(
                action="watch",
                inaction_reason="等待趋势确认",
                reeval_triggers=["价格站稳 60 日均线"],
                # 触发位申报齐备，隔离触发位申报打回回路（add-watch-trigger-tracking）
                trigger_high=28.5,
                trigger_low=24.0,
            ),
        ]
        result = risk_judge({"trader_plan": {}, "risk_debate_history": []})
        assert mock_llm.call_count == 3
        note = result["final_price_check"]["note"]
        assert "非执行动作" in note
        assert "价位齐备" not in note

    @patch("finance_agent.nodes._llm_utils.call_llm_streaming")
    def test_double_retry_to_buy_keeps_complete_price_wording(self, mock_llm):
        """双翻转终态为执行动作且价位齐备：仍须如实声称「价位齐备」（措辞精确化的另一半）。"""
        mock_llm.side_effect = [
            TestFinalPriceIntegrity._resp(action="buy"),
            TestFinalPriceIntegrity._resp(action="watch"),
            # 第三次换代回 buy 且价位齐备；触发条件申报齐备，隔离再评估触发条件打回回路
            TestFinalPriceIntegrity._resp(
                entry_price=26.0,
                stop_loss=25.0,
                target_price=28.0,
                reeval_triggers=["跌破止损位离场"],
            ),  # 默认 action=buy，价位齐备
        ]
        result = risk_judge({"trader_plan": {}, "risk_debate_history": []})
        assert mock_llm.call_count == 3
        assert result["final_trade_decision"].action == "buy"
        assert "价位齐备" in result["final_price_check"]["note"]


class TestFinalTriggerCheck:
    """add-watch-trigger-tracking：watch 终稿双向触发位申报完整性回路。

    触发位是 watch 唯一的可执行承诺，必须结构化可追踪：两个均缺失 → 打回恰一次；
    打回后已申报放行（note「打回后已申报」），仍缺放行 + 如实标注
    （note「已打回仍未申报触发位」，MUST NOT 虚构、MUST NOT 死循环）。
    buy/sell/hold 不约束。mock 模式沿用本文件既有：patch call_llm_streaming。
    """

    @staticmethod
    def _watch_resp(**fields: object) -> str:
        """watch 完整理由申报（隔离理由回路，只测触发位申报回路）。"""
        return TestFinalPriceIntegrity._resp(
            action="watch",
            inaction_reason="等待右侧信号",
            reeval_triggers=["放量站上 20 日线"],
            **fields,
        )

    @patch("finance_agent.nodes._llm_utils.call_llm_streaming")
    def test_watch_missing_triggers_bounces_once_then_passes_with_note(self, mock_llm):
        """watch 终稿缺触发位：打回一次，补齐后放行（note=打回后已申报）。"""
        mock_llm.side_effect = [
            self._watch_resp(),  # 第一次：watch 理由齐备但双向触发位均缺失
            self._watch_resp(trigger_high=28.5, trigger_low=24.0),  # 打回后补齐
        ]
        result = risk_judge({"trader_plan": {}, "risk_debate_history": []})
        assert mock_llm.call_count == 2
        # 打回反馈拼进重试（第二次）调用的 context，点名双向触发位申报要求
        retry_context = mock_llm.call_args_list[1].args[0]
        assert "触发位申报打回" in retry_context
        assert "trigger_high" in retry_context
        assert "trigger_low" in retry_context
        decision = result["final_trade_decision"]
        assert decision.action == "watch"
        assert decision.trigger_high == 28.5
        assert decision.trigger_low == 24.0
        assert result["final_trigger_check"] == {"result": "pass", "note": "打回后已申报"}
        # 重试响应理由/触发条件齐备 → 前序结论不受打回扰动
        assert result["final_inaction_check"] == {"result": "pass", "note": ""}
        assert result["final_price_check"] == {"result": "pass", "note": ""}

    @patch("finance_agent.nodes._llm_utils.call_llm_streaming")
    def test_watch_missing_triggers_still_missing_after_retry_annotated(self, mock_llm):
        """打回后仍缺：放行 + note「已打回仍未申报触发位」，不虚构。"""
        mock_llm.return_value = self._watch_resp()
        result = risk_judge({"trader_plan": {}, "risk_debate_history": []})
        assert mock_llm.call_count == 2
        assert result["final_trade_decision"].action == "watch"
        assert result["final_trade_decision"].trigger_high is None
        assert result["final_trade_decision"].trigger_low is None
        assert result["final_trigger_check"]["result"] == "pass"
        assert result["final_trigger_check"]["note"] == "已打回仍未申报触发位"

    @patch("finance_agent.nodes._llm_utils.call_llm_streaming")
    def test_retry_flip_to_non_watch_degrades_note_without_incomplete_marker(self, mock_llm):
        """打回使终稿换代翻转动作（watch→buy）：note 如实降级，不渲染完整性标注。

        final review Finding #1：翻转后触发位申报不再适用（buy/sell/hold 不约束
        触发位），旧措辞「已打回仍未申报触发位」对非 watch 终稿是错话，且含
        _FM_INCOMPLETE_MARKERS 标记词（仍未申报）→ 误渲染「审批对象结构不完整
        标注」进 FM 上下文与报告。降级注须避开标记词表。
        """
        mock_llm.side_effect = [
            self._watch_resp(),  # 第一次：watch 理由齐备但双向触发位均缺失
            # 打回后终稿换代为 buy（价位/触发条件申报齐备，隔离价位与再评估回路）
            TestFinalPriceIntegrity._resp(
                entry_price=26.35,
                stop_loss=25.3,
                target_price=28.0,
                reeval_triggers=["跌破止损位离场"],
            ),
        ]
        result = risk_judge({"trader_plan": {}, "risk_debate_history": []})
        assert mock_llm.call_count == 2
        decision = result["final_trade_decision"]
        assert decision.action == "buy"
        assert decision.trigger_high is None
        assert decision.trigger_low is None
        assert result["final_trigger_check"]["result"] == "pass"
        assert result["final_trigger_check"]["note"] == (
            "触发位重试后终稿改为非 watch 动作，触发位申报不再适用"
        )
        # 降级注不含完整性标记词（仍未申报/缺失）→ 不渲染「审批对象结构不完整标注」
        assert _fm_incomplete_integrity_block(result) == ""

    @patch("finance_agent.nodes._llm_utils.call_llm_streaming")
    def test_buy_decision_unaffected_by_trigger_check(self, mock_llm):
        """buy/sell 终稿无触发位：final_trigger_check 不打回不标注。"""
        mock_llm.return_value = TestFinalPriceIntegrity._resp(
            entry_price=26.35,
            stop_loss=25.3,
            target_price=28.0,
            reeval_triggers=["跌破止损位离场"],
        )
        result = risk_judge({"trader_plan": {}, "risk_debate_history": []})
        mock_llm.assert_called_once()
        assert result["final_trigger_check"] == {"result": "pass", "note": ""}


def _sell_decision_json(triggers: list[str]) -> str:
    """sell 终稿 JSON：结构化价位齐全（不触发既有三回路），变盘点在 triggers。"""
    return json.dumps(
        {
            "action": "sell",
            "confidence": 0.65,
            "position_size": "light",
            "entry_price": 640.0,
            "stop_loss": 700.0,
            "target_price": 571.0,
            "sell_type": "short",
            "reasoning": "趋势走弱，减仓规避回撤",
            "reeval_triggers": triggers,
        },
        ensure_ascii=False,
    )


# 95 元为正式批实证形态（丢位幻觉）；570 为已验证近期低点（偏差 83.33% > 2% 阈值）
_BAD_TRIGGER = ["股价回落至 95 元附近再评估"]
_GOOD_TRIGGER = ["股价跌破 570 一线再评估"]
# price_levels 只给 entry_ref（兜底最新收盘 653.8）与近期低点/高点，不给参考带 → 无豁免
_PRICE_GATE_STATE = {
    "trader_plan": {"action": "sell", "confidence": 0.7},
    "risk_debate_history": [],
    "price_levels": {
        "available": True,
        "entry_ref": 653.8,
        "recent_low": 570.0,
        "recent_high": 940.0,
    },
}


class TestDecisionPriceGate:
    """update-decision-price-gate：价位交叉校验 anomaly → 打回重试一次 → 仍异常 gate fail。"""

    @patch("finance_agent.nodes._llm_utils.call_llm_streaming")
    def test_anomaly_retry_fixed_passes(self, mock_llm):
        """偏差 anomaly（95 vs 570）→ 打回一次 → 修正后 gate pass、残留清空。"""
        mock_llm.side_effect = [
            _sell_decision_json(_BAD_TRIGGER),
            _sell_decision_json(_GOOD_TRIGGER),
        ]
        result = risk_judge(dict(_PRICE_GATE_STATE))
        assert mock_llm.call_count == 2
        gate = result["decision_price_gate"]
        assert gate["result"] == "pass"
        assert gate["note"] == "打回后已修正"
        assert result["decision_price_anomalies"] == []
        assert "570" in result["final_trade_decision"].reeval_triggers[0]

    @patch("finance_agent.nodes._llm_utils.call_llm_streaming")
    def test_anomaly_persists_same_source_after_retry_passes(self, mock_llm):
        """打回后原样输出 95（同源残留，update-decision-price-gate-admission
        分层语义）→ 未恶化放行 + note 待终裁，残留 anomaly 落 state 供 trace；
        恶化阻断形态由 TestGateAdmissionLayering 覆盖。"""
        mock_llm.side_effect = [
            _sell_decision_json(_BAD_TRIGGER),
            _sell_decision_json(_BAD_TRIGGER),
        ]
        result = risk_judge(dict(_PRICE_GATE_STATE))
        assert mock_llm.call_count == 2
        gate = result["decision_price_gate"]
        assert gate["result"] == "pass"
        assert "未恶化" in gate["note"]
        assert len(result["decision_price_anomalies"]) == 1
        assert result["decision_price_anomalies"][0]["kind"] == "deviation"

    @patch("finance_agent.nodes._llm_utils.call_llm_streaming")
    def test_no_anomaly_direct_pass(self, mock_llm):
        """触发价与已验证低点一致 → 不打回（LLM 恰调用 1 次），gate 直通。"""
        mock_llm.return_value = _sell_decision_json(_GOOD_TRIGGER)
        result = risk_judge(dict(_PRICE_GATE_STATE))
        mock_llm.assert_called_once()
        assert result["decision_price_gate"] == {"result": "pass", "note": ""}

    @patch("finance_agent.nodes.risk.check_decision_prices", side_effect=RuntimeError("boom"))
    def test_validator_crash_fails_open(self, _mock_check):
        """校验器自身异常 → fail-open 放行，gate pass，MUST NOT 阻断。"""
        with patch(
            "finance_agent.nodes._llm_utils.call_llm_streaming",
            return_value=_sell_decision_json(_BAD_TRIGGER),
        ):
            result = risk_judge(dict(_PRICE_GATE_STATE))
        assert result["decision_price_gate"]["result"] == "pass"
        assert "final_trade_decision" in result

    @patch("finance_agent.nodes._llm_utils.call_llm_streaming")
    def test_price_retry_feedback_includes_source_text(self, mock_llm):
        """评审 Minor：打回反馈四要素——原始文本片段（source_text）须随 message 拼入重试 prompt。

        delta spec 要求反馈含原始文本片段/指标名/已验证值/偏差幅度；context 本身
        不含 technical_indicators/price_levels（_build_risk_context 不下发），
        故断言的片段与数值只能来自 anomaly 明细拼接。
        """
        mock_llm.side_effect = [
            _sell_decision_json(_BAD_TRIGGER),
            _sell_decision_json(_GOOD_TRIGGER),
        ]
        risk_judge(dict(_PRICE_GATE_STATE))
        retry_context = mock_llm.call_args_list[1].args[0]
        assert "股价回落至 95 元附近再评估" in retry_context  # source_text 原文片段
        assert "近期低点" in retry_context  # 指标名（message 携带）
        assert "570" in retry_context  # 已验证值
        assert "83.33" in retry_context  # 偏差幅度

    @patch("finance_agent.nodes._llm_utils.call_llm_streaming")
    def test_price_retry_feedback_includes_mechanical_rules(self, mock_llm):
        """打回反馈 MUST 附机械可执行的修正规则（601066 10-07 阻断复盘，#254 后继）。

        实证死循环：无规则时修正 LLM 补了 24.60 有效门槛却保留「站上 entry_ref
        23.03」，改写文本成为新 source_text → 恶化判据（owner 批准的精确集合
        比较）阻断。规则须说到可执行：上破严格高于/跌破严格低于收盘价、等于
        收盘价的价位（含 entry_ref）禁止留在触发条件、确认语义建议。
        """
        mock_llm.side_effect = [
            _sell_decision_json(_BAD_TRIGGER),
            _sell_decision_json(_GOOD_TRIGGER),
        ]
        risk_judge(dict(_PRICE_GATE_STATE))
        retry_context = mock_llm.call_args_list[1].args[0]
        assert "严格高于最新收盘价" in retry_context
        assert "严格低于最新收盘价" in retry_context
        assert "entry_ref" in retry_context  # 点名 601066 实证违规形态
        assert "放量突破" in retry_context  # 确认语义建议

    @patch("finance_agent.nodes._llm_utils.call_llm_streaming")
    def test_price_gate_retry_to_watch_rechecks_sibling_notes(self, mock_llm):
        """评审 Minor：价位门禁重试换代（sell→watch 缺理由）→ 兄弟结论如实改注。

        终稿换代后仅复核价位结论不够：watch 缺 inaction_reason 时
        final_inaction_check 不得停留空注假阳性；reeval_triggers 一并缺失时
        final_reeval_check 同理——如实标注「未再次打回」，MUST NOT 虚报「已申报」。
        """
        mock_llm.side_effect = [
            _sell_decision_json(_BAD_TRIGGER),
            json.dumps(
                {
                    "action": "watch",
                    "confidence": 0.6,
                    "reasoning": "趋势不明，暂观望",
                    "position_size": "light",
                },
                ensure_ascii=False,
            ),
        ]
        result = risk_judge(dict(_PRICE_GATE_STATE))
        assert mock_llm.call_count == 2
        assert result["final_trade_decision"].action == "watch"
        inaction_note = result["final_inaction_check"]["note"]
        assert "价位交叉校验重试" in inaction_note
        assert "inaction_reason" in inaction_note
        assert "未再次打回" in inaction_note
        assert "未再次打回" in result["final_reeval_check"]["note"]

    @patch("finance_agent.nodes._llm_utils.call_llm_streaming")
    def test_price_gate_retry_to_watch_missing_triggers_annotated(self, mock_llm):
        """add-watch-trigger-tracking 评审收口：门禁重试翻转型（sell→watch 缺触发位）。

        门禁重试换代发生在 final_trigger_check 主块之后：翻转出的 watch 缺
        trigger_high/trigger_low 时不得停留空注假阳性；打回预算已被门禁用掉，
        只如实标注放行（MUST NOT 再打回/死循环）。理由与再评估触发条件齐备申报，
        隔离兄弟回路（inaction/reeval 结论保持干净，锁定本注只属触发位）。
        """
        mock_llm.side_effect = [
            _sell_decision_json(_BAD_TRIGGER),
            json.dumps(
                {
                    "action": "watch",
                    "confidence": 0.6,
                    "reasoning": "趋势不明，暂观望",
                    "position_size": "light",
                    "inaction_reason": "方向未明，等待触发信号",
                    "reeval_triggers": ["趋势指标翻转后再评估"],
                },
                ensure_ascii=False,
            ),
        ]
        result = risk_judge(dict(_PRICE_GATE_STATE))
        assert mock_llm.call_count == 2  # 预算已花在门禁打回：触发位不再打回
        assert result["final_trade_decision"].action == "watch"
        assert result["final_trade_decision"].trigger_high is None
        assert result["final_trade_decision"].trigger_low is None
        assert result["final_trigger_check"]["result"] == "pass"
        note = result["final_trigger_check"]["note"]
        assert "价位交叉校验重试" in note
        assert "触发位" in note
        assert "未再次打回" in note
        # 隔离证明：翻转稿理由/触发条件齐备 → 兄弟结论不被本注污染
        assert result["final_inaction_check"]["note"] == ""
        assert result["final_reeval_check"]["note"] == ""


class TestGateAdmissionLayering:
    """update-decision-price-gate-admission（incident 034 owner 终裁已批）：
    打回后残留按恶化判据分层——同源未恶化 → 放行+标注待终裁；恶化（新增
    source 或条数增加）→ 维持阻断。"""

    def _run(self, mock_llm, first: list[str], retry: list[str]) -> dict:
        mock_llm.side_effect = [
            _sell_decision_json(first),
            _sell_decision_json(retry),
        ]
        return risk_judge(dict(_PRICE_GATE_STATE))

    @patch("finance_agent.nodes._llm_utils.call_llm_streaming")
    def test_same_source_residue_passes_with_note(self, mock_llm):
        """同源残留（LLM 坚持原文本）→ pass + note 含未恶化/放行待终裁。"""
        result = self._run(mock_llm, _BAD_TRIGGER, _BAD_TRIGGER)
        assert mock_llm.call_count == 2
        gate = result["decision_price_gate"]
        assert gate["result"] == "pass"
        assert "未恶化" in gate["note"]
        assert "放行待人工终裁" in gate["note"]
        assert len(result["decision_price_anomalies"]) == 1  # 残留照落 trace

    @patch("finance_agent.nodes._llm_utils.call_llm_streaming")
    def test_new_source_residue_fails_gate(self, mock_llm):
        """重试引入新错误文本 → 恶化 → fail。"""
        result = self._run(
            mock_llm,
            _BAD_TRIGGER,
            ["股价回落至 96 元附近再评估"],  # 新幻觉价位（新 source_text）
        )
        gate = result["decision_price_gate"]
        assert gate["result"] == "fail"
        assert "恶化" in gate["note"]
        assert len(result["decision_price_anomalies"]) >= 1

    @patch("finance_agent.nodes._llm_utils.call_llm_streaming")
    def test_more_residues_fails_gate(self, mock_llm):
        """残留条数增加（1 → 2）→ 恶化 → fail。"""
        result = self._run(
            mock_llm,
            _BAD_TRIGGER,
            _BAD_TRIGGER + ["反弹至 94 元减仓再评估"],  # 原 1 条 + 新增 1 条
        )
        gate = result["decision_price_gate"]
        assert gate["result"] == "fail"
        assert "恶化" in gate["note"]

    @patch("finance_agent.nodes._llm_utils.call_llm_streaming")
    def test_shrunk_same_source_residue_passes(self, mock_llm):
        """残留减少（2 → 1，剩余同源）→ 有改善未恶化 → pass。"""
        result = self._run(
            mock_llm,
            _BAD_TRIGGER + ["反弹至 94 元减仓再评估"],
            _BAD_TRIGGER,
        )
        gate = result["decision_price_gate"]
        assert gate["result"] == "pass"
        assert "未恶化" in gate["note"]


def _sell_untyped_json(prices: bool = True) -> str:
    """sell 未申报 sell_type 的终稿 JSON（prices=False 时价位也缺——exit 逃逸形态）。"""
    payload: dict = {
        "action": "sell",
        "confidence": 0.6,
        "position_size": "light",
        "reasoning": "趋势走弱",
        "reeval_triggers": ["跌破 570 重估"],
    }
    if prices:
        payload.update({"entry_price": 640.0, "stop_loss": 700.0, "target_price": 571.0})
    return json.dumps(payload, ensure_ascii=False)


class TestSellTypeDeclarationLoop:
    """update-sell-action-typing（#188）：sell 缺 sell_type → 打回申报一次；
    申报 exit → 价位豁免生效；仍缺 → 默认 exit（add-holdings-context 可执行默认，
    原为 short）+ final_check 如实标注。"""

    def _base_state(self) -> dict:
        return {
            "trader_plan": {"action": "sell", "confidence": 0.7},
            "risk_debate_history": [],
        }

    @patch("finance_agent.nodes._llm_utils.call_llm_streaming")
    def test_untyped_sell_declares_after_retry(self, mock_llm):
        """打回申报 → 重试带 sell_type=exit + 减仓节奏 → 放行且价位豁免生效。"""
        typed_exit = json.loads(_sell_untyped_json(prices=False))
        typed_exit.update({"sell_type": "exit", "exit_schedule": "现价减半，跌破600清仓"})
        mock_llm.side_effect = [_sell_untyped_json(), json.dumps(typed_exit, ensure_ascii=False)]
        result = risk_judge(self._base_state())
        assert mock_llm.call_count == 2
        decision = result["final_trade_decision"]
        assert decision.sell_type == "exit"
        assert "打回后已申报" in result["final_price_check"]["note"]  # exit 豁免后价位齐备语义
        # 打回反馈含双型语义说明
        assert "sell_type" in mock_llm.call_args_list[1].args[0]

    @patch("finance_agent.nodes._llm_utils.call_llm_streaming")
    def test_untyped_sell_defaults_exit_after_retry(self, mock_llm):
        """打回申报仍缺 → 默认 exit（可执行默认）→ 减仓节奏已带 → 放行 + 如实标注。"""
        untyped_with_schedule = {
            "action": "sell",
            "confidence": 0.5,
            "reasoning": "看空",
            "position_size": "light",
            "exit_schedule": "分两批：现价减半、跌破 600 清仓",
            "reeval_triggers": ["重新站上 660 恢复持有"],
        }
        mock_llm.side_effect = [
            _sell_untyped_json(),  # 第一次：未申报 sell_type（带建仓价位形态）
            json.dumps(untyped_with_schedule, ensure_ascii=False),  # 打回后仍缺，但带减仓节奏
        ]
        result = risk_judge(self._base_state())
        assert mock_llm.call_count == 2  # 节奏齐备 → 不再触发第二次打回
        decision = result["final_trade_decision"]
        assert decision.sell_type == "exit"  # 默认推导落回决策对象
        note = result["final_price_check"]["note"]
        assert "默认 exit" in note

    @patch("finance_agent.nodes._llm_utils.call_llm_streaming")
    def test_untyped_sell_missing_schedule_retries_for_schedule_not_prices(self, mock_llm):
        """默认 exit 后若 exit_schedule 也缺，打回必须要求减仓节奏而非建仓价位。"""
        mock_llm.return_value = _sell_untyped_json()  # 两次均未申报 sell_type；价位齐全但无节奏
        risk_judge(self._base_state())
        assert mock_llm.call_count == 3  # 分型打回 + 默认 exit 后的减仓节奏打回
        retry_prompts = [
            c.args[0] if c.args else c.kwargs.get("context", "") for c in mock_llm.call_args_list
        ]
        # 「减仓节奏」在分型打回说明里也出现（exit 语义描述）——真正钉死新分支的是
        # 节奏打回的段头标记：旧默认 short 下该段永不出现（价位齐全直接放行）
        assert any("减仓节奏申报打回" in p for p in retry_prompts), "打回未要求 exit_schedule"
        assert any("减仓节奏" in p for p in retry_prompts), "打回未要求 exit_schedule"
        assert not any("补全 entry_price/stop_loss/target_price" in p for p in retry_prompts), (
            "exit 形态不应要求补建仓价位"
        )

    @patch("finance_agent.nodes._llm_utils.call_llm_streaming")
    def test_typed_sell_no_extra_retry(self, mock_llm):
        """已申报 sell_type → 不触发申报打回（恰 1 次调用）。"""
        typed = json.loads(_sell_untyped_json())
        typed["sell_type"] = "short"
        mock_llm.return_value = json.dumps(typed, ensure_ascii=False)
        result = risk_judge(self._base_state())
        mock_llm.assert_called_once()
        assert result["final_trade_decision"].sell_type == "short"


def _watch_from_exec_json(reasoning: str) -> str:
    """均衡带降级重申形态：LLM 重申仍输出执行动作（供 side_effect 第二段）。

    trigger_high/trigger_low 随重申决策申报：均衡带降级的 model_copy 不清洗
    触发位字段（只清仓位/价位），降级后的 watch 终稿据此满足触发位申报契约
    （add-watch-trigger-tracking），本测试不误入触发位申报打回回路。
    """
    return json.dumps(
        {
            "action": "sell",
            "confidence": 0.55,
            "position_size": "light",
            "sell_type": "short",
            "entry_price": 640.0,
            "stop_loss": 700.0,
            "target_price": 571.0,
            "trigger_high": 700.0,
            "trigger_low": 570.0,
            "reasoning": reasoning,
            "reeval_triggers": ["跌破 570 重估"],
        },
        ensure_ascii=False,
    )


class TestDecisionHysteresis:
    """add-decision-hysteresis：证据均衡带 + 方向滞回——无增量的执行/翻转
    打回重申一次，重申仍无 → 降级观望 / 维持前判；携带增量放行。"""

    @staticmethod
    def _state(rating: str | None, history: list[dict]) -> dict:
        return {
            "trader_plan": {"action": "sell", "confidence": 0.7},
            "risk_debate_history": [],
            "stock_code": "688072",
            "research_manager_rating": rating,
            "price_levels": {"available": True, "entry_ref": 640.0, "recent_low": 570.0},
        }

    @patch("finance_agent.nodes.risk.recent_directions", return_value=[])
    @patch("finance_agent.nodes._llm_utils.call_llm_streaming")
    def test_balanced_zone_exec_no_incremental_downgraded(self, mock_llm, _mock_hist):
        """RM 中性（均衡带）+ sell 无增量 → 重申仍无 → 降级 watch + 标注。"""
        mock_llm.side_effect = [
            _sell_decision_json(["跌破 570 重估"]),
            _watch_from_exec_json("技术形态走弱，趋势延续"),
        ]
        result = risk_judge(self._state("中性", []))
        assert mock_llm.call_count == 2
        decision = result["final_trade_decision"]
        assert decision.action == "watch"
        assert "证据均衡" in (decision.inaction_reason or "")
        hy = result["decision_hysteresis"]
        assert hy["balanced_zone"] is True
        assert "默认观望" in hy["applied"]

    @patch("finance_agent.nodes.risk.recent_directions", return_value=[])
    @patch("finance_agent.nodes._llm_utils.call_llm_streaming")
    def test_balanced_zone_exec_with_incremental_passes(self, mock_llm, _mock_hist):
        """均衡带内执行动作但 reasoning 申报增量（技术破位确认）→ 放行。"""
        mock_llm.return_value = json.dumps(
            {
                "action": "sell",
                "confidence": 0.6,
                "position_size": "light",
                "sell_type": "short",
                "entry_price": 640.0,
                "stop_loss": 700.0,
                "target_price": 571.0,
                "reasoning": "今日放量跌破 MA60，技术形态破位确认",
                "reeval_triggers": ["收复 645 上方重估"],
            },
            ensure_ascii=False,
        )
        result = risk_judge(self._state("中性", []))
        mock_llm.assert_called_once()
        assert result["final_trade_decision"].action == "sell"
        assert result["decision_hysteresis"]["applied"] == ""

    @patch(
        "finance_agent.nodes.risk.recent_directions",
        return_value=[{"date": "2026-10-01", "direction": "short", "confidence": 0.6}],
    )
    @patch("finance_agent.nodes._llm_utils.call_llm_streaming")
    def test_flip_without_incremental_maintains_prior(self, mock_llm, _mock_hist):
        """近窗前向 short，输出 buy（翻转）无增量 → 重申仍无 → 维持 sell + 价位清空。"""
        mock_llm.side_effect = [
            json.dumps(
                {
                    "action": "buy",
                    "confidence": 0.6,
                    "position_size": "light",
                    "entry_price": 640.0,
                    "stop_loss": 600.0,
                    "target_price": 750.0,
                    "reasoning": "超跌反弹",
                    "reeval_triggers": ["放量收复 645 上方"],
                },
                ensure_ascii=False,
            ),
            json.dumps(
                {
                    "action": "buy",
                    "confidence": 0.6,
                    "position_size": "light",
                    "entry_price": 640.0,
                    "stop_loss": 600.0,
                    "target_price": 750.0,
                    "reasoning": "反弹动能仍在",
                    "reeval_triggers": ["放量收复 645 上方"],
                },
                ensure_ascii=False,
            ),
            # 维持前判 sell（add-holdings-context：sell_type 默认 exit、价位清空）→
            # 减仓节奏缺失触发打回第三段
            json.dumps(
                {
                    "action": "sell",
                    "confidence": 0.6,
                    "position_size": "light",
                    "sell_type": "exit",
                    "exit_schedule": "现价减半，跌破 570 清仓",
                    "reasoning": "维持前判（无证据增量）",
                    "reeval_triggers": ["跌破 570 重估"],
                },
                ensure_ascii=False,
            ),
        ]
        result = risk_judge(
            self._state("看空", [{"date": "2026-10-01", "direction": "short", "confidence": 0.6}])
        )
        assert mock_llm.call_count == 3  # 滞回打回 + 维持前判后减仓节奏打回
        # 第三段打回要求的是减仓节奏（exit 形态），而非建仓价位
        assert "减仓节奏申报打回" in mock_llm.call_args_list[2].args[0]
        decision = result["final_trade_decision"]
        assert decision.action == "sell"  # 维持前判
        assert decision.sell_type == "exit"  # 维持前判落回可执行默认（不再默认 short）
        assert decision.exit_schedule == "现价减半，跌破 570 清仓"  # 节奏经打回重新申报
        assert decision.entry_price is None  # exit 形态不再要求建仓价位
        assert result["final_price_check"]["note"] == "打回后已申报"
        hy = result["decision_hysteresis"]
        assert hy["flip"] is True
        assert "维持前判" in hy["applied"]

    @patch(
        "finance_agent.nodes.risk.recent_directions",
        return_value=[{"date": "2026-10-01", "direction": "short", "confidence": 0.6}],
    )
    @patch("finance_agent.nodes._llm_utils.call_llm_streaming")
    def test_flip_with_incremental_passes(self, mock_llm, _mock_hist):
        """翻转但申报增量（三季报增速回落）→ 放行翻转。"""
        mock_llm.return_value = json.dumps(
            {
                "action": "buy",
                "confidence": 0.6,
                "position_size": "light",
                "entry_price": 640.0,
                "stop_loss": 600.0,
                "target_price": 750.0,
                "reasoning": "三季报披露后毛利率拐点确认，估值消化",
                "reeval_triggers": ["跌破 570"],
            },
            ensure_ascii=False,
        )
        result = risk_judge(
            self._state("看空", [{"date": "2026-10-01", "direction": "short", "confidence": 0.6}])
        )
        mock_llm.assert_called_once()
        assert result["final_trade_decision"].action == "buy"

    @patch("finance_agent.nodes.risk.recent_directions", return_value=[])
    @patch("finance_agent.nodes._llm_utils.call_llm_streaming")
    def test_no_rating_no_history_no_op(self, mock_llm, _mock_hist):
        """RM 看多（非均衡带）+ 无历史 → 无复核（恰 1 次调用），行为与现状一致。"""
        mock_llm.return_value = _sell_decision_json(["跌破 570 重估"])
        result = risk_judge(self._state("看多", []))
        mock_llm.assert_called_once()
        assert result["decision_hysteresis"]["applied"] == ""
        assert result["decision_hysteresis"]["balanced_zone"] is False

    @patch("finance_agent.nodes.risk.recent_directions", return_value=[])
    @patch("finance_agent.nodes._llm_utils.call_llm_streaming")
    def test_reaffirm_with_incremental_second_pass_keeps_exec(self, mock_llm, _mock_hist):
        """首次无增量被打回，重申携带增量 → 放行执行动作（恰 2 次调用）。"""
        mock_llm.side_effect = [
            _sell_decision_json(["跌破 570 重估"]),
            json.dumps(
                {
                    "action": "sell",
                    "confidence": 0.55,
                    "position_size": "light",
                    "sell_type": "short",
                    "entry_price": 640.0,
                    "stop_loss": 700.0,
                    "target_price": 571.0,
                    "reasoning": "中报披露后现金流质量证伪，破位风险上升",
                    "reeval_triggers": ["跌破 570 重估"],
                },
                ensure_ascii=False,
            ),
        ]
        result = risk_judge(self._state("中性", []))
        assert mock_llm.call_count == 2
        assert result["final_trade_decision"].action == "sell"
        assert result["decision_hysteresis"]["applied"] == ""
