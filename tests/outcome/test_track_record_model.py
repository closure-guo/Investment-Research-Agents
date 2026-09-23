"""add-track-record Task 1:predictions 数据模型(append-only + 冻结守卫 + 迁移)。"""

import pytest

from finance_agent.outcome.track_record.model import (
    PREDICTIONS_STATUSES,
    FrozenFieldError,
    count_predictions,
    init_predictions,
    insert_prediction,
    list_predictions,
    migrate_decision_log,
    prediction_stats,
    update_prediction_status,
)

BASE = {
    "source_type": "live",
    "symbol": "600519.SH",
    "symbol_name": "贵州茅台",
    "direction": "long",
    "entry_price": 100.0,
    "target_price": 120.0,
    "horizon_days": 252,
    "confidence": 0.8,
    "benchmark": "000300.SH",
    "rationale_snapshot": {"markdown": "原文快照", "decision": {"action": "buy"}},
}


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "track.db"
    init_predictions(path)
    return path


def _insert(db, **overrides):
    rec = dict(BASE)
    rec.update(overrides)
    return insert_prediction(rec, db_path=db)


def test_statuses_enum():
    assert PREDICTIONS_STATUSES == (
        "open",
        "resolved_win",
        "resolved_loss",
        "resolved_neutral",
        "unresolvable",
    )


def test_insert_and_list(db):
    _insert(db)
    rows = list_predictions(db_path=db)
    assert len(rows) == 1
    row = rows[0]
    assert row["direction"] == "long" and row["source_type"] == "live"
    assert row["status"] == "open"
    assert row["created_at"]  # 服务端生成


def test_frozen_field_update_raises(db):
    pid = _insert(db)
    with pytest.raises(FrozenFieldError):
        update_prediction_status(pid, {"direction": "short"}, db_path=db)


def test_status_update_allowed(db):
    pid = _insert(db)
    update_prediction_status(
        pid,
        {
            "status": "resolved_win",
            "exit_price": 110.0,
            "raw_return": 0.1,
            "excess_return": 0.05,
            "resolution_rule": "expiry",
            "resolved_at": "2026-09-02",
        },
        db_path=db,
    )
    row = list_predictions(db_path=db)[0]
    assert row["status"] == "resolved_win" and row["exit_price"] == 110.0
    assert row["direction"] == "long"  # 冻结字段未被改动


def test_list_filter_and_pagination(db):
    _insert(db, symbol="600519.SH")
    _insert(db, symbol="300308.SZ")
    assert len(list_predictions(ticker="600519", db_path=db)) == 1
    assert len(list_predictions(status="open", db_path=db)) == 2
    assert len(list_predictions(source_type="backtest", db_path=db)) == 0
    assert len(list_predictions(limit=1, db_path=db)) == 1


def test_stats_empty(db):
    s = prediction_stats(db_path=db)
    assert s["total"] == 0 and s["open"] == 0 and s["win_rate"] is None


def test_migrate_decision_log(db, tmp_path):
    # 预置 decision_log 数据(复用 outcome.store DDL)
    from finance_agent.outcome.store import init_decision_log, insert_decision

    init_decision_log(db)
    insert_decision(
        {
            "session_id": "s",
            "timestamp": "2026-09-01T10:00:00",
            "ticker": "600519",
            "name": "贵州茅台",
            "action": "buy",
            "entry_price": 100.0,
            "stop_loss": 90.0,
            "target_price": 120.0,
            "confidence": 0.8,
        },
        db_path=db,
    )
    n = migrate_decision_log(db_path=db)
    assert n == 1
    rows = list_predictions(db_path=db)
    assert rows[0]["symbol"] == "600519.SH"
    assert rows[0]["direction"] == "long"


# ── add-track-record-sort-filter：排序白名单 / 关键字 / 时间段 / count ──


def test_list_sort_by_return_desc(db):
    _insert(db, symbol="a.SH", entry_price=100.0)
    _insert(db, symbol="b.SH", entry_price=100.0)
    rows = list_predictions(db_path=db)
    update_prediction_status(
        rows[0]["prediction_id"],
        {"status": "resolved_win", "raw_return": 0.05, "excess_return": 0.02},
        db_path=db,
    )
    update_prediction_status(
        rows[1]["prediction_id"],
        {"status": "resolved_win", "raw_return": 0.2, "excess_return": 0.1},
        db_path=db,
    )
    desc = list_predictions(sort_by="raw_return", sort_dir="desc", db_path=db)
    assert [r["raw_return"] for r in desc] == [0.2, 0.05]
    asc = list_predictions(sort_by="raw_return", sort_dir="asc", db_path=db)
    assert [r["raw_return"] for r in asc] == [0.05, 0.2]


def test_list_invalid_sort_falls_back_default(db):
    _insert(db, symbol="a.SH", created_at="2026-09-01T10:00:00")
    _insert(db, symbol="b.SH", created_at="2026-09-02T10:00:00")
    rows = list_predictions(sort_by="unknown_column", db_path=db)
    # 默认 created_at DESC → 后插入的 09-02 在前
    assert rows[0]["symbol"] == "b.SH"


def test_list_keyword_matches_symbol_name_and_labels(db):
    _insert(db, symbol="600519.SH", symbol_name="贵州茅台", direction="long")
    _insert(db, symbol="300308.SZ", symbol_name="中际旭创", direction="short")
    _insert(db, symbol="000001.SZ", symbol_name="平安银行", direction="long")
    assert len(list_predictions(keyword="茅台", db_path=db)) == 1
    assert len(list_predictions(keyword="看空", db_path=db)) == 1  # 方向标签
    assert len(list_predictions(keyword="平安", db_path=db)) == 1
    # 状态标签「命中」→ resolved_win
    rows = list_predictions(db_path=db)
    update_prediction_status(rows[0]["prediction_id"], {"status": "resolved_win"}, db_path=db)
    assert len(list_predictions(keyword="命中", db_path=db)) == 1


def test_list_date_range_inclusive(db):
    _insert(db, symbol="a.SH", created_at="2026-09-01T08:00:00")
    _insert(db, symbol="b.SH", created_at="2026-09-15T08:00:00")
    _insert(db, symbol="c.SH", created_at="2026-10-01T08:00:00")
    rows = list_predictions(date_from="2026-09-01", date_to="2026-09-30", db_path=db)
    assert sorted(r["symbol"] for r in rows) == ["a.SH", "b.SH"]  # 含两端


def test_count_predictions_reflects_filters(db):
    _insert(db, symbol="a.SH", symbol_name="茅台", created_at="2026-09-01T08:00:00")
    _insert(db, symbol="b.SH", symbol_name="茅台", created_at="2026-09-15T08:00:00")
    _insert(db, symbol="c.SH", symbol_name="平安", created_at="2026-10-01T08:00:00")
    assert count_predictions(keyword="茅台", db_path=db) == 2
    assert (
        count_predictions(keyword="茅台", date_from="2026-09-01", date_to="2026-09-30", db_path=db)
        == 2
    )


# ── update-decision-settlement-contract Task 1：结算契约 schema 与迁移 ──

# Δ2 前（stage-a 形态）的 predictions DDL：无 version_seq/snapshot_hash、无新列。
# 老库夹具必须含 stage-a 全列（尤其 symbol）——init_track_record_tables 第一步
# executescript(PREDICTIONS_DDL) 会建 idx_predictions_symbol，缺列则在建索引处报错，
# 早于任何 ALTER；因此不能只建 prediction_id/status 的退化表。
_LEGACY_PREDICTIONS_DDL = """
CREATE TABLE predictions (
  prediction_id     TEXT PRIMARY KEY,
  source_type       TEXT NOT NULL CHECK (source_type IN ('backtest','live')),
  symbol            TEXT NOT NULL,
  symbol_name       TEXT,
  direction         TEXT NOT NULL CHECK (direction IN ('long','short','neutral')),
  entry_price       REAL,
  target_price      REAL,
  horizon_days      INTEGER NOT NULL DEFAULT 252,
  confidence        REAL CHECK (confidence BETWEEN 0 AND 1),
  benchmark         TEXT NOT NULL DEFAULT '000300.SH',
  rationale_snapshot TEXT NOT NULL,
  langfuse_trace_id TEXT,
  status            TEXT NOT NULL DEFAULT 'open',
  created_at        TEXT NOT NULL,
  resolved_at       TEXT,
  exit_price        REAL,
  raw_return        REAL,
  excess_return     REAL,
  resolution_rule   TEXT,
  updated_at        TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_predictions_status ON predictions(status);
CREATE INDEX IF NOT EXISTS idx_predictions_symbol ON predictions(symbol);
"""


def test_settlement_contract_columns_migrated(tmp_path):
    """老库（无新列）经 init 后补齐 avoidance_status / settle_entry_price，幂等。"""
    import sqlite3

    from finance_agent.outcome.track_record.model import init_track_record_tables

    db = tmp_path / "old.db"
    conn = sqlite3.connect(db)
    conn.executescript(_LEGACY_PREDICTIONS_DDL)
    conn.execute(
        """INSERT INTO predictions (prediction_id, source_type, symbol, direction, horizon_days,
             rationale_snapshot, status, created_at, updated_at)
           VALUES ('p_legacy', 'live', '600519.SH', 'long', 252, '{}', 'open',
                   '2026-09-01T10:00:00', '2026-09-01T10:00:00')"""
    )
    conn.commit()
    conn.close()

    init_track_record_tables(db)
    init_track_record_tables(db)  # 幂等
    conn = sqlite3.connect(db)
    try:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(predictions)")}
        assert {"avoidance_status", "settle_entry_price"} <= cols
        assert {"version_seq", "snapshot_hash"} <= cols  # 既有迁移不回归
        # 存量行不回填、窗口不追溯（delta Scenario：存量观点不追溯）
        legacy = conn.execute(
            """SELECT horizon_days, avoidance_status, settle_entry_price FROM predictions
               WHERE prediction_id='p_legacy'"""
        ).fetchone()
        assert legacy == (252, None, None)
    finally:
        conn.close()


def test_mutable_fields_include_settlement_outputs():
    from finance_agent.outcome.track_record.model import _FROZEN_FIELDS, _MUTABLE_FIELDS

    assert "avoidance_status" in _MUTABLE_FIELDS
    assert "settle_entry_price" in _MUTABLE_FIELDS
    assert "avoidance_status" not in _FROZEN_FIELDS
    assert "settle_entry_price" not in _FROZEN_FIELDS
