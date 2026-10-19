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


@pytest.fixture(autouse=True)
def _control_path(tmp_path, monkeypatch):
    """控制文件路径指向 tmp（调用期读 env；delta add-factor-research-control）。"""
    path = tmp_path / "control.json"
    monkeypatch.setenv("FACTOR_RESEARCH_CONTROL_PATH", str(path))
    return path


@pytest.fixture(autouse=True)
def _launcher_path(tmp_path, monkeypatch):
    """启动器心跳路径指向 tmp（调用期读 env；delta add-factor-research-selfheal）。"""
    path = tmp_path / "launcher.json"
    monkeypatch.setenv("FACTOR_RESEARCH_LAUNCHER_PATH", str(path))
    return path


_NO_CONTROL = {
    "desired_running": None,
    "requested_at": None,
    "pending": False,
    "honor": "unavailable",
    "honor_eta_seconds": None,
}
_NO_LAUNCHER = {"available": False, "reason": "missing"}


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


def _launcher(age_s: int = 40, agent_ok: bool = True) -> dict:
    """启动器心跳 fixture（形状与 ``--launcher-tick`` 契约一致）。"""
    epoch = int(time.time() - age_s)
    return {
        "checked_at": datetime.fromtimestamp(epoch, tz=_TZ).isoformat(),
        "checked_at_epoch": epoch,
        "agent_pid": 4321,
        "agent_ok": agent_ok,
        "snapshot_age_s": 12,
        "detail": None,
    }


def test_missing_file_reports_missing():
    """文件缺失 → 200 + available=false + reason=missing（第一档「未接入」状态）。"""
    resp = TestClient(app).get(_ENDPOINT)
    assert resp.status_code == 200
    assert resp.json() == {
        "available": False,
        "reason": "missing",
        "control": _NO_CONTROL,
        "launcher": _NO_LAUNCHER,
    }


def test_corrupt_text_reports_corrupt(_status_path):
    _status_path.write_text("not json at all", encoding="utf-8")
    resp = TestClient(app).get(_ENDPOINT)
    assert resp.status_code == 200
    assert resp.json() == {
        "available": False,
        "reason": "corrupt",
        "control": _NO_CONTROL,
        "launcher": _NO_LAUNCHER,
    }


def test_non_object_json_reports_corrupt(_status_path):
    _status_path.write_text("[1, 2, 3]", encoding="utf-8")
    assert TestClient(app).get(_ENDPOINT).json() == {
        "available": False,
        "reason": "corrupt",
        "control": _NO_CONTROL,
        "launcher": _NO_LAUNCHER,
    }


def test_missing_timestamp_reports_corrupt(_status_path):
    """无可解析时间戳（epoch 与 ISO 均缺）→ corrupt（age 不可计算即不可信）。"""
    _status_path.write_text(json.dumps({"supervisor": {"running": True}}), encoding="utf-8")
    assert TestClient(app).get(_ENDPOINT).json() == {
        "available": False,
        "reason": "corrupt",
        "control": _NO_CONTROL,
        "launcher": _NO_LAUNCHER,
    }


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


# ── control 块（delta add-factor-research-control T2；spec「只读查询端点」附加条款）──


def test_status_control_block_null_when_control_file_missing(_status_path):
    _status_path.write_text(json.dumps(_snapshot()), encoding="utf-8")
    body = TestClient(app).get(_ENDPOINT).json()
    assert body["control"] == {**_NO_CONTROL, "honor": "live", "honor_eta_seconds": 0}


def test_status_control_block_pending_reflects_desired_vs_actual(_status_path, _control_path):
    """pending = 期望与非陈旧快照实态不一致（两个方向都真）。"""
    _status_path.write_text(json.dumps(_snapshot()), encoding="utf-8")  # running=True
    _control_path.write_text(
        json.dumps({"desired_running": False, "requested_at": "2026-10-17T23:00:00+08:00"}),
        encoding="utf-8",
    )
    control = TestClient(app).get(_ENDPOINT).json()["control"]
    assert control["desired_running"] is False
    assert control["requested_at"] == "2026-10-17T23:00:00+08:00"
    assert control["pending"] is True

    _control_path.write_text(json.dumps({"desired_running": True}), encoding="utf-8")
    assert TestClient(app).get(_ENDPOINT).json()["control"]["pending"] is False


def test_status_control_block_pending_true_when_turning_on(_status_path, _control_path):
    """停止态 + 期望开启 → pending 同样如实（方向对称）。"""
    snap = _snapshot()
    snap["supervisor"] = {"running": False, "pid": None, "started_at": None}
    _status_path.write_text(json.dumps(snap), encoding="utf-8")
    _control_path.write_text(json.dumps({"desired_running": True}), encoding="utf-8")
    assert TestClient(app).get(_ENDPOINT).json()["control"]["pending"] is True


def test_status_control_block_pending_false_when_stale(_status_path, _control_path):
    """陈旧快照 → 实态不可证 → pending 恒假（MUST NOT 据陈旧数据判「切换中」）。"""
    _status_path.write_text(json.dumps(_snapshot(age_s=400)), encoding="utf-8")
    _control_path.write_text(json.dumps({"desired_running": False}), encoding="utf-8")
    body = TestClient(app).get(_ENDPOINT).json()
    assert body["stale"] is True
    assert body["control"]["desired_running"] is False
    assert body["control"]["pending"] is False


def test_status_control_block_invalid_control_file_null(_status_path, _control_path):
    """控制文件损坏 / 字段类型非法 → desired=null（如实不猜）。"""
    _status_path.write_text(json.dumps(_snapshot()), encoding="utf-8")
    fresh_idle = {**_NO_CONTROL, "honor": "live", "honor_eta_seconds": 0}
    _control_path.write_text("not json", encoding="utf-8")
    assert TestClient(app).get(_ENDPOINT).json()["control"] == fresh_idle
    _control_path.write_text(json.dumps({"desired_running": "true"}), encoding="utf-8")
    assert TestClient(app).get(_ENDPOINT).json()["control"] == fresh_idle
    _control_path.write_text(json.dumps([1, 2, 3]), encoding="utf-8")  # 合法 JSON 但非对象
    assert TestClient(app).get(_ENDPOINT).json()["control"] == fresh_idle


# ── launcher 块与 control.honor（delta add-factor-research-selfheal）──


def test_fresh_snapshot_honor_live_without_launcher(_status_path):
    _status_path.write_text(json.dumps(_snapshot()), encoding="utf-8")
    body = TestClient(app).get(_ENDPOINT).json()
    assert body["launcher"] == _NO_LAUNCHER
    assert body["control"]["honor"] == "live"
    assert body["control"]["honor_eta_seconds"] == 0


def test_stale_snapshot_with_fresh_launcher_honor_launcher(_status_path, _launcher_path):
    _status_path.write_text(json.dumps(_snapshot(age_s=400)), encoding="utf-8")
    _launcher_path.write_text(json.dumps(_launcher(age_s=60)), encoding="utf-8")
    body = TestClient(app).get(_ENDPOINT).json()
    assert body["launcher"]["available"] is True
    assert body["launcher"]["fresh"] is True
    assert 60 <= body["launcher"]["age_seconds"] <= 65
    assert body["control"]["honor"] == "launcher"
    assert body["control"]["honor_eta_seconds"] == 180
    assert body["control"]["pending"] is False


def test_stale_launcher_unavailable(_status_path, _launcher_path):
    _status_path.write_text(json.dumps(_snapshot(age_s=400)), encoding="utf-8")
    _launcher_path.write_text(json.dumps(_launcher(age_s=600)), encoding="utf-8")
    body = TestClient(app).get(_ENDPOINT).json()
    assert body["launcher"]["fresh"] is False
    assert body["control"]["honor"] == "unavailable"


def test_launcher_agent_not_ok_unavailable(_status_path, _launcher_path):
    _status_path.write_text(json.dumps(_snapshot(age_s=400)), encoding="utf-8")
    _launcher_path.write_text(json.dumps(_launcher(agent_ok=False)), encoding="utf-8")
    assert TestClient(app).get(_ENDPOINT).json()["control"]["honor"] == "unavailable"


def test_launcher_corrupt_reported(_launcher_path):
    _launcher_path.write_text("not json", encoding="utf-8")
    assert TestClient(app).get(_ENDPOINT).json()["launcher"] == {
        "available": False,
        "reason": "corrupt",
    }


# ── PUT /api/factor-research/control（spec「控制请求端点」）──

_CONTROL_ENDPOINT = "/api/factor-research/control"


def test_control_put_writes_file_and_returns_block(_status_path, _control_path):
    _status_path.write_text(json.dumps(_snapshot()), encoding="utf-8")  # running=True
    resp = TestClient(app).put(_CONTROL_ENDPOINT, json={"desired_running": False})
    assert resp.status_code == 200
    control = resp.json()["control"]
    assert control["desired_running"] is False
    assert control["pending"] is True
    on_disk = json.loads(_control_path.read_text(encoding="utf-8"))
    assert set(on_disk) == {"desired_running", "requested_at", "requested_at_epoch", "request_id"}
    assert on_disk["desired_running"] is False
    assert isinstance(on_disk["requested_at"], str) and on_disk["requested_at"]
    assert isinstance(on_disk["requested_at_epoch"], int)
    assert isinstance(on_disk["request_id"], str) and on_disk["request_id"]
    assert not list(_control_path.parent.glob("*.tmp"))  # 原子写：无残留临时文件


def test_control_put_pending_false_when_aligned(_status_path, _control_path):
    """期望与实态一致（停止态写 false）→ pending=false。"""
    snap = _snapshot()
    snap["supervisor"] = {"running": False, "pid": None, "started_at": None}
    _status_path.write_text(json.dumps(snap), encoding="utf-8")
    resp = TestClient(app).put(_CONTROL_ENDPOINT, json={"desired_running": False})
    assert resp.status_code == 200
    assert resp.json()["control"]["pending"] is False


def test_control_put_idempotent_repeat(_status_path, _control_path):
    """重复同值 → 幂等成功（requested_at 可更新留痕，期望态不变）。"""
    _status_path.write_text(json.dumps(_snapshot()), encoding="utf-8")
    client = TestClient(app)
    client.put(_CONTROL_ENDPOINT, json={"desired_running": False})
    first = json.loads(_control_path.read_text(encoding="utf-8"))
    resp = client.put(_CONTROL_ENDPOINT, json={"desired_running": False})
    assert resp.status_code == 200
    assert json.loads(_control_path.read_text(encoding="utf-8"))["desired_running"] is False
    assert first["desired_running"] == resp.json()["control"]["desired_running"] is False


def test_control_put_rejected_when_snapshot_missing(_control_path):
    """通道无存活证据（快照缺失）→ 409 control_unavailable 且不写控制文件。"""
    resp = TestClient(app).put(_CONTROL_ENDPOINT, json={"desired_running": True})
    assert resp.status_code == 409
    assert resp.json()["detail"] == "control_unavailable"
    assert not _control_path.exists()


def test_control_put_rejected_when_snapshot_corrupt(_status_path, _control_path):
    _status_path.write_text("not json", encoding="utf-8")
    resp = TestClient(app).put(_CONTROL_ENDPOINT, json={"desired_running": True})
    assert resp.status_code == 409
    assert not _control_path.exists()


def test_control_put_rejected_when_snapshot_stale(_status_path, _control_path):
    _status_path.write_text(json.dumps(_snapshot(age_s=400)), encoding="utf-8")
    resp = TestClient(app).put(_CONTROL_ENDPOINT, json={"desired_running": False})
    assert resp.status_code == 409
    assert not _control_path.exists()


def test_control_put_body_must_be_boolean(_status_path):
    """零命令面：仅接受布尔期望态（字符串/缺失 → 422）。"""
    _status_path.write_text(json.dumps(_snapshot()), encoding="utf-8")
    assert (
        TestClient(app).put(_CONTROL_ENDPOINT, json={"desired_running": "yes"}).status_code == 422
    )
    assert TestClient(app).put(_CONTROL_ENDPOINT, json={}).status_code == 422
    assert (
        TestClient(app)
        .put(_CONTROL_ENDPOINT, json={"desired_running": False, "cmd": "rm -rf /"})
        .status_code
        == 422
    )


def test_control_path_read_at_call_time(tmp_path, monkeypatch):
    """控制文件路径调用期读 env（与快照路径同纪律）。"""
    first = tmp_path / "c1.json"
    second = tmp_path / "c2.json"
    monkeypatch.setenv("FACTOR_RESEARCH_CONTROL_PATH", str(first))
    monkeypatch.setenv("FACTOR_RESEARCH_STATUS_PATH", str(tmp_path / "s.json"))
    (tmp_path / "s.json").write_text(json.dumps(_snapshot()), encoding="utf-8")
    client = TestClient(app)
    client.put(_CONTROL_ENDPOINT, json={"desired_running": False})
    assert first.exists() and not second.exists()
    monkeypatch.setenv("FACTOR_RESEARCH_CONTROL_PATH", str(second))
    client.put(_CONTROL_ENDPOINT, json={"desired_running": True})
    assert second.exists()


# ── PUT 受理门：快照 OR 启动器（delta add-factor-research-selfheal）──


def test_control_put_accepted_when_snapshot_stale_but_launcher_fresh(
    _status_path, _control_path, _launcher_path
):
    """研究侧离线但启动器可用 → 仍受理（honor=launcher），请求等待下一节拍执行。"""
    _status_path.write_text(json.dumps(_snapshot(age_s=400)), encoding="utf-8")
    _launcher_path.write_text(json.dumps(_launcher(age_s=30)), encoding="utf-8")

    resp = TestClient(app).put(_CONTROL_ENDPOINT, json={"desired_running": True})

    assert resp.status_code == 200
    assert resp.json()["control"]["honor"] == "launcher"
    assert resp.json()["control"]["honor_eta_seconds"] == 180
    on_disk = json.loads(_control_path.read_text(encoding="utf-8"))
    assert on_disk["desired_running"] is True


def test_control_put_rejected_when_launcher_agent_not_ok(
    _status_path, _control_path, _launcher_path
):
    _status_path.write_text(json.dumps(_snapshot(age_s=400)), encoding="utf-8")
    _launcher_path.write_text(json.dumps(_launcher(agent_ok=False)), encoding="utf-8")

    resp = TestClient(app).put(_CONTROL_ENDPOINT, json={"desired_running": True})

    assert resp.status_code == 409
    assert resp.json()["detail"] == "control_unavailable"
    assert not _control_path.exists()


def test_control_put_rejected_when_launcher_stale(_status_path, _control_path, _launcher_path):
    _status_path.write_text(json.dumps(_snapshot(age_s=400)), encoding="utf-8")
    _launcher_path.write_text(json.dumps(_launcher(age_s=600)), encoding="utf-8")

    assert TestClient(app).put(_CONTROL_ENDPOINT, json={"desired_running": True}).status_code == 409
    assert not _control_path.exists()
