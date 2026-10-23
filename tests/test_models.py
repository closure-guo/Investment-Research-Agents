"""TDD tests for models.py — Agent 间通信的结构化输出模型。

参考 ADR-0011 和 TradingAgents (arXiv:2412.20138)。
结构化输出解决"电话效应"：agent 间传递 Pydantic 对象而非自由 Markdown。
"""

from finance_agent.citation import Claim, verify_claims
from finance_agent.models import (
    AnalystReport,
    DebateMessage,
    FundManagerDecision,
    TradeDecision,
)


class TestAnalystReportPlainConclusion:
    """add-agent-readable-conclusion：分析师报告普通人可读结论字段。"""

    def test_plain_conclusion_required_nonblank(self):
        """缺失 / 空串 / 纯空白 → 校验失败（同 reasoning 强校验模式）。"""
        import pytest

        base = {
            "agent_name": "technical",
            "summary": "技术面偏空",
            "key_findings": [],
            "claims": [],
            "markdown": "## 分析\n内容",
        }
        with pytest.raises(ValueError):
            AnalystReport(**base)  # 缺 plain_conclusion
        with pytest.raises(ValueError):
            AnalystReport(**base, plain_conclusion="")
        with pytest.raises(ValueError):
            AnalystReport(**base, plain_conclusion="   ")

    def test_plain_conclusion_accepted(self):
        report = AnalystReport(
            agent_name="technical",
            summary="技术面偏空",
            key_findings=[],
            claims=[],
            markdown="## 分析\n内容",
            plain_conclusion="技术面偏空：MACD 死叉、反弹动能存疑，不宜右侧追入",
        )
        assert report.plain_conclusion.startswith("技术面偏空")


class TestAnalystReport:
    """Layer I 分析师输出模型。"""

    def test_report_contains_claims_for_verification(self):
        """AnalystReport 包含 Claim 列表，可提取用于引用校验。"""
        report = AnalystReport(
            agent_name="fundamental",
            summary="基本面分析",
            plain_conclusion="结论：基本面分析",
            key_findings=["ROE 28.33%", "资产负债率 40%"],
            claims=[
                Claim(
                    claim_type="numerical",
                    source_type="data",
                    field_ref="solvency_metrics.资产负债率.2024",
                    stated_value=40.0,
                    interpretation="资产负债率 40%",
                ),
            ],
            markdown="## 基本面分析\n...",
        )
        assert len(report.claims) == 1
        # 可以直接传给 verify_claims
        state = {"solvency_metrics": {"资产负债率": {"2024": 40.0}}}
        results = verify_claims(report.claims, state)
        assert results[0].status == "PASS"


class TestDebateMessage:
    """Layer II/IV 辩论消息模型。"""

    def test_bull_bear_debate_message(self):
        """Bull/Bear 辩论消息包含角色、轮次、论点。"""
        msg = DebateMessage(
            role="bull",
            round=1,
            content="基于强劲的 ROE 增长和低负债率，建议买入",
            key_arguments=["ROE 28.33% 高于行业平均", "资产负债率仅 40%"],
        )
        assert msg.role == "bull"
        assert msg.round == 1
        assert len(msg.key_arguments) == 2

    def test_risk_debate_message(self):
        """风险管理辩论消息。"""
        msg = DebateMessage(
            role="conservative",
            round=1,
            content="虽然基本面稳健，但需关注估值过高风险",
            key_arguments=["PE 处于历史 90 分位"],
        )
        assert msg.role == "conservative"


class TestTradeDecision:
    """Layer III/IV 交易决策模型。"""

    def test_buy_decision(self):
        """买入决策包含方向、置信度、理由。"""
        decision = TradeDecision(
            action="buy",
            confidence=0.75,
            reasoning="ROE 持续高于 15%，负债率健康，估值合理",
        )
        assert decision.action == "buy"
        assert decision.confidence > 0.5

    def test_invalid_action_rejected(self):
        """无效的交易方向被拒绝。"""
        import pytest

        with pytest.raises(ValueError):
            TradeDecision(
                action="strong_buy",
                confidence=0.8,
                reasoning="",
            )


class TestModelFieldConstraints:
    """枚举与范围约束（harden-llm-output-validation）。

    加固前 role 是裸 str、confidence 无 ge/le、round 无范围——
    LLM 串角色或返回百分数置信度不会被发现，只会污染报告渲染。
    """

    def test_debate_message_invalid_role_rejected(self):
        """非法角色值被拒绝（加固前为裸 str，静默透传）。"""
        import pytest

        with pytest.raises(ValueError):
            DebateMessage(
                role="随便",
                round=1,
                content="内容",
                key_arguments=["论据"],
            )

    def test_debate_message_all_legal_roles_accepted(self):
        """7 个合法角色值全部通过校验。"""
        for role in (
            "bull",
            "bear",
            "aggressive",
            "conservative",
            "neutral",
            "research_manager",
            "risk_judge",
        ):
            msg = DebateMessage(role=role, round=1, content="内容", key_arguments=["论据"])
            assert msg.role == role

    def test_debate_message_invalid_round_rejected(self):
        """round 小于 1 被拒绝。"""
        import pytest

        for badRound in (0, -1):
            with pytest.raises(ValueError):
                DebateMessage(
                    role="bull",
                    round=badRound,
                    content="内容",
                    key_arguments=["论据"],
                )

    def test_trade_decision_confidence_out_of_range_rejected(self):
        """置信度超出 0-1 被拒绝（如 LLM 按百分数返回 95）。"""
        import pytest

        for badConfidence in (95, -0.5, 1.5):
            with pytest.raises(ValueError):
                TradeDecision(
                    action="buy",
                    confidence=badConfidence,
                    reasoning="理由",
                )

    def test_trade_decision_confidence_boundaries_accepted(self):
        """置信度边界值 0 与 1 合法。"""
        for okConfidence in (0.0, 1.0):
            decision = TradeDecision(action="hold", confidence=okConfidence, reasoning="理由")
            assert decision.confidence == okConfidence


class TestFundManagerDecision:
    """Layer V 基金经理审批决策模型（harden-llm-output-validation）。"""

    def test_legal_decisions_accepted(self):
        # approve 须带操作定性（D1）；reject/return 可缺 action/confidence
        approve = FundManagerDecision(
            decision="approve", action="watch", confidence=0.55, reasoning="理由"
        )
        assert approve.action == "watch"
        for decision in ("reject", "return"):
            model = FundManagerDecision(decision=decision, reasoning="理由")
            assert model.decision == decision
            assert model.action is None and model.confidence is None

    def test_approve_without_action_or_confidence_rejected(self):
        """D1：approve 缺 action/confidence 任一即 ValidationError（操作性结论必填）。"""
        import pytest

        with pytest.raises(ValueError):
            FundManagerDecision(decision="approve", reasoning="缺 action")
        with pytest.raises(ValueError):
            FundManagerDecision(decision="approve", action="watch", reasoning="缺 confidence")

    def test_confidence_out_of_range_rejected(self):
        import pytest

        with pytest.raises(ValueError):
            FundManagerDecision(decision="approve", action="buy", confidence=1.5, reasoning="理由")

    def test_normalizes_case_and_whitespace(self):
        """大小写与首尾空白归一化。"""
        assert (
            FundManagerDecision(
                decision=" Approve ", action="watch", confidence=0.5, reasoning="理由"
            ).decision
            == "approve"
        )
        assert FundManagerDecision(decision="REJECT", reasoning="理由").decision == "reject"

    def test_invalid_decision_rejected(self):
        """非法值被拒绝，不做同义词映射。"""
        import pytest

        for illegal in ("revise", "拒绝", "maybe", ""):
            with pytest.raises(ValueError):
                FundManagerDecision(decision=illegal, reasoning="理由")

    def test_reasoning_required(self):
        """reasoning 必填且非空（spec「否决理由完整」：决策必须带理由，供审计/一致性核对）。"""
        import pytest

        with pytest.raises(ValueError):
            FundManagerDecision(decision="approve")  # 缺 reasoning
        with pytest.raises(ValueError):
            FundManagerDecision(decision="reject", reasoning="")  # 空 reasoning
        with pytest.raises(ValueError):
            FundManagerDecision(decision="return", reasoning="   ")  # 纯空白 reasoning


class TestTradeDecisionEvidenceRefs:
    """TradeDecision.evidence_refs 结构化论据引用（improve-decision-grounding）。"""

    def test_evidence_refs_parsed(self):
        decision = TradeDecision.model_validate(
            {
                "action": "buy",
                "confidence": 0.75,
                "reasoning": "理由",
                "evidence_refs": [
                    {"claim": "ROE 3.4% 高于行业均值", "source": "fundamental"},
                    {"claim": "股价站上 60 日均线", "source": "technical"},
                ],
            }
        )
        assert len(decision.evidence_refs) == 2
        assert decision.evidence_refs[0].source == "fundamental"
        assert decision.evidence_refs[0].claim == "ROE 3.4% 高于行业均值"

    def test_evidence_refs_default_empty(self):
        decision = TradeDecision.model_validate(
            {"action": "hold", "confidence": 0.5, "reasoning": "理由"}
        )
        assert decision.evidence_refs == []

    def test_source_aliases_normalized(self):
        decision = TradeDecision.model_validate(
            {
                "action": "buy",
                "confidence": 0.6,
                "reasoning": "理由",
                "evidence_refs": [
                    {"claim": "a", "source": "Technical_Analyst"},
                    {"claim": "b", "source": "BULL"},
                    {"claim": "c", "source": "research_manager_conclusion"},
                    {"claim": "d", "source": " sentiment "},
                ],
            }
        )
        sources = [r.source for r in decision.evidence_refs]
        assert sources == ["technical", "debate_bull", "research_manager", "sentiment"]

    def test_unknown_source_lenient(self):
        decision = TradeDecision.model_validate(
            {
                "action": "buy",
                "confidence": 0.6,
                "reasoning": "理由",
                "evidence_refs": [{"claim": "a", "source": "risk_debater"}],
            }
        )
        assert decision.evidence_refs[0].source == "risk_debater"

    def test_evidence_refs_none_scrubbed_to_empty(self):
        decision = TradeDecision.model_validate(
            {"action": "hold", "confidence": 0.5, "reasoning": "理由", "evidence_refs": None}
        )
        assert decision.evidence_refs == []

    def test_malformed_evidence_refs_items_dropped(self):
        decision = TradeDecision.model_validate(
            {
                "action": "hold",
                "confidence": 0.5,
                "reasoning": "理由",
                "evidence_refs": [
                    None,
                    {"claim": "缺 source"},
                    {"source": "缺 claim"},
                    {"claim": "正常", "source": "fundamental"},
                    "not-a-dict",
                ],
            }
        )
        assert [r.claim for r in decision.evidence_refs] == ["正常"]
        assert decision.evidence_refs[0].source == "fundamental"


class TestRiskEvidenceSources:
    """Risk Judge 的论据来源 = Trader 来源 + 三方风险辩论 + 风控指标（r1 复盘：
    裁决理由通篇引用「中性方/激进方/beta 1.96」，evidence_refs 却只能标 Trader 那套来源，
    judge 必然判「关键论据未列入引用」）。"""

    def test_risk_sources_superset_of_trade_sources(self):
        from finance_agent.models import RISK_EVIDENCE_SOURCES, TRADE_EVIDENCE_SOURCES

        assert TRADE_EVIDENCE_SOURCES < RISK_EVIDENCE_SOURCES
        assert {
            "risk_aggressive",
            "risk_conservative",
            "risk_neutral",
            "risk_metrics",
        } <= RISK_EVIDENCE_SOURCES

    def test_risk_role_aliases_normalize(self):
        from finance_agent.models import TradeDecision

        d = TradeDecision.model_validate(
            {
                "action": "sell",
                "confidence": 0.55,
                "reasoning": "r",
                "evidence_refs": [
                    {"claim": "隐含PE约25倍", "source": "neutral"},
                    {"claim": "beta 1.96", "source": "risk_metrics"},
                    {"claim": "左侧建仓被否", "source": "Aggressive"},
                ],
            }
        )
        assert [e.source for e in d.evidence_refs] == [
            "risk_neutral",
            "risk_metrics",
            "risk_aggressive",
        ]

    def test_risk_bull_bear_alias_to_debate_sources(self):
        """r2 实证：Risk Judge 输出 risk_bull / risk_bear 两个枚举外标签（把多空辩论误挂到风险层前缀），
        应归一为 debate_bull / debate_bear。"""
        from finance_agent.models import TradeDecision

        d = TradeDecision.model_validate(
            {
                "action": "watch",
                "confidence": 0.5,
                "reasoning": "r",
                "evidence_refs": [
                    {"claim": "x", "source": "risk_bull"},
                    {"claim": "y", "source": "risk_bear"},
                    {"claim": "z", "source": "RISK_METRICS"},
                ],
            }
        )
        assert [e.source for e in d.evidence_refs] == ["debate_bull", "debate_bear", "risk_metrics"]


class TestTradeDecisionInactionRationale:
    """require-watch-hold-rationale：非执行动作结构化理由字段与噪声清洗。"""

    def test_fields_default_none_and_empty(self):
        d = TradeDecision(action="watch", confidence=0.5, reasoning="r")
        assert d.inaction_reason is None
        assert d.reeval_triggers == []

    def test_reeval_triggers_single_string_to_list(self):
        d = TradeDecision(
            action="watch",
            confidence=0.5,
            reasoning="r",
            reeval_triggers="价格回落至 1500 以下",
        )
        assert d.reeval_triggers == ["价格回落至 1500 以下"]

    def test_reeval_triggers_none_and_other_types_to_empty(self):
        for raw in (None, 123, {"a": 1}):
            d = TradeDecision(action="hold", confidence=0.5, reasoning="r", reeval_triggers=raw)
            assert d.reeval_triggers == []

    def test_reeval_triggers_mixed_list_drops_non_str_and_blank(self):
        d = TradeDecision(
            action="watch",
            confidence=0.5,
            reasoning="r",
            reeval_triggers=["有效", 42, None, "  ", "第二条"],
        )
        assert d.reeval_triggers == ["有效", "第二条"]

    def test_blank_inaction_reason_normalized_to_none(self):
        d = TradeDecision(action="watch", confidence=0.5, reasoning="r", inaction_reason="   ")
        assert d.inaction_reason is None

    def test_inaction_reason_preserved_verbatim(self):
        """正路径钉子：合法理由原样保留（清洗不得改写或截断理由内容）。"""
        d = TradeDecision(
            action="watch", confidence=0.5, reasoning="r", inaction_reason="估值分位偏高"
        )
        assert d.inaction_reason == "估值分位偏高"

    def test_reeval_triggers_blank_string_to_empty(self):
        """纯空白字符串等同未申报（str 分支的空白口径与列表条目一致）。"""
        d = TradeDecision(action="watch", confidence=0.5, reasoning="r", reeval_triggers="  ")
        assert d.reeval_triggers == []

    def test_reeval_triggers_tuple_normalized(self):
        """tuple 与 list 同款归一，且非 str/空白条目被丢弃。

        评审 Minor 收口：纯 ("a","b") 形态 pydantic 自身也能 coerce → 不判别 tuple 分支；
        混合 tuple 在删除该分支时会落进 `return []`，本断言才真正钉住分支行为。
        """
        d = TradeDecision(action="watch", confidence=0.5, reasoning="r", reeval_triggers=("a", "b"))
        assert d.reeval_triggers == ["a", "b"]
        mixed = TradeDecision(
            action="watch",
            confidence=0.5,
            reasoning="r",
            reeval_triggers=("有效", 42, "  ", "第二条"),  # pyrefly: ignore[bad-argument-type]
        )
        assert mixed.reeval_triggers == ["有效", "第二条"]

    def test_non_str_inaction_reason_normalized_to_none_without_raising(self):
        """非 str 形态（int/dict/list）一律归一为 None——MUST NOT 抛 ValidationError 中断管线。

        与 `_normalize_anchors` 先例同因：trader.py / risk.py 的 LLM JSON 入口无 schema
        强制，LLM 真实会输出 dict 形态理由（如 {"reason": "..."}），原样交给 pydantic
        会炸掉整条 full-graph 运行。
        """
        for raw in (123, {"a": 1}, ["x"], 1.5, True):
            d = TradeDecision(action="watch", confidence=0.5, reasoning="r", inaction_reason=raw)
            assert d.inaction_reason is None, f"inaction_reason={raw!r} 未归一为 None"

    def test_buy_unaffected(self):
        """执行动作不受理由约束：价位原样回读，新字段保持默认。"""
        d = TradeDecision(
            action="buy",
            confidence=0.8,
            reasoning="r",
            entry_price=1.0,
            stop_loss=0.9,
            target_price=1.2,
        )
        assert d.action == "buy"
        assert (d.entry_price, d.stop_loss, d.target_price) == (1.0, 0.9, 1.2)
        assert d.inaction_reason is None
        assert d.reeval_triggers == []


class TestSellTypeTyping:
    """update-sell-action-typing（#188）：sell 分型字段——exit（持有者减仓）/
    short（做空建仓）+ exit_schedule 减仓节奏；归一容错与非 sell 归 None。"""

    @staticmethod
    def _sell(**extra) -> dict:
        base = {
            "action": "sell",
            "confidence": 0.6,
            "reasoning": "趋势走弱",
        }
        base.update(extra)
        return base

    def test_exit_with_schedule_accepted(self):
        d = TradeDecision.model_validate(
            self._sell(sell_type="exit", exit_schedule="分两批：现价减半、跌破600清仓")
        )
        assert d.sell_type == "exit"
        assert d.exit_schedule == "分两批：现价减半、跌破600清仓"

    def test_short_accepted(self):
        d = TradeDecision.model_validate(
            self._sell(sell_type="short", entry_price=640.0, stop_loss=700.0, target_price=571.0)
        )
        assert d.sell_type == "short"

    def test_case_and_synonym_normalization(self):
        # 大小写归一；同义词（减仓/退出→exit，做空→short）
        assert TradeDecision.model_validate(self._sell(sell_type="Exit")).sell_type == "exit"
        assert TradeDecision.model_validate(self._sell(sell_type="SHORT")).sell_type == "short"
        assert TradeDecision.model_validate(self._sell(sell_type="减仓")).sell_type == "exit"
        assert TradeDecision.model_validate(self._sell(sell_type="退出敞口")).sell_type == "exit"
        assert TradeDecision.model_validate(self._sell(sell_type="做空")).sell_type == "short"

    def test_unknown_sell_type_normalized_to_none(self):
        # 杂值归 None（交 risk_judge 校验层打回申报，仍缺默认 exit），不抛异常炸管线
        d = TradeDecision.model_validate(self._sell(sell_type="partially"))
        assert d.sell_type is None

    def test_non_sell_action_forces_none(self):
        # 非 sell 动作上的 sell_type 一律归 None（防 LLM 在 buy 上塞值）
        d = TradeDecision.model_validate(
            {"action": "buy", "confidence": 0.6, "reasoning": "r", "sell_type": "exit"}
        )
        assert d.sell_type is None
        assert d.exit_schedule is None

    def test_default_none_backcompat(self):
        # 历史决策对象（无字段）→ None，模型层不定型（risk_judge 打回后仍缺默认 exit）
        d = TradeDecision.model_validate(self._sell())
        assert d.sell_type is None
        assert d.exit_schedule is None


class TestTradeDecisionTriggerLevels:
    """add-watch-trigger-tracking：watch 双向触发位宽松清洗。"""

    def _make(self, **kwargs):
        base = {
            "action": "watch",
            "confidence": 0.55,
            "reasoning": "测试",
            "inaction_reason": "观望",
            "reeval_triggers": ["站上 24.6 重估"],
        }
        base.update(kwargs)
        return TradeDecision(**base)

    def test_valid_triggers_preserved(self):
        d = self._make(trigger_high=24.6, trigger_low=22.91)
        assert d.trigger_high == 24.6
        assert d.trigger_low == 22.91

    def test_numeric_string_coerced(self):
        d = self._make(trigger_high="24.6", trigger_low=" 22.91 ")
        assert d.trigger_high == 24.6
        assert d.trigger_low == 22.91

    def test_noise_normalized_to_none(self):
        d = self._make(trigger_high=None, trigger_low="abc")
        assert d.trigger_high is None
        assert d.trigger_low is None

    def test_invalid_values_normalized_to_none(self):
        d = self._make(trigger_high=-1, trigger_low=0)
        assert d.trigger_high is None  # 负值
        assert d.trigger_low is None  # 0

    def test_bool_and_nan_rejected(self):
        d = self._make(trigger_high=True, trigger_low=float("nan"))
        assert d.trigger_high is None
        assert d.trigger_low is None

    def test_absent_fields_default_none(self):
        d = self._make()
        assert d.trigger_high is None
        assert d.trigger_low is None
