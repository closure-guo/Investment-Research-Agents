"""screening/store.py 单测（add-screening-candidate-llm T2；C4/C5）。

计划 docs/superpowers/plans/2026-10-13-add-screening-candidate-llm.md：

- C4 稳定性计数：``consecutive_days_in_top(symbol, as_of)`` 从 ``as_of`` 起向前沿
  **表的交易日轴**（表里只有交易日行 → 自然日回溯由数据自身决定、非交易日不打断）
  数连续在榜段；``as_of`` 不在榜 → 0；``eligible_for_selection`` = 计数 ≥ ``min_days``
  （默认 3，可配置）；
- C5 落库：``l2_candidates`` / ``triage_decisions`` 两表幂等 upsert（同日重跑覆盖）；
  ``list_*`` 过滤读取。

spec 场景「断档重新计数」：第 1/2 日在榜、第 3 日出榜、第 4/5 日在榜 → 第 5 日计数 = 2。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from finance_agent.screening.candidate_llm import TriageDecision
from finance_agent.screening.ranker import Candidate
from finance_agent.screening.store import (
    consecutive_days_in_top,
    eligible_for_selection,
    init_screening_db,
    list_l2_candidates,
    list_screening_removals,
    list_triage_decisions,
    record_l2_candidates,
    record_screening_removals,
    record_triage_decision,
    record_triage_decisions,
    reset_screening_db,
    rewrite_day_l2_board,
)

# 交易日序列（含跨周末：10-09 周五 → 10-12 周一，10-10/11 周末无行）
_D1, _D2, _D3, _D4, _D5 = ("2026-10-06", "2026-10-07", "2026-10-08", "2026-10-09", "2026-10-12")


def _candidate(symbol: str, rank: int, *, score: float = 1.0) -> Candidate:
    return Candidate(
        symbol=symbol,
        trade_date=_D5,
        composite_score=score,
        factor_contribs=(),
        rank=rank,
    )


def _decision(symbol: str, verdict: str = "keep", reason: str = "无风险", *, degraded=False):
    return TriageDecision(symbol=symbol, verdict=verdict, reason=reason, degraded=degraded)


def _in_top(db_path: str, trade_date: str, symbol: str) -> None:
    """把 symbol 放进某交易日的 L2 榜（rank 固定 1；其余测试不需真实排名语义）。"""
    record_l2_candidates(trade_date, [_candidate(symbol, 1)], db_path=db_path)


# ── DDL 幂等与读取 ──────────────────────────────────────────────────────────


def test_init_screening_db_is_idempotent_and_creates_both_tables(tmp_path) -> None:
    db = tmp_path / "screening.db"
    first = init_screening_db(db)
    second = init_screening_db(db)  # 重跑不报错、不重复建
    assert first == second == db
    conn = sqlite3.connect(db)
    try:
        tables = {
            str(row[0])
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
    finally:
        conn.close()
    assert {"l2_candidates", "triage_decisions"} <= tables


def test_record_l2_candidates_is_idempotent_upsert(tmp_path) -> None:
    db = str(tmp_path / "screening.db")
    assert record_l2_candidates("2026-10-13", [], db_path=db) == 0  # 空批：零写入
    assert (
        record_l2_candidates(
            "2026-10-13",
            [_candidate("600519", 1, score=1.5), _candidate("000001", 2, score=1.2)],
            db_path=db,
        )
        == 2
    )
    # 同日重跑：覆盖为最新分/排名，行数不变
    assert (
        record_l2_candidates(
            "2026-10-13",
            [_candidate("600519", 1, score=1.6), _candidate("000001", 2, score=1.1)],
            db_path=db,
        )
        == 2
    )
    rows = list_l2_candidates("2026-10-13", db_path=db)
    assert len(rows) == 2
    assert [row["rank"] for row in rows] == [1, 2]  # rank 升序
    by_symbol = {row["symbol"]: row for row in rows}
    assert by_symbol["600519"]["composite_score"] == pytest.approx(1.6)
    assert by_symbol["000001"]["composite_score"] == pytest.approx(1.1)
    # 其他日期无记录 → 空列表（不抛错）
    assert list_l2_candidates("2026-10-01", db_path=db) == []


def test_record_l2_candidates_rejects_bad_inputs(tmp_path) -> None:
    db = str(tmp_path / "screening.db")
    with pytest.raises(ValueError, match="规范"):
        record_l2_candidates("2026-10-5", [_candidate("600519", 1)], db_path=db)
    with pytest.raises(ValueError, match="rank"):
        record_l2_candidates("2026-10-13", [_candidate("600519", 0)], db_path=db)
    with pytest.raises(ValueError, match="composite_score"):
        record_l2_candidates(
            "2026-10-13", [_candidate("600519", 1, score=float("nan"))], db_path=db
        )


def test_rewrite_day_l2_board_replaces_day_rows_and_clears_triage(tmp_path) -> None:
    """日批重跑入口（评审修正）：当日榜**原子替换**——旧 L2/triage 行无幸存者。

    「表内行的存在性 ≡ 当日在该榜内」：同日重跑产出更小集合时旧行残留会把已出榜
    标的的连续在榜天数虚增（C4 查询只认 ``l2_candidates`` 的日期轴）。
    """
    db = str(tmp_path / "screening.db")
    _in_top(db, _D4, "PRIOR")  # 前一交易日：替换只作用于当日
    record_l2_candidates(_D5, [_candidate("Y", 1), _candidate("Z", 2)], db_path=db)
    record_triage_decisions(_D5, [_decision("Y"), _decision("Z")], db_path=db)

    assert rewrite_day_l2_board(_D5, [_candidate("Y", 1)], db_path=db) == 1

    assert [row["symbol"] for row in list_l2_candidates(_D5, db_path=db)] == ["Y"]  # Z 出榜无残留
    assert list_triage_decisions(trade_date=_D5, db_path=db) == []  # 旧决策同事务清除
    assert [row["symbol"] for row in list_l2_candidates(_D4, db_path=db)] == ["PRIOR"]

    # 空批：当日榜替换为空（清旧行），而非保留旧榜
    assert rewrite_day_l2_board(_D5, [], db_path=db) == 0
    assert list_l2_candidates(_D5, db_path=db) == []


# ── 连续在榜计数（C4） ──────────────────────────────────────────────────────


def test_consecutive_days_counts_streak_from_as_of(tmp_path) -> None:
    db = str(tmp_path / "screening.db")
    for trade_date in (_D1, _D2, _D3, _D4):
        _in_top(db, trade_date, "Y")
    assert consecutive_days_in_top("Y", _D4, db_path=db) == 4
    assert consecutive_days_in_top("Y", _D2, db_path=db) == 2  # 从 as_of 向前截断
    assert consecutive_days_in_top("Y", _D1, db_path=db) == 1


def test_consecutive_days_resets_after_gap_spec_scenario(tmp_path) -> None:
    """spec 场景：1/2 在榜、3 出榜、4/5 在榜 → 第 5 日计数 = 2（断档重新计数）。"""
    db = str(tmp_path / "screening.db")
    for trade_date in (_D1, _D2, _D4, _D5):
        _in_top(db, trade_date, "Y")
    _in_top(db, _D3, "OTHER")  # 第 3 日 Y 出榜（当日榜单存在但无 Y）
    assert consecutive_days_in_top("Y", _D5, db_path=db) == 2
    assert consecutive_days_in_top("Y", _D4, db_path=db) == 1  # 第 4 日前一段已断
    assert consecutive_days_in_top("Y", _D2, db_path=db) == 2
    assert eligible_for_selection("Y", _D5, db_path=db) is False


def test_consecutive_days_returns_zero_when_absent_on_as_of(tmp_path) -> None:
    db = str(tmp_path / "screening.db")
    _in_top(db, _D1, "Y")
    _in_top(db, _D2, "Y")
    _in_top(db, _D5, "OTHER")  # as_of 当日榜单存在，但 Y 不在
    assert consecutive_days_in_top("Y", _D5, db_path=db) == 0
    assert consecutive_days_in_top("Y", _D3, db_path=db) == 0  # 当日榜单不存在 → 0
    assert consecutive_days_in_top("NEVER", _D5, db_path=db) == 0


def test_consecutive_days_ignores_non_trading_day_gaps(tmp_path) -> None:
    """表内只有交易日行：10-09（周五）→ 10-12（周一）之间无周末行，不打断连续段。"""
    db = str(tmp_path / "screening.db")
    _in_top(db, _D4, "Y")  # 2026-10-09
    _in_top(db, _D5, "Y")  # 2026-10-12（10-10/11 周末无行）
    _in_top(db, _D5, "X")
    assert consecutive_days_in_top("Y", _D5, db_path=db) == 2
    assert consecutive_days_in_top("X", _D5, db_path=db) == 1


# ── 稳定性门槛（C4） ────────────────────────────────────────────────────────


def test_eligible_for_selection_boundaries(tmp_path) -> None:
    db = str(tmp_path / "screening.db")
    for trade_date in (_D1, _D2):
        _in_top(db, trade_date, "Y")
    assert consecutive_days_in_top("Y", _D2, db_path=db) == 2
    assert eligible_for_selection("Y", _D2, db_path=db) is False  # 2 日 < 默认 3 日
    assert eligible_for_selection("Y", _D2, min_days=2, db_path=db) is True  # 可配置

    _in_top(db, _D3, "Y")
    assert eligible_for_selection("Y", _D3, db_path=db) is True  # 3 日达标
    assert eligible_for_selection("Y", _D3, min_days=4, db_path=db) is False

    with pytest.raises(ValueError, match="min_days"):
        eligible_for_selection("Y", _D3, min_days=0, db_path=db)


def test_stability_queries_reject_non_canonical_dates(tmp_path) -> None:
    db = str(tmp_path / "screening.db")
    with pytest.raises(ValueError, match="规范"):
        consecutive_days_in_top("Y", "20261012", db_path=db)
    with pytest.raises(ValueError, match="规范"):
        list_l2_candidates("2026-10-5", db_path=db)


# ── triage 决策落库读取（C5） ───────────────────────────────────────────────


def test_record_triage_decision_upserts_same_day(tmp_path) -> None:
    db = str(tmp_path / "screening.db")
    record_triage_decision("2026-10-13", _decision("600519", "keep"), db_path=db)
    record_triage_decision(
        "2026-10-13",
        _decision("600519", "drop", "收到证监会立案告知书", degraded=True),
        db_path=db,
    )
    rows = list_triage_decisions(trade_date="2026-10-13", db_path=db)
    assert len(rows) == 1  # 同日重跑覆盖，不增行
    assert rows[0]["verdict"] == "drop"
    assert rows[0]["reason"] == "收到证监会立案告知书"
    assert rows[0]["degraded"] == 1


def test_record_triage_decision_rejects_invalid_verdict(tmp_path) -> None:
    db = str(tmp_path / "screening.db")
    with pytest.raises(ValueError, match="verdict"):
        record_triage_decision("2026-10-13", _decision("600519", "maybe"), db_path=db)


def test_list_triage_decisions_filters(tmp_path) -> None:
    db = str(tmp_path / "screening.db")
    record_triage_decision("2026-10-13", _decision("600519", "keep"), db_path=db)
    record_triage_decision("2026-10-13", _decision("000001", "drop", "立案"), db_path=db)
    record_triage_decision("2026-10-12", _decision("600519", "drop", "减持"), db_path=db)

    assert len(list_triage_decisions(db_path=db)) == 3  # 无过滤 = 全量
    by_date = list_triage_decisions(trade_date="2026-10-13", db_path=db)
    assert [row["symbol"] for row in by_date] == ["000001", "600519"]  # 日期+代码升序
    by_symbol = list_triage_decisions(symbol="600519", db_path=db)
    assert [row["trade_date"] for row in by_symbol] == ["2026-10-12", "2026-10-13"]
    both = list_triage_decisions(trade_date="2026-10-13", symbol="000001", db_path=db)
    assert len(both) == 1 and both[0]["verdict"] == "drop"
    # 落库文件确实在注入路径（隔离纪律），非默认库
    assert Path(db).exists()


# ── 批量落库（T1 评审修正：单连接 executemany + 单次 commit） ───────────────


def test_record_triage_decisions_batch_upsert_is_idempotent(tmp_path) -> None:
    """整批一次写入：N 行落库；同日重跑覆盖不增行（与单行写路径同语义）。"""
    db = str(tmp_path / "screening.db")
    first = [_decision("600519", "keep"), _decision("000001", "drop", "立案", degraded=True)]
    assert record_triage_decisions("2026-10-13", first, db_path=db) == 2
    second = [_decision("600519", "drop", "减持"), _decision("000001", "keep")]
    assert record_triage_decisions("2026-10-13", second, db_path=db) == 2

    rows = list_triage_decisions(trade_date="2026-10-13", db_path=db)
    assert len(rows) == 2  # 幂等覆盖不翻倍
    by_symbol = {row["symbol"]: row for row in rows}
    assert by_symbol["600519"]["verdict"] == "drop"
    assert by_symbol["600519"]["reason"] == "减持"
    assert by_symbol["000001"]["verdict"] == "keep"
    assert by_symbol["000001"]["degraded"] == 0  # 覆盖后降级位同步复位


def test_record_triage_decisions_empty_batch_writes_nothing(tmp_path) -> None:
    """空批 → 0 且不建表（连库都不碰）。"""
    db = tmp_path / "screening.db"
    assert record_triage_decisions("2026-10-13", [], db_path=db) == 0
    assert not db.exists()


def test_record_triage_decisions_validates_all_before_write(tmp_path) -> None:
    """任一 verdict 非法 → ValueError，整批不落（先全量校验再写）。"""
    db = str(tmp_path / "screening.db")
    with pytest.raises(ValueError, match="verdict"):
        record_triage_decisions(
            "2026-10-13", [_decision("600519", "keep"), _decision("000001", "maybe")], db_path=db
        )
    assert list_triage_decisions(db_path=db) == []  # 合法行也未被写入
    with pytest.raises(ValueError, match="规范"):
        record_triage_decisions("2026-10-5", [_decision("600519")], db_path=db)


# ── 分析前移除审计（add-screening-pipeline 时效性校验接线） ────────────────


def test_record_screening_removals_upserts_and_lists(tmp_path) -> None:
    """移除审计行：同 (trade_date, symbol, reason) 幂等覆盖；同标的多因多行。"""
    db = tmp_path / "screening.db"
    assert (
        record_screening_removals(
            _D5,
            [
                {"symbol": "000001", "reason": "SUSPENDED", "detail": "停牌"},
                {"symbol": "000001", "reason": "LIMIT_LOCKED", "detail": "一字跌停封板"},
                {"symbol": "600519", "reason": "NEW_EVENT", "detail": "新事件 2026-10-12：公告"},
            ],
            db_path=db,
        )
        == 3
    )

    rows = list_screening_removals(db_path=db)
    assert [(row["symbol"], row["reason"]) for row in rows] == [
        ("000001", "LIMIT_LOCKED"),
        ("000001", "SUSPENDED"),
        ("600519", "NEW_EVENT"),
    ]
    assert rows[1]["detail"] == "停牌" and rows[1]["trade_date"] == _D5
    assert rows[1]["removed_at"]  # 审计时间戳不空

    # 幂等：同日同因重复写入覆盖 detail，不翻倍
    assert (
        record_screening_removals(
            _D5, [{"symbol": "000001", "reason": "SUSPENDED", "detail": "停牌（重检）"}], db_path=db
        )
        == 1
    )
    rows = list_screening_removals(trade_date=_D5, db_path=db)
    assert len(rows) == 3
    assert (
        next(row for row in rows if row["symbol"] == "000001" and row["reason"] == "SUSPENDED")[
            "detail"
        ]
        == "停牌（重检）"
    )
    assert list_screening_removals(trade_date=_D1, db_path=db) == []  # 按日过滤


def test_record_screening_removals_validates_before_write(tmp_path) -> None:
    """坏值 fail loud（不落坏行）：空 symbol / 空 reason / 非规范交易日 / 非 dict。"""
    db = str(tmp_path / "screening.db")
    for bad in (
        [{"symbol": " ", "reason": "SUSPENDED", "detail": "x"}],
        [{"symbol": "000001", "reason": "", "detail": "x"}],
        [{"symbol": "000001", "reason": "SUSPENDED", "detail": 1.5}],
        [{"symbol": "000001", "reason": "SUSPENDED"}],
    ):
        with pytest.raises(ValueError):
            record_screening_removals(_D5, bad, db_path=db)
    with pytest.raises(ValueError, match="规范"):
        record_screening_removals(
            "2026-10-5", [{"symbol": "000001", "reason": "SUSPENDED"}], db_path=db
        )
    assert list_screening_removals(db_path=db) == []


def test_reset_screening_db_clears_removals(tmp_path) -> None:
    """E2E reset 覆盖面：移除审计表一并清空。"""
    db = tmp_path / "screening.db"
    record_screening_removals(
        _D5, [{"symbol": "000001", "reason": "SUSPENDED", "detail": "停牌"}], db_path=db
    )

    removed = reset_screening_db(db_path=db)

    assert removed["screening_removals"] == 1
    assert list_screening_removals(db_path=db) == []


# ── 选择模式列与迁移（add-screening-empty-library-fallback T3） ─────────────


def test_selection_mode_migration_backfills_legacy_rows(tmp_path) -> None:
    """旧库（无 selection_mode 列）→ init 幂等补列，旧行默认 'factor'。"""
    db = tmp_path / "legacy_screening.db"
    conn = sqlite3.connect(db)
    try:
        conn.executescript(
            "CREATE TABLE screening_batches ("
            "trade_date TEXT PRIMARY KEY, status TEXT NOT NULL, error TEXT, "
            "started_at TEXT NOT NULL, finished_at TEXT NOT NULL, "
            "l1_passed INTEGER NOT NULL DEFAULT 0, l2_top INTEGER NOT NULL DEFAULT 0, "
            "l3_keep INTEGER NOT NULL DEFAULT 0, pool_size INTEGER NOT NULL DEFAULT 0);"
        )
        conn.execute(
            "INSERT INTO screening_batches (trade_date, status, started_at, finished_at) "
            "VALUES ('2026-10-01', 'ok', 'x', 'y')"
        )
        conn.commit()
    finally:
        conn.close()

    init_screening_db(db)

    conn = sqlite3.connect(db)
    try:
        row = conn.execute(
            "SELECT selection_mode FROM screening_batches WHERE trade_date = '2026-10-01'"
        ).fetchone()
    finally:
        conn.close()
    assert row == ("factor",)


def test_record_and_read_back_selection_mode(tmp_path) -> None:
    """record_screening_batch 携带 selection_mode；批次行回读三处一致。"""
    from finance_agent.screening.store import (
        latest_screening_batch,
        latest_successful_batch,
        list_batches,
        record_screening_batch,
    )

    db = tmp_path / "screening.db"
    record_screening_batch(
        _D1,
        status="ok",
        started_at="t0",
        finished_at="t1",
        selection_mode="fallback",
        db_path=db,
    )
    record_screening_batch(
        _D2,
        status="ok",
        started_at="t2",
        finished_at="t3",
        db_path=db,  # 缺省 factor
    )

    assert latest_screening_batch(db_path=db)["selection_mode"] == "factor"
    assert latest_successful_batch(db_path=db)["selection_mode"] == "factor"
    rows = list_batches(db_path=db)
    assert {row["trade_date"]: row["selection_mode"] for row in rows} == {
        _D2: "factor",
        _D1: "fallback",
    }


def test_record_screening_batch_rejects_unknown_selection_mode(tmp_path) -> None:
    from finance_agent.screening.store import record_screening_batch

    with pytest.raises(ValueError, match="selection_mode"):
        record_screening_batch(
            _D1,
            status="ok",
            started_at="t0",
            finished_at="t1",
            selection_mode="degraded",
            db_path=tmp_path / "screening.db",
        )


# ── 小票通道批次（pool 列 + 主键重建迁移，add-microcap-lane-guardrails T5） ─────


def test_pool_migration_rebuilds_pk_and_preserves_rows(tmp_path) -> None:
    """旧库（trade_date 单主键、无 selection_mode/frontier_gate）→ init 幂等重建：
    旧行落 pool='main'，主键变 (trade_date, pool)，同 trade_date 可并存 microcap 行。"""
    from finance_agent.screening.store import (
        latest_screening_batch,
        list_batches,
        list_selection_pool,
        record_screening_batch,
    )

    db = tmp_path / "legacy_screening.db"
    conn = sqlite3.connect(db)
    try:
        conn.executescript(
            "CREATE TABLE screening_batches ("
            "trade_date TEXT PRIMARY KEY, status TEXT NOT NULL, error TEXT, "
            "started_at TEXT NOT NULL, finished_at TEXT NOT NULL, "
            "l1_passed INTEGER NOT NULL DEFAULT 0, l2_top INTEGER NOT NULL DEFAULT 0, "
            "l3_keep INTEGER NOT NULL DEFAULT 0, pool_size INTEGER NOT NULL DEFAULT 0);"
            "CREATE TABLE selection_pool ("
            "trade_date TEXT NOT NULL, symbol TEXT NOT NULL, composite_score REAL NOT NULL, "
            "rank INTEGER NOT NULL, consecutive_days INTEGER NOT NULL, "
            "l3_verdict TEXT NOT NULL, l3_reason TEXT NOT NULL, "
            "factor_contribs_json TEXT NOT NULL, PRIMARY KEY (trade_date, symbol));"
            "CREATE INDEX idx_selection_pool_date_rank ON selection_pool (trade_date, rank);"
        )
        conn.execute(
            "INSERT INTO screening_batches (trade_date, status, started_at, finished_at) "
            "VALUES ('2026-10-01', 'ok', 'x', 'y')"
        )
        conn.execute(
            "INSERT INTO selection_pool VALUES "
            "('2026-10-01', '600519', 1.0, 1, 3, 'keep', '无风险事件', '[]')"
        )
        conn.commit()
    finally:
        conn.close()

    init_screening_db(db)
    init_screening_db(db)  # 第二次仍幂等（不重复重建、不丢行）

    conn = sqlite3.connect(db)
    try:
        batch_pk = [
            row[1]
            for row in conn.execute("PRAGMA table_info(screening_batches)").fetchall()
            if row[5] > 0
        ]
        pool_pk = [
            row[1]
            for row in conn.execute("PRAGMA table_info(selection_pool)").fetchall()
            if row[5] > 0
        ]
        pool_indexes = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='selection_pool'"
            ).fetchall()
        }
    finally:
        conn.close()
    assert batch_pk == ["trade_date", "pool"]
    assert pool_pk == ["trade_date", "pool", "symbol"]
    assert "idx_selection_pool_date_rank" in pool_indexes  # 重建后索引恢复

    legacy = latest_screening_batch(db_path=db)
    assert legacy is not None
    assert legacy["trade_date"] == "2026-10-01"
    assert legacy["pool"] == "main"  # 旧行语义 = 主档批次
    assert legacy["selection_mode"] == "factor"
    assert legacy["frontier_gate"] is None
    assert list_selection_pool("2026-10-01", db_path=db)[0]["pool"] == "main"

    # 同 trade_date 可并存 microcap 行（主键重建后不再互相覆盖）
    record_screening_batch(
        "2026-10-01",
        status="ok",
        started_at="x2",
        finished_at="y2",
        pool="microcap",
        frontier_gate="observation_only:missing=delist_pool",
        db_path=db,
    )
    assert len(list_batches(db_path=db)) == 1  # 主档读出不受影响
    micro = list_batches(db_path=db, pool="microcap")
    assert len(micro) == 1 and micro[0]["pool"] == "microcap"


def test_record_and_read_back_pool_and_frontier_gate(tmp_path) -> None:
    """``record_screening_batch`` 携带 pool/frontier_gate；读侧按 pool 过滤。"""
    from finance_agent.screening.store import (
        latest_screening_batch,
        latest_successful_batch,
        list_batches,
        record_screening_batch,
    )

    db = tmp_path / "screening.db"
    record_screening_batch(
        _D1,
        status="ok",
        started_at="t0",
        finished_at="t1",
        pool="microcap",
        frontier_gate="observation_only:missing=regime_data",
        db_path=db,
    )

    # 主档读侧看不到 microcap 行（既有页面/接口口径零变化）
    assert latest_screening_batch(db_path=db) is None
    assert latest_successful_batch(db_path=db) is None
    assert list_batches(db_path=db) == []

    micro = latest_screening_batch(db_path=db, pool="microcap")
    assert micro is not None
    assert micro["pool"] == "microcap"
    assert micro["frontier_gate"] == "observation_only:missing=regime_data"

    record_screening_batch(
        _D1,
        status="ok",
        started_at="t2",
        finished_at="t3",
        db_path=db,
    )
    main = latest_screening_batch(db_path=db)
    assert main is not None and main["pool"] == "main" and main["frontier_gate"] is None


def test_record_screening_batch_rejects_unknown_pool(tmp_path) -> None:
    from finance_agent.screening.store import record_screening_batch

    with pytest.raises(ValueError, match="pool"):
        record_screening_batch(
            _D1,
            status="ok",
            started_at="t0",
            finished_at="t1",
            pool="sidecar",
            db_path=tmp_path / "screening.db",
        )
    with pytest.raises(ValueError, match="frontier_gate"):
        record_screening_batch(
            _D1,
            status="ok",
            started_at="t0",
            finished_at="t1",
            frontier_gate="",
            db_path=tmp_path / "screening.db",
        )


def test_pool_rows_are_namespaced_by_pool(tmp_path) -> None:
    """同日两次批次（main/microcap）互不删对方池行（单事务 DELETE 按 pool 收窄）。"""
    from finance_agent.screening.store import (
        list_selection_pool,
        record_screening_batch,
    )

    db = tmp_path / "screening.db"
    pool_row = {
        "symbol": "600519",
        "composite_score": 1.5,
        "rank": 1,
        "consecutive_days": 3,
        "l3_verdict": "keep",
        "l3_reason": "无风险事件",
        "factor_contribs": [],
    }
    record_screening_batch(
        _D1, status="ok", started_at="t0", finished_at="t1", pool_rows=[pool_row], db_path=db
    )
    record_screening_batch(
        _D1,
        status="ok",
        started_at="t2",
        finished_at="t3",
        pool="microcap",
        pool_rows=[{**pool_row, "symbol": "300750"}],
        db_path=db,
    )

    assert [row["symbol"] for row in list_selection_pool(_D1, db_path=db)] == ["600519"]
    assert [row["symbol"] for row in list_selection_pool(_D1, db_path=db, pool="microcap")] == [
        "300750"
    ]
