"""Δ3 Task 1:cohort_runs 记账表(独立于 predictions,零 schema 变更)。

Delta add-forward-paper-trading-cohort;幂等键 (universe_version, ticker, trade_date, run_seq)。
"""

import sqlite3

import pytest

from finance_agent.outcome.cohort.model import (
    init_cohort_runs,
    insert_cohort_run,
    list_cohort_runs,
)


def test_init_and_insert_roundtrip(tmp_path):
    db = tmp_path / "s.db"
    init_cohort_runs(db)
    init_cohort_runs(db)  # 幂等
    rid = insert_cohort_run(
        {
            "universe_version": "v1",
            "ticker": "600519",
            "trade_date": "2026-09-24",
            "session_id": "s-1",
            "langfuse_trace_id": "t-1",
            "status": "success",
            "run_seq": 0,
            "llm_calls": 42,
            "tokens_prompt": 100000,
            "tokens_completion": 60000,
            "tokens_total": 160000,
            "trigger_time": "2026-09-24T18:00:03",
        },
        db,
    )
    rows = list_cohort_runs(db_path=db)
    assert len(rows) == 1 and rows[0]["run_id"] == rid
    assert rows[0]["tokens_total"] == 160000


def test_same_key_same_seq_conflict_and_force_seq(tmp_path):
    # UNIQUE(universe_version, ticker, trade_date, run_seq)：同 seq 重复插入抛 IntegrityError
    # run_seq=1（force 再跑）可插入 → 两行共存，list 返回 2 行
    db = tmp_path / "s.db"
    init_cohort_runs(db)
    base = {
        "universe_version": "v1",
        "ticker": "600519",
        "trade_date": "2026-09-24",
        "session_id": "s-1",
        "status": "success",
        "trigger_time": "2026-09-24T18:00:03",
    }
    insert_cohort_run({**base, "run_seq": 0}, db)
    with pytest.raises(sqlite3.IntegrityError):
        insert_cohort_run({**base, "run_seq": 0}, db)
    insert_cohort_run({**base, "run_seq": 1}, db)  # 显式 force 再跑
    rows = list_cohort_runs(db_path=db)
    assert len(rows) == 2
    assert [r["run_seq"] for r in rows] == [0, 1]


def test_run_seq_defaults_to_zero_and_created_at_server_side(tmp_path):
    db = tmp_path / "s.db"
    init_cohort_runs(db)
    insert_cohort_run(
        {
            "universe_version": "v1",
            "ticker": "000001",
            "trade_date": "2026-09-24",
            "status": "failure",
            "failure_reason": "no_report_ready",
            "trigger_time": "2026-09-24T18:00:03",
        },
        db,
    )
    row = list_cohort_runs(db_path=db)[0]
    assert row["run_seq"] == 0  # DDL 默认
    assert row["created_at"]  # 服务端生成，非空
    assert row["session_id"] is None
    assert row["tokens_total"] is None


def test_status_check_rejects_unknown(tmp_path):
    db = tmp_path / "s.db"
    init_cohort_runs(db)
    with pytest.raises(sqlite3.IntegrityError):
        insert_cohort_run(
            {
                "universe_version": "v1",
                "ticker": "600519",
                "trade_date": "2026-09-24",
                "status": "boom",
                "trigger_time": "2026-09-24T18:00:03",
            },
            db,
        )


def test_list_filters_and_ordering(tmp_path):
    db = tmp_path / "s.db"
    init_cohort_runs(db)

    def rec(**kw):
        base = {
            "universe_version": "v1",
            "ticker": "600519",
            "trade_date": "2026-09-24",
            "status": "success",
            "trigger_time": "2026-09-24T18:00:03",
        }
        base.update(kw)
        return base

    # 乱序写入：跨 trade_date / 跨 ticker / 同 key force 递增
    insert_cohort_run(rec(trade_date="2026-09-25", ticker="000001"), db)
    insert_cohort_run(rec(trade_date="2026-09-24", ticker="600519", run_seq=0), db)
    insert_cohort_run(rec(trade_date="2026-09-24", ticker="600519", run_seq=1), db)
    insert_cohort_run(rec(universe_version="v2", trade_date="2026-09-24", ticker="300750"), db)

    rows = list_cohort_runs(db_path=db)
    assert [(r["trade_date"], r["ticker"], r["run_seq"]) for r in rows] == [
        ("2026-09-24", "300750", 0),
        ("2026-09-24", "600519", 0),
        ("2026-09-24", "600519", 1),
        ("2026-09-25", "000001", 0),
    ]

    v2 = list_cohort_runs(universe_version="v2", db_path=db)
    assert [r["ticker"] for r in v2] == ["300750"]

    day = list_cohort_runs(trade_date="2026-09-25", db_path=db)
    assert [r["ticker"] for r in day] == ["000001"]

    since = list_cohort_runs(since="2026-09-25", db_path=db)
    assert [r["ticker"] for r in since] == ["000001"]


def test_init_is_idempotent_and_table_stays_usable(tmp_path):
    """同库双 init_cohort_runs → 不抛、不丢表、仍可插入（行为断言，非 DDL 子串恒真）。"""
    db = tmp_path / "s.db"
    init_cohort_runs(db)
    init_cohort_runs(db)
    insert_cohort_run(
        {
            "universe_version": "v1",
            "ticker": "600519",
            "trade_date": "2026-09-24",
            "status": "success",
            "trigger_time": "2026-09-24T18:00:03",
        },
        db,
    )
    assert len(list_cohort_runs(db_path=db)) == 1
