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


def _seed(db: Path, trade_date: str, **overrides) -> None:
    payload = {**_INDICATORS, **overrides}
    upsert_regime_daily(
        trade_date,
        payload,
        {"small_cap_share_pctile": 0.93, "total_amount_pctile": 0.5},
        computed_at=f"{trade_date}T15:35:00",
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
        # Breadth 类分位由历史窗口派生（样本 < min_history → null，如实置空）
        for key in ("up_count", "down_count", "limit_up_count", "limit_down_count"):
            assert set(indicators[key]) == {"value", "pctile"}
            assert indicators[key]["pctile"] is None
        assert indicators["up_count"]["value"] == 2500

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
