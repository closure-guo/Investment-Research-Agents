"""研报触发日预算 main 池过滤（add-microcap-lane-analysis T7）。"""

from __future__ import annotations

from finance_agent.screening.pool_analysis import (
    POOL_MICROCAP,
    record_pool_analysis_run,
)


def test_today_used_ignores_microcap(tmp_path, monkeypatch):
    db = tmp_path / "screening.db"
    for i in range(8):
        record_pool_analysis_run("2026-11-10", f"60000{i}", "s", "queued", db_path=db)
    for i in range(10):
        record_pool_analysis_run(
            "2026-11-10", f"00274{i}", "s", "queued", pool=POOL_MICROCAP, db_path=db
        )
    monkeypatch.setenv("SCREENING_DB_PATH", str(db))
    from finance_agent.screening.report_trigger import _today_used, init_db

    # 单测绕过了生产入口（prepare_report_analysis_batch 会 init_db），这里补建
    # report_analysis_runs 空表，使 _today_used 可运行、红相落在预算计数断言上。
    init_db(db)

    assert _today_used("2026-11-10", db) == 8  # 仅 main 行


def test_today_used_survives_legacy_db_without_pool_column(tmp_path):
    """旧库（pool_analysis_runs 存在但无 pool 列）读日预算不炸 OperationalError。

    评审 Finding 1：迁移只在 16:22 作业首跑时发生时，手动触发端点对旧库裸 SQL
    ``AND pool='main'`` 直接 500。读路径必须幂等补列（存量行默认 main），
    而不是靠应用启动链恰好先跑过。
    """
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
        "INSERT INTO pool_analysis_runs VALUES ('2026-11-10','600251','old','completed',NULL,'t','t')"
    )
    conn.commit()
    conn.close()

    from finance_agent.screening.report_trigger import _today_used, init_db

    init_db(db)  # report_analysis_runs 空表（同 test_today_used_ignores_microcap 的绕过补建）

    assert _today_used("2026-11-10", db) == 1  # 迁移后存量行按 main 计入预算
    assert _today_used("2026-11-11", db) == 0  # 次日为 0，不抛错
