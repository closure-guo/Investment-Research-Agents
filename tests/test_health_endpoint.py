"""GET /api/health DB 探针集成验证（harden-sqlite-availability task 8，issue #515）。

契约（delta specs/session-persistence）：全可用 → 200 + databases 全 ok；
任一库打不开 → 503 + 库名 + 短错误类；恢复后回到 200。
集成层（TestClient 直连真实应用），不 mock 被测系统——仅以 env 注入 tmp 库路径。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from finance_agent import api

DB_ENV_KEYS = (
    "SESSIONS_DB_PATH",
    "SCREENING_DB_PATH",
    "EXECUTION_DB_PATH",
    "EVENTS_DB_PATH",
    "REGIME_DB_PATH",
    "RESEARCH_REPORTS_DB_PATH",
    "FACTOR_LIBRARY_DB_PATH",
    "SNAPSHOT_DB_PATH",
)


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setenv("TESTING", "1")
    return TestClient(api.app)


def _all_ok(tmp_path: Path, monkeypatch) -> None:
    for key in DB_ENV_KEYS:
        monkeypatch.setenv(key, str(tmp_path / f"{key.lower()}.db"))


def test_health_200_with_all_databases_ok(tmp_path, monkeypatch, client):
    _all_ok(tmp_path, monkeypatch)
    resp = client.get("/api/health")
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["status"] == "ok"
    assert set(payload["databases"]) == {
        "sessions",
        "screening",
        "execution",
        "events",
        "regime",
        "research_reports",
        "factor_library",
        "market_snapshot",
    }
    assert all(v == {"status": "ok"} for v in payload["databases"].values())


def test_health_503_names_failing_database(tmp_path, monkeypatch, client):
    _all_ok(tmp_path, monkeypatch)
    monkeypatch.setenv("SCREENING_DB_PATH", str(tmp_path / "no-dir" / "screening.db"))
    resp = client.get("/api/health")
    assert resp.status_code == 503
    payload = resp.json()
    assert payload["status"] == "degraded"
    assert payload["databases"]["screening"]["status"] == "unavailable"
    assert payload["databases"]["screening"]["error"] == "OperationalError"
    assert payload["databases"]["sessions"]["status"] == "ok"


def test_health_recovers_to_200_after_fix(tmp_path, monkeypatch, client):
    _all_ok(tmp_path, monkeypatch)
    broken = tmp_path / "no-dir" / "events.db"
    monkeypatch.setenv("EVENTS_DB_PATH", str(broken))
    assert client.get("/api/health").status_code == 503
    broken.parent.mkdir(parents=True)  # 「修复」：父目录出现即可打开
    resp = client.get("/api/health")
    assert resp.status_code == 200
    assert resp.json()["databases"]["events"] == {"status": "ok"}
