"""小票池自动分析编排（add-microcap-lane-analysis T5）。"""

from __future__ import annotations

from finance_agent.screening import pool_analysis
from finance_agent.screening.pool_analysis import (
    POOL_MICROCAP,
    prepare_pool_analysis_batch,
    record_pool_analysis_run,
)


class _FakePool:
    """latest_successful_pool 的桩：返回固定 microcap 池。"""

    def __init__(self, rows):
        self.rows = rows

    def __call__(self, *, db_path=None, pool="main"):
        return (
            {"batch": {"trade_date": "2026-11-10"}, "pool": self.rows}
            if pool == POOL_MICROCAP
            else None
        )


def _rows(symbols):
    return [{"symbol": s, "rank": i + 1, "stock_name": f"票{s}"} for i, s in enumerate(symbols)]


def test_skip_analyzed_and_fill(monkeypatch, tmp_path):
    db = tmp_path / "screening.db"
    # 当日主档已分析 002040（全局幂等行）
    record_pool_analysis_run("2026-11-10", "002040", "s-main", "queued", db_path=db)
    rows = _rows(["002040", "002749", "002852", "301058"])
    monkeypatch.setattr(
        "finance_agent.screening.pool_analysis.latest_successful_pool", _FakePool(rows)
    )
    enqueue = _EnqueueSpy()
    result = prepare_pool_analysis_batch(
        as_of="2026-11-10",
        max_symbols=2,
        enqueue=enqueue,
        screening_db_path=db,
        db_path=db,
        pool=POOL_MICROCAP,
    )
    # 002040 跳过（主档行在），002749/002852 按序补位
    assert result["selected"] == 2
    assert result["started_symbols"] == ["002749", "002852"]
    assert result["skipped_existing"] == 1


class _EnqueueSpy:
    def __init__(self):
        self.calls = []

    def __call__(self, symbol, session_id):
        self.calls.append(symbol)
        return True


def test_fill_insufficient_disclosed(monkeypatch, tmp_path):
    db = tmp_path / "screening.db"
    record_pool_analysis_run("2026-11-10", "002040", "s-main", "queued", db_path=db)
    record_pool_analysis_run(
        "2026-11-10", "002749", "s-mc", "queued", pool=POOL_MICROCAP, db_path=db
    )
    rows = _rows(["002040", "002749", "002852"])
    monkeypatch.setattr(
        "finance_agent.screening.pool_analysis.latest_successful_pool", _FakePool(rows)
    )
    result = prepare_pool_analysis_batch(
        as_of="2026-11-10",
        max_symbols=10,
        enqueue=_EnqueueSpy(),
        screening_db_path=db,
        db_path=db,
        pool=POOL_MICROCAP,
    )
    assert result["selected"] == 1  # 只剩 002852 可选，如实披露


# ── 池陈旧跳过（delta spec 场景；模式照搬主档 test_stale_pool_skips） ──────────


class _StaleFakePool(_FakePool):
    """同 _FakePool 桩，但批次日可钉成更旧的日期。"""

    def __init__(self, rows, batch_date):
        super().__init__(rows)
        self.batch_date = batch_date

    def __call__(self, *, db_path=None, pool="main"):
        return (
            {"batch": {"trade_date": self.batch_date}, "pool": self.rows}
            if pool == POOL_MICROCAP
            else None
        )


def _calendar(dates: list[str] | None):
    return (lambda: list(dates)) if dates is not None else (lambda: None)


def test_microcap_stale_pool_skips(tmp_path, monkeypatch):
    """microcap 池批次日早于前一交易日 → skipped_stale_pool，不建会话、不入队。

    与主档同款时效门槛（当日或日历上的前一交易日可接受）；显式注入日历钉死
    「不在日历前一位的旧批次仍被拦」——2026-11-10 的前一交易日是 2026-11-09，
    2026-11-06 的池不在窗口内。
    """
    db = tmp_path / "screening.db"
    monkeypatch.setattr(
        "finance_agent.screening.pool_analysis.latest_successful_pool",
        _StaleFakePool(_rows(["002749"]), "2026-11-06"),
    )
    monkeypatch.setattr(
        pool_analysis,
        "read_snapshot_calendar",
        _calendar(["2026-11-09", "2026-11-10"]),
    )
    out = prepare_pool_analysis_batch(
        as_of="2026-11-10",
        enqueue=lambda s, sid: True,
        screening_db_path=db,
        db_path=db,
        pool=POOL_MICROCAP,
    )
    assert out["status"] == "skipped_stale_pool"
    assert out["batch_date"] == "2026-11-06"
    assert out["started"] == 0


# ── 补位封顶（T5 修正：补位以本池自有行数封顶，重跑不双烧） ─────────────────────


def test_microcap_rerun_no_double_burn(monkeypatch, tmp_path):
    """同日重跑不双烧：首跑已派 10 只（录 10 条 microcap queued 行）后重跑，
    窗口段全跳过 + 补位段因 own_count 已满 cap 即止 → selected=0、started=0、
    零新派发（首跑语义下重跑会再补 10 只，双烧）。"""
    db = tmp_path / "screening.db"
    rows = _rows([f"3000{i:02d}" for i in range(1, 15)])  # 14 只，cap=10
    monkeypatch.setattr(
        "finance_agent.screening.pool_analysis.latest_successful_pool", _FakePool(rows)
    )
    for row in rows[:10]:
        record_pool_analysis_run(
            "2026-11-10",
            row["symbol"],
            f"s-{row['symbol']}",
            "queued",
            pool=POOL_MICROCAP,
            db_path=db,
        )
    spy = _EnqueueSpy()
    result = prepare_pool_analysis_batch(
        as_of="2026-11-10",
        max_symbols=10,
        enqueue=spy,
        screening_db_path=db,
        db_path=db,
        pool=POOL_MICROCAP,
    )
    assert result["selected"] == 0
    assert result["started"] == 0
    assert spy.calls == []
    assert result["skipped_existing"] == 10


def test_microcap_overlap_fill_capped_at_cap(monkeypatch, tmp_path):
    """主档重叠 4 只 + cap=10 的不变量：首跑窗口 6 只 + 补位 4 只 = 10；
    重跑后 own_count 已满 → selected=0、started=0、无新会话。"""
    db = tmp_path / "screening.db"
    rows = _rows([f"3000{i:02d}" for i in range(1, 15)])
    monkeypatch.setattr(
        "finance_agent.screening.pool_analysis.latest_successful_pool", _FakePool(rows)
    )
    # 主档当日已分析 rank 2/4/6/8（前 10 名中的 4 只跨池重叠）
    for row in rows[1:8:2]:
        record_pool_analysis_run("2026-11-10", row["symbol"], "s-main", "queued", db_path=db)
    spy = _EnqueueSpy()
    first = prepare_pool_analysis_batch(
        as_of="2026-11-10",
        max_symbols=10,
        enqueue=spy,
        screening_db_path=db,
        db_path=db,
        pool=POOL_MICROCAP,
    )
    # 窗口 6 只（4 只重叠跳过）+ 补位 4 只（rank 11-14）= cap
    assert first["selected"] == 10
    assert first["started"] == 10
    assert first["skipped_existing"] == 4
    expected_window = [rows[i]["symbol"] for i in (0, 2, 4, 6, 8, 9)]
    expected_fill = [rows[i]["symbol"] for i in (10, 11, 12, 13)]
    assert spy.calls == expected_window + expected_fill
    second = prepare_pool_analysis_batch(
        as_of="2026-11-10",
        max_symbols=10,
        enqueue=spy,
        screening_db_path=db,
        db_path=db,
        pool=POOL_MICROCAP,
    )
    assert second["selected"] == 0
    assert second["started"] == 0
    assert len(spy.calls) == 10  # 重跑零新派发（无新会话）


def test_microcap_partial_failure_redispatch_no_fill(monkeypatch, tmp_path):
    """失败行重派、零补位：own_count 已满 cap，补位段即止——恰好重派 4 只
    failed 行，不向 rank 11-14 顺延补位。"""
    db = tmp_path / "screening.db"
    rows = _rows([f"3000{i:02d}" for i in range(1, 15)])
    monkeypatch.setattr(
        "finance_agent.screening.pool_analysis.latest_successful_pool", _FakePool(rows)
    )
    # 当日本池已有 10 行：rank 1/3/5/7（下标 0/2/4/6）失败，其余 queued
    for i, row in enumerate(rows[:10]):
        status = "failed" if i in (0, 2, 4, 6) else "queued"
        record_pool_analysis_run(
            "2026-11-10",
            row["symbol"],
            f"s-{row['symbol']}",
            status,
            pool=POOL_MICROCAP,
            db_path=db,
        )
    spy = _EnqueueSpy()
    result = prepare_pool_analysis_batch(
        as_of="2026-11-10",
        max_symbols=10,
        enqueue=spy,
        screening_db_path=db,
        db_path=db,
        pool=POOL_MICROCAP,
    )
    assert result["selected"] == 4
    assert result["started"] == 4
    assert spy.calls == [rows[i]["symbol"] for i in (0, 2, 4, 6)]
    assert result["skipped_existing"] == 6
