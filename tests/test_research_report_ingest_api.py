"""研报直投入库端点测试（add-research-report-ingest）。"""

from __future__ import annotations

from fastapi.testclient import TestClient

from finance_agent.api import app
from finance_agent.data import research_reports as rr
from finance_agent.data import research_reports_store as rr_store


def _isolate(tmp_path, monkeypatch):
    monkeypatch.setenv("RESEARCH_REPORTS_DB_PATH", str(tmp_path / "rr.db"))
    monkeypatch.setenv("RESEARCH_REPORT_INBOX_DIR", str(tmp_path / "inbox"))
    monkeypatch.setenv("TESTING", "1")


def test_upload_pdf_ingested(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    monkeypatch.setattr(
        rr, "extract_pdf_text", lambda path: ("直投解析文本", 2, [{"page": 1, "offset": 0}])
    )
    client = TestClient(app)
    resp = client.post(
        "/api/research/reports/ingest",
        files={"file": ("国信证券-金工日报-261019.pdf", b"%PDF fake", "application/pdf")},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ingested"
    assert body["doc_key"]
    assert body["char_count"] == len("直投解析文本")
    assert body["page_count"] == 2
    assert body["parse_error"] is None
    docs = rr_store.list_documents(db_path=tmp_path / "rr.db")
    assert len(docs) == 1 and docs[0]["filename"].endswith(".pdf")


def test_upload_duplicate_skipped(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    monkeypatch.setattr(rr, "extract_pdf_text", lambda path: ("文本", 1, None))
    client = TestClient(app)
    payload = {"file": ("报告.pdf", b"%PDF dup", "application/pdf")}
    first = client.post("/api/research/reports/ingest", files=payload)
    second = client.post("/api/research/reports/ingest", files=payload)
    assert first.json()["status"] == "ingested"
    assert second.json()["status"] == "skipped"
    assert len(rr_store.list_documents(db_path=tmp_path / "rr.db")) == 1


def test_unsupported_suffix_400(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    client = TestClient(app)
    resp = client.post(
        "/api/research/reports/ingest",
        files={"file": ("程序.exe", b"MZ", "application/octet-stream")},
    )
    assert resp.status_code == 400
    assert "pdf" in resp.json()["detail"]


def test_oversize_413(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    client = TestClient(app)
    resp = client.post(
        "/api/research/reports/ingest",
        files={"file": ("大文件.pdf", b"x" * (30 * 1024 * 1024 + 1), "application/pdf")},
    )
    assert resp.status_code == 413


def test_corrupt_pdf_ingested_with_parse_error(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    monkeypatch.setattr(
        rr, "extract_pdf_text", lambda path: (_ for _ in ()).throw(ValueError("bad pdf"))
    )
    client = TestClient(app)
    resp = client.post(
        "/api/research/reports/ingest",
        files={"file": ("坏文件.pdf", b"not a pdf", "application/pdf")},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ingested"
    assert body["parse_error"] and "bad pdf" in body["parse_error"]
    assert body["char_count"] == 0


def test_metadata_overrides(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    client = TestClient(app)
    resp = client.post(
        "/api/research/reports/ingest",
        files={"file": ("随便.txt", "正文".encode(), "text/plain")},
        data={"org": "某券商", "publish_date": "2026-10-24", "title": "深度报告"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["org"] == "某券商"
    assert body["publish_date"] == "2026-10-24"
    stored = rr_store.get_document(body["doc_key"], db_path=tmp_path / "rr.db")
    assert stored["title"] == "深度报告"


def test_documents_list_no_text_fields(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    rr_store.upsert_document(
        {
            "doc_key": "d1",
            "filename": "报告.txt",
            "file_path": "x.txt",
            "title": "报告",
            "org": "某券商",
            "publish_date": "2026-10-24",
            "page_count": 1,
            "char_count": 10,
            "text": "不应出现在列表响应里",
            "usage_policy": "personal_research",
            "source_type": "manual_feed",
        },
        db_path=tmp_path / "rr.db",
    )
    client = TestClient(app)
    resp = client.get("/api/research/reports/documents")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["documents"]) == 1
    item = body["documents"][0]
    assert "text" not in item and "page_offsets" not in item
    assert item["doc_key"] == "d1"
    empty = TestClient(app).get("/api/research/reports/documents")  # 同库非空，跳过空态断言
    assert empty.status_code == 200
