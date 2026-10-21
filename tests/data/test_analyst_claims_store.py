"""分析师事后检验存储层测试（add-analyst-ex-post-verification）。

覆盖：三表 DDL、主张幂等、结算状态流转、记分卡快照替换。
"""

from __future__ import annotations

import sqlite3

from finance_agent.data import analyst_claims_store as store


def _claim(**over):
    row = {
        "claim_key": "k1",
        "claim_type": "rating_upgrade",
        "code": "600519",
        "org": "中信证券",
        "analyst": "张三",
        "published_at": "2026-10-20",
        "rating": "买入",
        "prev_rating": "增持",
        "target_price": None,
        "prev_target_price": None,
        "eps_year": None,
        "eps_value": None,
        "prev_eps_value": None,
        "horizon_days": 20,
        "source": "cninfo",
        "industry": "白酒",
    }
    row.update(over)
    return row


def _count(db_path, table: str) -> int:
    conn = sqlite3.connect(db_path)
    try:
        return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]  # noqa: S608
    finally:
        conn.close()


class TestSchema:
    def test_init_creates_all_tables(self, tmp_path):
        db = tmp_path / "ac.db"
        store.init_db(db)
        conn = sqlite3.connect(db)
        try:
            names = {
                r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
            }
        finally:
            conn.close()
        assert {"analyst_claims", "analyst_claim_outcomes", "analyst_scorecard_snapshots"} <= names

    def test_init_idempotent(self, tmp_path):
        db = tmp_path / "ac.db"
        store.init_db(db)
        store.init_db(db)
        assert _count(db, "analyst_claims") == 0


class TestClaims:
    def test_upsert_claim_idempotent(self, tmp_path):
        db = tmp_path / "ac.db"
        assert store.upsert_claims([_claim()], db_path=db) == 1
        assert store.upsert_claims([_claim()], db_path=db) == 1
        assert _count(db, "analyst_claims") == 1

    def test_same_key_new_payload_does_not_duplicate(self, tmp_path):
        db = tmp_path / "ac.db"
        store.upsert_claims([_claim()], db_path=db)
        store.upsert_claims([_claim(rating="买入")], db_path=db)
        assert _count(db, "analyst_claims") == 1

    def test_list_pending_excludes_resolved_and_unresolved(self, tmp_path):
        db = tmp_path / "ac.db"
        store.upsert_claims(
            [_claim(claim_key="a"), _claim(claim_key="b"), _claim(claim_key="c")],
            db_path=db,
        )
        store.upsert_outcome(
            "a",
            status="resolved",
            entry_date="2026-10-21",
            entry_price=10.0,
            exit_date="2026-11-18",
            exit_price=11.0,
            raw_return=0.1,
            benchmark_return=0.02,
            excess_return=0.08,
            reason=None,
            db_path=db,
        )
        store.upsert_outcome(
            "b",
            status="unresolved",
            entry_date=None,
            entry_price=None,
            exit_date=None,
            exit_price=None,
            raw_return=None,
            benchmark_return=None,
            excess_return=None,
            reason="suspended",
            db_path=db,
        )
        pending = {row["claim_key"] for row in store.list_pending_claims(db_path=db)}
        assert pending == {"c"}

    def test_outcome_upsert_is_idempotent(self, tmp_path):
        db = tmp_path / "ac.db"
        store.upsert_claims([_claim()], db_path=db)
        args = {
            "status": "resolved",
            "entry_date": "2026-10-21",
            "entry_price": 10.0,
            "exit_date": "2026-11-18",
            "exit_price": 11.0,
            "raw_return": 0.1,
            "benchmark_return": 0.02,
            "excess_return": 0.08,
            "reason": None,
            "db_path": db,
        }
        store.upsert_outcome("k1", **args)
        store.upsert_outcome("k1", **args)
        assert _count(db, "analyst_claim_outcomes") == 1


class TestScorecardStore:
    def _row(self, **over):
        row = {
            "bucket_type": "org",
            "bucket_key": "中信证券",
            "n": 12,
            "wins": 8,
            "win_rate": 8 / 12,
            "mean_excess": 0.03,
            "median_excess": 0.02,
            "reliable": False,
            "weight": None,
            "tier": "1",
        }
        row.update(over)
        return row

    def test_replace_scorecard_is_idempotent_for_same_date(self, tmp_path):
        db = tmp_path / "ac.db"
        store.replace_scorecard("2026-10-20", [self._row()], db_path=db)
        store.replace_scorecard("2026-10-20", [self._row()], db_path=db)
        assert _count(db, "analyst_scorecard_snapshots") == 1

    def test_query_by_snapshot_and_bucket_type(self, tmp_path):
        db = tmp_path / "ac.db"
        store.replace_scorecard(
            "2026-10-20",
            [self._row(), self._row(bucket_type="rating", bucket_key="买入")],
            db_path=db,
        )
        rows = store.list_scorecard(snapshot_date="2026-10-20", bucket_type="org", db_path=db)
        assert [r["bucket_key"] for r in rows] == ["中信证券"]


class TestRegionMigration:
    def test_claim_region_defaults_to_domestic(self, tmp_path):
        db = tmp_path / "ac.db"
        store.upsert_claims([_claim()], db_path=db)
        conn = sqlite3.connect(db)
        conn.row_factory = sqlite3.Row
        try:
            row = conn.execute("SELECT region, depth_class FROM analyst_claims").fetchone()
        finally:
            conn.close()
        assert row["region"] == "domestic"
        assert row["depth_class"] is None

    def test_old_schema_gets_region_and_depth_columns(self, tmp_path):
        db = tmp_path / "old.db"
        conn = sqlite3.connect(db)
        conn.executescript(
            """
            CREATE TABLE analyst_claims (
              claim_key TEXT PRIMARY KEY, claim_type TEXT NOT NULL, code TEXT NOT NULL,
              org TEXT NOT NULL, analyst TEXT, published_at TEXT NOT NULL, rating TEXT,
              prev_rating TEXT, target_price REAL, prev_target_price REAL,
              eps_year INTEGER, eps_value REAL, prev_eps_value REAL,
              horizon_days INTEGER NOT NULL, industry TEXT, source TEXT NOT NULL,
              created_at TEXT NOT NULL
            );
            CREATE TABLE analyst_scorecard_snapshots (
              snapshot_date TEXT NOT NULL, bucket_type TEXT NOT NULL,
              bucket_key TEXT NOT NULL, n INTEGER NOT NULL, wins INTEGER NOT NULL,
              win_rate REAL, mean_excess REAL, median_excess REAL,
              reliable INTEGER NOT NULL, weight REAL, tier TEXT,
              updated_at TEXT NOT NULL,
              PRIMARY KEY (snapshot_date, bucket_type, bucket_key)
            );
            """
        )
        conn.commit()
        conn.close()
        store.init_db(db)
        conn = sqlite3.connect(db)
        try:
            claim_cols = {row[1] for row in conn.execute("PRAGMA table_info(analyst_claims)")}
            snap_cols = {
                row[1] for row in conn.execute("PRAGMA table_info(analyst_scorecard_snapshots)")
            }
        finally:
            conn.close()
        assert {"region", "depth_class"} <= claim_cols
        assert {"region", "score_family", "weighted_mean_excess", "deep_n"} <= snap_cols
