"""策略观点注入宏观分析师上下文测试（add-macro-strategy-report-context）。"""

from __future__ import annotations

from finance_agent.nodes.analysts import _build_fundamental_context, _build_macro_context

DIGEST = {
    "report_count": 1,
    "brokers": ["UBS"],
    "single_broker": True,
    "latest_report_date": "2026-10-19",
    "sector_views": {
        "银行": {"directions": {"看多": 1}, "rationales": ["净息差企稳"]},
    },
    "disagreements": [],
    "core_logic": ["外资情绪谨慎"],
    "catalysts": ["政策响应"],
    "risks": ["地产硬着陆"],
    "summaries": [
        {"broker": "UBS", "report_date": "2026-10-19", "summary": "外资谨慎，偏好银行。"}
    ],
    "market_views": [
        {
            "broker": "UBS",
            "report_date": "2026-10-19",
            "market_view": {"sentiment": "谨慎"},
        }
    ],
    "documents": [],
}


def test_macro_context_injects_digest():
    context = _build_macro_context({"macro_indicators": {}, "strategy_report_digest": DIGEST})
    assert "卖方策略观点（state 键 strategy_report_digest）" in context
    assert "净息差企稳" in context
    assert "单机构观点" in context
    assert "不得直接作为结论依据" in context


def test_macro_context_without_digest_has_no_section():
    context = _build_macro_context({"macro_indicators": {}})
    assert "卖方策略观点" not in context


def test_fundamental_context_does_not_consume_digest():
    context = _build_fundamental_context({"strategy_report_digest": DIGEST})
    assert "卖方策略观点" not in context
