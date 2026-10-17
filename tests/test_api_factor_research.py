"""因子研发监控只读端点单测（delta add-factor-research-monitor T2）。

spec factor-research-monitor「只读查询端点」：

- ``GET /api/factor-research/status`` 调用期读 ``FACTOR_RESEARCH_STATUS_PATH``
  （默认 data/factor_research/status.json，E2E env 注入即生效）；
- 快照合法 → 200 + ``available=true`` + 派生 ``age_seconds`` / ``stale``（>180s 判陈旧），
  快照原始字段逐字透传；
- 文件缺失 → 200 + ``available=false`` + ``reason=missing``；不可解析 → ``corrupt``
  （如实二分，MUST NOT 500 或伪快照冒充）；
- 端点零写入（请求前后文件字节不变）。

零网络：TestClient + tmp 快照文件（env 逐例注入，不读生产 data/）。
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from finance_agent.api import app

_ENDPOINT = "/api/factor-research/status"
_TZ = timezone(timedelta(hours=8))


@pytest.fixture(autouse=True)
def _status_path(tmp_path, monkeypatch):
    """快照路径指向 tmp（端点调用期读 env）。"""
    path = tmp_path / "status.json"
    monkeypatch.setenv("FACTOR_RESEARCH_STATUS_PATH", str(path))
    return path


def _snapshot(age_s: int = 40) -> dict:
    """合法快照 fixture：形状与采集器契约（factor-research-monitor spec）一致。"""
    epoch = int(time.time() - age_s)
    return {
        "collected_at": datetime.fromtimestamp(epoch, tz=_TZ).isoformat(),
        "collected_at_epoch": epoch,
        "supervisor": {"running": True, "pid": 7752, "started_at": "2026-10-17T11:36:00+08:00"},
        "current_session": {
            "log_file": "session-20261017-113600.log",
            "started_at": "2026-10-17T11:36:00+08:00",
            "duration_s": 960,
            "alive": True,
            "loop_index": 4,
            "step_index": 0,
            "step_name": "direct_exp_gen",
            "steps_total": 5,
            "step_elapsed_s": 595,
            "step_eta_s": 0,
            "factors": ["ret_5d_reversal", "max_ret_20d"],
            "log_tail": ["Workflow Progress line"],
        },
        "sessions": [
            {
                "log_file": "session-20261017-113600.log",
                "started_at": "2026-10-17T11:36:00+08:00",
                "ended_at": None,
                "duration_s": 960,
            }
        ],
        "totals": {"sessions": 1, "candidates": 135, "workspace_tasks": 222},
    }


def test_missing_file_reports_missing():
    """文件缺失 → 200 + available=false + reason=missing（第一类「未接入」状态）。"""
    resp = TestClient(app).get(_ENDPOINT)
    assert resp.status_code == 200
    assert resp.json() == {"available": False, "reason": "missing"}


def test_corrupt_text_reports_corrupt(_status_path):
    _status_path.write_text("not json at all", encoding="utf-8")
    resp = TestClient(app).get(_ENDPOINT)
    assert resp.status_code == 200
    assert resp.json() == {"available": False, "reason": "corrupt"}


def test_non_object_json_reports_corrupt(_status_path):
    _status_path.write_text("[1, 2, 3]", encoding="utf-8")
    assert TestClient(app).get(_ENDPOINT).json() == {"available": False, "reason": "corrupt"}


def test_missing_timestamp_reports_corrupt(_status_path):
    """无可解析时间戳（epoch 与 ISO 均缺）→ corrupt（age 不可计算即不可信）。"""
    _status_path.write_text(json.dumps({"supervisor": {"running": True}}), encoding="utf-8")
    assert TestClient(app).get(_ENDPOINT).json() == {"available": False, "reason": "corrupt"}


def test_fresh_snapshot_passthrough_with_derived(_status_path):
    snap = _snapshot(age_s=40)
    _status_path.write_text(json.dumps(snap), encoding="utf-8")
    body = TestClient(app).get(_ENDPOINT).json()
    assert body["available"] is True
    assert 40 <= body["age_seconds"] <= 45
    assert body["stale"] is False
    # 原始字段逐字透传（不是重造）
    for key in (
        "collected_at",
        "collected_at_epoch",
        "supervisor",
        "current_session",
        "sessions",
        "totals",
    ):
        assert body[key] == snap[key]


def test_stale_snapshot_marked(_status_path):
    _status_path.write_text(json.dumps(_snapshot(age_s=400)), encoding="utf-8")
    body = TestClient(app).get(_ENDPOINT).json()
    assert body["available"] is True
    assert body["stale"] is True
    assert 400 <= body["age_seconds"] <= 405


def test_not_stale_below_threshold(_status_path):
    """边界：age < 180 不判陈旧（阈值 180，严格大于才陈旧）。"""
    _status_path.write_text(json.dumps(_snapshot(age_s=175)), encoding="utf-8")
    body = TestClient(app).get(_ENDPOINT).json()
    assert body["stale"] is False


def test_iso_fallback_when_epoch_missing(_status_path):
    """collected_at_epoch 缺失 → 回退解析 ISO（+08:00）计算 age。"""
    snap = _snapshot(age_s=40)
    del snap["collected_at_epoch"]
    _status_path.write_text(json.dumps(snap), encoding="utf-8")
    body = TestClient(app).get(_ENDPOINT).json()
    assert body["available"] is True
    assert 40 <= body["age_seconds"] <= 45


def test_env_path_read_at_call_time(tmp_path, monkeypatch):
    """路径调用期读 env：两次请求间切换 env 即切换数据源。"""
    first = tmp_path / "a.json"
    second = tmp_path / "b.json"
    monkeypatch.setenv("FACTOR_RESEARCH_STATUS_PATH", str(first))
    client = TestClient(app)
    assert client.get(_ENDPOINT).json()["reason"] == "missing"
    second.write_text(json.dumps(_snapshot()), encoding="utf-8")
    monkeypatch.setenv("FACTOR_RESEARCH_STATUS_PATH", str(second))
    assert client.get(_ENDPOINT).json()["available"] is True


def test_endpoint_read_only(_status_path):
    """端点零写入：请求前后快照文件字节不变。"""
    _status_path.write_text(json.dumps(_snapshot()), encoding="utf-8")
    before = _status_path.read_bytes()
    TestClient(app).get(_ENDPOINT)
    assert _status_path.read_bytes() == before
