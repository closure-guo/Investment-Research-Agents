"""外资二手快讯测试（add-foreign-broker-sentiment）。

覆盖：RSS/JSON 解析、关键字过滤、摘要截断、单源失败、幂等、抽取失败保留、只读统计。
"""

from __future__ import annotations

import sqlite3

from finance_agent.data import foreign_broker_news as fbn

RSS = """<?xml version="1.0"?>
<rss version="2.0"><channel>
<item>
  <title>UBS 上调中国银行股评级</title>
  <link>https://example.invalid/a</link>
  <pubDate>Mon, 20 Oct 2026 08:00:00 GMT</pubDate>
  <description>&lt;p&gt;瑞银称中国银行股净息差企稳。&lt;/p&gt;</description>
</item>
<item>
  <title>Apple 发布新品</title>
  <link>https://example.invalid/b</link>
  <pubDate>Mon, 20 Oct 2026 09:00:00 GMT</pubDate>
  <description>无关新闻</description>
</item>
</channel></rss>"""

JSON_FEED = """[{"title":"高盛看多 A股","url":"https://example.invalid/c",
"published_at":"2026-10-19","summary":"高盛上调中国股票配置"}]"""


def _extracted(item):
    return (
        {
            "broker": "UBS",
            "published_at": item.get("published_at"),
            "action": "rating_up",
            "target_type": "sector",
            "target": "银行",
            "direction": "positive",
            "summary": "瑞银上调中国银行股评级",
            "confidence": "high",
        },
        {"model": "fake", "prompt_version": "1"},
    )


class TestParsing:
    def test_parse_rss(self):
        items = fbn.parse_feed(RSS, source="example")
        assert len(items) == 2
        assert items[0]["title"] == "UBS 上调中国银行股评级"
        assert items[0]["url"] == "https://example.invalid/a"
        assert items[0]["published_at"] == "2026-10-20"
        assert "净息差企稳" in items[0]["summary"]

    def test_parse_json(self):
        items = fbn.parse_feed(JSON_FEED, source="json")
        assert len(items) == 1
        assert items[0]["published_at"] == "2026-10-19"

    def test_keyword_filter(self, monkeypatch):
        monkeypatch.delenv("FOREIGN_NEWS_KEYWORDS", raising=False)
        assert fbn.matches_keywords({"title": "UBS 中国股票策略", "summary": ""})
        assert not fbn.matches_keywords({"title": "UBS 美股策略", "summary": ""})
        assert not fbn.matches_keywords({"title": "某公司公告", "summary": ""})

    def test_summary_truncated(self):
        item = fbn._normalize_item({"title": "UBS 中国", "summary": "x" * 900}, source="s")
        assert len(item["summary"]) == 500


class TestDailyRun:
    def test_empty_feeds_noop(self, tmp_path):
        db = tmp_path / "rr.db"
        result = fbn.run_foreign_broker_news_daily(feeds=[], db_path=db)
        assert result["status"] == "empty"
        assert result["ingested"] == 0
        conn = sqlite3.connect(db)
        try:
            assert conn.execute("SELECT COUNT(*) FROM foreign_broker_events").fetchone()[0] == 0
        finally:
            conn.close()

    def test_single_source_failure_degrades(self, tmp_path):
        db = tmp_path / "rr.db"

        def fetcher(url):
            if url == "bad":
                raise RuntimeError("503")
            return RSS

        result = fbn.run_foreign_broker_news_daily(
            feeds=["bad", "good"], fetcher=fetcher, extractor=_extracted, db_path=db
        )
        assert result["status"] == "degraded"
        assert result["ingested"] == 1
        assert result["errors"]

    def test_idempotent(self, tmp_path):
        db = tmp_path / "rr.db"
        kwargs = {
            "feeds": ["good"],
            "fetcher": lambda url: RSS,
            "extractor": _extracted,
            "db_path": db,
        }
        first = fbn.run_foreign_broker_news_daily(**kwargs)
        second = fbn.run_foreign_broker_news_daily(**kwargs)
        assert first["ingested"] == 1
        assert second["skipped_existing"] == 1
        conn = sqlite3.connect(db)
        try:
            assert conn.execute("SELECT COUNT(*) FROM foreign_broker_events").fetchone()[0] == 1
        finally:
            conn.close()

    def test_extraction_failure_keeps_raw(self, tmp_path):
        db = tmp_path / "rr.db"

        def broken(item):
            raise ValueError("bad json")

        result = fbn.run_foreign_broker_news_daily(
            feeds=["good"], fetcher=lambda url: RSS, extractor=broken, db_path=db
        )
        assert result["status"] == "degraded"
        assert result["ingested"] == 1
        rows = fbn.list_foreign_sentiment(db_path=db)["rows"]
        assert rows[0]["extraction_status"] == "failed"
        assert rows[0]["title"] == "UBS 上调中国银行股评级"

    def test_json_feed_and_filters(self, tmp_path):
        db = tmp_path / "rr.db"
        fbn.run_foreign_broker_news_daily(
            feeds=["json"], fetcher=lambda url: JSON_FEED, extractor=_extracted, db_path=db
        )
        result = fbn.list_foreign_sentiment(action="rating_up", db_path=db)
        assert result["status"] == "ok"
        assert result["counts"]["total"] == 1
        assert result["counts"]["by_broker"]["UBS"] == 1
