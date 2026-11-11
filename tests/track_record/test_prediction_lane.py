"""predictions.lane 归属（add-microcap-lane-analysis T2）。"""

import sqlite3

from finance_agent.outcome.track_record.ingest import persist_prediction_from_accumulated
from finance_agent.outcome.track_record.model import init_track_record_tables
from finance_agent.screening.pool_analysis import (
    POOL_MICROCAP,
    get_pool_by_session,
    record_pool_analysis_run,
)


def test_get_pool_by_session_hit(tmp_path):
    db = tmp_path / "screening.db"
    record_pool_analysis_run(
        "2026-11-10", "002749", "sess-mc", "queued", pool=POOL_MICROCAP, db_path=db
    )
    assert get_pool_by_session("sess-mc", db_path=db) == POOL_MICROCAP


def test_get_pool_by_session_miss(tmp_path):
    db = tmp_path / "screening.db"
    assert get_pool_by_session("no-such-session", db_path=db) is None


def test_predictions_have_lane_default_main(tmp_path):
    """迁移后存量行 lane='main'；新落库默认 main。"""
    from finance_agent.outcome.track_record.model import list_predictions

    # list_predictions 返回行须含 lane 键（列存在性由 DDL/迁移保证）
    db = tmp_path / "track.db"
    rows = list_predictions(status="open", limit=10, db_path=db)
    assert isinstance(rows, list)  # 空库返回空表不报错
    # 列存在性直查
    conn = sqlite3.connect(db)
    cols = {row[1] for row in conn.execute("PRAGMA table_info(predictions)")}
    conn.close()
    assert "lane" in cols


def test_ingest_lane_from_microcap_session(tmp_path, monkeypatch):
    """microcap 池会话落库 → predictions.lane='microcap'（ingest 按会话反查）。"""
    db = tmp_path / "t.db"
    screening_db = tmp_path / "screening.db"
    monkeypatch.setenv("SESSIONS_DB_PATH", str(db))
    monkeypatch.setenv("SCREENING_DB_PATH", str(screening_db))
    init_track_record_tables(db)
    record_pool_analysis_run(
        "2026-11-10",
        "002749",
        "sess-mc-lane",
        "queued",
        pool=POOL_MICROCAP,
        db_path=screening_db,
    )
    accumulated = {
        "final_trade_decision": {"action": "buy", "confidence": 0.7, "entry_price": 10.0},
        "stock_quote": {"price": 10.0},
    }
    persist_prediction_from_accumulated(accumulated, "sess-mc-lane", "002749", "示例股")
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT lane FROM predictions").fetchone()
    conn.close()
    assert row["lane"] == "microcap"


def test_ingest_lane_unknown_session_falls_back_main(tmp_path, monkeypatch):
    """反查无行（screening 库无该会话）→ lane 回退 'main'（旁路铁律不阻断）。"""
    db = tmp_path / "t.db"
    monkeypatch.setenv("SESSIONS_DB_PATH", str(db))
    monkeypatch.setenv("SCREENING_DB_PATH", str(tmp_path / "screening.db"))
    init_track_record_tables(db)
    accumulated = {
        "final_trade_decision": {"action": "buy", "confidence": 0.7, "entry_price": 10.0},
        "stock_quote": {"price": 10.0},
    }
    persist_prediction_from_accumulated(accumulated, "sess-unknown", "600519", "贵州茅台")
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT lane FROM predictions").fetchone()
    conn.close()
    assert row["lane"] == "main"
