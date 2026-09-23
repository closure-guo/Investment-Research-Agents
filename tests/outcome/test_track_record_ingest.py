"""add-track-record Task 3:观点全量记录(_persist_decision_log → predictions)。"""

import json
import logging
import sqlite3

import pytest

from finance_agent.api import _persist_decision_log
from finance_agent.outcome.track_record.ingest import persist_prediction_from_accumulated
from finance_agent.outcome.track_record.model import (
    init_predictions,
    init_track_record_tables,
    list_predictions,
)


def _db(monkeypatch, tmp_path):
    db = tmp_path / "t.db"
    init_predictions(db)
    monkeypatch.setattr("finance_agent.outcome.track_record.model._default_db_path", lambda: db)
    return db


def test_approve_buy_records_long(monkeypatch, tmp_path):
    _db(monkeypatch, tmp_path)
    _persist_decision_log(
        {
            "fund_manager_decision": "approve",
            "final_trade_decision": {
                "action": "buy",
                "confidence": 0.8,
                "entry_price": 100.0,
                "stop_loss": 90.0,
                "target_price": 120.0,
                "position_size": "30%",
            },
            "stock_quote": {"price": 100.0},
        },
        "sess-1",
        "600519",
        "贵州茅台",
    )
    rows = list_predictions(db_path=tmp_path / "t.db")
    assert len(rows) == 1
    assert rows[0]["direction"] == "long" and rows[0]["source_type"] == "live"
    assert rows[0]["entry_price"] == 100.0


def test_reject_also_records(monkeypatch, tmp_path):
    _db(monkeypatch, tmp_path)
    _persist_decision_log(
        {
            "fund_manager_decision": "reject",
            "final_trade_decision": {
                "action": "hold",
                "confidence": 0.5,
                "entry_price": 100.0,
            },
            "stock_quote": {"price": 100.0},
        },
        "sess-1",
        "600519",
        "贵州茅台",
    )
    rows = list_predictions(db_path=tmp_path / "t.db")
    assert len(rows) == 1
    assert rows[0]["direction"] == "neutral"  # hold → neutral
    assert rows[0]["status"] == "open"


def test_no_price_still_archives_open_with_warn(monkeypatch, tmp_path, caplog):
    """参考价不可得（quote 与 kline 均无）不再标 unresolvable：状态 open + WARN 留痕。

    delta update-decision-settlement-contract：判定不依赖参考价，结算入场价届时由行情派生。
    """
    db = _db(monkeypatch, tmp_path)
    with caplog.at_level(logging.WARNING, logger="finance_agent.track_record.ingest"):
        _persist_decision_log(
            {
                "fund_manager_decision": "approve",
                "final_trade_decision": {"action": "buy", "confidence": 0.8},
                "stock_quote": None,
            },
            "sess-1",
            "600519",
            "贵州茅台",
        )
    rows = list_predictions(db_path=db)
    assert len(rows) == 1
    assert rows[0]["status"] == "open"
    assert rows[0]["entry_price"] is None
    assert any("参考价不可得" in r.message for r in caplog.records)


def test_no_decision_skips(monkeypatch, tmp_path):
    db = _db(monkeypatch, tmp_path)
    _persist_decision_log(
        {"fund_manager_decision": "approve", "final_trade_decision": None},
        "sess-1",
        "600519",
        "贵州茅台",
    )
    assert list_predictions(db_path=db) == []


def test_ingest_writes_horizon_and_declared_prices(tmp_path, monkeypatch):
    """horizon_days 显式 20；申报价位冻结入快照。"""
    db = tmp_path / "s.db"
    monkeypatch.setenv("SESSIONS_DB_PATH", str(db))
    init_track_record_tables(db)
    accumulated = {
        "final_trade_decision": {
            "action": "buy",
            "confidence": 0.7,
            "entry_price": 101.5,
            "stop_loss": 95.0,
            "target_price": 120.0,
        },
        "stock_quote": {"price": 100.0},
        "langfuse_trace_id": "t1",
    }
    persist_prediction_from_accumulated(accumulated, "sess-1", "600519", "贵州茅台")
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM predictions").fetchone()
    assert row["horizon_days"] == 20
    assert row["status"] == "open"
    snap = json.loads(row["rationale_snapshot"])
    assert snap["declared_prices"] == {
        "entry_price": 101.5,
        "stop_loss": 95.0,
        "target_price": 120.0,
    }


def test_ingest_writes_horizon_explicitly(monkeypatch):
    """horizon_days 由 ingest 显式传入（不依赖表级默认值 20 隐式生效）。

    spy 到 insert_prediction 入参：删掉 ingest 的显式写入即红。
    """
    captured: dict = {}
    monkeypatch.setattr(
        "finance_agent.outcome.track_record.ingest.insert_prediction",
        lambda record, **kw: captured.update(record),
    )
    accumulated = {
        "final_trade_decision": {"action": "buy", "confidence": 0.7, "entry_price": 101.5},
        "stock_quote": {"price": 100.0},
        "langfuse_trace_id": "t1",
    }
    persist_prediction_from_accumulated(accumulated, "sess-h", "600519", "贵州茅台")
    assert captured["horizon_days"] == 20


def test_ingest_reference_price_missing_stays_open(tmp_path, monkeypatch):
    """参考价不可得 → 存档 + 状态 open（判定不依赖参考价）。"""
    db = tmp_path / "s.db"
    monkeypatch.setenv("SESSIONS_DB_PATH", str(db))
    init_track_record_tables(db)
    accumulated = {"final_trade_decision": {"action": "watch", "confidence": 0.5}}
    persist_prediction_from_accumulated(accumulated, "sess-2", "000001", "平安银行")
    conn = sqlite3.connect(db)
    row = conn.execute("SELECT status, entry_price FROM predictions").fetchone()
    assert row[0] == "open" and row[1] is None


@pytest.mark.parametrize(
    ("action", "expected"),
    [("buy", "long"), ("sell", "short"), ("hold", "neutral"), ("watch", "neutral")],
)
def test_ingest_direction_matches_shared_mapping(monkeypatch, action, expected):
    """ingest 落库方向 = judgment.direction_for_action（Δ2T6 审查：消除第三份拷贝）。"""
    captured: dict = {}
    monkeypatch.setattr(
        "finance_agent.outcome.track_record.ingest.insert_prediction",
        lambda record, **kw: captured.update(record),
    )
    accumulated = {"final_trade_decision": {"action": action, "confidence": 0.7}}
    persist_prediction_from_accumulated(accumulated, "sess-map", "600519", "贵州茅台")
    assert captured["direction"] == expected
