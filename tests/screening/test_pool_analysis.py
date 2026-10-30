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


def test_stale_pool_skips(tmp_path, monkeypatch):
    """批次既非当日也非日历上的前一交易日 → skipped_stale_pool。

    #533：原用例未注入日历、泄漏读到真实快照库，#456 放宽后真实日历含 10-19
    时「前一交易日补跑池」已是应接受场景。改为显式日历（10-19 缺席、10-20 的
    前一交易日为 10-16），钉死「不在日历前一位的旧批次仍被拦」；无历 fail-closed
    的分支由 test_missing_calendar_keeps_strict_gate 覆盖。
    """
    db = tmp_path / "screening.db"
    _seed(db, "2026-10-19", [_row("600519", 1)])
    monkeypatch.setattr(
        pool_analysis,
        "read_snapshot_calendar",
        _calendar(["2026-10-15", "2026-10-16", "2026-10-20"]),
    )
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


# ── issue #456：补跑池时效门槛放宽 + 运行行对账 ────────────────────────────────
#
# 缺陷 B：选股批深夜补跑出的池（trade_date=前一交易日）被「只分析当日池」硬门槛
# 挡住，次日 16:22 永远 skipped_stale_pool——10-21~23 连续三天池分析空转。放宽为
# 「当日或前一交易日」仍接受（时效性重校验照常把关），更旧的池维持跳过。
#
# 缺陷 A：分析完成不回写运行行（会话终态 ≠ 行状态），运维侧无法判断链路在不在
# 工作。补一个对账函数：按会话终态把 queued/dispatch_failed 行落成 completed/failed。


def _calendar(dates: list[str] | None):
    return (lambda: list(dates)) if dates is not None else (lambda: None)


def test_prev_trading_day_pool_still_analyzed(tmp_path, monkeypatch):
    db = tmp_path / "screening.db"
    _seed(db, "2026-10-19", [_row("600519", 1), _row("000001", 2)])
    monkeypatch.setattr(
        pool_analysis,
        "read_snapshot_calendar",
        _calendar(["2026-10-15", "2026-10-16", "2026-10-19", "2026-10-20"]),
    )
    started: list[str] = []
    out = pool_analysis.prepare_pool_analysis_batch(
        as_of="2026-10-20",
        enqueue=lambda s, sid: started.append(s) or True,
        db_path=db,
        screening_db_path=db,
    )
    assert out["status"] == "ok"
    assert out["batch_date"] == "2026-10-19"
    assert out["stale_batch"] == "2026-10-19"
    assert out["started"] == 2
    assert started == ["600519", "000001"]


def test_pool_older_than_prev_trading_day_still_skipped(tmp_path, monkeypatch):
    db = tmp_path / "screening.db"
    _seed(db, "2026-10-15", [_row("600519", 1)])
    monkeypatch.setattr(
        pool_analysis,
        "read_snapshot_calendar",
        _calendar(["2026-10-15", "2026-10-16", "2026-10-19", "2026-10-20"]),
    )
    out = pool_analysis.prepare_pool_analysis_batch(
        as_of="2026-10-20",
        enqueue=lambda s, sid: True,
        db_path=db,
        screening_db_path=db,
    )
    assert out["status"] == "skipped_stale_pool"
    assert out["started"] == 0


def test_missing_calendar_keeps_strict_gate(tmp_path, monkeypatch):
    """无交易日历不猜：维持「只分析当日池」的旧行为（fail-closed）。"""
    db = tmp_path / "screening.db"
    _seed(db, "2026-10-19", [_row("600519", 1)])
    monkeypatch.setattr(pool_analysis, "read_snapshot_calendar", _calendar(None))
    out = pool_analysis.prepare_pool_analysis_batch(
        as_of="2026-10-20",
        enqueue=lambda s, sid: True,
        db_path=db,
        screening_db_path=db,
    )
    assert out["status"] == "skipped_stale_pool"


def test_reconcile_maps_session_terminal_states(tmp_path):
    db = tmp_path / "screening.db"
    _seed(db, "2026-10-20", [_row("600519", 1), _row("000001", 2)])
    out = pool_analysis.prepare_pool_analysis_batch(
        as_of="2026-10-20", enqueue=lambda s, sid: True, db_path=db, screening_db_path=db
    )
    assert out["started"] == 2
    rows = pool_analysis.list_pool_analysis_runs("2026-10-20", db_path=db)
    by_symbol = {r["symbol"]: r for r in rows}
    session_store.update_session_status(by_symbol["600519"]["session_id"], "completed", None)
    session_store.update_session_status(by_symbol["000001"]["session_id"], "failed", "LLM 超时")

    reconciled = pool_analysis.reconcile_pool_analysis_runs("2026-10-20", db_path=db)

    assert reconciled == 2
    assert pool_analysis.get_pool_analysis_run("2026-10-20", "600519", db_path=db)["status"] == (
        "completed"
    )
    failed_row = pool_analysis.get_pool_analysis_run("2026-10-20", "000001", db_path=db)
    assert failed_row["status"] == "failed"
    assert "LLM 超时" in str(failed_row["error"])


def test_reconcile_leaves_inflight_alone(tmp_path):
    db = tmp_path / "screening.db"
    _seed(db, "2026-10-20", [_row("600519", 1)])
    pool_analysis.prepare_pool_analysis_batch(
        as_of="2026-10-20", enqueue=lambda s, sid: True, db_path=db, screening_db_path=db
    )
    # 会话仍是 running（刚创建的默认态）→ 行保持 queued
    reconciled = pool_analysis.reconcile_pool_analysis_runs("2026-10-20", db_path=db)
    assert reconciled == 0
    row = pool_analysis.get_pool_analysis_run("2026-10-20", "600519", db_path=db)
    assert row["status"] == "queued"


def test_reconcile_heals_dispatch_failed_row(tmp_path):
    db = tmp_path / "screening.db"
    _seed(db, "2026-10-20", [_row("600519", 1)])
    pool_analysis.prepare_pool_analysis_batch(
        as_of="2026-10-20", enqueue=lambda s, sid: False, db_path=db, screening_db_path=db
    )
    row = pool_analysis.get_pool_analysis_run("2026-10-20", "600519", db_path=db)
    session_store.update_session_status(row["session_id"], "completed", None)

    reconciled = pool_analysis.reconcile_pool_analysis_runs("2026-10-20", db_path=db)

    assert reconciled == 1
    healed = pool_analysis.get_pool_analysis_run("2026-10-20", "600519", db_path=db)
    assert healed["status"] == "completed"


def test_prepare_reconciles_then_skips_completed(tmp_path):
    """prepare 先对账再排队：已完成 (trade_date, symbol) 不重复烧一次分析。"""
    db = tmp_path / "screening.db"
    _seed(db, "2026-10-20", [_row("600519", 1)])
    pool_analysis.prepare_pool_analysis_batch(
        as_of="2026-10-20", enqueue=lambda s, sid: True, db_path=db, screening_db_path=db
    )
    row = pool_analysis.get_pool_analysis_run("2026-10-20", "600519", db_path=db)
    session_store.update_session_status(row["session_id"], "completed", None)

    started: list[str] = []
    out = pool_analysis.prepare_pool_analysis_batch(
        as_of="2026-10-20",
        enqueue=lambda s, sid: started.append(s) or True,
        db_path=db,
        screening_db_path=db,
    )

    assert out["started"] == 0
    assert out["skipped_existing"] == 1
    assert started == []
    assert pool_analysis.get_pool_analysis_run("2026-10-20", "600519", db_path=db)["status"] == (
        "completed"
    )


def test_reconcile_without_date_heals_all_dates(tmp_path):
    """trade_date=None 扫全表：历史僵尸行（如 10-20 挂三天的那条）一并治愈。"""
    db = tmp_path / "screening.db"
    _seed(db, "2026-10-20", [_row("600519", 1)])
    pool_analysis.prepare_pool_analysis_batch(
        as_of="2026-10-20", enqueue=lambda s, sid: True, db_path=db, screening_db_path=db
    )
    _seed(db, "2026-10-21", [_row("000001", 1)])
    pool_analysis.prepare_pool_analysis_batch(
        as_of="2026-10-21", enqueue=lambda s, sid: True, db_path=db, screening_db_path=db
    )
    for day, symbol in (("2026-10-20", "600519"), ("2026-10-21", "000001")):
        row = pool_analysis.get_pool_analysis_run(day, symbol, db_path=db)
        session_store.update_session_status(row["session_id"], "completed", None)

    healed = pool_analysis.reconcile_pool_analysis_runs(db_path=db)

    assert healed == 2
    assert pool_analysis.get_pool_analysis_run("2026-10-20", "600519", db_path=db)["status"] == (
        "completed"
    )
    assert pool_analysis.get_pool_analysis_run("2026-10-21", "000001", db_path=db)["status"] == (
        "completed"
    )
