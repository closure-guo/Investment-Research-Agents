"""研报数据面四表存储测试（add-research-report-dataplane）。

覆盖：期次键幂等、跨源合并、评级事件幂等、预期快照三态、覆盖状态三态、
分析师档案聚合、运行记录。
"""

from __future__ import annotations

import sqlite3
from datetime import date, timedelta

from finance_agent.data import research_reports_store as store


def _report(**over):
    row = {
        "report_type": "stock",
        "title": "宝信软件2026三季报点评：IDC 业务回暖",
        "org": "国元证券",
        "analyst": "张三",
        "publish_date": "2026-10-19",
        "code": "600845",
        "name": "宝信软件",
        "industry": "IT服务Ⅱ",
        "rating": "买入",
        "eps_forecasts": {"2026": 0.57, "2027": 0.68},
        "pe_forecasts": {"2026": 30.0},
        "pdf_url": "https://example.invalid/a.pdf",
        "detail_url": "https://example.invalid/a.html",
        "source": "eastmoney",
    }
    row.update(over)
    return row


def _count(db_path, table: str) -> int:
    conn = sqlite3.connect(db_path)
    try:
        return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]  # noqa: S608
    finally:
        conn.close()


class TestReportUpsert:
    def test_same_batch_twice_idempotent(self, tmp_path):
        db = tmp_path / "rr.db"
        assert store.upsert_reports([_report()], db_path=db) == 1
        assert store.upsert_reports([_report()], db_path=db) == 1
        assert _count(db, "research_reports") == 1

    def test_cross_source_same_report_merges_source(self, tmp_path):
        db = tmp_path / "rr.db"
        store.upsert_reports([_report(source="eastmoney")], db_path=db)
        store.upsert_reports([_report(source="sina")], db_path=db)
        assert _count(db, "research_reports") == 1
        conn = sqlite3.connect(db)
        try:
            source = conn.execute("SELECT source FROM research_reports").fetchone()[0]
        finally:
            conn.close()
        assert set(source.split(",")) == {"eastmoney", "sina"}

    def test_report_key_normalizes_whitespace(self):
        a = store.report_key("  宝信软件 点评：IDC 业务回暖 ", "国元证券", "2026-10-19")
        b = store.report_key("宝信软件 点评：IDC 业务回暖", "国元证券", "2026-10-19")
        assert a == b
        assert a != store.report_key("另一篇报告", "国元证券", "2026-10-19")


class TestRatingChangeUpsert:
    def _event(self, **over):
        row = {
            "code": "600845",
            "name": "宝信软件",
            "publish_date": "2026-10-16",
            "org": "国盛证券",
            "analyst": "李四",
            "rating": "买入",
            "is_first_rating": False,
            "rating_change": "维持",
            "prev_rating": "买入",
            "target_low": None,
            "target_high": None,
            "source": "cninfo",
        }
        row.update(over)
        return row

    def test_rating_event_idempotent(self, tmp_path):
        db = tmp_path / "rr.db"
        store.upsert_rating_changes([self._event()], db_path=db)
        store.upsert_rating_changes([self._event()], db_path=db)
        assert _count(db, "rating_changes") == 1

    def test_different_prev_rating_is_new_event(self, tmp_path):
        db = tmp_path / "rr.db"
        store.upsert_rating_changes([self._event()], db_path=db)
        store.upsert_rating_changes([self._event(prev_rating="增持")], db_path=db)
        assert _count(db, "rating_changes") == 2


class TestForecastSnapshot:
    def _row(self, code="600845", **over):
        row = {
            "code": code,
            "name": "宝信软件",
            "report_count": 4,
            "rating_buy": 3,
            "rating_add": 1,
            "rating_neutral": 0,
            "rating_reduce": 0,
            "rating_sell": 0,
            "eps_y1": 0.57,
            "eps_y2": 0.68,
            "eps_y3": 0.79,
            "eps_y1_year": 2026,
            "eps_y2_year": 2027,
            "eps_y3_year": 2028,
            "source": "eastmoney",
        }
        row.update(over)
        return row

    def test_snapshot_idempotent_per_day(self, tmp_path):
        db = tmp_path / "rr.db"
        store.upsert_earnings_forecasts("2026-10-19", [self._row()], db_path=db)
        store.upsert_earnings_forecasts("2026-10-19", [self._row()], db_path=db)
        assert _count(db, "earnings_forecasts") == 1

    def test_consensus_covered(self, tmp_path):
        db = tmp_path / "rr.db"
        store.upsert_earnings_forecasts("2026-10-19", [self._row()], db_path=db)
        got = store.get_consensus("600845", db_path=db)
        assert got["status"] == "covered"
        assert got["rating_buy"] == 3
        assert got["eps_y1"] == 0.57
        assert got["snapshot_date"] == "2026-10-19"

    def test_consensus_no_coverage_when_snapshot_exists(self, tmp_path):
        db = tmp_path / "rr.db"
        store.upsert_earnings_forecasts("2026-10-19", [self._row()], db_path=db)
        got = store.get_consensus("000001", db_path=db)
        assert got["status"] == "no_coverage"

    def test_consensus_missing_when_no_snapshot(self, tmp_path):
        db = tmp_path / "rr.db"
        assert store.get_consensus("600845", db_path=db)["status"] == "missing"


class TestCoverageStatus:
    def test_active_recent_report(self, tmp_path):
        db = tmp_path / "rr.db"
        today = date.today().isoformat()
        store.upsert_reports([_report(publish_date=today)], db_path=db)
        got = store.coverage_status("600845", db_path=db)
        assert got["status"] == "active"
        assert got["latest_report_date"] == today
        assert got["reports_30d"] == 1

    def test_stale_when_oldest_older_than_threshold(self, tmp_path):
        db = tmp_path / "rr.db"
        old = (date.today() - timedelta(days=200)).isoformat()
        store.upsert_reports([_report(publish_date=old)], db_path=db)
        got = store.coverage_status("600845", db_path=db)
        assert got["status"] == "stale"
        assert got["latest_report_date"] == old
        assert got["reports_30d"] == 0

    def test_no_coverage_when_never_seen(self, tmp_path):
        db = tmp_path / "rr.db"
        store.init_db(db)
        got = store.coverage_status("600845", db_path=db)
        assert got["status"] == "no_coverage"

    def test_collection_failed_when_run_failed_and_no_rows(self, tmp_path):
        db = tmp_path / "rr.db"
        store.record_run(
            "research_reports_daily",
            "2026-10-01",
            "2026-10-19",
            "failed",
            0,
            detail="eastmoney unreachable",
            db_path=db,
        )
        got = store.coverage_status("600845", db_path=db)
        assert got["status"] == "collection_failed"


class TestAnalystProfile:
    def test_aggregate_counts(self, tmp_path):
        db = tmp_path / "rr.db"
        today = date.today().isoformat()
        store.upsert_reports(
            [
                _report(title="报告A", publish_date=today),
                _report(title="报告B", publish_date=today, code="600000", name="浦发银行"),
            ],
            db_path=db,
        )
        store.refresh_analysts(db_path=db)
        conn = sqlite3.connect(db)
        try:
            row = conn.execute(
                "SELECT covered_codes, reports_90d, rating_buy FROM analysts "
                "WHERE org='国元证券' AND analyst='张三'"
            ).fetchone()
        finally:
            conn.close()
        assert row == (2, 2, 2)


class TestRunLog:
    def test_record_and_read_latest_run(self, tmp_path):
        db = tmp_path / "rr.db"
        store.record_run("research_reports_daily", "2026-10-17", "2026-10-19", "ok", 42, db_path=db)
        got = store.latest_run("research_reports_daily", db_path=db)
        assert got["status"] == "ok"
        assert got["rows"] == 42


class TestDocumentInbox:
    def _doc(self, **over):
        doc = {
            "doc_key": "hash-1",
            "filename": "国信证券-金融工程日报-261019.pdf",
            "file_path": "inbox/a.pdf",
            "title": "国信证券-金融工程日报-261019",
            "org": "国信证券",
            "publish_date": "2026-10-19",
            "page_count": 3,
            "char_count": 5,
            "text": "全文内容",
            "usage_policy": "personal_research",
            "source_type": "manual_feed",
        }
        doc.update(over)
        return doc

    def test_upsert_document_hash_idempotent(self, tmp_path):
        db = tmp_path / "rr.db"
        assert store.upsert_document(self._doc(), db_path=db) is True
        assert store.upsert_document(self._doc(), db_path=db) is False
        assert _count(db, "report_documents") == 1

    def test_list_excludes_text_and_get_includes_it(self, tmp_path):
        db = tmp_path / "rr.db"
        store.upsert_document(self._doc(), db_path=db)
        listed = store.list_documents(db_path=db)
        assert listed and "text" not in listed[0]
        stored = store.get_document("hash-1", db_path=db)
        assert stored["text"] == "全文内容"
        assert stored["usage_policy"] == "personal_research"


class TestExtractionStore:
    def _extraction(self, **over):
        data = {
            "broker": "国信证券",
            "analyst": "张三",
            "report_date": "2026-10-19",
            "report_type": "个股研报",
            "stocks": [{"code": "600845", "rating_normalized": "买入", "target_price": 30.0}],
            "core_logic": ["逻辑A"],
            "catalysts": [],
            "risks": ["风险"],
            "summary": "摘要",
        }
        data.update(over)
        return data

    def test_upsert_get_and_overwrite(self, tmp_path):
        db = tmp_path / "rr.db"
        store.upsert_extraction("k1", self._extraction(), model="m", prompt_version=7, db_path=db)
        got = store.get_extraction("k1", db_path=db)
        assert got["broker"] == "国信证券"
        assert got["prompt_version"] == "7"
        store.upsert_extraction("k1", self._extraction(broker="开源证券"), db_path=db)
        assert store.get_extraction("k1", db_path=db)["broker"] == "开源证券"
        assert len(store.list_extractions(db_path=db)) == 1
