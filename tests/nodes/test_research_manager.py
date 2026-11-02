"""TDD tests for nodes/research_manager.py — Layer II Research Manager。

D1/RM 结构化评级（harden-decision-report-semantics）：JSON 输出（reasoning 在前
rating 在后——先推理后结论）、评级前置拼装、解析失败降级不中断。
"""

import json
import logging
from unittest.mock import patch

from finance_agent.models import DebateMessage
from finance_agent.nodes.research_manager import (
    _build_research_context,
    format_assumptions_text,
    research_manager,
)


class TestResearchManager:
    """Layer II Research Manager — 总结 Bull/Bear 辩论。"""

    @patch("finance_agent.nodes.research_manager.call_llm_streaming_with_fallback")
    def test_produces_conclusion(self, mock_llm):
        mock_llm.return_value = "综合多空双方观点，基本面强劲但需关注估值风险。"
        state = {
            "analyst_reports": {},
            "debate_history": [],
        }
        result = research_manager(state)
        assert "research_manager_conclusion" in result
        assert "基本面" in result["research_manager_conclusion"]


class TestStructuredRating:
    """RM 结构化评级输出（D1 同族）：JSON → 评级前置 + state 直取字段。"""

    @patch("finance_agent.nodes.research_manager.call_llm_streaming_with_fallback")
    def test_json_output_prepends_rating(self, mock_llm):
        mock_llm.return_value = (
            '{"reasoning": "多方论据扎实但估值偏高，空头提示的回调风险真实存在。", '
            '"rating": "看多", "confidence": 0.65}'
        )
        result = research_manager({"analyst_reports": {}, "debate_history": []})
        conclusion = result["research_manager_conclusion"]
        # 评级前置（倒金字塔），reasoning 全文随后
        assert conclusion.startswith("评级: 看多")
        assert "置信度 0.65" in conclusion.split("\n")[0]
        assert "多方论据扎实" in conclusion
        # state 直取字段
        assert result["research_manager_rating"] == "看多"
        assert result["research_manager_confidence"] == 0.65

    def test_rating_prefix_at_front(self):
        """评级行必须是 conclusion 的第一行（消费端无需解析正文即可直取方向）。"""
        mock_text = '{"reasoning": "多空僵持，证据均衡。", "rating": "中性", "confidence": 0.5}'
        with patch(
            "finance_agent.nodes.research_manager.call_llm_streaming_with_fallback"
        ) as mock_llm:
            mock_llm.return_value = mock_text
            result = research_manager({"analyst_reports": {}, "debate_history": []})
        assert result["research_manager_conclusion"].startswith("评级: 中性")
        assert result["research_manager_rating"] == "中性"

    def test_parse_failure_degrades_to_plain_text(self):
        """解析失败降级：原文作 conclusion、rating/confidence 为 None，不中断。"""
        with patch(
            "finance_agent.nodes.research_manager.call_llm_streaming_with_fallback"
        ) as mock_llm:
            mock_llm.return_value = "这不是 JSON 的自由文本结论。"
            result = research_manager({"analyst_reports": {}, "debate_history": []})
        assert result["research_manager_conclusion"] == "这不是 JSON 的自由文本结论。"
        assert result["research_manager_rating"] is None
        assert result["research_manager_confidence"] is None
        assert result.get("parse_degraded") is True


class TestContextNumbering:
    """spec：RM 上下文复用「R{n} 论点: ①…」编号行；无 key_arguments 退化基础格式。"""

    def test_numbered_arguments_enter_context(self):
        msg = DebateMessage(
            role="bull",
            round=1,
            content="看多正文",
            key_arguments=[
                {"text": "份额 35% 创新高", "kind": "data", "anchors": ["x.y"]},
                {"text": "回购提振", "kind": "event", "anchors": ["回购公告"]},
            ],
        )
        ctx = _build_research_context({"analyst_reports": {}, "debate_history": [msg]})
        assert "R1 论点: ①.份额 35% 创新高 ②.回购提振" in ctx
        assert "bull(R1): 看多正文" in ctx

    def test_missing_key_arguments_degrades(self):
        ctx = _build_research_context(
            {
                "analyst_reports": {},
                "debate_history": [{"role": "bear", "round": 1, "content": "空方正文"}],
            }
        )
        assert "bear(R1): 空方正文" in ctx
        assert "论点:" not in ctx


def _valid_table():
    rows = []
    for code, dim in (
        ("H1", "竞争格局"),
        ("H2", "成长驱动"),
        ("H3", "盈利质量"),
        ("H4", "估值与预期"),
    ):
        rows.append(
            {
                "code": code,
                "dimension": dim,
                "assumption": f"{dim}命题 ≥ 30%",
                "bull_evidence": [f"Bull R1 论点①: {dim}利多"],
                "bear_evidence": [],
                "inflection_signal": f"{dim}连续两季恶化",
                "status": "成立",
                "time_tag": "long",
            }
        )
    return rows


def _rm_json(table):
    return json.dumps(
        {
            "reasoning": "多空交锋后偏多。",
            "rating": "看多",
            "confidence": 0.62,
            "assumption_table": table,
        },
        ensure_ascii=False,
    )


class TestAssumptionTable:
    """spec：合法表落 state + 拼进结论；非法整表丢弃不触碰评级降级语义。"""

    @patch("finance_agent.nodes.research_manager.call_llm_streaming_with_fallback")
    def test_valid_table_lands_state_and_conclusion(self, mock_llm):
        mock_llm.return_value = _rm_json(_valid_table())
        result = research_manager({"analyst_reports": {}, "debate_history": []})
        assert result["research_manager_assumptions"] == _valid_table()
        conclusion = result["research_manager_conclusion"]
        assert conclusion.startswith("评级: 看多")
        assert "| H1 竞争格局 |" in conclusion
        assert "✅ 成立" in conclusion
        assert "Bull R1 论点①: 竞争格局利多" in conclusion
        assert result["research_manager_rating"] == "看多"

    @patch("finance_agent.nodes.research_manager.call_llm_streaming_with_fallback")
    def test_missing_row_drops_table_keeps_rating(self, mock_llm, caplog):
        table = [r for r in _valid_table() if r["code"] != "H3"]
        mock_llm.return_value = _rm_json(table)
        with caplog.at_level(logging.WARNING, logger="finance_agent.research_manager"):
            result = research_manager({"analyst_reports": {}, "debate_history": []})
        assert result["research_manager_assumptions"] is None
        assert "| H1" not in result["research_manager_conclusion"]
        assert result["research_manager_rating"] == "看多"
        assert result["research_manager_confidence"] == 0.62
        assert "parse_degraded" not in result
        assert any("H3" in r.message for r in caplog.records)

    @patch("finance_agent.nodes.research_manager.call_llm_streaming_with_fallback")
    def test_bad_enum_drops_table(self, mock_llm):
        table = _valid_table()
        table[1]["status"] = "大概成立"
        mock_llm.return_value = _rm_json(table)
        result = research_manager({"analyst_reports": {}, "debate_history": []})
        assert result["research_manager_assumptions"] is None
        assert result["research_manager_rating"] == "看多"

    @patch("finance_agent.nodes.research_manager.call_llm_streaming_with_fallback")
    def test_unsupported_row_no_evidence_drops(self, mock_llm):
        table = _valid_table()
        table[3]["bull_evidence"] = []
        table[3]["bear_evidence"] = []
        mock_llm.return_value = _rm_json(table)
        result = research_manager({"analyst_reports": {}, "debate_history": []})
        assert result["research_manager_assumptions"] is None

    @patch("finance_agent.nodes.research_manager.call_llm_streaming_with_fallback")
    def test_free_text_degradation_sets_assumptions_none(self, mock_llm):
        mock_llm.return_value = "自由文本结论。"
        result = research_manager({"analyst_reports": {}, "debate_history": []})
        assert result.get("parse_degraded") is True
        assert result["research_manager_assumptions"] is None

    def test_pipe_in_cell_is_escaped(self):
        table = _valid_table()
        table[0]["assumption"] = "A|B"
        text = format_assumptions_text(table)
        assert "A／B" in text and "| A|B |" not in text
