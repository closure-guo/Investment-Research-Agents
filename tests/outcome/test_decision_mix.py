"""决策分布读数（看门狗）单测。"""

from __future__ import annotations

import json
import sqlite3

from finance_agent.outcome.ops.decision_mix import compute_decision_mix


def _seed(db_path, rows):
    conn = sqlite3.connect(db_path)
    conn.execute(
        "CREATE TABLE predictions (prediction_id TEXT PRIMARY KEY, "
        "created_at TEXT, rationale_snapshot TEXT)"
    )
    conn.executemany(
        "INSERT INTO predictions VALUES (?, ?, ?)",
        [
            (f"p{i}", ts, json.dumps({"action": act}, ensure_ascii=False))
            for i, (ts, act) in enumerate(rows)
        ],
    )
    conn.commit()
    conn.close()


def test_counts_by_day_and_flags_zero_buy_streak(tmp_path):
    db = tmp_path / "s.db"
    _seed(
        db,
        [
            ("2026-11-04T18:00:00", "sell"),
            ("2026-11-04T18:01:00", "watch"),
            ("2026-11-05T18:00:00", "sell"),
            ("2026-11-05T18:01:00", "sell"),
        ],
    )
    mix = compute_decision_mix(days=10, db_path=db)
    assert mix["total"] == {"buy": 0, "sell": 3, "watch": 1, "other": 0}
    assert mix["zero_buy_streak"] == 2
    assert [d["date"] for d in mix["days"]] == ["2026-11-04", "2026-11-05"]


def test_streak_resets_on_buy(tmp_path):
    db = tmp_path / "s.db"
    _seed(
        db,
        [
            ("2026-11-03T18:00:00", "sell"),
            ("2026-11-04T18:00:00", "buy"),
            ("2026-11-05T18:00:00", "watch"),
        ],
    )
    assert compute_decision_mix(days=10, db_path=db)["zero_buy_streak"] == 1


def test_missing_table_returns_empty(tmp_path):
    db = tmp_path / "empty.db"
    sqlite3.connect(db).close()
    mix = compute_decision_mix(days=5, db_path=db)
    assert mix == {
        "days": [],
        "zero_buy_streak": 0,
        "total": {"buy": 0, "sell": 0, "watch": 0, "other": 0},
    }
