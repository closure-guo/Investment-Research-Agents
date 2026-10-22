"""并发设置端点单测（delta add-analysis-concurrency-setting）。

覆盖：GET 默认 10 / PUT 持久化并立即作用于队列单例 / 非法值 400 且不落盘。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import finance_agent.api as api_module
from finance_agent import analysis_concurrency as ac
from finance_agent.api import app

ENDPOINT = "/api/settings/analysis-concurrency"


@pytest.fixture(autouse=True)
def _isolated(monkeypatch, tmp_path):
    monkeypatch.delenv(ac.ENV_VAR, raising=False)
    monkeypatch.setenv("ANALYSIS_CONCURRENCY_PATH", str(tmp_path / "analysis.json"))
    monkeypatch.setattr(api_module, "_analyze_queue", None)
    return tmp_path / "analysis.json"


def test_get_default_is_ten():
    body = TestClient(app).get(ENDPOINT).json()

    assert body["value"] == 10
    assert body["source"] == "default"
    assert body["min"] == 1 and body["max"] == 20


def test_put_persists_and_applies_to_queue(_isolated):
    with TestClient(app) as client:
        resp = client.put(ENDPOINT, json={"value": 7})

        assert resp.status_code == 200
        assert resp.json()["value"] == 7 and resp.json()["source"] == "file"
        assert api_module._get_analyze_queue().max_concurrency == 7
        assert client.get(ENDPOINT).json()["value"] == 7


@pytest.mark.parametrize("value", [0, 21, -1, "abc", True, 7.5, None])
def test_put_rejects_invalid_without_writing(value, _isolated):
    with TestClient(app) as client:
        resp = client.put(ENDPOINT, json={"value": value})

        assert resp.status_code == 400
        assert not _isolated.exists()
        assert client.get(ENDPOINT).json()["value"] == 10


def test_put_lowering_limit_does_not_kill_running_queue(_isolated):
    with TestClient(app) as client:
        client.put(ENDPOINT, json={"value": 4})
        queue = api_module._get_analyze_queue()
        assert queue.max_concurrency == 4

        client.put(ENDPOINT, json={"value": 1})

        assert queue.max_concurrency == 1
