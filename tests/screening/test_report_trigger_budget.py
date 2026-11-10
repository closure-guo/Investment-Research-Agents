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
