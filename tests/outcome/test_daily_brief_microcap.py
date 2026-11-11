"""日报主档行隔离 + 小票行输入（add-microcap-lane-analysis T7）。"""

from __future__ import annotations

import sqlite3

from finance_agent.outcome import daily_brief
from finance_agent.outcome.daily_brief import collect_daily_facts
from finance_agent.outcome.ops.model import finish_job_run, init_ops, insert_job_run
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


def _microcap_pool_rows(symbols: list[str]) -> list[dict]:
    return [
        {
            "symbol": symbol,
            "composite_score": 90.0,
            "rank": idx + 1,
            "consecutive_days": 1,
            "l3_verdict": "keep",
            "l3_reason": "测试",
            "factor_contribs": [],
        }
        for idx, symbol in enumerate(symbols)
    ]


def test_collect_facts_microcap_pool_size(tmp_path, monkeypatch):
    """池规模 = 当日 selection_pool microcap 行数；当日无行 → None（「无数据」）。"""
    db = tmp_path / "screening.db"
    record_screening_batch(
        "2026-11-10",
        status="ok",
        started_at="2026-11-10T15:45:00",
        finished_at="2026-11-10T15:50:00",
        pool=POOL_MICROCAP,
        pool_rows=_microcap_pool_rows(["002749", "300001", "300002"]),
        db_path=db,
    )
    monkeypatch.setenv("EXECUTION_MICROCAP_DB_PATH", str(tmp_path / "missing-microcap.db"))
    facts = collect_daily_facts(
        "2026-11-10",
        sessions_db_path=tmp_path / "sessions.db",
        screening_db_path=db,
        execution_db_path=tmp_path / "execution.db",
        factor_db_path=tmp_path / "factor.db",
    )
    assert facts["screening"]["microcap_pool_size"] == 3

    # 换一个无池行的日期采集 → None（prompt 侧写「无数据」）
    facts_missing = collect_daily_facts(
        "2026-11-11",
        sessions_db_path=tmp_path / "sessions.db",
        screening_db_path=db,
        execution_db_path=tmp_path / "execution.db",
        factor_db_path=tmp_path / "factor.db",
    )
    assert facts_missing["screening"]["microcap_pool_size"] is None


def test_collect_facts_microcap_run_summary_from_ops_store(tmp_path, monkeypatch):
    """分析/跳过/派发失败数权威来源 = ops 作业摘要（auto_pool_analysis_microcap 最新行）。"""
    db = tmp_path / "screening.db"
    record_screening_batch(
        "2026-11-10",
        status="ok",
        started_at="2026-11-10T15:45:00",
        finished_at="2026-11-10T15:50:00",
        pool=POOL_MICROCAP,
        pool_rows=_microcap_pool_rows(["002749"]),
        db_path=db,
    )
    sessions = tmp_path / "sessions.db"
    run_id = insert_job_run("auto_pool_analysis_microcap", "scheduled", "running", db_path=sessions)
    finish_job_run(
        run_id,
        status="ok",
        summary={
            "trade_date": "2026-11-10",
            "pool_size": 5,
            "selected": 4,
            "started": 3,
            "skipped_existing": 1,
            "dispatch_failed": 2,
        },
        db_path=sessions,
    )
    monkeypatch.setenv("EXECUTION_MICROCAP_DB_PATH", str(tmp_path / "missing-microcap.db"))
    facts = collect_daily_facts(
        "2026-11-10",
        sessions_db_path=sessions,
        screening_db_path=db,
        execution_db_path=tmp_path / "execution.db",
        factor_db_path=tmp_path / "factor.db",
    )
    summary = facts["screening"]["microcap_run_summary"]
    assert summary["started"] == 3
    assert summary["skipped_existing"] == 1
    assert summary["dispatch_failed"] == 2


def test_collect_facts_microcap_run_summary_missing_run_returns_none(tmp_path, monkeypatch):
    """作业从未运行（库在、无运行行）→ 摘要为 None（prompt 侧逐字段写「无数据」）。"""
    db = tmp_path / "screening.db"
    record_screening_batch(
        "2026-11-10",
        status="ok",
        started_at="2026-11-10T15:45:00",
        finished_at="2026-11-10T15:50:00",
        pool=POOL_MICROCAP,
        pool_rows=_microcap_pool_rows(["002749"]),
        db_path=db,
    )
    sessions = tmp_path / "sessions.db"
    init_ops(db_path=sessions)  # 建空 ops 库
    monkeypatch.setenv("EXECUTION_MICROCAP_DB_PATH", str(tmp_path / "missing-microcap.db"))
    facts = collect_daily_facts(
        "2026-11-10",
        sessions_db_path=sessions,
        screening_db_path=db,
        execution_db_path=tmp_path / "execution.db",
        factor_db_path=tmp_path / "factor.db",
    )
    assert facts["screening"]["microcap_run_summary"] is None


def test_collect_facts_microcap_run_summary_stale_date_returns_none(tmp_path, monkeypatch):
    """评审 Finding 2：摘要 trade_date ≠ 采集日 → None，昨日数字不得冒充当日。

    当日 16:47 作业 misfire（后端宕/被关）时 ops 库最新一行是昨天的摘要；
    日报 MUST 按日期过滤后写「无数据」，MUST NOT 推测补齐。
    """
    db = tmp_path / "screening.db"
    record_screening_batch(
        "2026-11-10",
        status="ok",
        started_at="2026-11-10T15:45:00",
        finished_at="2026-11-10T15:50:00",
        pool=POOL_MICROCAP,
        pool_rows=_microcap_pool_rows(["002749"]),
        db_path=db,
    )
    sessions = tmp_path / "sessions.db"
    run_id = insert_job_run("auto_pool_analysis_microcap", "scheduled", "running", db_path=sessions)
    finish_job_run(
        run_id,
        status="ok",
        summary={
            "trade_date": "2026-11-09",  # 昨日摘要
            "pool_size": 5,
            "selected": 4,
            "started": 3,
            "skipped_existing": 1,
            "dispatch_failed": 2,
        },
        db_path=sessions,
    )
    monkeypatch.setenv("EXECUTION_MICROCAP_DB_PATH", str(tmp_path / "missing-microcap.db"))
    facts = collect_daily_facts(
        "2026-11-10",  # 采集 2026-11-10，摘要却是 11-09 的
        sessions_db_path=sessions,
        screening_db_path=db,
        execution_db_path=tmp_path / "execution.db",
        factor_db_path=tmp_path / "factor.db",
    )
    assert facts["screening"]["microcap_run_summary"] is None
