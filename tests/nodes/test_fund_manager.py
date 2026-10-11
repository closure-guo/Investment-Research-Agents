"""TDD tests for nodes/fund_manager.py — Layer V Fund Manager Agent。"""

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from pydantic import ValidationError

from finance_agent.models import TradeDecision
from finance_agent.nodes.fund_manager import (
    _build_fund_manager_context,
    final_integrity_notes,
    fund_manager,
)


def _mock_response(decision: str) -> str:
    """构造指定决策值的 LLM 响应（approve 自动带操作定性，D1 必填）。"""
    payload = {"decision": decision, "reasoning": "测试理由"}
    if decision.strip().lower() == "approve":
        payload["action"] = "watch"
        payload["confidence"] = 0.55
    return json.dumps(payload, ensure_ascii=False)


def _base_state() -> dict:
    return {
        "final_trade_decision": {
            "action": "buy",
            "confidence": 0.75,
            "reasoning": "...",
        },
        "return_count": 0,
    }


def _pin_single_profile():
    """固定 primary 为无 fallback 配置的 profile（隔离链长对「重试后仍失败」断言的干扰）。

    本文件钉的是「同 profile 带校验错误摘要重试一次，仍失败则抛」语义；fallback 链切换
    单独在 tests/nodes/test_llm_utils_fallback.py 覆盖。注意本地 `litellm` import 会经
    load_dotenv 载入仓库 `.env`（LLM_MODEL → env profile，链长 1），CI 无 `.env` 时
    primary 落到 registry 默认 deepseek-official（链长 2）——不 pin 则本地/CI 行为分叉。
    """
    import dataclasses

    from finance_agent.llm.registry import get_profile_preset

    return patch(
        "finance_agent.llm.gateway.resolve_profile",
        return_value=dataclasses.replace(get_profile_preset("deepseek-official"), fallback=()),
    )


class TestFundManager:
    """Layer V Fund Manager Agent。"""

    @patch("finance_agent.nodes._llm_utils.call_llm_streaming")
    def test_approve_decision(self, mock_llm):
        mock_llm.return_value = _mock_response("approve")
        result = fund_manager(_base_state())
        assert result["fund_manager_decision"] == "approve"

    @patch("finance_agent.nodes._llm_utils.call_llm_streaming")
    def test_return_decision(self, mock_llm):
        """return 决策：递增 return_count 且退回理由落 state（calibrate-fm-approval
        回路契约：FM 理由 → state → trader 重跑 context，缺一不可）。"""
        mock_llm.return_value = _mock_response("return")
        result = fund_manager(_base_state())
        assert result["fund_manager_decision"] == "return"
        assert result["return_count"] == 1
        assert result["fund_manager_decision_reasoning"] == "测试理由"

    @patch("finance_agent.nodes._llm_utils.call_llm_streaming")
    def test_reject_decision(self, mock_llm):
        """reject 决策：不递增 return_count（此前缺失该用例）。"""
        mock_llm.return_value = _mock_response("reject")
        result = fund_manager(_base_state())
        assert result["fund_manager_decision"] == "reject"
        assert "return_count" not in result


class TestFundManagerValidation:
    """决策枚举强校验（harden-llm-output-validation）。

    加固前 `data["decision"]` 裸取键：非法值原样透传、缺键抛裸 KeyError，
    且非法值经 routing.py 的 else 分支被静默降级为 approve 语义。
    """

    @pytest.mark.parametrize("illegal", ["revise", "拒绝", "maybe", "APPROVED", ""])
    @patch("finance_agent.nodes._llm_utils.call_llm_streaming")
    def test_invalid_decision_raises(self, mock_llm, illegal):
        """非法决策值抛 ValidationError，不静默降级。"""
        mock_llm.return_value = _mock_response(illegal)
        with pytest.raises(ValidationError):
            fund_manager(_base_state())

    @patch("finance_agent.nodes._llm_utils.call_llm_streaming")
    def test_missing_decision_key_raises(self, mock_llm):
        """缺 decision 键抛 ValidationError（而非裸 KeyError）。"""
        mock_llm.return_value = json.dumps({"reasoning": "忘了给决策"}, ensure_ascii=False)
        with pytest.raises(ValidationError):
            fund_manager(_base_state())

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("Approve", "approve"),
            ("APPROVE", "approve"),
            (" approve ", "approve"),
            ("Reject", "reject"),
            (" REJECT ", "reject"),
            ("Return", "return"),
            ("  RETURN  ", "return"),
        ],
    )
    @patch("finance_agent.nodes._llm_utils.call_llm_streaming")
    def test_decision_normalizes_case_and_whitespace(self, mock_llm, raw, expected):
        """大小写与首尾空白归一化后通过校验，写入 state 的是小写值。"""
        mock_llm.return_value = _mock_response(raw)
        result = fund_manager(_base_state())
        assert result["fund_manager_decision"] == expected

    @patch("finance_agent.nodes._llm_utils.call_llm_streaming")
    def test_normalized_return_increments_count(self, mock_llm):
        """归一化前的大小写变体同样触发 return_count 递增。"""
        mock_llm.return_value = _mock_response("Return")
        result = fund_manager(_base_state())
        assert result["fund_manager_decision"] == "return"
        assert result["return_count"] == 1


class TestReasoningPreserved:
    """refine #111：FM 审批理由不得丢弃——完整落 state，报告可渲染。"""

    @patch("finance_agent.nodes._llm_utils.call_llm_streaming")
    def test_reasoning_flows_to_state(self, mock_llm):
        mock_llm.return_value = json.dumps(
            {"decision": "reject", "reasoning": "回撤38.8%超审慎标准"}, ensure_ascii=False
        )
        result = fund_manager({"final_trade_decision": {}, "risk_metrics": {}})
        assert result["fund_manager_decision"] == "reject"
        assert result["fund_manager_decision_reasoning"] == "回撤38.8%超审慎标准"


class TestTradeDecisionVisibleToFM:
    """FM 审批对象必须进上下文（baseline-decision-semantics-r1 健康检查发现）。

    risk_judge 把 TradeDecision pydantic 对象原样放 state（与 trader_plan 同惯例），
    此前 context 构建用 isinstance(dict) 守卫——遇对象静默跳过，FM 在看不到交易方案
    的情况下审批（Langfuse 实证：4/4 trace 的 fund_manager 输入均无「交易决策」段，
    reject 理由为「无交易方案的仓位/止损」，approve 理由凭风控指标脑补方案）。
    旧测试 fixture 用 dict 喂 state，恰好绕过了这条生产路径。
    """

    def _decision_obj(self) -> TradeDecision:
        return TradeDecision(
            action="sell", confidence=0.62, reasoning="盈利走弱", position_size="轻仓"
        )

    def test_context_includes_pydantic_trade_decision(self):
        ctx = _build_fund_manager_context(
            {"final_trade_decision": self._decision_obj(), "return_count": 0}
        )
        assert "交易决策:" in ctx
        assert '"action": "sell"' in ctx
        assert '"position_size": "轻仓"' in ctx

    def test_context_still_accepts_dict(self):
        ctx = _build_fund_manager_context(_base_state())
        assert "交易决策:" in ctx and '"action": "buy"' in ctx

    @patch("finance_agent.nodes._llm_utils.call_llm_streaming")
    def test_llm_prompt_carries_trade_decision(self, mock_llm):
        """端到端：发给 LLM 的 prompt 正文含交易决策段。"""
        mock_llm.return_value = _mock_response("approve")
        fund_manager({"final_trade_decision": self._decision_obj(), "return_count": 0})
        sent_prompt = mock_llm.call_args.args[0]
        assert "交易决策:" in sent_prompt and '"action": "sell"' in sent_prompt


class TestApproveFieldRetry:
    """approve 缺 action/confidence：带校验错误重试一次，仍缺则抛（重试 ≠ 降级）。

    实验实证：17 条 item 中 1 条 FM 首答 approve 无 action/confidence，
    ValidationError 未经重试直接炸整条 trace（SDK 丢弃 item，无重试）。
    生产路径同样会中断用户会话。
    """

    _bad = json.dumps({"decision": "approve", "reasoning": "整体风险可控"}, ensure_ascii=False)

    @patch("finance_agent.nodes._llm_utils.call_llm_streaming")
    def test_retry_once_with_validation_hint(self, mock_llm):
        mock_llm.side_effect = [self._bad, _mock_response("approve")]
        result = fund_manager(_base_state())
        assert result["fund_manager_decision"] == "approve"
        assert result["fund_manager_action"] == "watch"
        assert result["fund_manager_confidence"] == 0.55
        assert mock_llm.call_count == 2
        retry_prompt = mock_llm.call_args_list[1].args[0]
        assert "action" in retry_prompt and "confidence" in retry_prompt

    @patch("finance_agent.nodes._llm_utils.call_llm_streaming")
    def test_still_invalid_after_retry_raises(self, mock_llm):
        mock_llm.side_effect = [self._bad, self._bad]
        with _pin_single_profile(), pytest.raises(ValidationError):
            fund_manager(_base_state())
        assert mock_llm.call_count == 2

    @patch("finance_agent.nodes._llm_utils.call_llm_streaming")
    def test_valid_first_answer_no_retry(self, mock_llm):
        mock_llm.return_value = _mock_response("approve")
        fund_manager(_base_state())
        assert mock_llm.call_count == 1


def test_final_integrity_notes_includes_price_gate_note():
    """update-decision-price-gate：gate「打回后已修正」复核标注进 FM 上下文。"""
    state = {
        "final_price_check": {"result": "pass", "note": ""},
        "final_inaction_check": {"result": "pass", "note": ""},
        "final_reeval_check": {"result": "pass", "note": ""},
        "decision_price_gate": {"result": "pass", "note": "打回后已修正"},
    }
    notes = final_integrity_notes(state)
    assert ("决策价位校验", "打回后已修正") in notes


def test_final_integrity_notes_empty_gate_note_skipped():
    state = {
        "final_price_check": {"result": "pass", "note": ""},
        "final_inaction_check": {"result": "pass", "note": ""},
        "final_reeval_check": {"result": "pass", "note": ""},
        "decision_price_gate": {"result": "pass", "note": ""},
    }
    assert final_integrity_notes(state) == []


class TestFMGroundingSurface:
    """add-fm-grounding-surface：FM 审批上下文三类 grounding 输入（非空才出现）。

    消费 add-anchor-value-grounding 产出的 debate_anchor_checks 确定性信号
    （断点 2 delta）+ 估值完整性 + 披露节原文；仲裁权保留（不改路由）。
    """

    def test_valuation_incompleteness_section(self):
        state = _base_state() | {
            "valuation_snapshot": {"missing_reasons": ["market_cap 缺失", "快照缺失"]}
        }
        ctx = _build_fund_manager_context(state)
        assert "估值完整性标注" in ctx
        assert "market_cap 缺失" in ctx and "快照缺失" in ctx

    def test_valuation_section_absent_when_clean(self):
        assert "估值完整性标注" not in _build_fund_manager_context(_base_state())
        empty = _base_state() | {"valuation_snapshot": {"missing_reasons": []}}
        assert "估值完整性标注" not in _build_fund_manager_context(empty)

    def test_anchor_warning_lists_offenders(self):
        checks = [
            {
                "role": "bull",
                "round": 1,
                "index": 2,
                "kind": "data",
                "anchors": ["fundamental.中报净利润同比"],
                "anchor_statuses": ["resolved"],
                "matched_via": ["field_ref"],
                "echo_only_field_refs": [],
                "status": "value_mismatch",
                "anchored": True,
            }
        ]
        ctx = _build_fund_manager_context(_base_state() | {"debate_anchor_checks": checks})
        assert "辩论锚点告警" in ctx
        assert "value_mismatch" in ctx and "fundamental.中报净利润同比" in ctx
        assert "bull R1 #2" in ctx

    def test_anchor_warning_absent_when_clean(self):
        assert "辩论锚点告警" not in _build_fund_manager_context(_base_state())
        clean = [
            {
                "role": "bull",
                "round": 1,
                "index": 1,
                "kind": "data",
                "anchors": ["technical_indicators.MA.5.-1"],
                "anchor_statuses": ["resolved"],
                "matched_via": ["field_ref"],
                "echo_only_field_refs": [],
                "status": "resolved",
                "anchored": True,
            }
        ]
        ctx = _build_fund_manager_context(_base_state() | {"debate_anchor_checks": clean})
        assert "辩论锚点告警" not in ctx

    def test_anchor_warning_covers_unresolved_and_echo_only(self):
        checks = [
            {
                "role": "bear",
                "round": 2,
                "index": 1,
                "kind": "inference",
                "anchors": ["fundamental.中报净利润同比"],
                "anchor_statuses": ["unresolved"],
                "matched_via": [""],
                "echo_only_field_refs": [],
                "status": "unresolved",
                "anchored": False,
            },
            {
                "role": "bull",
                "round": 1,
                "index": 3,
                "kind": "inference",
                "anchors": ["fundamental.中报净利润同比"],
                "anchor_statuses": ["resolved"],
                "matched_via": ["echo"],
                "echo_only_field_refs": ["fundamental.中报净利润同比"],
                "status": "resolved",
                "anchored": True,
            },
        ]
        ctx = _build_fund_manager_context(_base_state() | {"debate_anchor_checks": checks})
        assert "辩论锚点告警" in ctx
        assert "unresolved" in ctx and "echo_only" in ctx

    def test_anchor_warning_cap_five(self):
        checks = [
            {
                "role": "bull",
                "round": i + 1,
                "index": 1,
                "kind": "data",
                "anchors": [f"bad.anchor.{i}"],
                "anchor_statuses": ["unresolved"],
                "matched_via": [""],
                "echo_only_field_refs": [],
                "status": "unresolved",
                "anchored": False,
            }
            for i in range(6)
        ]
        ctx = _build_fund_manager_context(_base_state() | {"debate_anchor_checks": checks})
        assert "另 1 条" in ctx
        assert ctx.count("bad.anchor.") == 5

    def test_disclosure_section_in_context(self):
        state = _base_state() | {
            "latest_period_snapshot": {
                "报告日": "2026-06-30",
                "期类型": "中报",
                "毛利率(%)": None,
            }
        }
        ctx = _build_fund_manager_context(state)
        assert "最新报告期快照" in ctx
        # 未配置的段不出现（非空才出现）
        assert "估值完整性标注" not in ctx and "辩论锚点告警" not in ctx

    def test_base_state_context_unchanged(self):
        ctx = _build_fund_manager_context(_base_state())
        assert "估值完整性标注" not in ctx and "辩论锚点告警" not in ctx
        assert "交易决策" in ctx  # 既有段零回归


class TestFMPromptPreApprovalClause:
    """fund_manager.md 审批前核查条款（条件式，仲裁权保留）。"""

    def test_prompt_has_preapproval_clause(self):
        prompt = (
            Path(__file__).resolve().parents[2] / "src/finance_agent/prompts/fund_manager.md"
        ).read_text(encoding="utf-8")
        assert "审批前核查" in prompt
        assert "估值完整性标注" in prompt
        assert "辩论锚点告警" in prompt
        assert "数据口径披露" in prompt
        assert "仲裁权" in prompt  # 条款不得禁止 approve
