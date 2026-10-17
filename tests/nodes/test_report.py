"""TDD tests for nodes/report.py — 5 层架构报告生成节点。

节点行为：
1. 从 state 读取 5 层 Agent 输出
2. 组装为结构化 Markdown 报告
3. 返回 {"final_report": markdown}
"""

from unittest.mock import patch

import pytest

from finance_agent.models import AnalystReport, TradeDecision
from finance_agent.nodes.report import generate_report


class TestGenerateReport:
    """报告生成节点测试。"""

    def test_report_contains_analyst_summaries(self):
        """报告包含各分析师的摘要。"""
        state = {
            "stock_name": "贵州茅台",
            "stock_code": "600519",
            "analyst_reports": {
                "fundamental": AnalystReport(
                    agent_name="fundamental",
                    summary="基本面强劲",
                    plain_conclusion="结论：基本面强劲",
                    key_findings=["ROE 28.33%"],
                    claims=[],
                    markdown="## 基本面分析\n...",
                ),
                "technical": AnalystReport(
                    agent_name="technical",
                    summary="技术面偏多",
                    plain_conclusion="结论：技术面偏多",
                    key_findings=["MA5 上穿 MA20"],
                    claims=[],
                    markdown="## 技术面分析\n...",
                ),
            },
        }
        result = generate_report(state)
        report = result["final_report"]
        assert "基本面强劲" in report
        assert "技术面偏多" in report

    def test_report_renders_peer_comparison_section(self):
        """add-peer-comparison R4：报告确定性渲染同业对照表——分析师 markdown 正文
        不进报告（摘要式渲染是既有设计），对照段必须由代码直出，不依赖 LLM 转述。"""
        state = {
            "stock_name": "贵州茅台",
            "stock_code": "600519",
            "peer_codes": ["000858"],
            "peer_comparison": (
                "同业对比（主标的首行；估值为行情快照口径，财务组为最新报告期累计同比口径）：\n"
                "| 名称 | 代码 | PE | PB | 总市值(亿) | 营收同比(%) | 归母净利同比(%) | 毛利率(%) | 报告期 |\n"
                "|---|---|---|---|---|---|---|---|---|\n"
                "| 贵州茅台 | 600519 | 19.39 | 6.28 | 15788.53 | 1.30 | -1.95 | 89.73 | 2026-06-30 |\n"
                "| 五粮液 | 000858 | — | 2.31 | 2738.86 | 20.87 | 89.30 | 80.29 | 2026-06-30 |"
            ),
            "relative_valuation": {
                "PE": {
                    "target": 19.39,
                    "peer_avg": None,
                    "peer_min": None,
                    "peer_max": None,
                    "conclusion": "N/A",
                },
                "PB": {
                    "target": 6.28,
                    "peer_avg": 2.31,
                    "peer_min": 2.31,
                    "peer_max": 2.31,
                    "conclusion": "overvalued",
                },
            },
        }
        result = generate_report(state)
        report = result["final_report"]
        assert "同业对比" in report
        assert "五粮液" in report and "2.31" in report and "2738.86" in report
        assert "口径" in report  # 材料自带口径标注随表渲染
        assert "相对同业偏高" in report  # 相对估值结论（PE N/A 不渲染）
        assert "相对估值（PE）" not in report

    def test_report_renders_peer_missing_declaration(self):
        """对比请求成立但同业数据不可得 → 报告渲染缺失声明（R4 场景「peer 缺失如实声明」）。"""
        state = {
            "stock_name": "贵州茅台",
            "stock_code": "600519",
            "peer_codes": ["000858"],
            "peer_comparison": "同业数据不可用（抓取失败或未执行），无法提供同业对比；不作对比结论。",
        }
        result = generate_report(state)
        assert "同业数据不可用" in result["final_report"]

    def test_report_no_peer_section_without_peer_request(self):
        """未携带 peer_codes 且无 peer_comparison → 报告不含同业对比章节（回归）。"""
        state = {"stock_name": "贵州茅台", "stock_code": "600519"}
        result = generate_report(state)
        assert "同业对比" not in result["final_report"]

    def test_report_contains_trade_decision(self):
        """报告包含交易决策。"""
        state = {
            "stock_name": "贵州茅台",
            "stock_code": "600519",
            "final_trade_decision": TradeDecision(
                action="buy",
                confidence=0.75,
                reasoning="ROE 持续高于 15%",
            ),
        }
        result = generate_report(state)
        report = result["final_report"]
        assert "buy" in report
        assert "75%" in report

    def test_report_contains_fund_manager_decision(self):
        """报告包含基金经理决策（以中文标注呈现，非原始英文枚举值）。"""
        state = {
            "stock_name": "贵州茅台",
            "stock_code": "600519",
            "fund_manager_decision": "approve",
        }
        result = generate_report(state)
        report = result["final_report"]
        assert "审批通过" in report

    @pytest.mark.parametrize(
        ("decision", "expected"),
        [
            ("approve", "审批通过"),
            ("reject", "未通过审批"),
            ("return", "退回"),
        ],
    )
    def test_fund_manager_decision_chinese_annotation(self, decision, expected):
        """三种决策各自渲染为语义明确的中文标注（ADR-0011 Layer V）。

        加固前 report.py 只输出 `**reject**`，读者无法从报告识别「未通过审批」。
        """
        state = {
            "stock_name": "贵州茅台",
            "stock_code": "600519",
            "fund_manager_decision": decision,
        }
        report = generate_report(state)["final_report"]
        assert expected in report

    def test_focus_summary_generated_without_focus(self):
        """D3（3.1）：focus 为空时仍生成聚焦摘要（fallback 结构化拼接）并落 state——
        研究聚焦是无条件产物，不再依赖用户填写 focus。"""
        state = {
            "stock_name": "贵州茅台",
            "stock_code": "600519",
            "fund_manager_decision": "approve",
            "research_manager_conclusion": "中性偏谨慎：证据均衡，等待右侧信号。",
        }
        result = generate_report(state)
        assert result.get("focus_summary"), "focus 为空也应有聚焦摘要（fallback）"

    def test_focus_summary_reused_when_present(self, monkeypatch):
        """渲染幂等（2026-09-19）：state 已有非空 focus_summary 时复用、不重烧 LLM——
        评估外科手术臂据此冻结导语（共享层 byte 一致），首跑行为零变化。"""
        from finance_agent.nodes import report as report_mod

        calls = []

        def fake_build(state, focus, tags):
            calls.append(1)
            return "新采样的导语"

        monkeypatch.setattr(report_mod, "_build_focus_summary", fake_build)
        state = {
            "stock_name": "贵州茅台",
            "stock_code": "600519",
            "focus_summary": "既有导语（冻结值）",
            "research_manager_conclusion": "中性。",
        }
        out = report_mod.generate_report(state)
        assert calls == [], "已有非空 focus_summary 不应重算"
        assert out["focus_summary"] == "既有导语（冻结值）"
        assert "既有导语（冻结值）" in out["final_report"]

    def test_focus_summary_still_generated_when_absent(self, monkeypatch):
        """无预置值时行为不变（首跑语义零变化）。"""
        from finance_agent.nodes import report as report_mod

        monkeypatch.setattr(report_mod, "_build_focus_summary", lambda s, f, tags: "新采样导语")
        state = {"stock_name": "贵州茅台", "stock_code": "600519"}
        out = report_mod.generate_report(state)
        assert out["focus_summary"] == "新采样导语"

    def test_focus_summary_from_llm_landed_in_state(self):
        """D3：LLM 生成的聚焦摘要 SHALL 写入 state["focus_summary"]（judge 变量直取源）。"""
        from finance_agent.nodes import report as report_mod

        state = {
            "stock_name": "贵州茅台",
            "stock_code": "600519",
            "focus": "全面分析贵州茅台的投资价值",
            "research_manager_conclusion": "中性：证据均衡。",
        }
        with patch.object(report_mod, "complete_text") as mock_ct:
            mock_ct.return_value = ("聚焦摘要：多空均衡，建议观望。", {})
            result = generate_report(state)
        assert result.get("focus_summary") == "聚焦摘要：多空均衡，建议观望。"
        assert "## 研究聚焦" in result["final_report"]

    def test_fm_action_and_ruling_action_shown_side_by_side(self):
        """D1：FM 操作定性（action/置信度）与裁决 action 并排展示——
        「批准的是什么方案」直接可见，方向相悖时矛盾自明（agent-node-contracts）。"""
        state = {
            "stock_name": "贵州茅台",
            "stock_code": "600519",
            "fund_manager_decision": "approve",
            "fund_manager_action": "watch",
            "fund_manager_confidence": 0.55,
            "final_trade_decision": {"action": "watch"},
        }
        report = generate_report(state)["final_report"]
        assert "操作定性 watch" in report
        assert "置信度 0.55" in report
        assert "裁决: watch" in report

    def test_fm_action_omitted_when_absent(self):
        """历史 state 无 action/confidence 时保持旧行为（仅中文标注+理由）。"""
        state = {
            "stock_name": "贵州茅台",
            "stock_code": "600519",
            "fund_manager_decision": "approve",
        }
        report = generate_report(state)["final_report"]
        assert "审批通过" in report
        assert "操作定性" not in report

    def test_report_tolerates_legacy_invalid_decision(self):
        """历史非法决策值不应让报告生成抛错，回退显示原始值。

        读路径不因历史数据失败（harden-llm-output-validation Migration Plan）。
        """
        state = {
            "stock_name": "贵州茅台",
            "stock_code": "600519",
            "fund_manager_decision": "revise",  # 加固前可能写入的非法值
        }
        report = generate_report(state)["final_report"]
        assert "revise" in report

    def test_report_contains_stock_header(self):
        """报告包含股票名称和代码。"""
        state = {
            "stock_name": "贵州茅台",
            "stock_code": "600519",
        }
        result = generate_report(state)
        report = result["final_report"]
        assert "贵州茅台" in report
        assert "600519" in report

    def test_focus_reorders_and_folds_sections(self, monkeypatch):
        """focus 命中维度时：重点分析师前置并标星，非重点折叠，出现研究聚焦摘要。"""
        from finance_agent.nodes import report as report_mod

        # 打桩 LLM 摘要调用（complete_text 返回 (text, metadata) 元组），避免真实请求
        monkeypatch.setattr(report_mod, "complete_text", lambda *a, **k: ("围绕估值的摘要文本", {}))

        state = {
            "stock_name": "贵州茅台",
            "stock_code": "600519",
            "focus": "估值是否合理，中长期持有",
            "analyst_reports": {
                "fundamental": AnalystReport(
                    agent_name="fundamental",
                    summary="基本面强劲",
                    plain_conclusion="结论：基本面强劲",
                    key_findings=["ROE 28.33%"],
                    claims=[],
                    markdown="",
                ),
                "technical": AnalystReport(
                    agent_name="technical",
                    summary="技术面偏多",
                    plain_conclusion="结论：技术面偏多",
                    key_findings=["MA5 上穿 MA20"],
                    claims=[],
                    markdown="",
                ),
            },
        }
        result = generate_report(state)
        report = result["final_report"]

        # 研究聚焦摘要出现
        assert "研究聚焦" in report
        assert "围绕估值的摘要文本" in report
        # 报告头部标注研究聚焦
        assert "研究聚焦: 估值是否合理" in report
        # fundamental（命中 valuation/growth）为重点，标星且出现在 technical 之前
        # （标题已中文化，按中文显示名断言顺序）
        assert report.index("基本面分析师") < report.index("技术面分析师")
        assert "★ 重点" in report
        # technical（未命中）被折叠
        assert "<details>" in report


class TestFundManagerReasoningRendered:
    """refine #111：报告渲染 FM 审批理由（在场时）。"""

    def test_report_contains_fm_reasoning(self):
        state = {
            "stock_code": "600519",
            "final_trade_decision": {"action": "watch", "confidence": 0.5, "reasoning": "x"},
            "fund_manager_decision": "reject",
            "fund_manager_decision_reasoning": "最大回撤38.8%超出审慎投资标准",
        }
        md = generate_report(state)["final_report"]
        assert "最大回撤38.8%超出审慎投资标准" in md

    def test_no_reasoning_falls_back_to_annotation_only(self):
        state = {
            "stock_code": "600519",
            "final_trade_decision": {"action": "watch", "confidence": 0.5, "reasoning": "x"},
            "fund_manager_decision": "approve",
        }
        md = generate_report(state)["final_report"]
        assert "审批通过" in md


class TestDeriveFocusFromQuery:
    """D4：focus 兜底——query 提取关注点关键词合成弱 focus。"""

    def test_extracts_keywords_from_query(self):
        from finance_agent.nodes.report import derive_focus_from_query

        focus = derive_focus_from_query("分析贵州茅台当前适不适合作为长期持有股买入")
        assert focus, "应提取出关键词"
        assert "长期" in focus or "持有" in focus

    def test_zero_hit_returns_empty(self):
        from finance_agent.nodes.report import derive_focus_from_query

        assert derive_focus_from_query("帮我看看这只股票") == ""

    def test_empty_query_returns_empty(self):
        from finance_agent.nodes.report import derive_focus_from_query

        assert derive_focus_from_query("") == ""


class TestReportTitlesChinese:
    """报告标题中文化：分析师标题渲染为中文显示名，辩论发言人中文标注。

    此前报告直接以 state 键名作标题（### technical 等），中文报告夹生英文标题。
    显示名映射仅作用于渲染层——state 键名（trace/eval/路由契约）不变，
    未知键名回退原值（新增分析师/历史数据容错）。
    """

    @staticmethod
    def _analyst(name: str) -> AnalystReport:
        return AnalystReport(
            agent_name=name,
            summary=f"{name} 摘要正文",
            plain_conclusion="结论：中性",
            key_findings=[],
            claims=[],
            markdown="",
        )

    def test_four_analyst_titles_rendered_in_chinese(self):
        state = {
            "stock_name": "贵州茅台",
            "stock_code": "600519",
            "analyst_reports": {
                "technical": self._analyst("technical"),
                "macro": self._analyst("macro"),
                "fundamental": self._analyst("fundamental"),
                "sentiment": self._analyst("sentiment"),
            },
        }
        md = generate_report(state)["final_report"]
        assert "### 技术面分析师" in md
        assert "### 宏观分析师" in md
        assert "### 基本面分析师" in md
        assert "### 舆情分析师" in md
        for raw in ("### technical", "### macro", "### fundamental", "### sentiment"):
            assert raw not in md, f"英文键名标题残留：{raw}"

    def test_starred_analyst_title_keeps_chinese(self, monkeypatch):
        """focus 命中时标星标题同样是中文显示名。"""
        from finance_agent.nodes import report as report_mod

        monkeypatch.setattr(report_mod, "complete_text", lambda *a, **k: ("聚焦摘要", {}))
        state = {
            "stock_name": "贵州茅台",
            "stock_code": "600519",
            "focus": "估值是否合理",
            "analyst_reports": {"fundamental": self._analyst("fundamental")},
        }
        md = generate_report(state)["final_report"]
        assert "### 基本面分析师（★ 重点）" in md

    def test_unknown_analyst_key_falls_back_to_raw(self):
        """映射表外的键名（新增分析师/历史数据）回退原值，不空标题。"""
        state = {
            "stock_code": "600519",
            "analyst_reports": {"esg": self._analyst("esg")},
        }
        md = generate_report(state)["final_report"]
        assert "### esg" in md

    def test_risk_debate_speakers_rendered_in_chinese(self):
        state = {
            "stock_code": "600519",
            "risk_debate_history": [
                {"role": "aggressive", "round": 1, "content": "激进方观点", "key_arguments": []},
                {"role": "conservative", "round": 1, "content": "保守方观点", "key_arguments": []},
                {"role": "neutral", "round": 1, "content": "中性方观点", "key_arguments": []},
            ],
        }
        md = generate_report(state)["final_report"]
        assert "**激进风控**: 激进方观点" in md
        assert "**保守风控**: 保守方观点" in md
        assert "**中性风控**: 中性方观点" in md
        for raw in ("aggressive", "conservative", "neutral"):
            assert raw not in md, f"英文 role 残留：{raw}"


class TestTradeDecisionOperationalParams:
    """report-render-operational-params：交易决策节渲染完整操作参数。

    spec 三条硬规则：buy/sell 渲染仓位+入场/止损/目标价（0/缺失如实「未提供」）；
    watch/hold 不渲染硬价格行，渲染结构化「不行动原因」与「再评估触发条件」（缺失
    如实「未申报」，require-watch-hold-rationale）；派生指标（止损距离/赔率）
    由代码按参数原值计算，不采用 reasoning 中心算值。
    """

    def test_buy_renders_full_params(self):
        state = {
            "stock_code": "600519",
            "final_trade_decision": {
                "action": "buy",
                "confidence": 0.5,
                "position_size": "light",
                "entry_price": 52.0,
                "stop_loss": 47.8,
                "target_price": 60.0,
                "reasoning": "证据均衡",
            },
        }
        md = generate_report(state)["final_report"]
        assert "light" in md
        assert "止损" in md and "47.8" in md
        assert "目标价" in md and "60" in md
        assert "入场" in md and "52" in md
        # 派生指标：代码计算（止损距离 4.2/52≈8.1%，赔率 8/4.2≈1.90）
        assert "8.1%" in md
        assert "1.90" in md

    def test_watch_missing_rationale_marked_unprovided(self):
        state = {
            "stock_code": "600519",
            "final_trade_decision": {
                "action": "watch",
                "confidence": 0.5,
                "position_size": "light",
                "entry_price": None,
                "stop_loss": None,
                "target_price": None,
                "reasoning": "等待站稳均线",
            },
        }
        md = generate_report(state)["final_report"]
        assert "watch" in md
        assert "- **不行动原因**: 未申报" in md
        assert "- **再评估触发条件**: 未申报" in md
        # arity 断言（恢复被替换旧用例的「恰好一行」强度）：占位行不得重复渲染
        assert md.count("- **再评估触发条件**:") == 1
        # 锚定渲染器特有的加粗标签行断言：watch/hold 不渲染硬价格行
        param_lines = [
            ln.strip()
            for ln in md.split("\n")
            if any(k in ln for k in ("**入场价**", "**止损价**", "**目标价**"))
        ]
        assert param_lines == []

    def test_watch_renders_structured_rationale(self):
        state = {
            "stock_code": "600519",
            "final_trade_decision": {
                "action": "watch",
                "confidence": 0.5,
                "reasoning": "等待站稳均线",
                "inaction_reason": "估值分位偏高且缺乏催化剂",
                "reeval_triggers": ["价格回落至 1500 以下", "季报毛利率低于 60%"],
            },
        }
        md = generate_report(state)["final_report"]
        assert "- **不行动原因**: 估值分位偏高且缺乏催化剂" in md
        assert "- **再评估触发条件**: ① 价格回落至 1500 以下；② 季报毛利率低于 60%" in md

    def test_watch_pydantic_decision_renders_structured_rationale(self):
        from finance_agent.models import TradeDecision

        state = {
            "stock_code": "600519",
            "final_trade_decision": TradeDecision(
                action="hold",
                confidence=0.6,
                reasoning="r",
                inaction_reason="维持仓位等待趋势确认",
                reeval_triggers=["价格跌破 1450"],
            ),
        }
        md = generate_report(state)["final_report"]
        assert "- **不行动原因**: 维持仓位等待趋势确认" in md
        assert "- **再评估触发条件**: ① 价格跌破 1450" in md

    def test_more_than_ten_triggers_numbered_with_fallback(self):
        """超过 ⑩ 的编号回退为 (11)/(12)（评审 Minor 收口：回退分支此前未测）。"""
        state = {
            "stock_code": "600519",
            "final_trade_decision": {
                "action": "watch",
                "confidence": 0.5,
                "reasoning": "r",
                "inaction_reason": "等待",
                "reeval_triggers": [f"条件{i}" for i in range(1, 13)],
            },
        }
        md = generate_report(state)["final_report"]
        assert "⑩ 条件10" in md
        assert "(11) 条件11" in md and "(12) 条件12" in md
        assert "⑪" not in md

    def test_watch_dict_string_triggers_rendered_leniently(self):
        """dict 形态（历史落库 JSON）triggers 为字符串时宽容渲染为单条目，不抛异常。"""
        state = {
            "stock_code": "600519",
            "final_trade_decision": {
                "action": "hold",
                "confidence": 0.6,
                "reasoning": "维持",
                "inaction_reason": "趋势未确认",
                "reeval_triggers": "价格跌破 1450",
            },
        }
        md = generate_report(state)["final_report"]
        assert "- **再评估触发条件**: ① 价格跌破 1450" in md

    def test_sell_zero_params_marked_unprovided(self):
        """比亚迪形态：stop/target 均为 0，如实「未提供」，其余参数不受影响。"""
        state = {
            "stock_code": "002594",
            "final_trade_decision": {
                "action": "sell",
                "confidence": 0.52,
                "position_size": "light",
                "entry_price": 86.0,
                "stop_loss": 0,
                "target_price": 0,
                "reasoning": "维持卖出方向",
            },
        }
        md = generate_report(state)["final_report"]
        assert "未提供" in md
        assert "86" in md and "light" in md

    def test_derived_metrics_ignore_reasoning_mental_math(self):
        """reasoning 自算赔率错误时，报告渲染代码计算值。"""
        state = {
            "stock_code": "600519",
            "final_trade_decision": {
                "action": "buy",
                "confidence": 0.5,
                "position_size": "light",
                "entry_price": 52.0,
                "stop_loss": 47.8,
                "target_price": 60.0,
                "reasoning": "风险收益比约 2.5:1（自算）",
            },
        }
        md = generate_report(state)["final_report"]
        assert "1.90" in md  # 代码计算值在场
        # 自算值只允许出现在 reasoning 原文里，派生指标行不得采用
        derived_line = [ln for ln in md.split("\n") if "派生指标" in ln]
        assert derived_line and "2.5" not in derived_line[0]

    def test_price_correction_annotation_preserved(self):
        state = {
            "stock_code": "600519",
            "final_trade_decision": {
                "action": "buy",
                "confidence": 0.5,
                "reasoning": "x",
                "price_level_corrected": True,
                "price_level_correction_reason": "entry 偏离参考带，已修正",
            },
        }
        md = generate_report(state)["final_report"]
        assert "价位修正" in md and "entry 偏离参考带，已修正" in md

    def test_position_missing_renders_weitigong(self):
        """spec report-decision-rendering：仓位档位为必含字段——缺失时渲染「未提供」，
        不得整行省略（#140 终审 C-4 既有不一致的代码侧收口）。"""
        state = {
            "stock_code": "600519",
            "final_trade_decision": {
                "action": "watch",
                "confidence": 0.45,
                "reasoning": "趋势未确认",
                "inaction_reason": "低于 0.4 执行阈值",
                "reeval_triggers": ["放量站上 20 日线"],
            },
        }
        md = generate_report(state)["final_report"]
        assert "- **仓位**: 未提供" in md
        # 其余必含字段不受影响
        assert "- **方向**: watch" in md
        assert "- **不行动原因**: 低于 0.4 执行阈值" in md

    def test_reeval_triggers_beyond_ten_use_paren_form(self):
        """触发条件超过 10 条（①-⑩ 用尽）→ 第 11 条起 (N) 形态，不越界。"""
        state = {
            "stock_code": "600519",
            "final_trade_decision": {
                "action": "watch",
                "confidence": 0.45,
                "reasoning": "r",
                "inaction_reason": "x",
                "reeval_triggers": [f"条件{i}" for i in range(1, 12)],
            },
        }
        md = generate_report(state)["final_report"]
        trigger_line = [ln for ln in md.split("\n") if "再评估触发条件" in ln]
        assert trigger_line, "触发条件行缺失"
        assert "⑩ 条件10" in trigger_line[0]
        assert "(11) 条件11" in trigger_line[0]
