"""策略观点纳入 citation 回声源测试（add-macro-strategy-report-context）。"""

from __future__ import annotations

from finance_agent.citation import collect_text_sources


def test_collects_strategy_digest_items():
    sources = collect_text_sources(
        {
            "strategy_report_digest": {
                "sector_views": {"银行": {"directions": {"看多": 1}, "rationales": ["净息差企稳"]}},
                "core_logic": ["外资情绪谨慎"],
                "catalysts": ["政策响应"],
                "risks": ["地产硬着陆"],
                "summaries": [{"summary": "外资谨慎，偏好银行。"}],
            }
        }
    )
    assert "净息差企稳" in sources
    assert "外资情绪谨慎" in sources
    assert "政策响应" in sources
    assert "地产硬着陆" in sources
    assert "外资谨慎，偏好银行。" in sources
