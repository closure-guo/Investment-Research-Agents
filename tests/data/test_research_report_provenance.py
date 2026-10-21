"""投喂全文溯源与跨源关联测试（complete-research-report-provenance）。

覆盖：页偏移、深度分级、标题归一与关联、抽取页码、入库字段、重解析幂等。
"""

from __future__ import annotations

import json
import sqlite3

from finance_agent.data import research_reports as rr
from finance_agent.data import research_reports_store as store


def _pages():
    return ["第一页正文", "第二页正文", "第三页正文"]


class TestPageOffsets:
    def test_offsets_slice_back_to_pages(self):
        pages = _pages()
        text = "\n".join(pages)
        offsets = rr.page_offsets(pages)
        assert len(offsets) == 3
        for item in offsets:
            assert text[item["start"] : item["end"]] == pages[item["page"] - 1]
        assert offsets[0]["start"] == 0
        assert offsets[-1]["end"] == len(text)

    def test_empty_page_keeps_boundary(self):
        offsets = rr.page_offsets(["abc", "", "de"])
        text = "\n".join(["abc", "", "de"])
        assert len(offsets) == 3
        assert text[offsets[1]["start"] : offsets[1]["end"]] == ""


class TestDepthAndTitle:
    def test_depth_classification(self):
        assert rr.classify_depth("贵州茅台深度报告") == "deep"
        assert rr.classify_depth("贵州茅台2026三季报点评") == "comment"
        assert rr.classify_depth("贵州茅台公告跟踪") == "comment"
        assert rr.classify_depth("贵州茅台") == "unknown"

    def test_normalize_title_removes_punctuation_and_width(self):
        assert rr.normalize_title("贵州茅台（深度）报告") == rr.normalize_title(
            " 贵州茅台 深度 报告 "
        )

    def test_clean_doc_title_strips_org_and_date(self):
        title = rr.clean_doc_title("中信证券-贵州茅台深度报告-261020", "中信证券")
        assert title == "贵州茅台深度报告"


class TestLinkage:
    def _report(self, **over):
        row = {
            "report_type": "stock",
            "title": "贵州茅台深度报告",
            "org": "中信证券",
            "publish_date": "2026-10-20",
            "code": "600519",
            "rating": "买入",
            "eps_forecasts": {"2026": 60.0},
            "source": "eastmoney",
        }
        row.update(over)
        return row

    def test_match_single_candidate(self, tmp_path):
        db = tmp_path / "rr.db"
        store.upsert_reports([self._report()], db_path=db)
        key = rr.match_report_key("中信证券", "2026-10-20", "贵州茅台（深度）报告", db_path=db)
        assert key == store.report_key("贵州茅台深度报告", "中信证券", "2026-10-20")

    def test_ambiguous_candidates_return_none(self, tmp_path):
        db = tmp_path / "rr.db"
        store.upsert_reports(
            [
                self._report(title="贵州茅台 深度"),
                self._report(title="贵州茅台（深度）"),
            ],
            db_path=db,
        )
        assert rr.match_report_key("中信证券", "2026-10-20", "贵州茅台深度", db_path=db) is None

    def test_no_candidate_returns_none(self, tmp_path):
        db = tmp_path / "rr.db"
        assert rr.match_report_key("中信证券", "2026-10-20", "不存在", db_path=db) is None


class TestExtractionSources:
    def test_object_and_string_forms(self):
        coerced = rr._coerce_extraction(
            {
                "broker": "UBS",
                "report_date": "2026-09-30",
                "core_logic": [
                    {"text": "逻辑A", "source_page": 2},
                    "逻辑B",
                ],
                "catalysts": [{"text": "催化A", "source_page": 99}],
                "risks": [],
                "stocks": [],
            }
        )
        assert coerced["core_logic"] == ["逻辑A", "逻辑B"]
        assert coerced["sources"][0] == {
            "kind": "core_logic",
            "index": 0,
            "text": "逻辑A",
            "source_page": 2,
        }
        # 越界页码由调用方在入库前按 page_count 归 null；这里保留原值供校验
        assert coerced["sources"][-1]["source_page"] == 99

    def test_legacy_string_forms_still_work(self):
        coerced = rr._coerce_extraction(
            {"core_logic": ["旧逻辑"], "catalysts": [], "risks": [], "stocks": []}
        )
        assert coerced["core_logic"] == ["旧逻辑"]
        assert coerced["sources"] == []


class TestIngestDocument:
    def _pdf(self, path):
        path.write_bytes(b"%PDF-1.4 fake")

    def test_ingest_writes_offsets_depth_and_link(self, tmp_path, monkeypatch):
        db = tmp_path / "rr.db"
        inbox = tmp_path / "inbox"
        inbox.mkdir()
        pdf = inbox / "中信证券-贵州茅台深度报告-261020.pdf"
        self._pdf(pdf)
        pages = _pages()
        monkeypatch.setattr(
            rr,
            "extract_pdf_text",
            lambda path: ("\n".join(pages), len(pages), rr.page_offsets(pages)),
        )
        store.upsert_reports(
            [
                {
                    "report_type": "stock",
                    "title": "贵州茅台深度报告",
                    "org": "中信证券",
                    "publish_date": "2026-10-20",
                    "code": "600519",
                    "source": "eastmoney",
                }
            ],
            db_path=db,
        )
        result = rr.ingest_inbox(inbox, db_path=db, extract=False)
        assert result["ingested"] == 1
        doc = store.list_documents(db_path=db)[0]
        assert doc["depth_class"] == "deep"
        assert doc["report_key"] == store.report_key("贵州茅台深度报告", "中信证券", "2026-10-20")
        stored = store.get_document(doc["doc_key"], db_path=db)
        offsets = json.loads(stored["page_offsets"])
        assert len(offsets) == 3

    def test_force_reparse_updates_metadata_without_duplicate(self, tmp_path, monkeypatch):
        db = tmp_path / "rr.db"
        inbox = tmp_path / "inbox"
        inbox.mkdir()
        pdf = inbox / "中信证券-贵州茅台深度报告-261020.pdf"
        self._pdf(pdf)
        pages = _pages()
        store.upsert_document(
            {
                "doc_key": rr._document_key(pdf.read_bytes()),
                "filename": pdf.name,
                "file_path": str(pdf),
                "title": pdf.stem,
                "org": "中信证券",
                "publish_date": "2026-10-20",
                "page_count": 3,
                "char_count": len("\n".join(pages)),
                "text": "\n".join(pages),
            },
            db_path=db,
        )
        monkeypatch.setattr(
            rr,
            "extract_pdf_text",
            lambda path: ("\n".join(pages), len(pages), rr.page_offsets(pages)),
        )
        result = rr.ingest_inbox(inbox, db_path=db, extract=False, force_reparse=True)
        assert result["reparsed"] == 1
        assert len(store.list_documents(db_path=db)) == 1
        stored = store.get_document(rr._document_key(pdf.read_bytes()), db_path=db)
        assert stored["page_offsets"] is not None
        assert stored["depth_class"] == "deep"

    def test_force_reparse_missing_file_keeps_row(self, tmp_path):
        db = tmp_path / "rr.db"
        inbox = tmp_path / "inbox"
        inbox.mkdir()
        store.upsert_document(
            {
                "doc_key": "gone",
                "filename": "gone.pdf",
                "file_path": str(inbox / "gone.pdf"),
                "title": "gone",
                "org": "中信证券",
                "publish_date": "2026-10-20",
                "page_count": 1,
                "char_count": 1,
                "text": "x",
            },
            db_path=db,
        )
        result = rr.ingest_inbox(inbox, db_path=db, extract=False, force_reparse=True)
        assert result["errors"]
        assert len(store.list_documents(db_path=db)) == 1
        conn = sqlite3.connect(db)
        try:
            detail = conn.execute(
                "SELECT detail FROM collection_runs WHERE job='research_report_inbox_daily'"
                " ORDER BY run_id DESC LIMIT 1"
            ).fetchone()[0]
        finally:
            conn.close()
        assert "file_missing" in (detail or "")

    def test_parse_failure_still_ingests_with_null_offsets(self, tmp_path, monkeypatch):
        db = tmp_path / "rr.db"
        inbox = tmp_path / "inbox"
        inbox.mkdir()
        (inbox / "中信证券-贵州茅台深度报告-261020.pdf").write_bytes(b"not-a-pdf")
        monkeypatch.setattr(
            rr,
            "extract_pdf_text",
            lambda path: (_ for _ in ()).throw(ValueError("no text layer")),
        )
        result = rr.ingest_inbox(inbox, db_path=db, extract=False)
        assert result["ingested"] == 1
        assert result["errors"]
        doc = store.list_documents(db_path=db)[0]
        stored = store.get_document(doc["doc_key"], db_path=db)
        assert stored["page_offsets"] is None
        assert stored["char_count"] == 0
        assert stored["depth_class"] == "deep"


class TestMigration:
    def test_old_database_gets_new_columns(self, tmp_path):
        db = tmp_path / "old.db"
        conn = sqlite3.connect(db)
        conn.executescript(
            """
            CREATE TABLE report_documents (
              doc_key TEXT PRIMARY KEY, filename TEXT NOT NULL, file_path TEXT,
              title TEXT, org TEXT, publish_date TEXT, page_count INTEGER,
              char_count INTEGER, text TEXT NOT NULL,
              usage_policy TEXT NOT NULL DEFAULT 'personal_research',
              source_type TEXT NOT NULL DEFAULT 'manual_feed', ingested_at TEXT NOT NULL
            );
            CREATE TABLE report_extractions (
              doc_key TEXT PRIMARY KEY, broker TEXT, analyst TEXT, report_date TEXT,
              report_type TEXT, stocks_json TEXT, core_logic_json TEXT,
              catalysts_json TEXT, risks_json TEXT, summary TEXT, model TEXT,
              prompt_version TEXT, extracted_at TEXT NOT NULL
            );
            """
        )
        conn.commit()
        conn.close()
        store.init_db(db)
        conn = sqlite3.connect(db)
        try:
            doc_cols = {row[1] for row in conn.execute("PRAGMA table_info(report_documents)")}
            ext_cols = {row[1] for row in conn.execute("PRAGMA table_info(report_extractions)")}
        finally:
            conn.close()
        assert {"page_offsets", "report_key", "depth_class"} <= doc_cols
        assert "sources_json" in ext_cols
