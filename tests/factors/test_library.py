"""factors/library.py 单测（add-factor-library-and-monitor Task 2）。

覆盖计划关键设计决策 E7（结构层查重：规范化后子树同构，retired 不参与）与
E8（分桶保种：桶内 active 按 pool_increment 降序 top-k，并列取 created_at 早者，
其他桶不动），以及三表 DDL 幂等、状态机（合法/非法迁移 → FactorStateError）、
``record_ic`` 幂等 upsert、事件审计。

数据库全部落在 ``tmp_path``（零网络、零共享状态）。
"""

from __future__ import annotations

import sqlite3

import pytest

from finance_agent.factors.dsl import parse, serialize
from finance_agent.factors.library import (
    FACTOR_STATES,
    FactorStateError,
    _connect,
    add_factor,
    apply_bucket_quota,
    find_structural_duplicate,
    init_library,
    list_active_factors,
    list_events,
    list_factors,
    list_ic_series,
    record_ic,
    set_orientation,
    set_pool_increment,
    set_status,
)

# Global Constraints 逐字钉死的类别（propose.CATEGORIES 同源词表）
_CATEGORY = "动量"
_OTHER_CATEGORY = "反转"


def _db(tmp_path) -> str:
    return str(tmp_path / "factor_library.db")


def _seed(tmp_path, expression: str, *, category: str = _CATEGORY, status: str = "candidate"):
    """建库并插入一条因子，返回 (db_path, factor_id)。"""
    db = _db(tmp_path)
    ast_json = serialize(parse(expression))
    return db, add_factor(
        expression=expression,
        hypothesis="测试用假设文本，描述该因子与未来收益的关系。",
        category=category,
        status=status,
        ast_json=ast_json,
        db_path=db,
    )


# ── schema / 连接纪律 ───────────────────────────────────────────────────────


def test_init_library_creates_tables_and_is_idempotent(tmp_path):
    """init 幂等建 3 表 + 索引；列名逐字一致；连接为 WAL + busy_timeout。"""
    db = tmp_path / "factor_library.db"
    assert init_library(db) == db
    assert init_library(db) == db  # 二次调用不报错（CREATE IF NOT EXISTS）

    conn = _connect(db)
    try:
        tables = {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }
        assert {"factors", "factor_ic", "factor_events"} <= tables

        factor_cols = {row[1] for row in conn.execute("PRAGMA table_info(factors)").fetchall()}
        assert factor_cols == {
            "id",
            "expression",
            "hypothesis",
            "category",
            "status",
            "ast_json",
            "source",
            "alignment_score",
            "orientation",
            "pool_increment",
            "created_at",
            "updated_at",
        }
        ic_cols = {row[1] for row in conn.execute("PRAGMA table_info(factor_ic)").fetchall()}
        assert ic_cols == {"factor_id", "trade_date", "rank_ic"}
        event_cols = {row[1] for row in conn.execute("PRAGMA table_info(factor_events)").fetchall()}
        assert event_cols == {
            "id",
            "factor_id",
            "event",
            "from_status",
            "to_status",
            "detail",
            "created_at",
        }

        indexes = {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='index'").fetchall()
        }
        assert {
            "idx_factors_status",
            "idx_factors_category_status",
            "idx_factor_ic_factor_date",
            "idx_factor_events_factor",
        } <= indexes

        assert str(conn.execute("PRAGMA journal_mode").fetchone()[0]).lower() == "wal"
        assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == 15000
    finally:
        conn.close()


def test_default_db_path_reads_env_at_call_time(tmp_path, monkeypatch):
    """库路径调用期读 FACTOR_LIBRARY_DB_PATH（测试/运维可覆盖）。"""
    target = tmp_path / "custom" / "factor_library.db"
    monkeypatch.setenv("FACTOR_LIBRARY_DB_PATH", str(target))
    assert init_library() == target
    assert target.exists()


def test_primary_keys_enforce_uniqueness(tmp_path):
    """factor_ic 主键 (factor_id, trade_date) 生效；(trade_date, code) 类重复写入靠 upsert。"""
    db, factor_id = _seed(tmp_path, "ts_mean(close,10)")
    record_ic(factor_id, "2026-10-12", 0.02, db_path=db)
    # 同主键直插（绕过 upsert）→ IntegrityError，证明主键存在
    conn = _connect(db)
    try:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO factor_ic (factor_id, trade_date, rank_ic) VALUES (?, ?, ?)",
                (factor_id, "2026-10-12", 0.99),
            )
    finally:
        conn.close()


# ── 状态机（Global Constraints 逐字） ──────────────────────────────────────


def test_states_vocabulary_is_verbatim():
    """状态枚举逐字：candidate/active/degraded/retired。"""
    assert tuple(FACTOR_STATES) == ("candidate", "active", "degraded", "retired")


def test_add_factor_accepts_candidate_and_active_only(tmp_path):
    """初始状态仅 candidate / active；degraded/retired/未知 → FactorStateError。"""
    db = _db(tmp_path)
    candidate = add_factor(
        expression="ts_mean(close,10)",
        hypothesis="假设",
        category=_CATEGORY,
        db_path=db,
    )
    active = add_factor(
        expression="ts_mean(volume,10)",
        hypothesis="假设",
        category=_CATEGORY,
        status="active",
        db_path=db,
    )
    assert isinstance(candidate, int) and isinstance(active, int)
    assert listed_status(db, candidate) == "candidate"
    assert listed_status(db, active) == "active"

    for illegal in ("degraded", "retired", "bogus"):
        with pytest.raises(FactorStateError, match="初始状态"):
            add_factor(
                expression="ts_mean(close,10)",
                hypothesis="假设",
                category=_CATEGORY,
                status=illegal,
                db_path=db,
            )


def test_add_factor_row_content_and_created_event(tmp_path):
    """add_factor 落库字段齐全（source/alignment_score/ast_json）+ created 事件。"""
    db = _db(tmp_path)
    factor_id = add_factor(
        expression="div(close,ts_mean(close,5))",
        hypothesis="短均线偏离度，偏离越高次日回归概率越大。",
        category=_CATEGORY,
        ast_json="div(close,ts_mean(close,5))",
        source="proposal",
        alignment_score=0.8,
        db_path=db,
    )
    rows = list_factors(db_path=db)
    assert len(rows) == 1
    row = rows[0]
    assert row["id"] == factor_id
    assert row["expression"] == "div(close,ts_mean(close,5))"
    assert row["category"] == _CATEGORY
    assert row["status"] == "candidate"
    assert row["ast_json"] == "div(close,ts_mean(close,5))"
    assert row["source"] == "proposal"
    assert row["alignment_score"] == pytest.approx(0.8)
    assert row["pool_increment"] is None
    assert row["created_at"] and row["updated_at"]

    events = list_events(factor_id, db_path=db)
    assert len(events) == 1
    assert events[0]["event"] == "created"
    assert events[0]["from_status"] is None
    assert events[0]["to_status"] == "candidate"
    assert events[0]["detail"] is None


def test_set_status_legal_transitions_and_events(tmp_path):
    """合法迁移全通过：candidate→active、active→degraded、degraded→active、
    active→retired、degraded→retired；每次迁移落 status_change 事件（含 detail）。"""
    db, factor_id = _seed(tmp_path, "ts_mean(close,10)")
    set_status(factor_id, "active", detail={"reason": "ADMITTED"}, db_path=db)
    assert listed_status(db, factor_id) == "active"
    set_status(factor_id, "degraded", detail={"rolling_icir": 0.05}, db_path=db)
    assert listed_status(db, factor_id) == "degraded"
    set_status(factor_id, "active", detail={"reason": "REVIEW_RESTORED"}, db_path=db)
    set_status(factor_id, "retired", detail={"reason": "MANUAL"}, db_path=db)
    assert listed_status(db, factor_id) == "retired"

    transitions = [
        (event["from_status"], event["to_status"]) for event in list_events(factor_id, db_path=db)
    ]
    assert transitions == [
        (None, "candidate"),
        ("candidate", "active"),
        ("active", "degraded"),
        ("degraded", "active"),
        ("active", "retired"),
    ]
    degraded_event = list_events(factor_id, db_path=db)[2]
    assert degraded_event["event"] == "status_change"
    assert degraded_event["detail"] == {"rolling_icir": 0.05}


def test_degraded_can_be_retired_then_no_more_transitions(tmp_path):
    """degraded→retired 合法；retired→∅（任何目标状态都非法）。"""
    db, factor_id = _seed(tmp_path, "ts_mean(close,10)")
    set_status(factor_id, "active", db_path=db)
    set_status(factor_id, "degraded", db_path=db)
    set_status(factor_id, "retired", db_path=db)
    for target in ("active", "degraded", "candidate", "retired"):
        with pytest.raises(FactorStateError, match="非法状态迁移"):
            set_status(factor_id, target, db_path=db)


def test_set_status_illegal_transitions_raise(tmp_path):
    """非法迁移抛 FactorStateError 且不落任何状态/事件变更。"""
    db, factor_id = _seed(tmp_path, "ts_mean(close,10)")  # candidate
    with pytest.raises(FactorStateError, match="candidate"):
        set_status(factor_id, "degraded", db_path=db)
    with pytest.raises(FactorStateError, match="candidate"):
        set_status(factor_id, "retired", db_path=db)
    set_status(factor_id, "active", db_path=db)
    with pytest.raises(FactorStateError, match="active"):
        set_status(factor_id, "candidate", db_path=db)
    with pytest.raises(FactorStateError, match="非法状态迁移"):
        set_status(factor_id, "bogus", db_path=db)
    assert listed_status(db, factor_id) == "active"
    assert len(list_events(factor_id, db_path=db)) == 2  # created + candidate→active


def test_set_status_unknown_factor_raises(tmp_path):
    """目标因子不存在 → FactorStateError（fail loud，不静默建行）。"""
    db = _db(tmp_path)
    with pytest.raises(FactorStateError, match="不存在"):
        set_status(999, "active", db_path=db)


def listed_status(db: str, factor_id: int) -> str:
    """按 id 读回状态（测试辅助；不假设行序）。"""
    for row in list_factors(db_path=db):
        if row["id"] == factor_id:
            return str(row["status"])
    raise AssertionError(f"因子不存在: {factor_id}")


# ── 结构层查重（E7） ───────────────────────────────────────────────────────


def test_find_structural_duplicate_hits_window_variant(tmp_path):
    """库内 ts_mean(close,10) → 提交 ts_mean(close,11) 命中该 id（数字只按「有」匹配）。"""
    db, factor_id = _seed(tmp_path, "ts_mean(close,10)")
    assert find_structural_duplicate("ts_mean(close,11)", db_path=db) == factor_id


def test_find_structural_duplicate_misses_different_field(tmp_path):
    """ts_mean(volume,10) 与库内 ts_mean(close,10) 不同构 → None。"""
    db, _ = _seed(tmp_path, "ts_mean(close,10)")
    assert find_structural_duplicate("ts_mean(volume,10)", db_path=db) is None


def test_find_structural_duplicate_uses_normalization(tmp_path):
    """交换律等价视为同构：库内 add(close,volume) 命中提交的 add(volume,close)。"""
    db, factor_id = _seed(tmp_path, "add(close,volume)")
    assert find_structural_duplicate("add(volume,close)", db_path=db) == factor_id


def test_find_structural_duplicate_excludes_retired(tmp_path):
    """retired 不参与查重（允许重提）。"""
    db, retired_id = _seed(tmp_path, "ts_mean(close,10)")
    set_status(retired_id, "active", db_path=db)
    set_status(retired_id, "retired", db_path=db)
    assert find_structural_duplicate("ts_mean(close,11)", db_path=db) is None


def test_find_structural_duplicate_participating_states(tmp_path):
    """candidate / active / degraded 三态均参与查重（各自命中对应 id）。"""
    db = _db(tmp_path)
    candidate = add_factor(
        expression="ts_mean(amount,20)",
        hypothesis="假设",
        category=_OTHER_CATEGORY,
        db_path=db,
    )
    assert find_structural_duplicate("ts_mean(amount,99)", db_path=db) == candidate

    active = add_factor(
        expression="ts_mean(volume,20)",
        hypothesis="假设",
        category=_OTHER_CATEGORY,
        status="active",
        db_path=db,
    )
    assert find_structural_duplicate("ts_mean(volume,99)", db_path=db) == active

    degraded = add_factor(
        expression="ts_mean(high,20)",
        hypothesis="假设",
        category=_OTHER_CATEGORY,
        db_path=db,
    )
    set_status(degraded, "active", db_path=db)
    set_status(degraded, "degraded", db_path=db)
    assert find_structural_duplicate("ts_mean(high,99)", db_path=db) == degraded


def test_find_structural_duplicate_falls_back_to_expression_column(tmp_path):
    """库内行未存 ast_json（None）时回退用 expression 解析比较。"""
    db = _db(tmp_path)
    factor_id = add_factor(
        expression="ts_mean(close,10)",
        hypothesis="假设",
        category=_CATEGORY,
        db_path=db,
    )
    assert find_structural_duplicate("ts_mean(close,999)", db_path=db) == factor_id


# ── factor_ic：幂等 upsert 与序列读取 ─────────────────────────────────────


def test_record_ic_idempotent_and_series_order(tmp_path):
    """同 (factor, date) 覆盖写（幂等）；list_ic_series 升序返回，limit 取最近 N 条。"""
    db, factor_id = _seed(tmp_path, "ts_mean(close,10)")
    for day, value in [
        ("2026-10-08", 0.01),
        ("2026-10-09", -0.02),
        ("2026-10-10", 0.03),
        ("2026-10-11", 0.04),
        ("2026-10-12", 0.05),
    ]:
        record_ic(factor_id, day, value, db_path=db)
    record_ic(factor_id, "2026-10-12", 0.06, db_path=db)  # 同日重跑：覆盖不新增

    series = list_ic_series(factor_id, db_path=db)
    assert series == [
        ("2026-10-08", 0.01),
        ("2026-10-09", -0.02),
        ("2026-10-10", 0.03),
        ("2026-10-11", 0.04),
        ("2026-10-12", 0.06),
    ]
    assert list_ic_series(factor_id, limit=3, db_path=db) == [
        ("2026-10-10", 0.03),
        ("2026-10-11", 0.04),
        ("2026-10-12", 0.06),
    ]
    assert list_ic_series(factor_id, db_path=db).count(("2026-10-12", 0.06)) == 1
    assert list_ic_series(999, db_path=db) == []


def test_record_ic_validates_date_value_and_factor(tmp_path):
    """日期须规范 YYYY-MM-DD；rank_ic 须有限；因子须存在——否则异常。"""
    db, factor_id = _seed(tmp_path, "ts_mean(close,10)")
    for bad_date in ("2026-10-5", "20261012", "2026/10/12", ""):
        with pytest.raises(ValueError, match="交易日"):
            record_ic(factor_id, bad_date, 0.01, db_path=db)
    for bad_value in (float("nan"), float("inf"), float("-inf")):
        with pytest.raises(ValueError, match="rank_ic"):
            record_ic(factor_id, "2026-10-12", bad_value, db_path=db)
    with pytest.raises(FactorStateError, match="不存在"):
        record_ic(999, "2026-10-12", 0.01, db_path=db)


# ── 分桶保种（E8） ─────────────────────────────────────────────────────────


def test_apply_bucket_quota_retires_lowest_increment_only_within_bucket(tmp_path):
    """桶内 11 个 active → 退役 pool_increment 最低者；其他桶与 candidate 不动。"""
    db = _db(tmp_path)
    ids: list[int] = []
    for rank in range(11):  # 增量 11..1（id 越小增量越高）
        factor_id = add_factor(
            expression=f"ts_mean(close,{rank + 2})",
            hypothesis="假设",
            category=_CATEGORY,
            status="active",
            db_path=db,
        )
        set_pool_increment(factor_id, float(11 - rank), db_path=db)
        ids.append(factor_id)
    other_active = add_factor(
        expression="ts_mean(volume,10)",
        hypothesis="假设",
        category=_OTHER_CATEGORY,
        status="active",
        db_path=db,
    )
    other_candidate = add_factor(
        expression="ts_mean(amount,10)",
        hypothesis="假设",
        category=_CATEGORY,  # 同桶但非 active：不占配额、不被退役
        db_path=db,
    )

    retired = apply_bucket_quota(_CATEGORY, db_path=db)
    assert retired == [ids[-1]]  # 增量 1.0 的末位
    by_id = {row["id"]: row for row in list_factors(db_path=db)}
    assert by_id[ids[-1]]["status"] == "retired"
    assert all(by_id[factor_id]["status"] == "active" for factor_id in ids[:-1])
    assert by_id[other_active]["status"] == "active"
    assert by_id[other_candidate]["status"] == "candidate"
    assert len(list_active_factors(category=_CATEGORY, db_path=db)) == 10

    events = list_events(ids[-1], db_path=db)
    retired_event = events[-1]
    assert retired_event["event"] == "status_change"
    assert (retired_event["from_status"], retired_event["to_status"]) == ("active", "retired")
    assert retired_event["detail"]["reason"] == "BUCKET_QUOTA"
    assert retired_event["detail"]["pool_increment"] == pytest.approx(1.0)


def test_apply_bucket_quota_tie_keeps_earlier_created_at(tmp_path):
    """增量并列 → created_at 早者优先保留（晚者退役）；created_at 亦并列 → id 大者退役。"""
    db = _db(tmp_path)
    first = add_factor(
        expression="ts_mean(close,3)",
        hypothesis="假设",
        category=_CATEGORY,
        status="active",
        db_path=db,
    )
    second = add_factor(
        expression="ts_mean(close,4)",
        hypothesis="假设",
        category=_CATEGORY,
        status="active",
        db_path=db,
    )
    set_pool_increment(first, 0.5, db_path=db)
    set_pool_increment(second, 0.5, db_path=db)
    _force_created_at(db, first, "2026-01-01T09:00:00")
    _force_created_at(db, second, "2026-01-02T09:00:00")

    assert apply_bucket_quota(_CATEGORY, k=1, db_path=db) == [second]
    assert list_active_factors(category=_CATEGORY, db_path=db)[0]["id"] == first

    # created_at 完全相同 → 后备序为 id 升序（晚插入者退役）
    third = add_factor(
        expression="ts_mean(close,5)",
        hypothesis="假设",
        category=_CATEGORY,
        status="active",
        db_path=db,
    )
    set_pool_increment(third, 0.5, db_path=db)
    _force_created_at(db, third, "2026-01-01T09:00:00")
    assert apply_bucket_quota(_CATEGORY, k=1, db_path=db) == [third]


def test_apply_bucket_quota_null_increment_is_lowest_priority(tmp_path):
    """pool_increment NULL（尚未评估）按最低优先级：桶满时先于有增量者退役。"""
    db = _db(tmp_path)
    unscored = add_factor(
        expression="ts_mean(close,3)",
        hypothesis="假设",
        category=_CATEGORY,
        status="active",
        db_path=db,
    )
    scored = add_factor(
        expression="ts_mean(close,4)",
        hypothesis="假设",
        category=_CATEGORY,
        status="active",
        db_path=db,
    )
    set_pool_increment(scored, -5.0, db_path=db)
    assert apply_bucket_quota(_CATEGORY, k=1, db_path=db) == [unscored]
    assert [row["id"] for row in list_active_factors(category=_CATEGORY, db_path=db)] == [scored]


def test_apply_bucket_quota_within_quota_is_noop(tmp_path):
    """桶内 active ≤ k → 无操作返回 []（不产生事件、不改状态）。"""
    db = _db(tmp_path)
    factor_id = add_factor(
        expression="ts_mean(close,3)",
        hypothesis="假设",
        category=_CATEGORY,
        status="active",
        db_path=db,
    )
    assert apply_bucket_quota(_CATEGORY, db_path=db) == []
    assert listed_status(db, factor_id) == "active"
    assert len(list_events(factor_id, db_path=db)) == 1  # 仅 created
    with pytest.raises(ValueError, match="k"):
        apply_bucket_quota(_CATEGORY, k=-1, db_path=db)


def _force_created_at(db: str, factor_id: int, created_at: str) -> None:
    """直接改 created_at（测试构造并列时序；状态机没有写入口径）。"""
    conn = _connect(db)
    try:
        conn.execute("UPDATE factors SET created_at = ? WHERE id = ?", (created_at, factor_id))
        conn.commit()
    finally:
        conn.close()


# ── 查询与指标写入 ─────────────────────────────────────────────────────────


def test_list_factors_and_active_factors_filters(tmp_path):
    """list_factors 按 status/category 过滤；list_active_factors 只返回 active。"""
    db = _db(tmp_path)
    active_candidate = add_factor(
        expression="ts_mean(close,10)",
        hypothesis="假设",
        category=_CATEGORY,
        status="active",
        db_path=db,
    )
    degraded = add_factor(
        expression="ts_mean(close,20)",
        hypothesis="假设",
        category=_CATEGORY,
        status="active",
        db_path=db,
    )
    set_status(degraded, "degraded", db_path=db)
    other_bucket = add_factor(
        expression="ts_mean(volume,10)",
        hypothesis="假设",
        category=_OTHER_CATEGORY,
        status="active",
        db_path=db,
    )
    add_factor(
        expression="ts_mean(amount,10)",
        hypothesis="假设",
        category=_CATEGORY,
        db_path=db,
    )

    assert [row["id"] for row in list_factors(status="active", db_path=db)] == [
        active_candidate,
        other_bucket,
    ]
    assert [row["id"] for row in list_active_factors(db_path=db)] == [
        active_candidate,
        other_bucket,
    ]
    assert [row["id"] for row in list_active_factors(category=_CATEGORY, db_path=db)] == [
        active_candidate
    ]
    assert len(list_factors(db_path=db)) == 4


def test_set_pool_increment_roundtrip_and_validation(tmp_path):
    """set_pool_increment 写回可读；非有限值 / 不存在因子 → 异常。"""
    db, factor_id = _seed(tmp_path, "ts_mean(close,10)")
    set_pool_increment(factor_id, 0.42, db_path=db)
    assert list_factors(db_path=db)[0]["pool_increment"] == pytest.approx(0.42)
    with pytest.raises(ValueError, match="pool_increment"):
        set_pool_increment(factor_id, float("nan"), db_path=db)
    with pytest.raises(FactorStateError, match="不存在"):
        set_pool_increment(999, 0.1, db_path=db)


def test_list_events_all_and_filtered(tmp_path):
    """list_events 无参返回全库事件（按写入序）；带 factor_id 只返回该因子。"""
    db, first = _seed(tmp_path, "ts_mean(close,10)")
    _, second = _seed(tmp_path, "ts_mean(volume,10)")
    set_status(first, "active", detail={"reason": "ADMITTED"}, db_path=db)

    all_events = list_events(db_path=db)
    assert [event["factor_id"] for event in all_events] == [first, second, first]
    assert all(event["created_at"] for event in all_events)
    assert [event["event"] for event in list_events(second, db_path=db)] == ["created"]


# ── 跨批次候选检验账本（add-fdr-candidate-ledger / issue #355 使能层） ──


def test_admission_ledger_schema_and_roundtrip(tmp_path):
    """账本 DDL 幂等；批量写入单事务；汇总含分批明细与总量。"""
    from finance_agent.factors.library import (
        ledger_summary,
        list_ledger_entries,
        record_admission_ledger,
    )

    db = tmp_path / "factor_library.db"
    init_library(db)
    conn = _connect(db)
    try:
        cols = {
            row[1] for row in conn.execute("PRAGMA table_info(factor_admission_ledger)").fetchall()
        }
    finally:
        conn.close()
    assert cols == {
        "id",
        "batch_id",
        "source",
        "expression",
        "p_value",
        "p_adj",
        "q",
        "n_hypotheses",
        "admitted",
        "factor_id",
        "rejected_gate",
        "created_at",
    }

    inserted = record_admission_ledger(
        [
            {
                "batch_id": "b1",
                "source": "monthly_broad",
                "expression": "cs_zscore(close, 20)",
                "p_value": 0.01,
                "p_adj": 0.02,
                "q": 0.1,
                "n_hypotheses": 3,
                "admitted": True,
                "factor_id": 7,
                "rejected_gate": None,
            },
            {
                "batch_id": "b1",
                "source": "monthly_broad",
                "expression": "cs_rank(volume, 5)",
                "p_value": None,
                "p_adj": None,
                "q": None,
                "n_hypotheses": None,
                "admitted": False,
                "factor_id": None,
                "rejected_gate": "WEAK_ICIR",
            },
            {
                "batch_id": "b2",
                "source": "harvest",
                "expression": "ts_mean(amount, 10)",
                "p_value": 0.2,
                "p_adj": 0.3,
                "q": 0.1,
                "n_hypotheses": 1,
                "admitted": False,
                "factor_id": None,
                "rejected_gate": "NOT_SIGNIFICANT",
            },
        ],
        db_path=db,
    )
    assert inserted == 3

    entries = list_ledger_entries(db_path=db)
    assert [e["expression"] for e in entries] == [
        "cs_zscore(close, 20)",
        "cs_rank(volume, 5)",
        "ts_mean(amount, 10)",
    ]
    assert entries[0]["admitted"] is True and entries[0]["factor_id"] == 7
    assert entries[1]["p_value"] is None and entries[1]["rejected_gate"] == "WEAK_ICIR"

    summary = ledger_summary(db_path=db)
    assert summary["total"] == 3
    assert summary["tested"] == 2  # p 值非空 = 已验证（跨批 FDR 家族面）
    assert summary["admitted"] == 1
    by_batch = {row["batch_id"]: row for row in summary["batches"]}
    assert by_batch["b1"]["size"] == 2 and by_batch["b1"]["tested"] == 1
    assert by_batch["b2"]["source"] == "harvest"


def test_reset_factor_library_clears_admission_ledger(tmp_path):
    """复位四表：账本随 factors/factor_ic/factor_events 一并清空。"""
    from finance_agent.factors.library import record_admission_ledger, reset_factor_library

    db = tmp_path / "factor_library.db"
    init_library(db)
    record_admission_ledger(
        [
            {
                "batch_id": "b1",
                "source": "builtin",
                "expression": "close",
                "p_value": 0.5,
                "p_adj": 0.5,
                "q": 0.1,
                "n_hypotheses": 1,
                "admitted": False,
                "rejected_gate": "NOT_SIGNIFICANT",
            }
        ],
        db_path=db,
    )
    removed = reset_factor_library(db_path=db)
    assert removed["factor_admission_ledger"] == 1


def test_cumulative_fdr_preview_pure_function(tmp_path):
    """全局 BH 反事实预览：跨批次家族一次校正；NULL p 不入家族；不改任何行。"""
    from finance_agent.factors.library import (
        ledger_summary,
        list_ledger_entries,
        record_admission_ledger,
    )
    from finance_agent.factors.validate import cumulative_fdr_preview

    db = tmp_path / "factor_library.db"
    init_library(db)
    record_admission_ledger(
        [
            # 强信号（跨批两批各一）＋ 噪声＋ 未测（NULL，不入家族）
            {
                "batch_id": "b1",
                "source": "monthly_broad",
                "expression": "e1",
                "p_value": 0.001,
                "p_adj": 0.004,
                "q": 0.1,
                "n_hypotheses": 2,
                "admitted": True,
                "factor_id": 1,
                "rejected_gate": None,
            },
            {
                "batch_id": "b1",
                "source": "monthly_broad",
                "expression": "e2",
                "p_value": 0.8,
                "p_adj": 0.8,
                "q": 0.1,
                "n_hypotheses": 2,
                "admitted": False,
                "factor_id": None,
                "rejected_gate": "NOT_SIGNIFICANT",
            },
            {
                "batch_id": "b2",
                "source": "harvest",
                "expression": "e3",
                "p_value": 0.002,
                "p_adj": 0.004,
                "q": 0.1,
                "n_hypotheses": 1,
                "admitted": True,
                "factor_id": 2,
                "rejected_gate": None,
            },
            {
                "batch_id": "b2",
                "source": "harvest",
                "expression": "e4",
                "p_value": None,
                "p_adj": None,
                "q": None,
                "n_hypotheses": None,
                "admitted": False,
                "factor_id": None,
                "rejected_gate": "WEAK_ICIR",
            },
        ],
        db_path=db,
    )

    entries = list_ledger_entries(db_path=db)
    before = [dict(e) for e in entries]

    preview = cumulative_fdr_preview(entries, q=0.1)
    assert preview["family_size"] == 3  # NULL 不入家族
    assert preview["significant_count"] == 2
    assert {r["expression"] for r in preview["significant"]} == {"e1", "e3"}
    assert all(r["p_adj"] is not None for r in preview["significant"])

    # 只读：读数前后逐行一致
    assert list_ledger_entries(db_path=db) == before
    # 空家族 → 全零
    empty = cumulative_fdr_preview([], q=0.1)
    assert empty["family_size"] == 0 and empty["significant"] == []
    # 汇总与家族面一致
    summary = ledger_summary(db_path=db)
    assert summary["tested"] == preview["family_size"]


def test_find_structural_duplicate_fast_path_and_variants(tmp_path):
    """精确串预筛（issue #464）语义不变：规范串命中直接返回；数字/交换律变体与
    ``ast_json`` NULL 回退仍走同构扫描命中；非同构返回 None。"""
    db = str(tmp_path / "factor_library.db")
    exact_id = add_factor(
        expression="ts_mean(close,10)",
        hypothesis="测试用经济学假设：均值因子与未来收益存在关系。",
        category="价量",
        status="active",
        db_path=db,
    )
    variant_id = add_factor(
        expression="add(close,volume)",
        hypothesis="测试用经济学假设：价量相加与未来收益存在关系。",
        category="价量",
        status="active",
        db_path=db,
    )
    legacy_id = add_factor(  # ast_json=NULL → 回退 expression（原样保留空格形态）
        expression="ts_std(close, 5)",
        hypothesis="测试用经济学假设：波动率因子与未来收益存在关系。",
        category="价量",
        status="active",
        ast_json=None,
        db_path=db,
    )
    # 规范串精确命中（快路径）
    assert find_structural_duplicate("ts_mean(close,10)", db_path=db) == exact_id
    # 原始（非规范）输入等价命中
    assert find_structural_duplicate("ts_mean( close , 10 )", db_path=db) == exact_id
    # 数字变体（同构、不同串）走扫描
    assert find_structural_duplicate("ts_mean(close,11)", db_path=db) == exact_id
    # 交换律变体走扫描
    assert find_structural_duplicate("add(volume,close)", db_path=db) == variant_id
    # ast_json NULL → expression 回退（非规范串走扫描）
    assert find_structural_duplicate("ts_std(close,5)", db_path=db) == legacy_id
    # 无同构
    assert find_structural_duplicate("log(amount)", db_path=db) is None


class TestOrientationColumn:
    def test_fresh_db_has_orientation_column(self, tmp_path):
        db = str(tmp_path / "factor_library.db")
        init_library(db)
        conn = sqlite3.connect(db)
        cols = {row[1] for row in conn.execute("PRAGMA table_info(factors)")}
        conn.close()
        assert "orientation" in cols

    def test_existing_db_migrated_idempotently(self, tmp_path):
        db = str(tmp_path / "factor_library.db")
        # 模拟旧库：先手工建旧 schema（无 orientation 列）
        conn = sqlite3.connect(db)
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS factors (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              expression TEXT NOT NULL, hypothesis TEXT NOT NULL,
              category TEXT NOT NULL, status TEXT NOT NULL,
              ast_json TEXT, source TEXT NOT NULL DEFAULT 'proposal',
              alignment_score REAL, pool_increment REAL,
              created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            );
            """
        )
        conn.commit()
        conn.close()
        init_library(db)  # 迁移
        init_library(db)  # 重跑幂等（不抛 duplicate column）
        conn = sqlite3.connect(db)
        cols = {row[1] for row in conn.execute("PRAGMA table_info(factors)")}
        conn.close()
        assert "orientation" in cols

    def test_set_orientation_writes_and_validates(self, tmp_path):
        db = str(tmp_path / "factor_library.db")
        fid = add_factor(
            expression="ts_mean(close,5)",
            hypothesis="h",
            category="测试",
            status="active",
            db_path=db,
        )
        set_orientation(fid, -1, db_path=db)
        conn = sqlite3.connect(db)
        value = conn.execute("SELECT orientation FROM factors WHERE id=?", (fid,)).fetchone()[0]
        conn.close()
        assert value == -1
        with pytest.raises(ValueError):
            set_orientation(fid, 7, db_path=db)
