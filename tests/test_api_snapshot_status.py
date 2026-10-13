"""add-market-snapshot T8：``GET /api/snapshot/status`` + TESTING ``/api/test/seed`` 快照钩子。

契约（D5/D6）：
- 状态端点永不 500：读库任何异常 → 降级 ``last_error="status_unavailable"`` 且
  ``stale=False``（前端横幅轮询不得被后端故障打断）；
- seed 钩子（仅 TESTING=1 注册）：``snapshot`` 写 meta（缺 ``last_attempt_at`` 时
  合成，否则 D5 走 fresh-DB 分支造不出 stale 态）、``snapshot_reset`` 清快照三表；
  两者都在**调用期**读 ``SNAPSHOT_DB_PATH``（导入期冻结会让 E2E 注入失效）。
"""

from __future__ import annotations

import importlib
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from finance_agent.api import app
from finance_agent.data.snapshot import (
    get_all_meta,
    get_security_master,
    latest_snapshot_date,
    set_meta_many,
    upsert_security_master,
    write_snapshot_rows,
)

_DEGRADED = {
    "latest_date": None,
    "stale": False,
    "message": None,
    "last_error": "status_unavailable",
    "last_error_date": None,
}


def _today_beijing() -> str:
    return datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat()


def _now_beijing_iso() -> str:
    return datetime.now(ZoneInfo("Asia/Shanghai")).isoformat()


@pytest.fixture(autouse=True)
def _snapshot_db(tmp_path, monkeypatch):
    """快照库指向 tmp（env 调用期读取；任何用例都不得污染 data/market_snapshot.db）。"""
    path = tmp_path / "market_snapshot.db"
    monkeypatch.setenv("SNAPSHOT_DB_PATH", str(path))
    return path


@pytest.fixture
def testing_client(monkeypatch):
    """TESTING=1 下 reload 出带 /api/test/seed 的客户端（仓库既有模式）。"""
    monkeypatch.setenv("TESTING", "1")
    import finance_agent.api as api_module

    importlib.reload(api_module)
    try:
        yield TestClient(api_module.app)
    finally:
        monkeypatch.delenv("TESTING", raising=False)
        importlib.reload(api_module)


class TestSnapshotStatusEndpoint:
    def test_status_ok_payload(self, _snapshot_db):
        today = _today_beijing()
        set_meta_many(
            {"last_success_date": today, "last_attempt_at": _now_beijing_iso()},
            db_path=_snapshot_db,
        )

        resp = TestClient(app).get("/api/snapshot/status")

        assert resp.status_code == 200
        assert resp.json() == {
            "latest_date": today,
            "stale": False,
            "message": None,
            "last_error": None,
            "last_error_date": None,
        }

    def test_status_stale_when_failed_today(self, _snapshot_db):
        """失败态：当日失败 → stale + 逐字文案（前端横幅直接渲染 message）。"""
        today = _today_beijing()
        set_meta_many(
            {
                "last_success_date": "2026-10-09",
                "last_attempt_at": _now_beijing_iso(),
                "last_error": "SnapshotError: boom",
                "last_error_date": today,
            },
            db_path=_snapshot_db,
        )

        body = TestClient(app).get("/api/snapshot/status").json()

        assert body["stale"] is True
        assert body["message"] == "行情快照未更新（数据日期: 2026-10-09）"
        assert body["last_error"] == "SnapshotError: boom"
        assert body["last_error_date"] == today

    def test_status_degraded_on_read_error(self, monkeypatch):
        """读库异常 → 200 + 降级载荷（永不 500）。"""

        def boom():
            raise RuntimeError("db locked")

        monkeypatch.setattr("finance_agent.api.get_all_meta", boom)

        resp = TestClient(app).get("/api/snapshot/status")

        assert resp.status_code == 200
        assert resp.json() == _DEGRADED

    def test_status_fresh_db_is_not_stale(self, _snapshot_db):
        """空库（从未尝试）→ 不告警（免 dev/E2E 噪音）。"""
        body = TestClient(app).get("/api/snapshot/status").json()

        assert body["stale"] is False
        assert body["message"] is None
        assert body["latest_date"] is None


class TestSeedSnapshotHooks:
    def test_seed_endpoint_placeholder_untouched_for_symbol_payload(self, testing_client):
        """既有 {symbol} 早退路径保持占位响应（不被 snapshot 钩子改动）。"""
        resp = testing_client.post("/api/test/seed", json={"symbol": "300308"})

        assert resp.status_code == 200
        assert resp.json() == {"status": "ok", "mode": "testing"}

    def test_seed_snapshot_meta_roundtrip(self, testing_client, _snapshot_db):
        """seed snapshot 键 → 写 meta（自动合成 last_attempt_at）→ 状态端点可见。"""
        today = _today_beijing()
        resp = testing_client.post(
            "/api/test/seed",
            json={
                "snapshot": {
                    "last_success_date": "2026-10-09",
                    "last_error_date": today,
                    "last_error": "boom",
                }
            },
        )

        assert resp.status_code == 200
        assert resp.json() == {"status": "ok", "mode": "testing"}
        meta = get_all_meta()  # 不带路径：证明 seed 写的是 env 指向的库
        assert meta["last_success_date"] == "2026-10-09"
        assert meta["last_error_date"] == today
        assert "last_attempt_at" in meta  # 合成（否则 D5 fresh-DB 分支不告警）

        status = testing_client.get("/api/snapshot/status").json()
        assert status["stale"] is True
        assert status["message"] == "行情快照未更新（数据日期: 2026-10-09）"

    def test_seed_reset_then_snapshot_in_one_request(self, testing_client, _snapshot_db):
        """E2E 造态顺序：同一请求内 snapshot_reset 先清、snapshot 后写。"""
        set_meta_many({"last_error": "stale-old"}, db_path=_snapshot_db)

        resp = testing_client.post(
            "/api/test/seed",
            json={"snapshot_reset": True, "snapshot": {"last_success_date": "2026-10-09"}},
        )

        assert resp.status_code == 200
        meta = get_all_meta()
        assert meta["last_success_date"] == "2026-10-09"
        assert "last_error" not in meta  # reset 已清掉旧键
        assert "last_attempt_at" in meta

    def test_seed_snapshot_reset_clears_rows(self, testing_client, _snapshot_db):
        """snapshot_reset → 快照三表清空（E2E 用例间隔离）。"""
        write_snapshot_rows(
            "2026-10-12",
            [{"trade_date": "2026-10-12", "code": "600519", "close_raw": 10.0, "close_qfq": 10.0}],
            db_path=_snapshot_db,
        )
        upsert_security_master([{"code": "600519", "name": "贵州茅台"}], db_path=_snapshot_db)
        set_meta_many({"last_success_date": "2026-10-12"}, db_path=_snapshot_db)

        resp = testing_client.post("/api/test/seed", json={"snapshot_reset": True})

        assert resp.status_code == 200
        assert latest_snapshot_date() is None
        assert get_all_meta() == {}
        assert get_security_master() == {}
