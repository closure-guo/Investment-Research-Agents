"""TDD tests for data/snapshot.py — 市场快照 schema/meta 与行构造纯函数。

覆盖 add-market-snapshot 计划 Task 1（schema/连接/meta 工具）与 Task 2
（build_snapshot_rows / compute_rebase_factors）的验收行为。
"""

from __future__ import annotations

from finance_agent.data.snapshot import (
    _connect,
    get_all_meta,
    get_meta,
    init_snapshot_db,
    set_meta,
)

# Global Constraints 钉死的 market_snapshot 列集合（逐字，不得改名）。
_SNAPSHOT_COLUMNS = {
    "trade_date",
    "code",
    "name",
    "open",
    "high",
    "low",
    "close_raw",
    "close_qfq",
    "volume",
    "amount",
    "pct_chg",
    "prev_close",
    "is_st",
    "is_suspended",
    "limit_up_sealed",
    "limit_down_sealed",
    "listed_days",
    "list_date",
}


def test_init_creates_tables_and_is_idempotent(tmp_path):
    """init 幂等建 3 表 + 2 索引；列名逐字一致；连接为 WAL + busy_timeout。"""
    db = tmp_path / "market_snapshot.db"
    assert init_snapshot_db(db) == db
    assert init_snapshot_db(db) == db  # 二次调用不报错（CREATE IF NOT EXISTS）

    conn = _connect(db)
    try:
        tables = {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }
        assert {"market_snapshot", "snapshot_meta", "security_master"} <= tables

        cols = {row[1] for row in conn.execute("PRAGMA table_info(market_snapshot)").fetchall()}
        assert cols == _SNAPSHOT_COLUMNS

        meta_cols = {row[1] for row in conn.execute("PRAGMA table_info(snapshot_meta)").fetchall()}
        assert meta_cols == {"key", "value"}
        master_cols = {
            row[1] for row in conn.execute("PRAGMA table_info(security_master)").fetchall()
        }
        assert master_cols == {"code", "name", "list_date", "updated_at"}

        indexes = {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='index'").fetchall()
        }
        assert {"idx_market_snapshot_code_date", "idx_market_snapshot_date"} <= indexes

        assert str(conn.execute("PRAGMA journal_mode").fetchone()[0]).lower() == "wal"
        assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == 15000
    finally:
        conn.close()


def test_meta_roundtrip(tmp_path):
    """set/get/get_all_meta：缺失 → None，覆盖写生效，全量读出。"""
    db = tmp_path / "market_snapshot.db"
    init_snapshot_db(db)

    assert get_meta("last_success_date", db_path=db) is None
    assert get_all_meta(db_path=db) == {}

    set_meta("last_success_date", "2026-10-13", db_path=db)
    set_meta("last_error", "boom", db_path=db)
    assert get_meta("last_success_date", db_path=db) == "2026-10-13"
    assert get_all_meta(db_path=db) == {
        "last_success_date": "2026-10-13",
        "last_error": "boom",
    }

    set_meta("last_success_date", "2026-10-14", db_path=db)  # 幂等覆盖
    assert get_meta("last_success_date", db_path=db) == "2026-10-14"
    assert len(get_all_meta(db_path=db)) == 2
