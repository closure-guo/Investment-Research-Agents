"""批量分析并发设置模块单测（delta add-analysis-concurrency-setting）。

覆盖：默认 10 / env 回退 / 文件覆盖 env 并披露 / 非法保存拒绝 /
损坏与越界文件如实降级（file_status）。
"""

from __future__ import annotations

import json

import pytest

from finance_agent import analysis_concurrency as ac


@pytest.fixture(autouse=True)
def _isolated(monkeypatch, tmp_path):
    monkeypatch.delenv(ac.ENV_VAR, raising=False)
    monkeypatch.setenv("ANALYSIS_CONCURRENCY_PATH", str(tmp_path / "analysis.json"))
    return tmp_path / "analysis.json"


def test_default_is_ten(_isolated):
    body = ac.resolve()
    assert body["value"] == 10
    assert body["source"] == "default"
    assert body["file_status"] == "missing"
    assert body["env_value"] is None
    assert body["min"] == 1 and body["max"] == 20


def test_env_fallback(_isolated, monkeypatch):
    monkeypatch.setenv(ac.ENV_VAR, "3")
    body = ac.resolve()
    assert body["value"] == 3
    assert body["source"] == "env"


def test_save_persists_and_file_wins_over_env(_isolated, monkeypatch):
    monkeypatch.setenv(ac.ENV_VAR, "3")
    saved = ac.save(7)
    assert saved["value"] == 7 and saved["source"] == "file"
    assert saved["env_value"] == 3  # 被覆盖的环境变量如实披露
    on_disk = json.loads(_isolated.read_text(encoding="utf-8"))
    assert on_disk["max_concurrency"] == 7
    assert ac.resolve()["value"] == 7


@pytest.mark.parametrize("value", [0, 21, -1, True, "7", 7.0, None])
def test_save_rejects_invalid(value, _isolated):
    with pytest.raises(ValueError):
        ac.save(value)
    assert not _isolated.exists()  # 非法值不得落盘
    assert ac.resolve()["value"] == 10


def test_corrupt_file_falls_back(_isolated, monkeypatch):
    _isolated.parent.mkdir(parents=True, exist_ok=True)
    _isolated.write_text("{not-json", encoding="utf-8")
    monkeypatch.setenv(ac.ENV_VAR, "3")
    body = ac.resolve()
    assert body["value"] == 3
    assert body["source"] == "env"
    assert body["file_status"] == "corrupt"


def test_invalid_saved_value_falls_back(_isolated):
    _isolated.parent.mkdir(parents=True, exist_ok=True)
    _isolated.write_text(json.dumps({"max_concurrency": 99}), encoding="utf-8")
    body = ac.resolve()
    assert body["value"] == 10
    assert body["source"] == "default"
    assert body["file_status"] == "invalid"


def test_non_object_file_is_corrupt(_isolated):
    _isolated.parent.mkdir(parents=True, exist_ok=True)
    _isolated.write_text("[1, 2]", encoding="utf-8")
    body = ac.resolve()
    assert body["value"] == 10
    assert body["file_status"] == "corrupt"
