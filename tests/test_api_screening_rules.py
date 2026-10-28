"""选股规则设置端点单测（delta add-screening-rules-config-ui）。

覆盖：GET 默认值与来源披露 / PUT 持久化 / 非法值与缺字段 400 且不落盘。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from finance_agent.api import app
from finance_agent.screening import rules_config

ENDPOINT = "/api/settings/screening-rules"


@pytest.fixture(autouse=True)
def _isolated(monkeypatch, tmp_path):
    monkeypatch.setenv(rules_config.ENV_VAR_PATH, str(tmp_path / "screening_rules.json"))
    for var in rules_config.ENV_VARS.values():
        monkeypatch.delenv(var, raising=False)
    return tmp_path / "screening_rules.json"


def test_get_returns_defaults():
    body = TestClient(app).get(ENDPOINT).json()

    assert body["min_trading_days"]["value"] == 60
    assert body["min_trading_days"]["source"] == "default"
    assert body["min_avg_amount_20d"]["value"] == 50_000_000.0
    assert body["l2_top_n"]["value"] == 100
    assert body["file_status"] == "missing"


def test_put_persists_and_returns_file_source(_isolated):
    with TestClient(app) as client:
        resp = client.put(
            ENDPOINT,
            json={"min_trading_days": 80, "min_avg_amount_20d": 8e7, "l2_top_n": 50},
        )

        assert resp.status_code == 200
        assert resp.json()["l2_top_n"]["source"] == "file"
        assert client.get(ENDPOINT).json()["l2_top_n"]["value"] == 50


def test_put_invalid_returns_400_and_keeps_file(_isolated):
    with TestClient(app) as client:
        client.put(
            ENDPOINT,
            json={"min_trading_days": 80, "min_avg_amount_20d": 8e7, "l2_top_n": 50},
        )
        resp = client.put(
            ENDPOINT,
            json={"min_trading_days": 0, "min_avg_amount_20d": 8e7, "l2_top_n": 50},
        )

        assert resp.status_code == 400
        assert client.get(ENDPOINT).json()["l2_top_n"]["value"] == 50


def test_put_missing_field_returns_400(_isolated):
    with TestClient(app) as client:
        resp = client.put(ENDPOINT, json={"l2_top_n": 50})

        assert resp.status_code == 400
        assert not _isolated.exists()


def test_get_discloses_env_value(_isolated, monkeypatch):
    monkeypatch.setenv("SCREENING_L2_TOP_N", "50")
    body = TestClient(app).get(ENDPOINT).json()

    assert body["l2_top_n"]["source"] == "env"
    assert body["l2_top_n"]["env_value"] == 50
