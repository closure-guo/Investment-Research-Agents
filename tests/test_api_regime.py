"""``GET /api/regime`` 只读端点 + 载荷单测（delta add-crowding-regime-positioning T4；先红后绿）。

契约（spec crowding-regime「regime 只读端点」/ 计划 Task 6）：

- 有记录：200、``available=true``、``state``/``multiplier``/``reasons`` 与四指标
  （当日值 + 历史分位）、近 250 交易日历史序列；``age_days`` 与 ``stale``
  （``age_days > 7``）按口径派生；
- 无记录 → 200 + ``available=false`` + ``missing``；记录/配置不可解析 → ``corrupt``
  （MUST NOT 以 500 或伪数据冒充）；
- 端点 MUST NOT 写入或修改任何状态（读取可幂等建空表，但不得产行/改行）。
"""

from __future__ import annotations

import importlib
import json
from datetime import date, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from finance_agent.api import app
from finance_agent.outcome.regime.model import (
    latest_regime,
    list_regime_history,
    upsert_regime_daily,
)
from finance_agent.outcome.regime.payload import build_regime_payload

_INDICATORS = {
    "small_cap_share": 0.0714,
    "up_count": 2500,
    "down_count": 2300,
    "flat_count": 200,
    "limit_up_count": 60,
    "limit_down_count": 5,
    "total_amount": 8.0e11,
}


@pytest.fixture(autouse=True)
def _regime_db(tmp_path, monkeypatch):
    """regime 库指向 tmp（env 调用期读取；任何用例都不得读/写开发库 data/regime.db）。"""
    path = tmp_path / "regime.db"
    monkeypatch.setenv("REGIME_DB_PATH", str(path))
    return path


def _seed(db: Path, trade_date: str, *, cap_source: str | None = None, **overrides) -> None:
    payload = {**_INDICATORS, **overrides}
    upsert_regime_daily(
        trade_date,
        payload,
        {"small_cap_share_pctile": 0.93, "total_amount_pctile": 0.5},
        computed_at=f"{trade_date}T15:35:00",
        cap_source=cap_source,
        db_path=db,
    )


class TestPayloadWithRecord:
    def test_available_payload_shape(self, _regime_db):
        today = date.today().isoformat()
        _seed(_regime_db, today)

        body = build_regime_payload()

        assert body["available"] is True
        assert body["as_of"] == today
        # pctile 0.93 ≥ 0.9 → danger/0.0（默认配置）；reasons 含触发读数
        assert body["state"] == "danger"
        assert body["multiplier"] == 0.0
        assert "small_cap_share_pctile=0.93>=0.9" in body["reasons"]
        assert body["age_days"] == 0
        assert body["stale"] is False

    def test_indicators_carry_value_and_pctile(self, _regime_db):
        today = date.today().isoformat()
        _seed(_regime_db, today)

        indicators = build_regime_payload()["indicators"]

        assert indicators["small_cap_share"] == {"value": 0.0714, "pctile": 0.93}
        assert indicators["total_amount"] == {"value": 8.0e11, "pctile": 0.5}
        # Breadth 类（含 平）分位由历史窗口派生（样本 < min_history → null，如实置空）
        for key in ("up_count", "down_count", "flat_count", "limit_up_count", "limit_down_count"):
            assert set(indicators[key]) == {"value", "pctile"}
            assert indicators[key]["pctile"] is None
        assert indicators["up_count"]["value"] == 2500
        assert indicators["flat_count"]["value"] == 200  # 平家数透出（不折算 0/省略）

    def test_cap_source_passthrough(self, _regime_db):
        """市值取数源留痕随载荷透出（spot|tencent；老行/未传 → null）。"""
        _seed(_regime_db, date.today().isoformat(), cap_source="tencent")

        assert build_regime_payload()["cap_source"] == "tencent"

        _seed(_regime_db, date.today().isoformat())
        assert build_regime_payload()["cap_source"] is None

    def test_breadth_pctile_derived_from_history_window(self, _regime_db):
        """≥ 60 样本时 Breadth 分位由窗口派生（``#{x≤v}/n`` 手算）。"""
        base = date.today()
        for offset in range(59, 0, -1):
            _seed(_regime_db, (base - timedelta(days=offset)).isoformat(), up_count=100)
        _seed(_regime_db, base.isoformat(), up_count=2500)

        indicators = build_regime_payload()["indicators"]

        assert indicators["up_count"]["pctile"] == pytest.approx(1.0)  # 60/60

    def test_history_series_ascending_and_capped_by_window(self, _regime_db):
        base = date.today()
        for offset in (2, 1, 0):
            _seed(_regime_db, (base - timedelta(days=offset)).isoformat())

        body = build_regime_payload()

        assert [row["trade_date"] for row in body["history"]] == [
            (base - timedelta(days=2)).isoformat(),
            (base - timedelta(days=1)).isoformat(),
            base.isoformat(),
        ]
        assert body["history"][-1]["up_count"] == 2500

    @pytest.mark.parametrize(("offset", "expected_stale"), [(7, False), (8, True)])
    def test_age_days_and_stale_around_7(self, _regime_db, offset, expected_stale):
        """陈旧边界两侧：``age_days == 7`` 不陈旧、``8`` 陈旧（spec ``age_days > 7``）。"""
        _seed(_regime_db, (date.today() - timedelta(days=offset)).isoformat())

        body = build_regime_payload()

        assert body["age_days"] == offset
        assert body["stale"] is expected_stale


class TestPayloadDegradation:
    def test_empty_db_is_missing(self, _regime_db):
        assert build_regime_payload() == {"available": False, "reason": "missing"}

    def test_corrupt_config_is_corrupt(self, tmp_path, monkeypatch, _regime_db):
        _seed(_regime_db, date.today().isoformat())
        bad = tmp_path / "bad-config.json"
        bad.write_text("{not json", encoding="utf-8")
        monkeypatch.setenv("REGIME_CONFIG_PATH", str(bad))

        assert build_regime_payload() == {"available": False, "reason": "corrupt"}

    def test_corrupt_db_is_corrupt(self, tmp_path, monkeypatch):
        bad_db = tmp_path / "corrupt-regime.db"
        bad_db.write_bytes(b"not a sqlite database")
        monkeypatch.setenv("REGIME_DB_PATH", str(bad_db))

        assert build_regime_payload() == {"available": False, "reason": "corrupt"}

    def test_never_raises_on_unexpected_read_error(self, _regime_db, monkeypatch):
        import finance_agent.outcome.regime.payload as payload_module

        def boom(*args, **kwargs):
            raise RuntimeError("db locked")

        monkeypatch.setattr(payload_module, "latest_regime", boom)
        assert build_regime_payload() == {"available": False, "reason": "corrupt"}


class TestReadOnly:
    def test_payload_does_not_mutate_rows(self, _regime_db):
        _seed(_regime_db, "2026-10-15")
        _seed(_regime_db, "2026-10-16")
        before = list_regime_history(db_path=_regime_db)

        build_regime_payload()
        build_regime_payload()

        assert list_regime_history(db_path=_regime_db) == before
        assert latest_regime(db_path=_regime_db)["trade_date"] == "2026-10-16"


class TestEndpoint:
    def test_endpoint_available(self, _regime_db):
        _seed(_regime_db, date.today().isoformat())

        resp = TestClient(app).get("/api/regime")

        assert resp.status_code == 200
        body = resp.json()
        assert body["available"] is True
        assert body["state"] == "danger" and body["multiplier"] == 0.0
        assert set(body["indicators"]) == {
            "small_cap_share",
            "up_count",
            "down_count",
            "flat_count",
            "limit_up_count",
            "limit_down_count",
            "total_amount",
        }

    def test_endpoint_empty_is_200_missing(self, _regime_db):
        resp = TestClient(app).get("/api/regime")

        assert resp.status_code == 200
        assert resp.json() == {"available": False, "reason": "missing"}

    def test_endpoint_corrupt_is_200_corrupt(self, tmp_path, monkeypatch, _regime_db):
        bad = tmp_path / "bad-config.json"
        bad.write_text(json.dumps({"thresholds": []}), encoding="utf-8")
        monkeypatch.setenv("REGIME_CONFIG_PATH", str(bad))

        resp = TestClient(app).get("/api/regime")

        assert resp.status_code == 200
        assert resp.json() == {"available": False, "reason": "corrupt"}


# ── seed 扩展（T8：E2E 温度计造数，TESTING=1） ─────────────────────────────


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


# 行字段与 ``regime_daily`` DDL 列名逐字一致（含 cap_source 新列）
_SEED_ROW = {
    "trade_date": "2026-10-16",
    "small_cap_share": 0.08,
    "up_count": 2500,
    "down_count": 2300,
    "flat_count": 200,
    "limit_up_count": 60,
    "limit_down_count": 5,
    "total_amount": 8.0e11,
    "small_cap_share_pctile": 0.85,
    "total_amount_pctile": 0.42,
    "computed_at": "2026-10-16T15:35:00",
    "cap_source": "spot",
}


class TestSeedExtension:
    """``/api/test/seed`` 的 regime 造数块（E2E 构造温度计三态的前提）。"""

    def test_seed_regime_rows_roundtrip(self, testing_client, _regime_db):
        """``regime: [rows]`` 写 env 指向的库（未被 chat_history 早退吞掉）。"""
        resp = testing_client.post("/api/test/seed", json={"regime": [dict(_SEED_ROW)]})

        assert resp.status_code == 200
        # 占位响应（未创建会话）；行真写入与否由下方断言证明
        assert resp.json() == {"status": "ok", "mode": "testing"}

        row = latest_regime(db_path=_regime_db)
        assert row is not None
        assert row["trade_date"] == _SEED_ROW["trade_date"]
        assert row["small_cap_share"] == pytest.approx(0.08)
        assert row["flat_count"] == 200  # 平家数列透传（不折算）
        assert row["small_cap_share_pctile"] == pytest.approx(0.85)
        assert row["computed_at"] == "2026-10-16T15:35:00"
        assert row["cap_source"] == "spot"

        body = TestClient(app).get("/api/regime").json()
        assert body["available"] is True
        assert body["as_of"] == "2026-10-16"
        assert body["state"] == "elevated" and body["multiplier"] == 0.5
        assert body["cap_source"] == "spot"

    def test_seed_regime_multiple_rows_latest_wins(self, testing_client, _regime_db):
        """多行造数（曲线/分位窗口前提）：latest 取最大交易日，history 升序。"""
        older = {**_SEED_ROW, "trade_date": "2026-10-14", "small_cap_share_pctile": 0.5}
        testing_client.post("/api/test/seed", json={"regime": [dict(older), dict(_SEED_ROW)]})

        body = TestClient(app).get("/api/regime").json()
        assert body["as_of"] == "2026-10-16"
        assert [h["trade_date"] for h in body["history"]] == ["2026-10-14", "2026-10-16"]

    def test_seed_regime_reset_clears_rows(self, testing_client, _regime_db):
        """``regime_reset`` 清空表（同样不受 chat_history 早退约束）。"""
        testing_client.post("/api/test/seed", json={"regime": [dict(_SEED_ROW)]})
        assert latest_regime(db_path=_regime_db) is not None

        resp = testing_client.post("/api/test/seed", json={"regime_reset": True})

        assert resp.status_code == 200
        assert resp.json() == {"status": "ok", "mode": "testing"}
        assert latest_regime(db_path=_regime_db) is None
        assert TestClient(app).get("/api/regime").json() == {
            "available": False,
            "reason": "missing",
        }
