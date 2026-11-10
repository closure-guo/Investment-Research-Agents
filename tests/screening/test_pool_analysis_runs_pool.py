"""pool_analysis_runs.pool 归属列（add-microcap-lane-analysis T1）。"""

from finance_agent.screening.pool_analysis import (
    POOL_MAIN,
    POOL_MICROCAP,
    get_pool_analysis_run,
    list_pool_analysis_runs,
    record_pool_analysis_run,
)


def test_pool_column_default_main(tmp_path):
    db = tmp_path / "screening.db"
    record_pool_analysis_run("2026-11-10", "002040", "sess-1", "queued", db_path=db)
    row = get_pool_analysis_run("2026-11-10", "002040", db_path=db)
    assert row["pool"] == POOL_MAIN


def test_pool_column_explicit_microcap(tmp_path):
    db = tmp_path / "screening.db"
    record_pool_analysis_run(
        "2026-11-10", "002749", "sess-2", "queued", pool=POOL_MICROCAP, db_path=db
    )
    row = get_pool_analysis_run("2026-11-10", "002749", db_path=db)
    assert row["pool"] == POOL_MICROCAP


def test_pool_filter_list(tmp_path):
    db = tmp_path / "screening.db"
    record_pool_analysis_run("2026-11-10", "002040", "s1", "queued", db_path=db)
    record_pool_analysis_run("2026-11-10", "002749", "s2", "queued", pool=POOL_MICROCAP, db_path=db)
    assert [
        r["symbol"] for r in list_pool_analysis_runs("2026-11-10", pool=POOL_MAIN, db_path=db)
    ] == ["002040"]
    assert [
        r["symbol"] for r in list_pool_analysis_runs("2026-11-10", pool=POOL_MICROCAP, db_path=db)
    ] == ["002749"]
    assert len(list_pool_analysis_runs("2026-11-10", db_path=db)) == 2


def test_old_db_migration_adds_pool(tmp_path):
    """旧库（无 pool 列）经 init 幂等补列，存量行 pool='main'。"""
    import sqlite3

    db = tmp_path / "screening.db"
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE pool_analysis_runs (trade_date TEXT NOT NULL, symbol TEXT NOT NULL,"
        " session_id TEXT NOT NULL, status TEXT NOT NULL, error TEXT,"
        " created_at TEXT NOT NULL, updated_at TEXT NOT NULL,"
        " PRIMARY KEY (trade_date, symbol))"
    )
    conn.execute(
        "INSERT INTO pool_analysis_runs VALUES ('2026-11-09','600251','old','completed',NULL,'t','t')"
    )
    conn.commit()
    conn.close()
    record_pool_analysis_run("2026-11-10", "002040", "s1", "queued", db_path=db)
    old = get_pool_analysis_run("2026-11-09", "600251", db_path=db)
    assert old["pool"] == POOL_MAIN  # 迁移默认值
