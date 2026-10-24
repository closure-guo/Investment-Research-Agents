# tests/test_api_session_stats.py
"""add-composer-stat-pills：GET /api/sessions/{id}/stats 契约。"""

from __future__ import annotations

from fastapi.testclient import TestClient

from finance_agent import session_store
from finance_agent.api import app


def _make_session(tmp_path, monkeypatch) -> str:
    monkeypatch.setattr(session_store, "_DB_PATH", tmp_path / "test.db")
    session_store.init_db()
    return session_store.create_session(
        stock_code="600519", stock_name="贵州茅台", status="completed"
    )


def test_session_stats_endpoint_returns_real_numbers(tmp_path, monkeypatch):
    sid = _make_session(tmp_path, monkeypatch)
    session_store.append_chat(sid, role="user", content="分析茅台")
    session_store.append_session_event(sid, {"type": "node_start", "node_id": "check_cache"})
    session_store.append_session_event(sid, {"type": "node_start", "node_id": "trader"})
    session_store.update_session_usage(
        sid, prompt=1200, completion=80, total=1280, context_peak=1200
    )

    resp = TestClient(app).get(f"/api/sessions/{sid}/stats")

    assert resp.status_code == 200
    body = resp.json()
    assert body["turns"] == 1
    assert body["steps"] == 2
    assert body["tokens_prompt"] == 1200
    assert body["tokens_completion"] == 80
    assert body["tokens_total"] == 1280
    assert body["context_peak_tokens"] == 1200


def test_session_stats_endpoint_404(tmp_path, monkeypatch):
    _make_session(tmp_path, monkeypatch)
    resp = TestClient(app).get("/api/sessions/nope/stats")
    assert resp.status_code == 404
