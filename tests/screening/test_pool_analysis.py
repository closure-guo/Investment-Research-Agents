"""精选池自动深度分析编排测试（Task 2/3）。"""

from __future__ import annotations

from finance_agent import session_store
from finance_agent.screening import pool_analysis
from finance_agent.screening.store import record_screening_batch


def _row(symbol: str, rank: int) -> dict:
    return {
        "symbol": symbol,
        "composite_score": 100.0 - rank,
        "rank": rank,
        "consecutive_days": 3,
        "l3_verdict": "keep",
        "l3_reason": "测试",
        "factor_contribs": [],
    }


def _seed(db, trade_date: str, rows: list[dict], status: str = "ok") -> None:
    record_screening_batch(
        trade_date,
        status=status,
        started_at=f"{trade_date}T15:40:00",
        finished_at=f"{trade_date}T16:09:00",
        l1_passed=100,
        l2_top=100,
        l3_keep=len(rows),
        pool_rows=rows,
        db_path=db,
    )


def test_empty_pool_skips(tmp_path):
    db = tmp_path / "screening.db"
    _seed(db, "2026-10-20", [])
    out = pool_analysis.prepare_pool_analysis_batch(
        as_of="2026-10-20", enqueue=lambda s, sid: True, db_path=db, screening_db_path=db
    )
    assert out["status"] == "skipped_empty_pool"
    assert out["started"] == 0


def test_stale_pool_skips(tmp_path):
    db = tmp_path / "screening.db"
    _seed(db, "2026-10-19", [_row("600519", 1)])
    out = pool_analysis.prepare_pool_analysis_batch(
        as_of="2026-10-20", enqueue=lambda s, sid: True, db_path=db, screening_db_path=db
    )
    assert out["status"] == "skipped_stale_pool"
    assert out["batch_date"] == "2026-10-19"


def test_top_n_and_idempotent(tmp_path):
    db = tmp_path / "screening.db"
    _seed(db, "2026-10-20", [_row("600519", 1), _row("000001", 2), _row("300750", 3)])
    started: list[str] = []
    out1 = pool_analysis.prepare_pool_analysis_batch(
        as_of="2026-10-20",
        max_symbols=2,
        enqueue=lambda s, sid: started.append(s) or True,
        db_path=db,
        screening_db_path=db,
    )
    assert out1["status"] == "ok"
    assert out1["selected"] == 2
    assert out1["started"] == 2
    assert started == ["600519", "000001"]

    out2 = pool_analysis.prepare_pool_analysis_batch(
        as_of="2026-10-20",
        max_symbols=2,
        enqueue=lambda s, sid: started.append(s) or True,
        db_path=db,
        screening_db_path=db,
    )
    assert out2["started"] == 0
    assert out2["skipped_existing"] == 2
    assert started == ["600519", "000001"]


def test_freshness_removal(tmp_path, monkeypatch):
    db = tmp_path / "screening.db"
    _seed(db, "2026-10-20", [_row("600519", 1), _row("000001", 2)])
    monkeypatch.setattr(
        pool_analysis,
        "screening_freshness_removals",
        lambda rows, batch_date: [{"symbol": "600519", "reason": "SUSPENDED"}],
    )
    started: list[str] = []
    out = pool_analysis.prepare_pool_analysis_batch(
        as_of="2026-10-20",
        enqueue=lambda s, sid: started.append(s) or True,
        db_path=db,
        screening_db_path=db,
    )
    assert [item["symbol"] for item in out["removed"]] == ["600519"]
    assert started == ["000001"]


def test_dispatch_failure_reuses_session_on_retry(tmp_path):
    db = tmp_path / "screening.db"
    _seed(db, "2026-10-20", [_row("600519", 1)])

    out1 = pool_analysis.prepare_pool_analysis_batch(
        as_of="2026-10-20",
        enqueue=lambda s, sid: False,
        db_path=db,
        screening_db_path=db,
    )
    assert out1["dispatch_failed"] == 1
    row1 = pool_analysis.get_pool_analysis_run("2026-10-20", "600519", db_path=db)
    assert row1 is not None and row1["status"] == "dispatch_failed"
    first_session = row1["session_id"]
    assert first_session

    out2 = pool_analysis.prepare_pool_analysis_batch(
        as_of="2026-10-20",
        enqueue=lambda s, sid: True,
        db_path=db,
        screening_db_path=db,
    )
    assert out2["started"] == 1
    row2 = pool_analysis.get_pool_analysis_run("2026-10-20", "600519", db_path=db)
    assert row2 is not None
    assert row2["session_id"] == first_session
    assert row2["status"] == "queued"


def test_session_created_with_display_name(tmp_path):
    db = tmp_path / "screening.db"
    _seed(db, "2026-10-20", [_row("600519", 1)])
    out = pool_analysis.prepare_pool_analysis_batch(
        as_of="2026-10-20",
        enqueue=lambda s, sid: True,
        db_path=db,
        screening_db_path=db,
    )
    assert out["started"] == 1
    row = pool_analysis.get_pool_analysis_run("2026-10-20", "600519", db_path=db)
    session = session_store.get_session(row["session_id"])
    assert session is not None
    assert session["display_name"] == "600519 自动选股分析"
    assert session["status"] == "running"


def test_run_daily_disabled_skips(monkeypatch):
    monkeypatch.setenv("AUTO_POOL_ANALYSIS_ENABLED", "0")
    assert pool_analysis.run_pool_analysis_daily() == {"status": "skipped_disabled"}


def test_run_daily_passes_clamped_limit(monkeypatch):
    monkeypatch.setenv("AUTO_POOL_ANALYSIS_ENABLED", "1")
    monkeypatch.setenv("AUTO_POOL_ANALYSIS_MAX_SYMBOLS", "999")
    captured: dict = {}

    def fake_submit(**kwargs):
        captured.update(kwargs)
        return {"status": "ok"}

    monkeypatch.setattr(pool_analysis, "_submit", fake_submit)
    assert pool_analysis.run_pool_analysis_daily() == {"status": "ok"}
    assert captured["max_symbols"] == 100
