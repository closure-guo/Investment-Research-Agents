"""行业研报标题纳入 citation 回声源测试（add-industry-report-context）。"""

from __future__ import annotations

from finance_agent.citation import collect_text_sources


def test_collects_industry_digest_titles():
    sources = collect_text_sources(
        {
            "industry_report_digest": {
                "industry": "电池",
                "status": "ok",
                "documents": [
                    {"title": "动力电池行业跟踪：装机同比回升"},
                    {"title": "电池材料价格周报"},
                ],
            }
        }
    )
    assert "动力电池行业跟踪：装机同比回升" in sources
    assert "电池材料价格周报" in sources


def test_ignores_empty_titles():
    sources = collect_text_sources(
        {"industry_report_digest": {"documents": [{"title": ""}, {}, {"org": "东吴证券"}]}}
    )
    assert "" not in sources
