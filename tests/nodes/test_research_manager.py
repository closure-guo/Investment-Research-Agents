"""TDD tests for nodes/research_manager.py — Layer II Research Manager。

D1/RM 结构化评级（harden-decision-report-semantics）：JSON 输出（reasoning 在前
rating 在后——先推理后结论）、评级前置拼装、解析失败降级不中断。
"""

from unittest.mock import patch

from finance_agent.models import DebateMessage
from finance_agent.nodes.research_manager import _build_research_context, research_manager


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
