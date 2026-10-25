"""研报直投入库共享服务测试（add-research-report-ingest）。

覆盖：PDF/文本分流、内容哈希幂等、元数据覆盖、抽取失败披露、
后缀白名单、解码失败、投喂目录落盘。目录扫描（ingest_inbox）复用
共享函数后的行为回归在 test_research_reports_collectors.py 原有用例。
"""

from __future__ import annotations

import pytest

from finance_agent.data import research_reports as rr
from finance_agent.data import research_reports_store as store


class TestIngestResearchDocument:
    def test_pdf_direct_ingest(self, tmp_path, monkeypatch):
        db = tmp_path / "rr.db"
        monkeypatch.setattr(
            rr, "extract_pdf_text", lambda path: ("解析文本内容", 3, [{"page": 1, "offset": 0}])
        )
        got = rr.ingest_research_document(
            "国信证券-金融工程日报-261019.pdf", b"%PDF fake", db_path=db
        )
        assert got["status"] == "ingested"
        assert got["doc_key"]
        assert got["char_count"] == len("解析文本内容")
        assert got["page_count"] == 3
        assert got["org"] == "国信证券"
        assert got["publish_date"] == "2026-10-19"
        assert got["parse_error"] is None
        stored = store.get_document(got["doc_key"], db_path=db)
        assert stored["text"] == "解析文本内容"
        assert stored["usage_policy"] == "personal_research"
        assert stored["source_type"] == "manual_feed"

    def test_text_direct_ingest(self, tmp_path):
        db = tmp_path / "rr.db"
        text = "这是一段研报正文，包含目标价 12.5 元。"
        got = rr.ingest_research_document("手工粘贴报告.txt", text.encode("utf-8"), db_path=db)
        assert got["status"] == "ingested"
        assert got["page_count"] == 1
        assert got["char_count"] == len(text)
        stored = store.get_document(got["doc_key"], db_path=db)
        assert stored["text"] == text

    def test_gbk_decode_fallback(self, tmp_path):
        db = tmp_path / "rr.db"
        text = "GBK 编码的研报正文"
        got = rr.ingest_research_document("旧报告.txt", text.encode("gbk"), db_path=db)
        assert got["status"] == "ingested"
        stored = store.get_document(got["doc_key"], db_path=db)
        assert stored["text"] == text

    def test_undecodable_bytes_rejected(self, tmp_path):
        db = tmp_path / "rr.db"
        with pytest.raises(ValueError, match="解码"):
            rr.ingest_research_document("乱码.txt", b"\xff\xff\xff\xff", db_path=db)

    def test_unsupported_suffix_rejected(self, tmp_path):
        db = tmp_path / "rr.db"
        with pytest.raises(ValueError, match="pdf|txt|md"):
            rr.ingest_research_document("程序.exe", b"MZ", db_path=db)

    def test_duplicate_content_skipped(self, tmp_path):
        db = tmp_path / "rr.db"
        data = "同一份研报正文".encode()
        first = rr.ingest_research_document("报告.txt", data, db_path=db)
        second = rr.ingest_research_document("报告.txt", data, db_path=db)
        assert first["status"] == "ingested"
        assert second["status"] == "skipped"
        assert second["doc_key"] == first["doc_key"]
        assert len(store.list_documents(db_path=db)) == 1

    def test_overrides_win_over_filename(self, tmp_path):
        db = tmp_path / "rr.db"
        got = rr.ingest_research_document(
            "无约定文件名.txt",
            "正文".encode(),
            db_path=db,
            overrides={"org": "某券商", "publish_date": "2026-10-24", "title": "深度报告"},
        )
        stored = store.get_document(got["doc_key"], db_path=db)
        assert stored["org"] == "某券商"
        assert stored["publish_date"] == "2026-10-24"
        assert stored["title"] == "深度报告"

    def test_extraction_failure_disclosed_not_blocking(self, tmp_path, monkeypatch):
        db = tmp_path / "rr.db"
        monkeypatch.setattr(
            rr,
            "extract_document",
            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("LLM 超时")),
        )
        got = rr.ingest_research_document("报告.txt", "有正文".encode(), db_path=db, extract=True)
        assert got["status"] == "ingested"
        assert got["extraction_error"] and "LLM 超时" in got["extraction_error"]
        assert len(store.list_documents(db_path=db)) == 1

    def test_saves_file_copy_into_inbox(self, tmp_path):
        db = tmp_path / "rr.db"
        inbox = tmp_path / "inbox"
        got = rr.ingest_research_document(
            "投喂报告.txt", "正文内容".encode(), db_path=db, inbox_dir=inbox
        )
        assert (inbox / "投喂报告.txt").read_bytes() == "正文内容".encode()
        stored = store.get_document(got["doc_key"], db_path=db)
        assert stored["file_path"] == str((inbox / "投喂报告.txt").resolve())

    def test_name_conflict_gets_unique_path(self, tmp_path):
        db = tmp_path / "rr.db"
        inbox = tmp_path / "inbox"
        inbox.mkdir()
        (inbox / "同名报告.txt").write_bytes(b"old content")
        got = rr.ingest_research_document(
            "同名报告.txt", b"new content", db_path=db, inbox_dir=inbox
        )
        stored = store.get_document(got["doc_key"], db_path=db)
        assert stored["text"] == "new content"
        assert stored["file_path"] != str((inbox / "同名报告.txt").resolve())

    def test_skip_does_not_touch_first_ingested_at(self, tmp_path):
        db = tmp_path / "rr.db"
        data = "正文".encode()
        first = rr.ingest_research_document("报告.txt", data, db_path=db)
        before = store.get_document(first["doc_key"], db_path=db)["ingested_at"]
        rr.ingest_research_document("报告.txt", data, db_path=db)
        after = store.get_document(first["doc_key"], db_path=db)["ingested_at"]
        assert before == after
