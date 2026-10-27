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
    days_in_top_recent,
    eligible_for_selection,
    init_screening_db,
    list_l2_candidates,
    list_screening_removals,
    list_triage_decisions,
    record_l2_candidates,
    record_screening_batch,
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
    # P0-1（update-stability-gate-rolling-window）：资格判定改同模式近 5 日命中 ≥3——
    # 单日断档不再一票否决（4/5 命中 → 有资格）；连续计数本函数保留原语义不变。
    assert eligible_for_selection("Y", _D5, db_path=db) is True


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


# ── 迁移原子性与中断恢复（评审 F3，add-microcap-lane-guardrails） ─────────────


def _old_shape_db(db) -> None:
    """预造旧库：单列主键批次表 + 池表（各 1 行），无 selection_mode/pool 列。"""
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


def _objects(db) -> set[str]:
    conn = sqlite3.connect(db)
    try:
        return {str(row[0]) for row in conn.execute("SELECT name FROM sqlite_master").fetchall()}
    finally:
        conn.close()


def test_init_recovers_interrupted_batch_rebuild(tmp_path) -> None:
    """F3（先红）：中断形态（DROP 已生效、RENAME 未执行）→ 重入 init 恢复旧行。

    真实形态：崩溃后 ``screening_batches__migrate`` 留存旧行、正式表缺失；下一次 init 的
    ``CREATE TABLE IF NOT EXISTS`` 会补出**空壳**正式表——不得因「正式表已存在」跳过恢复，
    否则旧行静默丢失。
    """
    db = tmp_path / "screening.db"
    _old_shape_db(db)
    # 模拟中断后状态：旧行已复制进 __migrate，原表已被 DROP
    conn = sqlite3.connect(db)
    try:
        conn.executescript(
            "CREATE TABLE screening_batches__migrate ("
            "trade_date TEXT NOT NULL, pool TEXT NOT NULL DEFAULT 'main', status TEXT NOT NULL, "
            "error TEXT, started_at TEXT NOT NULL, finished_at TEXT NOT NULL, "
            "l1_passed INTEGER NOT NULL DEFAULT 0, l2_top INTEGER NOT NULL DEFAULT 0, "
            "l3_keep INTEGER NOT NULL DEFAULT 0, pool_size INTEGER NOT NULL DEFAULT 0, "
            "selection_mode TEXT NOT NULL DEFAULT 'factor', frontier_gate TEXT, "
            "PRIMARY KEY (trade_date, pool));"
            "INSERT INTO screening_batches__migrate (trade_date, pool, status, started_at, "
            "finished_at, selection_mode) VALUES ('2026-10-01', 'main', 'ok', 'x', 'y', 'factor');"
            "DROP TABLE screening_batches;"
        )
        conn.commit()
    finally:
        conn.close()

    init_screening_db(db)
    init_screening_db(db)  # 恢复后再跑一次仍幂等

    from finance_agent.screening.store import latest_screening_batch

    row = latest_screening_batch(db_path=db)
    assert row is not None, "中断残留的旧行必须被恢复（MUST NOT 静默丢行）"
    assert row["trade_date"] == "2026-10-01" and row["pool"] == "main"
    assert "screening_batches__migrate" not in _objects(db), "恢复后不得留残留表"


def test_init_recovers_interrupted_pool_rebuild(tmp_path) -> None:
    """F3：池表同款中断形态 → 旧行恢复、索引恢复、无残留。"""
    db = tmp_path / "screening.db"
    _old_shape_db(db)
    conn = sqlite3.connect(db)
    try:
        conn.executescript(
            "CREATE TABLE selection_pool__migrate ("
            "trade_date TEXT NOT NULL, pool TEXT NOT NULL DEFAULT 'main', symbol TEXT NOT NULL, "
            "composite_score REAL NOT NULL, rank INTEGER NOT NULL, consecutive_days INTEGER NOT NULL, "
            "l3_verdict TEXT NOT NULL, l3_reason TEXT NOT NULL, factor_contribs_json TEXT NOT NULL, "
            "PRIMARY KEY (trade_date, pool, symbol));"
            "INSERT INTO selection_pool__migrate VALUES "
            "('2026-10-01', 'main', '600519', 1.0, 1, 3, 'keep', '无风险事件', '[]');"
            "DROP TABLE selection_pool;"
        )
        conn.commit()
    finally:
        conn.close()

    init_screening_db(db)

    from finance_agent.screening.store import list_selection_pool

    rows = list_selection_pool("2026-10-01", db_path=db)
    assert [row["symbol"] for row in rows] == ["600519"]
    assert rows[0]["pool"] == "main"
    objects = _objects(db)
    assert "selection_pool__migrate" not in objects
    assert "idx_selection_pool_date_rank" in objects  # 重建/恢复后索引在位


def test_rebuild_failure_rolls_back_without_data_loss(tmp_path, monkeypatch) -> None:
    """F3：重建中途失败 → 整体回滚（原表与旧行在位、无残留 migrate 表）。"""
    import finance_agent.screening.store as store_module

    db = tmp_path / "screening.db"
    _old_shape_db(db)
    # 注入失败语句（在 DROP 之后）→ 若无显式事务，DROP 已生效即丢表
    monkeypatch.setattr(
        store_module,
        "_BATCH_TABLE_REBUILD_SQL",
        (*store_module._BATCH_TABLE_REBUILD_SQL, "SELECT * FROM table_does_not_exist"),
    )

    with pytest.raises(sqlite3.Error):
        init_screening_db(db)

    objects = _objects(db)
    assert "screening_batches" in objects, "失败必须回滚（原表仍在）"
    assert "screening_batches__migrate" not in objects, "失败必须回滚（无残留）"
    conn = sqlite3.connect(db)
    try:
        rows = conn.execute("SELECT trade_date, status FROM screening_batches").fetchall()
    finally:
        conn.close()
    assert rows == [("2026-10-01", "ok")], "旧行不得丢失"


def test_successful_rebuild_leaves_no_migrate_tables(tmp_path) -> None:
    """成功迁移后不得残留 ``*__migrate`` 表（幂等重入的基线形态）。"""
    db = tmp_path / "screening.db"
    _old_shape_db(db)

    init_screening_db(db)
    init_screening_db(db)

    objects = _objects(db)
    assert "screening_batches__migrate" not in objects
    assert "selection_pool__migrate" not in objects


# ── 执行约束记录字段（评审 F5，add-microcap-lane-guardrails） ─────────────────


def test_exec_constraints_round_trip(tmp_path) -> None:
    """F5（先红）：批次记录含建议执行约束（JSON 记录字段）；三处读侧一致解析为 dict。"""
    from finance_agent.screening.store import (
        latest_screening_batch,
        latest_successful_batch,
        list_batches,
        record_screening_batch,
    )

    db = tmp_path / "screening.db"
    constraints = {"suggested_single_cap": 0.05, "slippage_tier_bump": 1}
    record_screening_batch(
        _D1,
        status="ok",
        started_at="t0",
        finished_at="t1",
        pool="microcap",
        frontier_gate="ready",
        exec_constraints=constraints,
        db_path=db,
    )

    micro = latest_screening_batch(db_path=db, pool="microcap")
    assert micro is not None and micro["exec_constraints"] == constraints
    assert latest_successful_batch(db_path=db, pool="microcap")["exec_constraints"] == constraints
    assert list_batches(db_path=db, pool="microcap")[0]["exec_constraints"] == constraints

    # 主档缺省：无执行约束记录（NULL → None）
    record_screening_batch(_D2, status="ok", started_at="t2", finished_at="t3", db_path=db)
    assert latest_screening_batch(db_path=db)["exec_constraints"] is None


def test_exec_constraints_rejects_bad_values(tmp_path) -> None:
    from finance_agent.screening.store import record_screening_batch

    for bad in ("", [], 0.0):
        with pytest.raises(ValueError, match="exec_constraints"):
            record_screening_batch(
                _D1,
                status="ok",
                started_at="t0",
                finished_at="t1",
                exec_constraints=bad,  # type: ignore[arg-type]
                db_path=tmp_path / "screening.db",
            )


def test_exec_constraints_column_migrated_on_legacy_db(tmp_path) -> None:
    """F5：老库（无该列）→ init 幂等补列；旧行读出 None。"""
    from finance_agent.screening.store import latest_screening_batch

    db = tmp_path / "screening.db"
    _old_shape_db(db)

    init_screening_db(db)

    conn = sqlite3.connect(db)
    try:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(screening_batches)")}
    finally:
        conn.close()
    assert "exec_constraints" in columns
    assert latest_screening_batch(db_path=db)["exec_constraints"] is None


# ── 恢复规则的行数守卫（评审 round 3：防空 migrate 覆盖有行正式表） ────────────

_NEW_SHAPE_BATCHES_DDL = (
    "CREATE TABLE {name} ("
    "trade_date TEXT NOT NULL, pool TEXT NOT NULL DEFAULT 'main', status TEXT NOT NULL, "
    "error TEXT, started_at TEXT NOT NULL, finished_at TEXT NOT NULL, "
    "l1_passed INTEGER NOT NULL DEFAULT 0, l2_top INTEGER NOT NULL DEFAULT 0, "
    "l3_keep INTEGER NOT NULL DEFAULT 0, pool_size INTEGER NOT NULL DEFAULT 0, "
    "selection_mode TEXT NOT NULL DEFAULT 'factor', frontier_gate TEXT, "
    "exec_constraints TEXT, PRIMARY KEY (trade_date, pool))"
)
_NEW_SHAPE_POOL_DDL = (
    "CREATE TABLE {name} ("
    "trade_date TEXT NOT NULL, pool TEXT NOT NULL DEFAULT 'main', symbol TEXT NOT NULL, "
    "composite_score REAL NOT NULL, rank INTEGER NOT NULL, consecutive_days INTEGER NOT NULL, "
    "l3_verdict TEXT NOT NULL, l3_reason TEXT NOT NULL, factor_contribs_json TEXT NOT NULL, "
    "PRIMARY KEY (trade_date, pool, symbol))"
)


def _seed_two_row_old_shape_db(db) -> None:
    """正式表 2 行批次 + 1 行池行（旧形状；复刻评审实测形态）。"""
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
        )
        conn.executemany(
            "INSERT INTO screening_batches (trade_date, status, started_at, finished_at) "
            "VALUES (?, 'ok', 'x', 'y')",
            [("2026-10-01",), ("2026-10-02",)],
        )
        conn.execute(
            "INSERT INTO selection_pool VALUES "
            "('2026-10-01', '600519', 1.0, 1, 3, 'keep', '无风险事件', '[]')"
        )
        conn.commit()
    finally:
        conn.close()


def _create_empty_migrate_tables(db) -> None:
    """中断形态 B：CREATE 之后、INSERT 之前中断 → migrate 为空、正式表持全部旧行。"""
    conn = sqlite3.connect(db)
    try:
        conn.executescript(
            _NEW_SHAPE_BATCHES_DDL.format(name="screening_batches__migrate")
            + ";"
            + _NEW_SHAPE_POOL_DDL.format(name="selection_pool__migrate")
        )
        conn.commit()
    finally:
        conn.close()


def test_recovery_guard_keeps_real_table_when_migrate_empty(tmp_path) -> None:
    """R3（先红）：空 migrate + 有行正式表 → **不得**换入空表（否则静默丢全部历史行）。

    评审实测：正式表 2 行 + 空 migrate → 旧规则恢复后 0 行。修法 = 行数守卫
    （仅当正式表缺失或 ``rows(migrate) >= rows(real)`` 才换入）；保留正式表后，
    同一次 init 的列补齐 + 事务化主键重建继续完成迁移（幂等）。
    """
    from finance_agent.screening.store import latest_screening_batch, list_selection_pool

    db = tmp_path / "screening.db"
    _seed_two_row_old_shape_db(db)
    _create_empty_migrate_tables(db)

    init_screening_db(db)
    init_screening_db(db)  # 幂等重入

    objects = _objects(db)
    assert "screening_batches__migrate" not in objects, "空残留须被清理"
    assert "selection_pool__migrate" not in objects

    conn = sqlite3.connect(db)
    try:
        batch_rows = conn.execute(
            "SELECT trade_date, pool FROM screening_batches ORDER BY trade_date"
        ).fetchall()
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
    finally:
        conn.close()
    assert batch_rows == [("2026-10-01", "main"), ("2026-10-02", "main")], "历史行不得丢失"
    assert batch_pk == ["trade_date", "pool"], "保留正式表后仍须完成主键重建"
    assert pool_pk == ["trade_date", "pool", "symbol"]
    assert latest_screening_batch(db_path=db)["trade_date"] == "2026-10-02"
    assert [r["symbol"] for r in list_selection_pool("2026-10-01", db_path=db)] == ["600519"]


def test_recovery_guard_keeps_real_table_when_migrate_has_fewer_rows(tmp_path) -> None:
    """R3：migrate 行数 < 正式表（部分复制等异常态）→ 同样保留正式表、清残留。"""
    db = tmp_path / "screening.db"
    _seed_two_row_old_shape_db(db)
    conn = sqlite3.connect(db)
    try:
        conn.executescript(
            _NEW_SHAPE_BATCHES_DDL.format(name="screening_batches__migrate")
            + ";"
            + _NEW_SHAPE_POOL_DDL.format(name="selection_pool__migrate")
        )
        conn.execute(
            "INSERT INTO screening_batches__migrate (trade_date, pool, status, started_at, "
            "finished_at) VALUES ('2026-10-01', 'main', 'ok', 'x', 'y')"  # 只 1 行 < 正式 2 行
        )
        conn.commit()
    finally:
        conn.close()

    init_screening_db(db)

    conn = sqlite3.connect(db)
    try:
        dates = [
            str(row[0])
            for row in conn.execute(
                "SELECT trade_date FROM screening_batches ORDER BY trade_date"
            ).fetchall()
        ]
    finally:
        conn.close()
    assert dates == ["2026-10-01", "2026-10-02"]
    assert "screening_batches__migrate" not in _objects(db)


# ── L2 榜与 triage 决策的通道隔离（评审 C1：小票通道不得覆盖主档当日榜/决策） ──


def test_l2_and_triage_are_pool_namespaced(tmp_path) -> None:
    """C1（先红）：同 trade_date 两档写入互不覆盖（主档榜/决策逐行不变）。"""
    from finance_agent.screening.store import (
        list_l2_candidates,
        list_triage_decisions,
        record_triage_decisions,
        rewrite_day_l2_board,
    )

    db = tmp_path / "screening.db"
    main_board = [
        Candidate(symbol="600519", trade_date=_D1, composite_score=1.5, rank=1, factor_contribs=())
    ]
    micro_board = [
        Candidate(symbol="300750", trade_date=_D1, composite_score=0.9, rank=1, factor_contribs=())
    ]
    rewrite_day_l2_board(_D1, main_board, db_path=db)
    record_triage_decisions(
        _D1, [TriageDecision(symbol="600519", verdict="keep", reason="无风险事件")], db_path=db
    )

    # 小票通道（同 trade_date）：原子替换 + 决策落库
    rewrite_day_l2_board(_D1, micro_board, db_path=db, pool="microcap")
    record_triage_decisions(
        _D1,
        [TriageDecision(symbol="300750", verdict="drop", reason="流动性偏弱")],
        db_path=db,
        pool="microcap",
    )

    main_l2 = list_l2_candidates(_D1, db_path=db)
    assert [(row["symbol"], row["pool"]) for row in main_l2] == [("600519", "main")]
    micro_l2 = list_l2_candidates(_D1, db_path=db, pool="microcap")
    assert [(row["symbol"], row["pool"]) for row in micro_l2] == [("300750", "microcap")]

    main_triage = list_triage_decisions(trade_date=_D1, db_path=db)
    assert [(row["symbol"], row["verdict"], row["pool"]) for row in main_triage] == [
        ("600519", "keep", "main")
    ]
    micro_triage = list_triage_decisions(trade_date=_D1, db_path=db, pool="microcap")
    assert [(row["symbol"], row["verdict"]) for row in micro_triage] == [("300750", "drop")]


def test_consecutive_days_in_top_is_pool_scoped(tmp_path) -> None:
    """C1：稳定性计数按档隔离——通道在榜不得虚增/打断主档连续天数。"""
    from finance_agent.screening.store import (
        consecutive_days_in_top,
        eligible_for_selection,
        rewrite_day_l2_board,
    )

    db = tmp_path / "screening.db"
    for day in (_D1, _D2, _D3):
        rewrite_day_l2_board(
            day,
            [
                Candidate(
                    symbol="600519", trade_date=day, composite_score=1.0, rank=1, factor_contribs=()
                )
            ],
            db_path=db,
        )
    # 通道同日榜只含另一标的：不得影响主档计数
    for day in (_D1, _D2, _D3):
        rewrite_day_l2_board(
            day,
            [
                Candidate(
                    symbol="300750", trade_date=day, composite_score=1.0, rank=1, factor_contribs=()
                )
            ],
            db_path=db,
            pool="microcap",
        )

    assert consecutive_days_in_top("600519", _D3, db_path=db) == 3
    assert eligible_for_selection("600519", _D3, db_path=db) is True
    assert consecutive_days_in_top("600519", _D3, db_path=db, pool="microcap") == 0
    assert consecutive_days_in_top("300750", _D3, db_path=db) == 0
    assert consecutive_days_in_top("300750", _D3, db_path=db, pool="microcap") == 3


def test_l2_triage_legacy_migration_adds_pool_pk(tmp_path) -> None:
    """C1：老库两表（单列主键）→ init 重建为 (trade_date, pool, symbol)，旧行落 'main'。"""
    from finance_agent.screening.store import list_l2_candidates, list_triage_decisions

    db = tmp_path / "screening.db"
    conn = sqlite3.connect(db)
    try:
        conn.executescript(
            "CREATE TABLE l2_candidates ("
            "trade_date TEXT NOT NULL, symbol TEXT NOT NULL, composite_score REAL NOT NULL, "
            "rank INTEGER NOT NULL, PRIMARY KEY (trade_date, symbol));"
            "CREATE INDEX idx_l2_candidates_date_rank ON l2_candidates (trade_date, rank);"
            "CREATE TABLE triage_decisions ("
            "trade_date TEXT NOT NULL, symbol TEXT NOT NULL, verdict TEXT NOT NULL, "
            "reason TEXT NOT NULL, degraded INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL, "
            "PRIMARY KEY (trade_date, symbol));"
            "INSERT INTO l2_candidates VALUES ('2026-10-01', '600519', 1.5, 1);"
            "INSERT INTO triage_decisions VALUES "
            "('2026-10-01', '600519', 'keep', '无风险事件', 0, 't0');"
        )
        conn.commit()
    finally:
        conn.close()

    init_screening_db(db)
    init_screening_db(db)  # 幂等

    objects = _objects(db)
    assert "l2_candidates__migrate" not in objects
    assert "triage_decisions__migrate" not in objects
    conn = sqlite3.connect(db)
    try:
        l2_pk = [
            row[1]
            for row in conn.execute("PRAGMA table_info(l2_candidates)").fetchall()
            if row[5] > 0
        ]
        triage_pk = [
            row[1]
            for row in conn.execute("PRAGMA table_info(triage_decisions)").fetchall()
            if row[5] > 0
        ]
    finally:
        conn.close()
    assert l2_pk == ["trade_date", "pool", "symbol"]
    assert triage_pk == ["trade_date", "pool", "symbol"]
    assert list_l2_candidates("2026-10-01", db_path=db)[0]["symbol"] == "600519"
    assert list_l2_candidates("2026-10-01", db_path=db)[0]["pool"] == "main"
    assert list_triage_decisions(trade_date="2026-10-01", db_path=db)[0]["verdict"] == "keep"


# ── 稳定性门槛滚动窗口（P0-1 / update-stability-gate-rolling-window） ─────────
#
# 换挡（factor↔fallback）会把所有人的连续在榜计数清零、池子清空 2–3 天
# （docs/audit/2026-10-23-screening-funnel-review.md P0-1，10-20 池 1 只、10-23 池 96 只）。
# 资格判定改为「**同模式**近 5 个交易日命中 ≥ min_days 次」：其他口径的日子不进轴、
# 不打断；模式内单日跌出榜不再一票否决。``consecutive_days_in_top`` 原语义保留
# （仍供精选池 ``consecutive_days`` 信息列使用）。


def _seed_mode(db_path: str, trade_date: str, mode: str, *, pool: str = "main") -> None:
    record_screening_batch(
        trade_date,
        status="ok",
        started_at=f"{trade_date}T15:40:00",
        finished_at=f"{trade_date}T16:09:00",
        l2_top=1,
        pool_rows=(),
        selection_mode=mode,
        pool=pool,
        db_path=db_path,
    )


def test_days_in_top_recent_counts_within_same_mode_window(tmp_path) -> None:
    """Y 在 factor 日 1/2/4/5 在榜、fallback 日 3 不在 → 同模式近 5 日命中 4 → 有资格。

    旧连续口径此场景计数 = 2（第 3 日断档）→ 无资格；这正是换挡误伤的形态。
    """
    db = str(tmp_path / "screening.db")
    _seed_mode(db, _D1, "factor")
    _seed_mode(db, _D2, "factor")
    _seed_mode(db, _D3, "fallback")
    _seed_mode(db, _D4, "factor")
    _seed_mode(db, _D5, "factor")
    for trade_date in (_D1, _D2, _D4, _D5):
        _in_top(db, trade_date, "Y")
    _in_top(db, _D3, "OTHER")  # fallback 榜：Y 不在
    assert days_in_top_recent("Y", _D5, db_path=db) == 4
    assert eligible_for_selection("Y", _D5, db_path=db) is True


def test_mode_switch_does_not_reset_streak(tmp_path) -> None:
    """factor(1,2) → fallback(3,4) → factor(5)：第 5 日同模式轴命中 3 → 有资格。

    换挡周不在榜的日子不进轴也不打断——11-02 因子模式恢复时老因子票不再清零重攒。
    """
    db = str(tmp_path / "screening.db")
    _seed_mode(db, _D1, "factor")
    _seed_mode(db, _D2, "factor")
    _seed_mode(db, _D3, "fallback")
    _seed_mode(db, _D4, "fallback")
    _seed_mode(db, _D5, "factor")
    for trade_date in (_D1, _D2):
        _in_top(db, trade_date, "Y")
    _in_top(db, _D3, "OTHER")
    _in_top(db, _D4, "OTHER")
    _in_top(db, _D5, "Y")
    assert days_in_top_recent("Y", _D5, db_path=db) == 3
    assert eligible_for_selection("Y", _D5, db_path=db) is True
    assert consecutive_days_in_top("Y", _D5, db_path=db) == 1  # 信息列仍如实记「连续 1 日」


def test_single_day_gap_within_mode_tolerated(tmp_path) -> None:
    """全 factor：1/2/4/5 在榜、3 出榜 → 4/5 命中 → 有资格（单日跌出不否决）。"""
    db = str(tmp_path / "screening.db")
    for trade_date in (_D1, _D2, _D3, _D4, _D5):
        _seed_mode(db, trade_date, "factor")
    for trade_date in (_D1, _D2, _D4, _D5):
        _in_top(db, trade_date, "Y")
    _in_top(db, _D3, "OTHER")
    assert days_in_top_recent("Y", _D5, db_path=db) == 4
    assert eligible_for_selection("Y", _D5, db_path=db) is True


def test_below_threshold_still_ineligible(tmp_path) -> None:
    db = str(tmp_path / "screening.db")
    for trade_date in (_D1, _D2, _D3, _D4, _D5):
        _seed_mode(db, trade_date, "factor")
    for trade_date in (_D1, _D5):
        _in_top(db, trade_date, "Y")
    _in_top(db, _D2, "OTHER")
    _in_top(db, _D3, "OTHER")
    _in_top(db, _D4, "OTHER")
    assert days_in_top_recent("Y", _D5, db_path=db) == 2
    assert eligible_for_selection("Y", _D5, db_path=db) is False
    assert eligible_for_selection("Y", _D5, min_days=2, db_path=db) is True  # 可配置保留


def test_window_rolls_forward(tmp_path) -> None:
    """窗口滚动：命中集中在早期、近 5 日只够 2 次 → 无资格（防老资格躺赢）。"""
    db = str(tmp_path / "screening.db")
    for trade_date in (_D1, _D2, _D3, _D4, _D5):
        _seed_mode(db, trade_date, "factor")
    for trade_date in (_D1, _D2, _D4):
        _in_top(db, trade_date, "Y")
    _in_top(db, _D3, "OTHER")
    _in_top(db, _D5, "Y")
    # 近 5 日（= 全部 5 日）命中 4 → 有资格；再造第 6 日把窗口推走后旧命中出局
    _D6 = "2026-10-13"
    _seed_mode(db, _D6, "factor")
    _in_top(db, _D6, "Y")
    _in_top(db, _D6, "OTHER2")
    # 窗口 = D2..D6（D1 已滚出）：命中 D2/D4/D5/D6 = 4（全史命中 5 > 窗口，旧命中出局）
    assert days_in_top_recent("Y", _D6, db_path=db) == 4
    # 窗口收紧到 2 = D5..D6：命中 2（更早的命中一律不可见）
    assert days_in_top_recent("Y", _D6, window=2, db_path=db) == 2
    assert eligible_for_selection("Y", _D6, db_path=db) is True


def test_days_in_top_recent_absent_on_as_of_returns_zero(tmp_path) -> None:
    db = str(tmp_path / "screening.db")
    _seed_mode(db, _D1, "factor")
    _seed_mode(db, _D5, "factor")
    _in_top(db, _D1, "Y")
    _in_top(db, _D5, "OTHER")  # as_of 榜单存在但 Y 不在
    assert days_in_top_recent("Y", _D5, db_path=db) == 0
    assert eligible_for_selection("Y", _D5, db_path=db) is False


def test_missing_batch_mode_falls_back_to_unfiltered_axis(tmp_path) -> None:
    """批次行缺 selection_mode（如纯 l2 造数的旧库）：退回全轴滚动，不猜模式。"""
    db = str(tmp_path / "screening.db")
    for trade_date in (_D1, _D2, _D4, _D5):
        _in_top(db, trade_date, "Y")
    _in_top(db, _D3, "OTHER")
    assert days_in_top_recent("Y", _D5, db_path=db) == 4
    assert eligible_for_selection("Y", _D5, db_path=db) is True


def test_explicit_mode_uses_same_mode_axis_when_as_of_row_absent(tmp_path) -> None:
    """日批形态（fix-stability-gate-mode-axis）：资格判定先于当日批次行落库，当日批次行
    必然不存在。显式传当日模式 → 轴只含同模式交易日，fallback 日不稀释。

    生产 10-27 形态：Y 因子口径命中 D1/D2/D6 = 3 → 入池；旧行为（当日无批次行 → 全轴
    混合 [D6..D2]）把 D1 命中推出窗外、只数出 2 → 挡在池外。缺省路径的降级语义保留
    （spec「无模式带外调用降级保留」）。
    """
    db = str(tmp_path / "screening.db")
    _D6 = "2026-10-13"
    _seed_mode(db, _D1, "factor")
    _seed_mode(db, _D2, "factor")
    _seed_mode(db, _D3, "fallback")
    _seed_mode(db, _D4, "fallback")
    _seed_mode(db, _D5, "factor")
    _in_top(db, _D1, "Y")
    _in_top(db, _D2, "Y")
    _in_top(db, _D3, "OTHER")
    _in_top(db, _D4, "OTHER")
    _in_top(db, _D5, "OTHER")  # 模式内单日跌出
    _in_top(db, _D6, "Y")  # as_of 在榜；**不落 D6 批次行**（= 生产判定时点）
    assert days_in_top_recent("Y", _D6, mode="factor", db_path=db) == 3
    assert eligible_for_selection("Y", _D6, mode="factor", db_path=db) is True
    # 对照：缺省路径（不传 mode）仍是「读当日批次行 → 缺失退全轴」，带外兼容不变
    assert days_in_top_recent("Y", _D6, db_path=db) == 2
    assert eligible_for_selection("Y", _D6, db_path=db) is False


def test_explicit_mode_rejects_invalid_values(tmp_path) -> None:
    """非法 mode 值 fail loud（与 ``pool`` 参数校验同风格，防静默 misuse）。"""
    db = str(tmp_path / "screening.db")
    _seed_mode(db, _D1, "factor")
    _in_top(db, _D1, "Y")
    with pytest.raises(ValueError, match="非法 selection_mode"):
        days_in_top_recent("Y", _D1, mode="bogus", db_path=db)
    with pytest.raises(ValueError, match="非法 selection_mode"):
        eligible_for_selection("Y", _D1, mode="bogus", db_path=db)
