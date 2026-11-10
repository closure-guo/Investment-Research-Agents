"""日报主档行隔离 + 小票行输入（add-microcap-lane-analysis T7）。"""

from __future__ import annotations

import sqlite3

from finance_agent.outcome import daily_brief
from finance_agent.outcome.daily_brief import collect_daily_facts
from finance_agent.screening.pool_analysis import (
    POOL_MICROCAP,
    record_pool_analysis_run,
)
from finance_agent.screening.store import record_screening_batch


def test_brief_runs_query_pools_split(tmp_path):
    """daily_brief 的运行行读取：主档行与 microcap 行分开统计。"""
    db = tmp_path / "screening.db"
    record_pool_analysis_run("2026-11-10", "600001", "s1", "queued", db_path=db)
    record_pool_analysis_run("2026-11-10", "002749", "s2", "queued", pool=POOL_MICROCAP, db_path=db)
    from finance_agent.screening.pool_analysis import list_pool_analysis_runs

    assert len(list_pool_analysis_runs("2026-11-10", pool="main", db_path=db)) == 1
    assert len(list_pool_analysis_runs("2026-11-10", pool=POOL_MICROCAP, db_path=db)) == 1


def test_collect_facts_splits_main_and_microcap_runs(tmp_path, monkeypatch):
    """facts.screening 拆分主档行与小票行：microcap_runs 只含 lane=microcap 行。"""
    db = tmp_path / "screening.db"
    # _screening_facts 先读批次/池（缺表即整段降级），先落一条最小批次
    record_screening_batch(
        "2026-11-10",
        status="ok",
        started_at="2026-11-10T15:40:00",
        finished_at="2026-11-10T16:09:00",
        l1_passed=1,
        l2_top=1,
        l3_keep=1,
        pool_rows=[],
        db_path=db,
    )
    record_pool_analysis_run("2026-11-10", "600001", "s1", "queued", db_path=db)
    record_pool_analysis_run("2026-11-10", "002749", "s2", "queued", pool=POOL_MICROCAP, db_path=db)
    monkeypatch.setenv("EXECUTION_MICROCAP_DB_PATH", str(tmp_path / "missing-microcap.db"))
    facts = collect_daily_facts(
        "2026-11-10",
        sessions_db_path=tmp_path / "sessions.db",
        screening_db_path=db,
        execution_db_path=tmp_path / "execution.db",
        factor_db_path=tmp_path / "factor.db",
    )
    runs = facts["screening"]["auto_analysis_runs"]
    microcap = facts["screening"]["microcap_runs"]
    assert [row["symbol"] for row in runs] == ["600001"]
    assert [row["symbol"] for row in microcap] == ["002749"]
    assert microcap[0]["dispatch_status"] == "enqueued"
    # 小票净值库缺失 → None（prompt 侧写「无数据」）
    assert facts["screening"]["microcap_equity"] is None


def test_read_microcap_equity_latest_row(tmp_path, monkeypatch):
    """小票账本净值取最新一行。"""
    mc_db = tmp_path / "execution_microcap.db"
    con = sqlite3.connect(mc_db)
    con.execute(
        "CREATE TABLE paper_equity ("
        "trade_date TEXT PRIMARY KEY, cash REAL, positions_value REAL, equity REAL)"
    )
    con.execute("INSERT INTO paper_equity VALUES ('2026-11-09', 90.0, 10.0, 100.0)")
    con.execute("INSERT INTO paper_equity VALUES ('2026-11-10', 95.0, 15.0, 110.0)")
    con.commit()
    con.close()
    monkeypatch.setenv("EXECUTION_MICROCAP_DB_PATH", str(mc_db))
    row = daily_brief._read_microcap_equity()
    assert row == {
        "trade_date": "2026-11-10",
        "cash": 95.0,
        "positions_value": 15.0,
        "equity": 110.0,
    }


def test_read_microcap_equity_missing_db_returns_none(tmp_path, monkeypatch):
    monkeypatch.setenv("EXECUTION_MICROCAP_DB_PATH", str(tmp_path / "nope.db"))
    assert daily_brief._read_microcap_equity() is None
