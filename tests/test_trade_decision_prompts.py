"""TradeDecision 相关 prompt 的内容契约（improve-decision-grounding）。

prompt 是行为契约的一部分：trader/risk_judge 必须要求模型输出
evidence_refs，否则 judge 无结构化引用可核对。
"""

from pathlib import Path

from finance_agent.models import TRADE_EVIDENCE_SOURCES

_PROMPTS_DIR = Path(__file__).resolve().parents[1] / "src/finance_agent/prompts"


def _load(name: str) -> str:
    return (_PROMPTS_DIR / name).read_text(encoding="utf-8")


class TestTraderPromptEvidenceRefs:
    def test_example_contains_evidence_refs(self):
        assert '"evidence_refs"' in _load("trader.md")

    def test_mandates_ref_for_each_reasoning_claim(self):
        text = _load("trader.md")
        assert "evidence_ref" in text
        assert "source" in text

    def test_source_enum_listed(self):
        text = _load("trader.md")
        assert "technical" in text
        assert "debate_bull" in text
        assert "research_manager" in text


class TestRiskJudgePromptEvidenceRefs:
    def test_example_contains_evidence_refs(self):
        assert '"evidence_refs"' in _load("risk_judge.md")

    def test_mandates_passthrough_without_fabrication(self):
        text = _load("risk_judge.md")
        assert "evidence_ref" in text
        # 不虚构来源 + 允许空数组（无可对应来源时）
        assert "不得" in text
        assert "[]" in text


class TestPromptSourceEnumDriftGuard:
    """prompt 的 source 枚举必须与 models.TRADE_EVIDENCE_SOURCES 一致（final review F5）。"""

    def test_trader_lists_trade_sources(self):
        text = _load("trader.md")
        for src in TRADE_EVIDENCE_SOURCES:
            assert src in text, f"trader.md 缺 source: {src}"

    def test_risk_judge_lists_risk_sources(self):
        """Risk Judge 在风险辩论之后裁决，来源集须含三方风险辩论与风控指标。"""
        from finance_agent.models import RISK_EVIDENCE_SOURCES

        text = _load("risk_judge.md")
        for src in RISK_EVIDENCE_SOURCES:
            assert src in text, f"risk_judge.md 缺 source: {src}"


class TestInactionRationalePromptContract:
    """require-watch-hold-rationale：prompt 必须声明非执行动作理由字段与必填义务。"""

    def test_trader_prompt_declares_fields_and_mandate(self):
        text = _load("trader.md")
        assert "inaction_reason" in text
        assert "reeval_triggers" in text
        # 契约段独有短语：字段名在 JSON 示例里也出现、「hold 或 watch」被既有价位行
        # 预先满足——只断言它们杀不死「删除整段义务」的变异（review Minor #2）。
        assert "1-3 条" in text
        assert "禁止空泛表述" in text
        # 与 reasoning 的分工（design D1）：禁止照抄整体决策叙述
        assert "禁止照抄" in text

    def test_risk_judge_prompt_inherits_mandate(self):
        text = _load("risk_judge.md")
        assert "inaction_reason" in text
        assert "reeval_triggers" in text
        assert "1-3 条" in text
        # 继承行同样须钉死与 reasoning 的分工（design D1）
        assert "禁止照抄" in text


class TestAudienceExecutabilityContract:
    """add-holdings-context：决策层受众可执行性约束（prompt 政策）。"""

    def test_trader_declares_a_share_constraint(self):
        text = _load("trader.md")
        assert "融资融券" in text
        assert "普通证券账户" in text or "普通账户" in text

    def test_trader_enumerates_three_states(self):
        text = _load("trader.md")
        assert "已申报持有" in text
        assert "已申报未持有" in text
        assert "未申报" in text

    def test_risk_judge_declares_a_share_constraint(self):
        text = _load("risk_judge.md")
        assert "融资融券" in text or "两融" in text
        assert "普通证券账户" in text or "普通账户" in text

    def test_risk_judge_enumerates_three_states(self):
        text = _load("risk_judge.md")
        assert "已申报持有" in text
        assert "已申报未持有" in text
        assert "未申报" in text

    def test_undeclared_default_is_exit_not_short(self):
        """未申报兜底表述必须改口——原「默认按 short」把受众推向不可执行模板。"""
        for name in ("trader.md", "risk_judge.md"):
            text = _load(name)
            assert "默认按 short" not in text, f"{name} 仍声明默认 short"
            assert "默认按 exit" in text, f"{name} 未声明默认 exit"
