"""行业研报注入基本面上下文测试（add-industry-report-context）。"""

from __future__ import annotations

from finance_agent.nodes.analysts import (
    _build_fundamental_context,
    _build_macro_context,
    _build_sentiment_context,
    _build_technical_context,
)

DIGEST = {
    "industry": "电池",
    "status": "ok",
    "report_count": 2,
    "brokers": ["东吴证券", "中信证券"],
    "latest_report_date": "2026-10-20",
    "age_days": 1,
    "documents": [
        {
            "report_key": "k1",
            "title": "动力电池行业跟踪：装机同比回升",
            "org": "东吴证券",
            "analyst": "曾朵红",
            "publish_date": "2026-10-20",
            "depth_class": "comment",
        },
        {
            "report_key": "k2",
            "title": "电池材料价格周报",
            "org": "中信证券",
            "analyst": "李四",
            "publish_date": "2026-10-19",
            "depth_class": "unknown",
        },
    ],
}

UNMATCHED = {
    "industry": "通信服务",
    "status": "unmatched",
    "report_count": 0,
    "brokers": [],
    "latest_report_date": None,
    "age_days": None,
    "documents": [],
}


def test_fundamental_context_injects_digest():
    context = _build_fundamental_context({"industry_report_digest": DIGEST})
    assert "同行业研报" in context
    assert "industry_report_digest" in context
    assert "仅元数据" in context
    assert "动力电池行业跟踪" in context
    assert "不得直接作为结论依据" in context


def test_fundamental_context_without_digest_has_no_section():
    context = _build_fundamental_context({})
    assert "同行业研报" not in context


def test_unmatched_digest_never_claims_no_reports():
    context = _build_fundamental_context({"industry_report_digest": UNMATCHED})
    assert "通信服务" in context
    assert "无研报" not in context


def test_other_analysts_do_not_consume_digest():
    for builder, state in (
        (_build_macro_context, {"macro_indicators": {}, "industry_report_digest": DIGEST}),
        (_build_technical_context, {"industry_report_digest": DIGEST}),
        (_build_sentiment_context, {"industry_report_digest": DIGEST}),
    ):
        assert "同行业研报" not in builder(state)


def _patch_digest_sources(monkeypatch, seen: dict):
    from finance_agent.data import research_reports as rr

    monkeypatch.setattr(rr, "get_consensus_for_analysis", lambda code, **kw: {"status": "missing"})
    monkeypatch.setattr(rr, "get_manual_report_digest", lambda code, **kw: None)
    monkeypatch.setattr(rr, "get_strategy_digest", lambda **kw: None)
    monkeypatch.setattr(
        rr, "get_industry_digest", lambda industry, **kw: seen.setdefault("industry", industry)
    )


def test_attach_report_digests_passes_stock_industry(monkeypatch):
    from finance_agent.nodes.fetch import _attach_report_digests

    seen: dict = {}
    _patch_digest_sources(monkeypatch, seen)
    _attach_report_digests({"industry_info": {"industry": "电池"}}, "300750")
    assert seen["industry"] == "电池"


def test_attach_report_digests_missing_industry_is_none(monkeypatch):
    from finance_agent.nodes.fetch import _attach_report_digests

    seen: dict = {}
    _patch_digest_sources(monkeypatch, seen)
    _attach_report_digests({}, "300750")
    assert seen["industry"] is None
