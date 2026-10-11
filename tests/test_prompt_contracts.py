"""提示词行为契约（enhance-agent-prompt-quality）。

prompt 是行为契约的一部分：分析师必须含反幻觉硬规则与分析方法论，
辩论者必须含对抗性指令，决策层必须含语义契约，research_manager 必须
含评级表态，deep_mode 必须含输出约束。
"""

from pathlib import Path

import pytest

_PROMPTS_DIR = Path(__file__).resolve().parents[1] / "src/finance_agent/prompts"


def _load(name: str) -> str:
    return (_PROMPTS_DIR / name).read_text(encoding="utf-8")


ANALYSTS = ["fundamental_analyst", "macro_analyst", "technical_analyst", "sentiment_analyst"]


@pytest.mark.parametrize("name", ANALYSTS)
class TestAnalystAntiHallucination:
    def test_has_methodology_section(self, name):
        assert "## 分析方法论" in _load(f"{name}.md")

    def test_has_hard_rules_section(self, name):
        assert "## 反幻觉硬规则" in _load(f"{name}.md")

    def test_mandates_data_sufficiency_declaration(self, name):
        assert "数据不足" in _load(f"{name}.md")

    def test_mandates_period_annotation_discipline(self, name):
        """期次标注纪律（add-period-key-citation-validation）：多期次并存数值不得裸引。"""
        text = _load(f"{name}.md")
        assert "显式标注期次" in text
        assert "裸引" in text


class TestSentimentCaliberDisclosure:
    """板块口径显式化（update-report-data-disclosure，issue #233）。"""

    def test_mandates_sector_caliber_annotation(self):
        text = _load("sentiment_analyst.md")
        assert "板块口径" in text
        assert "混写" in text or "误读" in text


DEBATERS = ["bull_debater", "bear_debater", "risk_debater"]


@pytest.mark.parametrize("name", DEBATERS)
class TestDebaterAdversarialInstruction:
    def test_has_debate_discipline_section(self, name):
        assert "## 辩论纪律" in _load(f"{name}.md")

    def test_mandates_refute_opponent(self, name):
        text = _load(f"{name}.md")
        assert "反驳" in text
        assert "对方" in text

    def test_mandates_period_annotation_discipline(self, name):
        """辩论者引用财务数据同样承担期次标注纪律（issue #231）。"""
        text = _load(f"{name}.md")
        assert "显式标注期次" in text


DECISION_PROMPTS = ["trader", "risk_judge", "fund_manager"]


@pytest.mark.parametrize("name", DECISION_PROMPTS)
class TestDecisionSemantics:
    def test_has_semantics_section(self, name):
        assert "## 决策语义" in _load(f"{name}.md")


class TestTraderSemantics:
    def test_position_size_defined(self):
        text = _load("trader.md")
        assert "light" in text
        assert "moderate" in text
        assert "heavy" in text

    def test_confidence_anchored(self):
        assert "≥0.7" in _load("trader.md")


class TestRiskJudgeSemantics:
    def test_balanced_evidence_guidance(self):
        assert "均衡" in _load("risk_judge.md")


class TestFundManagerSemantics:
    def test_decision_options_defined(self):
        text = _load("fund_manager.md")
        assert "approve" in text
        assert "reject" in text
        assert "return" in text

    def test_action_confidence_contract(self):
        """D1：approve 必含操作定性（action）与置信度（confidence）的格式与语义说明。"""
        text = _load("fund_manager.md")
        assert '"action"' in text and '"confidence"' in text
        assert "操作定性" in text
        # approve 的对象是最终方案而非对裁决的赞成票（round5 误判根因）
        assert "最终交易方案" in text

    def test_reasoning_boundary_clause(self):
        """D2：审批理由职责边界——限定范围 + 禁止方向性投资背书（含反例锚点）。"""
        text = _load("fund_manager.md")
        assert "职责边界" in text
        assert "风控结论的一致性" in text
        assert "长期价值投资" in text  # 反例锚点
        assert "MUST NOT" in text


class TestResearchManagerStance:
    def test_has_stance_section(self):
        assert "## 评级表态" in _load("research_manager.md")

    def test_mandates_direction(self):
        text = _load("research_manager.md")
        assert "看多" in text
        assert "看空" in text
        assert "中性" in text


class TestDeepModeOutputConstraint:
    def test_has_output_constraint_section(self):
        assert "## 输出约束" in _load("deep_mode.md")

    def test_bounded_to_tool_output(self):
        assert "工具输出" in _load("deep_mode.md")


class TestReportSummaryGrounding:
    def test_mandates_material_only(self):
        src = Path(__file__).resolve().parents[1] / "src/finance_agent/nodes/report.py"
        text = src.read_text(encoding="utf-8")
        assert "仅基于" in text
        assert "不得引入" in text


SRC_DIR = Path(__file__).resolve().parents[1] / "src/finance_agent"


class TestStockParsingConvergence:
    def test_stock_parsing_prompt_lives_only_in_react_agent(self):
        # nlp.py 必须不存在；股票解析提示词文本必须只出现在 react_agent.py 一处
        assert not (SRC_DIR / "nlp.py").exists()
        hits = []
        for py in SRC_DIR.rglob("*.py"):
            if "你是A股股票代码解析助手" in py.read_text(encoding="utf-8"):
                hits.append(str(py.relative_to(SRC_DIR)))
        assert hits == ["react_agent.py"], hits

    def test_react_system_prompt_removed(self):
        ra = (SRC_DIR / "react_agent.py").read_text(encoding="utf-8")
        assert "REACT_SYSTEM_PROMPT" not in ra


class TestCycleFitMethodology:
    """周期适配方法论契约（update-agent-prompt-cycle-fit）。"""

    def test_fundamental_relative_and_cycle_aware(self):
        text = _load("fundamental_analyst.md")
        assert "同业" in text
        assert "周期" in text or "环境" in text

    def test_technical_mandates_rsa_blunting_in_strong_trend(self):
        text = _load("technical_analyst.md")
        assert "钝化" in text
        assert "趋势" in text

    def test_macro_mandates_m1_m2_scissors(self):
        text = _load("macro_analyst.md")
        assert "剪刀差" in text

    def test_macro_mandates_stale_downweight(self):
        text = _load("macro_analyst.md")
        assert "滞后" in text


class TestTechnicalArrayOrderContract:
    """incident 022：引用 -1 前须核对序列尾部（正序，末尾=最新）。"""

    def test_technical_prompt_mandates_tail_verification(self):
        text = _load("technical_analyst.md")
        assert "序列尾部" in text or "末尾" in text
        assert "正序" in text


class TestClaimSemanticDeclarationPrompts:
    """harden-citation-semantic-coverage：分析师 prompt 声明 metric_name/period。"""

    @pytest.mark.parametrize(
        "prompt", ["technical_analyst", "macro_analyst", "fundamental_analyst"]
    )
    def test_data_analyst_prompts_declare_metric_name_and_period(self, prompt):
        text = (_PROMPTS_DIR / f"{prompt}.md").read_text(encoding="utf-8")
        assert '"metric_name"' in text
        assert '"period"' in text

    def test_prompts_declare_coverage_discipline(self):
        """覆盖纪律：正文每个关键数值须可追溯到 claim（ALCE recall 压力）。"""
        for prompt in ("technical_analyst", "macro_analyst", "fundamental_analyst"):
            text = (_PROMPTS_DIR / f"{prompt}.md").read_text(encoding="utf-8")
            assert "正文" in text and "claim" in text.lower()

    def test_sentiment_prompt_declares_optional_fields(self):
        """舆情分析师以 entity claim 为主：metric_name/period 标注为可选。"""
        text = (_PROMPTS_DIR / "sentiment_analyst.md").read_text(encoding="utf-8")
        assert "metric_name" in text


class TestOutOfVocabNullRule:
    """三标的冒烟实证（2026-09-01）：词表外 metric_name 判 FAIL 全为误报 →
    prompt 须指引「词表外/不确定置 null」；fundamental 另需 growth/quarterly 申报规则。"""

    @pytest.mark.parametrize(
        "prompt", ["technical_analyst", "macro_analyst", "fundamental_analyst"]
    )
    def test_prompts_mandate_null_for_out_of_vocab(self, prompt):
        text = (_PROMPTS_DIR / f"{prompt}.md").read_text(encoding="utf-8")
        assert "置 null" in text and "词表" in text

    def test_fundamental_mandates_growth_base_metric(self):
        """growth_rates.* 引用填基指标名（勿带 增长率/同比 后缀）。"""
        text = (_PROMPTS_DIR / "fundamental_analyst.md").read_text(encoding="utf-8")
        assert "基指标名" in text


@pytest.mark.parametrize("name", ANALYSTS)
class TestClaimDirectionDiscipline:
    """ehr-style-claim-direction：分析师 claim 申报纪律含 direction 必填。"""

    def test_mentions_direction_declaration(self, name):
        text = _load(f"{name}.md")
        assert "direction" in text
        assert "direction=negative" in text

    def test_has_negative_modifier_example(self, name):
        # 「下滑 X%」→ stated_value=X、direction=negative 的申报示例
        text = _load(f"{name}.md")
        assert "下滑 X%" in text
        assert "direction=negative" in text


class TestCoverageSourcesPrompts:
    """新信源消费契约（add-analyst-data-coverage Task 5）。"""

    def test_fundamental_consumes_announcements_and_reports(self):
        text = _load("fundamental_analyst.md")
        assert "公司公告列表" in text
        assert "券商研报列表" in text
        assert "不得直接作为你的结论依据" in text  # 防锚定条款

    def test_sentiment_consumes_unlock_and_blocks(self):
        text = _load("sentiment_analyst.md")
        assert "限售解禁" in text
        assert "大宗交易" in text


class TestFreshnessAndValuationPrompt:
    """prompt 契约：最新报告期校验 + 估值数字依据（update-financial-freshness-and-valuation）。"""

    def test_fundamental_mandates_latest_period_crosscheck(self):
        text = _load("fundamental_analyst.md")
        # 输入清单含两个新数据段
        assert "估值快照" in text
        assert "最新报告期快照" in text
        # 分析要点：年报趋势与最新报告期冲突必须并陈
        assert "最新报告期" in text
        assert "MUST" in text

    def test_fundamental_mandates_valuation_numbers(self):
        text = _load("fundamental_analyst.md")
        assert "PE_caliber" in text
        # 缺估值数据时显式声明，不得无数值断言贵贱
        assert "估值数据缺失" in text


class TestNarrativeFreshnessContract:
    """update-narrative-freshness（#189）：论据期次新鲜度契约。"""

    def test_bear_prompt_requires_latest_period_and_reversal_pairing(self):
        # 直读权威源（本文件其余契约同款 _load），不走 loader：
        # loader 是 Langfuse production 优先，dev 环境 .env 密钥经 api 导入注入
        # 进程后，本机 Langfuse 的过期部署快照会令权威源契约误红（快照滞后非
        # 代码回归；AGENTS.md：prompts/*.md 是唯一权威源，Langfuse 仅为快照）。
        assert "最新披露" in _load("bear_debater.md")
        assert "并列呈现" in _load("bear_debater.md")

    def test_focus_summary_builder_requires_pairing(self):
        import inspect

        from finance_agent.nodes import report

        src = inspect.getsource(report._build_focus_summary)
        assert "最新披露期次" in src
        assert "并列呈现" in src
        assert "单边" in src


class TestDeepModePeerComparison:
    def test_has_peer_comparison_branch(self):
        text = _load("deep_mode.md")
        assert "类型 D" in text
        assert "peer_codes" in text

    def test_single_pipeline_constraint_preserved(self):
        """对比=单次管线：禁止多跑的纪律仍在。"""
        text = _load("deep_mode.md")
        assert "单次" in text
        assert "禁止为多标的分别发起多次完整分析" in text

    def test_ambiguous_primary_disclosure(self):
        text = _load("deep_mode.md")
        assert "已以" in text and "主视角" in text


class TestFundamentalPeerConsumption:
    def test_has_peer_consumption_rule(self):
        text = _load("fundamental_analyst.md")
        assert "同业对比材料消费" in text
        assert "peer_comparison" in text

    def test_missing_marker_and_caliber(self):
        text = _load("fundamental_analyst.md")
        assert "—" in text  # 缺失标记不得虚构补齐
        assert "口径" in text


def test_fundamental_analyst_banking_cashflow_exemption():
    """add-banking-industry-calibration：银行业 OCF 倍数与偿债阈值豁免指令。"""
    prompt = (_PROMPTS_DIR / "fundamental_analyst.md").read_text(encoding="utf-8")
    assert "金融业" in prompt or "银行业" in prompt
    assert "不适用" in prompt
    assert "MUST NOT" in prompt or "不得" in prompt
