"""研报触发分析端点测试（add-report-triggered-analysis）。"""

from __future__ import annotations

from fastapi.testclient import TestClient

from finance_agent.api import app
from finance_agent.data import research_reports_store as rr_store


def _isolate(tmp_path, monkeypatch):
    for name, filename in (
        ("RESEARCH_REPORTS_DB_PATH", "rr.db"),
        ("SCREENING_DB_PATH", "screening.db"),
        ("EVENTS_DB_PATH", "events.db"),
        ("EXECUTION_DB_PATH", "execution.db"),
    ):
        monkeypatch.setenv(name, str(tmp_path / filename))
    monkeypatch.setenv("TESTING", "1")


def test_trigger_dry_run_no_side_effects(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    rr_store.upsert_extraction(
        "d1",
        {
            "broker": "中信证券",
            "report_date": "2026-10-20",
            "report_type": "个股研报",
            "stocks": [{"code": "600519", "name": "贵州茅台"}],
            "core_logic": [],
            "catalysts": [],
            "risks": [],
            "summary": "s",
        },
        db_path=tmp_path / "rr.db",
    )
    client = TestClient(app)
    resp = client.post("/api/research/reports/d1/trigger-analysis", params={"dry_run": "true"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "dry_run"
    assert body["docs_scanned"] == 1
    assert body["candidates"] == 1
    assert body["triggered"] == 0
    # dry-run 不写触发表
    import sqlite3

    conn = sqlite3.connect(tmp_path / "screening.db")
    try:
        count = conn.execute("SELECT COUNT(*) FROM report_analysis_runs").fetchone()[0]
    finally:
        conn.close()
    assert count == 0


def test_unknown_doc_returns_empty(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    client = TestClient(app)
    resp = client.post("/api/research/reports/missing/trigger-analysis", params={"dry_run": "true"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["docs_scanned"] == 0
    assert body["would_trigger"] == []


def test_batch_zero_extractions_discloses_dormancy(tmp_path, monkeypatch):
    """库内 0 条抽取时，批量触发必须显式披露输入为空，不得静默 ok。"""
    _isolate(tmp_path, monkeypatch)
    from finance_agent.screening.report_trigger import prepare_report_analysis_batch

    body = prepare_report_analysis_batch(
        doc_keys=None,
        dry_run=True,
        enqueue=None,
        research_db_path=str(tmp_path / "rr.db"),
        screening_db_path=str(tmp_path / "screening.db"),
        events_db_path=str(tmp_path / "events.db"),
        execution_db_path=str(tmp_path / "execution.db"),
        scope_symbols=set(),
    )
    assert body["docs_scanned"] == 0
    assert body["input_scope"] == "all_extractions"
    assert body["notice"] is not None
    assert body["notice"].startswith("no_extractions")


def test_batch_with_extractions_has_no_notice(tmp_path, monkeypatch):
    """有抽取输入时不应带空态提示。"""
    _isolate(tmp_path, monkeypatch)
    rr_store.upsert_extraction(
        "d1",
        {
            "broker": "中信证券",
            "report_date": "2026-10-20",
            "stocks": [{"code": "600519"}],
            "core_logic": [],
            "catalysts": [],
            "risks": [],
        },
        db_path=tmp_path / "rr.db",
    )
    from finance_agent.screening.report_trigger import prepare_report_analysis_batch

    body = prepare_report_analysis_batch(
        doc_keys=None,
        dry_run=True,
        enqueue=None,
        as_of="2026-10-21",
        research_db_path=str(tmp_path / "rr.db"),
        screening_db_path=str(tmp_path / "screening.db"),
        events_db_path=str(tmp_path / "events.db"),
        execution_db_path=str(tmp_path / "execution.db"),
        scope_symbols={"600519"},
        max_age_days=7,
    )
    assert body["docs_scanned"] == 1
    assert body["input_scope"] == "all_extractions"
    assert body["notice"] is None


def test_explicit_missing_doc_discloses_reason(tmp_path, monkeypatch):
    """显式指定的文档不存在时，原因必须可区分于「库内本来没有抽取」。"""
    _isolate(tmp_path, monkeypatch)
    client = TestClient(app)
    body = client.post(
        "/api/research/reports/missing/trigger-analysis", params={"dry_run": "true"}
    ).json()
    assert body["input_scope"] == "explicit_doc_keys"
    assert body["notice"] is not None
    assert body["notice"].startswith("doc_not_found")
