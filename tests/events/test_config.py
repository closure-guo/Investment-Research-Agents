"""事件域名白名单契约（add-key-events-cache Req2）。"""

from finance_agent.events.config import ALLOWED_DOMAINS, WEBSEARCH_QUERY_TEMPLATE
from finance_agent.events.web_fetcher import _domain_allowed


def test_whitelist_contains_news_sources_added_by_key_events_cache():
    for domain in (
        "cls.cn",
        "xueqiu.com",
        "news.qq.com",
        "investing.com",
        "yicai.com",
        "nbd.com.cn",
    ):
        assert domain in ALLOWED_DOMAINS, f"缺少新增域名: {domain}"


def test_whitelist_size_is_pinned():
    # 21 域：原 15 + 新增 6；增删须先改 spec（openspec/specs/key-events）
    assert len(ALLOWED_DOMAINS) == 21


def test_quote_page_domains_are_not_whitelisted():
    for domain in ("cfi.cn", "9fzt.com"):
        assert domain not in ALLOWED_DOMAINS, f"行情页域名不应入选: {domain}"


def test_domain_allowed_matches_substring():
    assert _domain_allowed("https://www.cls.cn/detail/2385250")
    assert _domain_allowed("https://xueqiu.com/s/sh601952")
    assert _domain_allowed("https://cn.investing.com/news/article-1")
    assert not _domain_allowed("https://quote.cfi.cn/quote23007_601952.html")
    assert not _domain_allowed("https://stock.9fzt.com/index/sh_601952.html")


def test_websearch_query_template_snapshot():
    """查询模板快照（issue #535-3）：模板本体被改坏无测试拦截——钉死原文。

    带名查询召回依赖三占位符齐备（缺 {stock_name} 会静默退化纯代码查询）；
    有意改模板时请同步更新本快照并在改动说明里给出召回对比依据。
    """
    assert WEBSEARCH_QUERY_TEMPLATE == "{stock_name} {stock_code} {year_range} 提价 渠道 业绩 产品"
    # 占位符齐备性独立断言：即使快照被有意更新，三占位符缺一即拦
    for token in ("{stock_name}", "{stock_code}", "{year_range}"):
        assert token in WEBSEARCH_QUERY_TEMPLATE
