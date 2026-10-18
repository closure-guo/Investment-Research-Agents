"""策略族成绩单只读端点测试（delta add-strategy-family-scorecards）。

断言面：200 + 全族返回 + 字段序（四槽先于胜率）+ 空数据族 null + 原因码（不 500）
+ 只读（重复请求载荷一致、台账零改写）。
"""

from __future__ import annotations

import sqlite3

from fastapi.testclient import TestClient

from finance_agent.api import app
from finance_agent.outcome.track_record.model import (
    init_track_record_tables,
    insert_prediction,
    update_prediction_status,
)

_SLOT_REASONS = {
    "expectancy_reason": "no_samples",
    "max_drawdown_reason": "insufficient_history",
    "capacity_reason": "no_liquidity_data",
}


def _scorecards() -> dict:
    response = TestClient(app).get("/api/scorecards")
    assert response.status_code == 200
    return response.json()


def _add_settled(db, *, symbol: str, day: str, ret: float, status: str = "resolved_win") -> None:
    prediction_id = insert_prediction(
        {
            "source_type": "live",
            "symbol": symbol,
            "direction": "long",
            "horizon_days": 20,
            "rationale_snapshot": {"action": "buy"},
            "created_at": "2026-08-01T09:30:00",
        },
        db_path=db,
    )
    update_prediction_status(
        prediction_id,
        {"status": status, "resolved_at": f"{day}T15:00:00", "raw_return": ret},
        db_path=db,
    )


def _prediction_rows(db) -> list[tuple]:
    conn = sqlite3.connect(db)
    try:
        return conn.execute(
            "SELECT prediction_id, status, raw_return FROM predictions ORDER BY prediction_id"
        ).fetchall()
    finally:
        conn.close()


def test_scorecards_endpoint_empty_databases(monkeypatch, tmp_path) -> None:
    """空库：200（不 500），两族四槽 null + 原因码，口径注释随行。"""
    monkeypatch.setenv("SESSIONS_DB_PATH", str(tmp_path / "missing-s.db"))
    monkeypatch.setenv("EXECUTION_DB_PATH", str(tmp_path / "missing-e.db"))
    monkeypatch.setenv("SNAPSHOT_DB_PATH", str(tmp_path / "missing-snap.db"))

    payload = _scorecards()

    families = payload["families"]
    assert [family["family_id"] for family in families] == [
        "predictions",
        "execution",
        "event",
    ]
    for family in families:
        assert family["expectancy"] is None
        assert family["max_drawdown"] is None
        assert family["capacity"] is None
        assert family["extreme_samples"] == []
        assert family["win_rate"] is None
        assert family["as_of"] is None
        assert family["caliber_note"]
        for key, reason in _SLOT_REASONS.items():
            assert family[key] == reason


def test_scorecards_endpoint_payload_and_read_only(monkeypatch, tmp_path) -> None:
    """有数据：四槽先于胜率；缺数据槽如实置空；重复请求载荷一致且台账零改写。"""
    sessions = tmp_path / "s.db"
    init_track_record_tables(sessions)
    for index in range(1, 11):
        last = index == 10
        _add_settled(
            sessions,
            symbol="600519",
            day=f"2026-09-{index:02d}",
            ret=-0.20 if last else 0.03,
            status="resolved_loss" if last else "resolved_win",
        )
    monkeypatch.setenv("SESSIONS_DB_PATH", str(sessions))
    monkeypatch.setenv("EXECUTION_DB_PATH", str(tmp_path / "e.db"))
    monkeypatch.setenv("SNAPSHOT_DB_PATH", str(tmp_path / "snap.db"))

    payload = _scorecards()

    pred = payload["families"][0]
    assert pred["family_id"] == "predictions"
    assert pred["expectancy"]["n"] == 10
    assert pred["expectancy"]["net"] < pred["expectancy"]["gross"]
    assert pred["max_drawdown"] is None  # 10 结算日 < 门槛 20：如实置空
    assert pred["max_drawdown_reason"] == "insufficient_history"
    assert pred["capacity"] is None
    assert pred["capacity_reason"] == "no_liquidity_data"
    assert [row["ticker"] for row in pred["extreme_samples"]] == ["600519"]
    assert pred["win_rate"] == 0.9  # 9 胜 / (9 胜 + 1 负)，仅展示
    assert pred["as_of"] == "2026-09-10"
    keys = list(pred.keys())
    assert keys.index("expectancy") < keys.index("max_drawdown") < keys.index("capacity")
    assert keys.index("extreme_samples") < keys.index("win_rate") < keys.index("caliber_note")

    exe = payload["families"][1]
    assert exe["family_id"] == "execution"
    assert exe["expectancy"] is None
    assert exe["expectancy_reason"] == "no_samples"

    event = payload["families"][2]  # delta add-event-driven-lane：事件族（空库如实置空）
    assert event["family_id"] == "event" and event["name"] == "事件线"
    assert event["expectancy"] is None
    assert event["expectancy_reason"] == "no_samples"
    assert event["win_rate"] is None
    assert "往返成本" in event["caliber_note"]

    before = _prediction_rows(sessions)
    assert _scorecards() == payload
    assert _prediction_rows(sessions) == before
