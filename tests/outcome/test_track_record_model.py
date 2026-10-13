"""add-track-record Task 1:predictions 数据模型(append-only + 冻结守卫 + 迁移)。"""

import pytest

from finance_agent.outcome.track_record.model import (
    PREDICTIONS_STATUSES,
    FrozenFieldError,
    avoidance_stats,
    count_predictions,
    duplicate_prediction_ids,
    get_prediction,
    init_track_record_tables,
    insert_daily_mark,
    insert_prediction,
    list_current_predictions,
    list_predictions,
    migrate_decision_log,
    prediction_stats,
    update_prediction_status,
)

BASE = {
    "source_type": "live",
    "symbol": "600519.SH",
    "symbol_name": "贵州茅台",
    "direction": "long",
    "entry_price": 100.0,
    "target_price": 120.0,
    "horizon_days": 252,
    "confidence": 0.8,
    "benchmark": "000300.SH",
    "rationale_snapshot": {"markdown": "原文快照", "decision": {"action": "buy"}},
}


@pytest.fixture
def db(tmp_path):
    # init_track_record_tables：predictions + daily_marks 等 stage-b/c 全表（超集，
    # update-track-record-display-clarity 起盯市用例需要 daily_marks 存在）
    path = tmp_path / "track.db"
    init_track_record_tables(path)
    return path


def _insert(db, **overrides):
    rec = dict(BASE)
    rec.update(overrides)
    return insert_prediction(rec, db_path=db)


def test_statuses_enum():
    assert PREDICTIONS_STATUSES == (
        "open",
        "resolved_win",
        "resolved_loss",
        "resolved_neutral",
        "avoidance",  # neutral 回避判定终态(delta update-decision-settlement-contract)
        "unresolvable",
        # 同日重复观点关闭终态(add-prediction-pool-integrity)：不计统计分母
        "duplicate_of_day",
    )


def test_terminal_avoidance_status_is_whitelisted():
    """job 落库的 neutral 终态必须在白名单内（否则 API/前端过滤认不出该状态）。"""
    from finance_agent.outcome.track_record.judgment import TERMINAL_AVOIDANCE_STATUS

    assert TERMINAL_AVOIDANCE_STATUS in PREDICTIONS_STATUSES


def test_insert_and_list(db):
    _insert(db)
    rows = list_predictions(db_path=db)
    assert len(rows) == 1
    row = rows[0]
    assert row["direction"] == "long" and row["source_type"] == "live"
    assert row["status"] == "open"
    assert row["created_at"]  # 服务端生成


def test_frozen_field_update_raises(db):
    pid = _insert(db)
    with pytest.raises(FrozenFieldError):
        update_prediction_status(pid, {"direction": "short"}, db_path=db)


def test_status_update_allowed(db):
    pid = _insert(db)
    update_prediction_status(
        pid,
        {
            "status": "resolved_win",
            "exit_price": 110.0,
            "raw_return": 0.1,
            "excess_return": 0.05,
            "resolution_rule": "expiry",
            "resolved_at": "2026-09-02",
        },
        db_path=db,
    )
    row = list_predictions(db_path=db)[0]
    assert row["status"] == "resolved_win" and row["exit_price"] == 110.0
    assert row["direction"] == "long"  # 冻结字段未被改动


def test_list_filter_and_pagination(db):
    _insert(db, symbol="600519.SH")
    _insert(db, symbol="300308.SZ")
    assert len(list_predictions(ticker="600519", db_path=db)) == 1
    assert len(list_predictions(status="open", db_path=db)) == 2
    assert len(list_predictions(source_type="backtest", db_path=db)) == 0
    assert len(list_predictions(limit=1, db_path=db)) == 1


def test_status_resolved_group_filter(db):
    """update-prediction-log-tabs:status=resolved 组值 → 全部非 open 终态。

    非 open 状态须经 update_prediction_status 状态流转产生(insert 契约:写入必为 open)。
    """
    from finance_agent.outcome.track_record.model import update_prediction_status

    _insert(db, symbol="600519.SH")  # open(默认)
    _insert(db, symbol="300308.SZ")
    _insert(db, symbol="601318.SH")
    _insert(db, symbol="600036.SH")
    _insert(db, symbol="601888.SH")
    _insert(db, symbol="601988.SH")
    rows = list_predictions(db_path=db)
    by_symbol = {r["symbol"]: r["prediction_id"] for r in rows}
    update_prediction_status(
        by_symbol["300308.SZ"],
        {"status": "resolved_win", "raw_return": 0.1, "excess_return": 0.05},
        db_path=db,
    )
    update_prediction_status(by_symbol["601318.SH"], {"status": "resolved_loss"}, db_path=db)
    update_prediction_status(by_symbol["600036.SH"], {"status": "resolved_neutral"}, db_path=db)
    update_prediction_status(by_symbol["601888.SH"], {"status": "avoidance"}, db_path=db)
    update_prediction_status(by_symbol["601988.SH"], {"status": "unresolvable"}, db_path=db)
    resolved = list_predictions(status="resolved", db_path=db)
    assert len(resolved) == 5
    assert all(r["status"] != "open" for r in resolved)
    assert len(list_predictions(status="open", db_path=db)) == 1  # 精确匹配不受影响
    assert len(list_predictions(status="resolved_win", db_path=db)) == 1
    assert count_predictions(status="resolved", db_path=db) == 5  # total 与 list 同口径


def test_stats_empty(db):
    s = prediction_stats(db_path=db)
    assert s["total"] == 0 and s["open"] == 0 and s["win_rate"] is None


def test_migrate_decision_log(db, tmp_path):
    # 预置 decision_log 数据(复用 outcome.store DDL)
    from finance_agent.outcome.store import init_decision_log, insert_decision

    init_decision_log(db)
    insert_decision(
        {
            "session_id": "s",
            "timestamp": "2026-09-01T10:00:00",
            "ticker": "600519",
            "name": "贵州茅台",
            "action": "buy",
            "entry_price": 100.0,
            "stop_loss": 90.0,
            "target_price": 120.0,
            "confidence": 0.8,
        },
        db_path=db,
    )
    n = migrate_decision_log(db_path=db)
    assert n == 1
    rows = list_predictions(db_path=db)
    assert rows[0]["symbol"] == "600519.SH"
    assert rows[0]["direction"] == "long"
    assert rows[0]["horizon_days"] == 252  # 存量不追溯：迁移行按其原语义窗口


@pytest.mark.parametrize(
    ("action", "expected"),
    [("buy", "long"), ("sell", "short"), ("hold", "neutral"), ("watch", "neutral")],
)
def test_migrate_direction_matches_shared_mapping(db, action, expected):
    """迁移方向 = judgment.direction_for_action（Δ2T6 审查：消除决策日志迁移的拷贝）。"""
    from finance_agent.outcome.store import init_decision_log, insert_decision

    init_decision_log(db)
    insert_decision(
        {
            "session_id": "s",
            "timestamp": "2026-09-01T10:00:00",
            "ticker": "600519",
            "name": "贵州茅台",
            "action": action,
            "entry_price": 100.0,
            "confidence": 0.8,
        },
        db_path=db,
    )
    assert migrate_decision_log(db_path=db) == 1
    assert list_predictions(db_path=db)[0]["direction"] == expected


# ── add-track-record-sort-filter：排序白名单 / 关键字 / 时间段 / count ──


def test_list_sort_by_return_desc(db):
    _insert(db, symbol="a.SH", entry_price=100.0)
    _insert(db, symbol="b.SH", entry_price=100.0)
    rows = list_predictions(db_path=db)
    update_prediction_status(
        rows[0]["prediction_id"],
        {"status": "resolved_win", "raw_return": 0.05, "excess_return": 0.02},
        db_path=db,
    )
    update_prediction_status(
        rows[1]["prediction_id"],
        {"status": "resolved_win", "raw_return": 0.2, "excess_return": 0.1},
        db_path=db,
    )
    desc = list_predictions(sort_by="raw_return", sort_dir="desc", db_path=db)
    assert [r["raw_return"] for r in desc] == [0.2, 0.05]
    asc = list_predictions(sort_by="raw_return", sort_dir="asc", db_path=db)
    assert [r["raw_return"] for r in asc] == [0.05, 0.2]


def test_list_invalid_sort_falls_back_default(db):
    _insert(db, symbol="a.SH", created_at="2026-09-01T10:00:00")
    _insert(db, symbol="b.SH", created_at="2026-09-02T10:00:00")
    rows = list_predictions(sort_by="unknown_column", db_path=db)
    # 默认 created_at DESC → 后插入的 09-02 在前
    assert rows[0]["symbol"] == "b.SH"


def test_list_keyword_matches_symbol_name_and_labels(db):
    _insert(db, symbol="600519.SH", symbol_name="贵州茅台", direction="long")
    _insert(db, symbol="300308.SZ", symbol_name="中际旭创", direction="short")
    _insert(db, symbol="000001.SZ", symbol_name="平安银行", direction="long")
    assert len(list_predictions(keyword="茅台", db_path=db)) == 1
    assert len(list_predictions(keyword="看空", db_path=db)) == 1  # 方向标签
    assert len(list_predictions(keyword="平安", db_path=db)) == 1
    # 状态标签「命中」→ resolved_win
    rows = list_predictions(db_path=db)
    update_prediction_status(rows[0]["prediction_id"], {"status": "resolved_win"}, db_path=db)
    assert len(list_predictions(keyword="命中", db_path=db)) == 1
    # 状态标签「回避」→ avoidance（neutral 回避判定终态）
    update_prediction_status(
        rows[1]["prediction_id"],
        {"status": "avoidance", "avoidance_status": "avoidance_win"},
        db_path=db,
    )
    assert len(list_predictions(keyword="回避", db_path=db)) == 1


def test_list_date_range_inclusive(db):
    _insert(db, symbol="a.SH", created_at="2026-09-01T08:00:00")
    _insert(db, symbol="b.SH", created_at="2026-09-15T08:00:00")
    _insert(db, symbol="c.SH", created_at="2026-10-01T08:00:00")
    rows = list_predictions(date_from="2026-09-01", date_to="2026-09-30", db_path=db)
    assert sorted(r["symbol"] for r in rows) == ["a.SH", "b.SH"]  # 含两端


def test_count_predictions_reflects_filters(db):
    _insert(db, symbol="a.SH", symbol_name="茅台", created_at="2026-09-01T08:00:00")
    _insert(db, symbol="b.SH", symbol_name="茅台", created_at="2026-09-15T08:00:00")
    _insert(db, symbol="c.SH", symbol_name="平安", created_at="2026-10-01T08:00:00")
    assert count_predictions(keyword="茅台", db_path=db) == 2
    assert (
        count_predictions(keyword="茅台", date_from="2026-09-01", date_to="2026-09-30", db_path=db)
        == 2
    )


# ── update-decision-settlement-contract Task 1：结算契约 schema 与迁移 ──

# Δ2 前（stage-a 形态）的 predictions DDL：无 version_seq/snapshot_hash、无新列。
# 老库夹具必须含 stage-a 全列（尤其 symbol）——init_track_record_tables 第一步
# executescript(PREDICTIONS_DDL) 会建 idx_predictions_symbol，缺列则在建索引处报错，
# 早于任何 ALTER；因此不能只建 prediction_id/status 的退化表。
_LEGACY_PREDICTIONS_DDL = """
CREATE TABLE predictions (
  prediction_id     TEXT PRIMARY KEY,
  source_type       TEXT NOT NULL CHECK (source_type IN ('backtest','live')),
  symbol            TEXT NOT NULL,
  symbol_name       TEXT,
  direction         TEXT NOT NULL CHECK (direction IN ('long','short','neutral')),
  entry_price       REAL,
  target_price      REAL,
  horizon_days      INTEGER NOT NULL DEFAULT 252,
  confidence        REAL CHECK (confidence BETWEEN 0 AND 1),
  benchmark         TEXT NOT NULL DEFAULT '000300.SH',
  rationale_snapshot TEXT NOT NULL,
  langfuse_trace_id TEXT,
  status            TEXT NOT NULL DEFAULT 'open',
  created_at        TEXT NOT NULL,
  resolved_at       TEXT,
  exit_price        REAL,
  raw_return        REAL,
  excess_return     REAL,
  resolution_rule   TEXT,
  updated_at        TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_predictions_status ON predictions(status);
CREATE INDEX IF NOT EXISTS idx_predictions_symbol ON predictions(symbol);
"""


def test_settlement_contract_columns_migrated(tmp_path):
    """老库（无新列）经 init 后补齐 avoidance_status / settle_entry_price，幂等。"""
    import sqlite3

    from finance_agent.outcome.track_record.model import init_track_record_tables

    db = tmp_path / "old.db"
    conn = sqlite3.connect(db)
    conn.executescript(_LEGACY_PREDICTIONS_DDL)
    conn.execute(
        """INSERT INTO predictions (prediction_id, source_type, symbol, direction, horizon_days,
             rationale_snapshot, status, created_at, updated_at)
           VALUES ('p_legacy', 'live', '600519.SH', 'long', 252, '{}', 'open',
                   '2026-09-01T10:00:00', '2026-09-01T10:00:00')"""
    )
    conn.commit()
    conn.close()

    # 自证「老库」：四个待补列在 init 前均不存在，否则断言可能空转
    pre = sqlite3.connect(db)
    try:
        pre_cols = {r[1] for r in pre.execute("PRAGMA table_info(predictions)")}
    finally:
        pre.close()
    assert not {"avoidance_status", "settle_entry_price", "version_seq", "snapshot_hash"} & pre_cols

    init_track_record_tables(db)
    init_track_record_tables(db)  # 幂等
    conn = sqlite3.connect(db)
    try:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(predictions)")}
        assert {"avoidance_status", "settle_entry_price"} <= cols
        assert {"version_seq", "snapshot_hash"} <= cols  # 既有迁移不回归
        # 存量行不回填、窗口不追溯（delta Scenario：存量观点不追溯）
        legacy = conn.execute(
            """SELECT horizon_days, avoidance_status, settle_entry_price FROM predictions
               WHERE prediction_id='p_legacy'"""
        ).fetchone()
        assert legacy == (252, None, None)
    finally:
        conn.close()


def test_mutable_fields_include_settlement_outputs():
    from finance_agent.outcome.track_record.model import _FROZEN_FIELDS, _MUTABLE_FIELDS

    assert "avoidance_status" in _MUTABLE_FIELDS
    assert "settle_entry_price" in _MUTABLE_FIELDS
    assert "avoidance_status" not in _FROZEN_FIELDS
    assert "settle_entry_price" not in _FROZEN_FIELDS


def test_fresh_db_horizon_days_ddl_default_is_20(tmp_path):
    """DDL 表级默认值钉死：裸 INSERT（省略 horizon_days）落 20，不依赖 insert_prediction 兜底。"""
    import sqlite3

    from finance_agent.outcome.track_record.model import init_track_record_tables

    db = tmp_path / "fresh.db"
    init_track_record_tables(db)
    conn = sqlite3.connect(db)
    try:
        conn.execute(
            """INSERT INTO predictions (prediction_id, source_type, symbol, direction,
                 rationale_snapshot, created_at, updated_at)
               VALUES ('p_bare', 'live', '600519.SH', 'long', '{}', 't', 't')"""
        )
        conn.commit()
        assert (
            conn.execute(
                "SELECT horizon_days FROM predictions WHERE prediction_id='p_bare'"
            ).fetchone()[0]
            == 20
        )
    finally:
        conn.close()


def test_insert_prediction_default_horizon_matches_config(db):
    """insert_prediction 兜底 = 配置默认值（消除第二处 252 字面量，DDL 默认不再不可达）。"""
    from finance_agent.outcome.track_record.judgment import DEFAULT_HORIZON_DAYS

    rec = dict(BASE)
    rec.pop("horizon_days")
    insert_prediction(rec, db_path=db)
    assert list_predictions(db_path=db)[0]["horizon_days"] == DEFAULT_HORIZON_DAYS == 20


# ── delta update-decision-settlement-contract Task 5：口径过滤 + 回避独立统计（§1.9①）──


def _settle(db, pid, status, excess=0.05):
    update_prediction_status(
        pid,
        {"status": status, "excess_return": excess, "raw_return": excess},
        db_path=db,
    )


def test_win_rate_excludes_neutral_direction_rows(db):
    """胜率分母只含 long/short：存量 neutral 方向已 resolved_win 行不得进分子/分母。"""
    for i in range(4):
        _settle(db, _insert(db, symbol=f"l{i}.SH", direction="long"), "resolved_win")
    # 存量遗留：neutral 方向却落了 resolved_win（Δ2 前语义），不得进胜率
    _settle(db, _insert(db, symbol="n.SH", direction="neutral"), "resolved_win", 0.5)
    s = prediction_stats(db_path=db)
    assert s["settled"] == 4
    assert s["win_rate"] == 1.0


def test_avg_excess_population_is_three_state_long_short(db):
    """avg_excess 人口 = long/short 三态（含带内 neutral），排除 neutral 方向与 avoidance 行。"""
    for status, exc in (
        ("resolved_win", 0.05),
        ("resolved_loss", -0.05),
        ("resolved_neutral", 0.01),
    ):
        _settle(db, _insert(db, symbol=f"{status}.SH", direction="long"), status, exc)
    # neutral 方向观点：非主指标人口（不进胜率也不进均值）
    _settle(db, _insert(db, symbol="nav.SH", direction="neutral"), "resolved_neutral", 0.9)
    # 回避终态行：有 excess 但不在主指标人口
    update_prediction_status(
        _insert(db, symbol="avoid.SH", direction="neutral"),
        {"status": "avoidance", "avoidance_status": "avoidance_win", "excess_return": -0.1},
        db_path=db,
    )
    s = prediction_stats(db_path=db)
    assert s["avg_excess"] == round((0.05 - 0.05 + 0.01) / 3, 4)  # 0.0033
    assert s["settled"] == 2  # 胜率仍只认 win/loss


def test_prediction_stats_horizon_filter(db):
    """horizon_days 过滤：252 存量行不进头条口径（默认窗口 20）；缺省不过滤。"""
    _settle(db, _insert(db, symbol="new.SH", direction="long", horizon_days=20), "resolved_win")
    _settle(db, _insert(db, symbol="old.SH", direction="long", horizon_days=252), "resolved_win")
    headline = prediction_stats(db_path=db, horizon_days=20)
    assert headline["total"] == 1 and headline["settled"] == 1
    assert headline["avg_excess"] == 0.05
    full = prediction_stats(db_path=db)
    assert full["total"] == 2 and full["settled"] == 2


def test_avoidance_stats_rate_and_settled(db):
    """回避正确率 = win/(win+loss)；avoidance_neutral 与未判定不进分母、不计 settled。"""
    for i in range(6):
        update_prediction_status(
            _insert(db, symbol=f"w{i}.SH", direction="neutral"),
            {"status": "avoidance", "avoidance_status": "avoidance_win"},
            db_path=db,
        )
    for i in range(3):
        update_prediction_status(
            _insert(db, symbol=f"l{i}.SH", direction="neutral"),
            {"status": "avoidance", "avoidance_status": "avoidance_loss"},
            db_path=db,
        )
    for i in range(3):
        update_prediction_status(
            _insert(db, symbol=f"n{i}.SH", direction="neutral"),
            {"status": "avoidance", "avoidance_status": "avoidance_neutral"},
            db_path=db,
        )
    _insert(db, symbol="open.SH", direction="neutral")  # 未判定
    a = avoidance_stats(db_path=db)
    assert a["avoidance_win"] == 6 and a["avoidance_loss"] == 3
    assert a["avoidance_neutral"] == 3
    assert a["settled"] == 9  # neutral 不进 settled
    assert a["avoidance_rate"] == round(6 / 9, 4)  # 0.6667


def test_avoidance_stats_empty_is_none(db):
    a = avoidance_stats(db_path=db)
    assert a == {
        "avoidance_win": 0,
        "avoidance_loss": 0,
        "avoidance_neutral": 0,
        "settled": 0,
        "avoidance_rate": None,
    }


def test_avoidance_stats_source_and_version_filter(db):
    """回避统计与胜率同源过滤（source_type / version_seq）。"""
    update_prediction_status(
        _insert(db, symbol="live.SH", direction="neutral", source_type="live"),
        {"status": "avoidance", "avoidance_status": "avoidance_win"},
        db_path=db,
    )
    update_prediction_status(
        _insert(db, symbol="bt.SH", direction="neutral", source_type="backtest"),
        {"status": "avoidance", "avoidance_status": "avoidance_loss"},
        db_path=db,
    )
    live = avoidance_stats(source_type="live", db_path=db)
    assert live["avoidance_win"] == 1 and live["settled"] == 1


class TestMetricsBetaAlphaColumns:
    def test_old_db_migrated_idempotent(self, tmp_path):
        """旧 schema(无 beta/jensen_alpha 列)→ init 补列,重跑不加列。"""
        import sqlite3

        from finance_agent.outcome.track_record.model import (
            init_track_record_tables,
        )

        db = tmp_path / "old.db"
        conn = sqlite3.connect(db)
        # predictions 用文件内既有 _LEGACY_PREDICTIONS_DDL 全列夹具——brief 原文的退化表
        # (仅 prediction_id)会在 init 第一步建 idx_predictions_status 时报 no such column
        # (本文件 line 236 注释所载约束);本用例主题是 agent_metrics_daily 迁移。
        conn.executescript(_LEGACY_PREDICTIONS_DDL)
        conn.executescript(
            """
            CREATE TABLE agent_metrics_daily (
              metric_date TEXT PRIMARY KEY, sample_size INTEGER NOT NULL DEFAULT 0,
              settled INTEGER NOT NULL DEFAULT 0, win_rate REAL, avg_excess REAL,
              annual_return REAL, volatility REAL, sharpe REAL, max_drawdown REAL,
              risk_score INTEGER, risk_label TEXT, segment_json TEXT NOT NULL DEFAULT '{}'
            );
            """
        )
        conn.commit()
        conn.close()
        init_track_record_tables(db)
        init_track_record_tables(db)  # 重跑幂等
        conn = sqlite3.connect(db)
        cols = {r[1] for r in conn.execute("PRAGMA table_info(agent_metrics_daily)")}
        conn.close()
        assert {"beta", "jensen_alpha"} <= cols

    def test_upsert_and_read_back(self, tmp_path):
        from finance_agent.outcome.track_record.model import (
            get_latest_metrics,
            init_track_record_tables,
            upsert_metrics_daily,
        )

        db = tmp_path / "t.db"
        init_track_record_tables(db)
        upsert_metrics_daily("2026-10-03", {"beta": 0.85, "jensen_alpha": -0.004}, db_path=db)
        row = get_latest_metrics(db_path=db)
        assert row["beta"] == 0.85 and row["jensen_alpha"] == -0.004
        # 覆盖写(null 覆盖非 null,同日 upsert 语义)
        upsert_metrics_daily("2026-10-03", {"beta": None, "jensen_alpha": None}, db_path=db)
        row = get_latest_metrics(db_path=db)
        assert row["beta"] is None and row["jensen_alpha"] is None


def test_prediction_ids_by_direction(db):
    """组合聚合排除 neutral 的取数辅助（incident 032 根因 A）。"""
    from finance_agent.outcome.track_record.model import prediction_ids_by_direction

    long_id = _insert(db, direction="long")
    neutral_id = _insert(db, direction="neutral")
    got = prediction_ids_by_direction("neutral", db_path=db)
    assert got == {neutral_id}
    assert prediction_ids_by_direction("long", db_path=db) == {long_id}
    assert prediction_ids_by_direction("short", db_path=db) == set()


# ---- add-prediction-pool-integrity: duplicate_of_day 状态与 helper ----


def test_duplicate_of_day_is_valid_status_total_but_not_win_rate_denominator(db):
    """是合法状态且计入总数不计入统计分母：新增终态入枚举；total 不受限（UI 展示），
    settled/胜率分母不含它。"""
    dup_id = _insert(db, symbol="600519.SH", direction="neutral")
    update_prediction_status(
        dup_id,
        {"status": "duplicate_of_day", "resolution_rule": "duplicate_of_day"},
        db_path=db,
    )
    assert "duplicate_of_day" in PREDICTIONS_STATUSES
    stats = prediction_stats(db_path=db)
    assert stats["total"] == 1  # 总数不受限（UI 展示）
    assert stats["settled"] == 0 and stats["win_rate"] is None  # 不进胜率分母


def test_duplicate_prediction_ids_filters_by_resolution_rule(db):
    """按 resolution_rule 筛选：helper 只认 resolution_rule='duplicate_of_day' 的行
    （marks 聚合排除用）。"""
    dup_id = _insert(db, symbol="600519.SH", direction="neutral")
    _insert(db, symbol="000001.SZ", direction="long")
    update_prediction_status(
        dup_id,
        {"status": "duplicate_of_day", "resolution_rule": "duplicate_of_day"},
        db_path=db,
    )
    assert duplicate_prediction_ids(db_path=db) == {dup_id}


# ── add-current-stance-view:每股最新一条 open 的立场视图 ──


def test_list_current_latest_open_per_symbol(db):
    """同股多条 open 只返回 created_at 最新一条;多股各自一行,倒序。"""
    _insert(db, symbol="601058.SH", created_at="2026-10-05T10:00:00")
    _insert(db, symbol="601058.SH", created_at="2026-10-06T10:00:00")
    _insert(db, symbol="300033.SZ", created_at="2026-10-06T11:00:00")
    rows = list_current_predictions(db_path=db)
    assert [r["symbol"] for r in rows] == ["300033.SZ", "601058.SH"]
    assert [r["created_at"] for r in rows] == ["2026-10-06T11:00:00", "2026-10-06T10:00:00"]


def test_list_current_excludes_closed_and_dup(db):
    """dup/已结算/unresolvable 行不进入;该标的取其最新一条 open;仅剩关闭行的标的整体不出现。"""
    old = _insert(db, symbol="600519.SH", created_at="2026-10-01T10:00:00")
    dup = _insert(db, symbol="600519.SH", created_at="2026-10-02T10:00:00")
    update_prediction_status(
        dup, {"status": "duplicate_of_day", "resolution_rule": "duplicate_of_day"}, db_path=db
    )
    update_prediction_status(
        old,
        {
            "status": "resolved_neutral",
            "resolution_rule": "superseded",
            "resolved_at": "2026-10-03",
        },
        db_path=db,
    )
    keep = _insert(db, symbol="600519.SH", created_at="2026-10-04T10:00:00")
    only_closed = _insert(db, symbol="600026.SH", created_at="2026-10-01T09:00:00")
    update_prediction_status(
        only_closed, {"status": "unresolvable", "resolution_rule": "stale_no_market"}, db_path=db
    )
    rows = list_current_predictions(db_path=db)
    assert [r["prediction_id"] for r in rows] == [keep]


def test_list_current_empty_db(db):
    assert list_current_predictions(db_path=db) == []


def test_list_current_source_filter(db):
    """source 过滤;缺省行内携带 source_type 供区分。"""
    _insert(db, symbol="600015.SH", source_type="live", created_at="2026-10-05T10:00:00")
    _insert(db, symbol="600016.SH", source_type="backtest", created_at="2026-10-05T11:00:00")
    assert [r["symbol"] for r in list_current_predictions("live", db_path=db)] == ["600015.SH"]
    assert [r["symbol"] for r in list_current_predictions("backtest", db_path=db)] == ["600016.SH"]
    assert len(list_current_predictions(db_path=db)) == 2


# ── update-track-record-display-clarity:open 行最新盯市注入 ──


def test_list_predictions_injects_latest_mark_for_open(db):
    """open 行取 mark_date 最新的盯市；字段三元组完整。"""
    pid = _insert(db, symbol="600015.SH", created_at="2026-10-01T10:00:00")
    insert_daily_mark(pid, "2026-10-02", cum_return=0.01, cum_excess=0.005, db_path=db)
    insert_daily_mark(pid, "2026-10-03", cum_return=-0.012, cum_excess=-0.008, db_path=db)
    rows = list_predictions(status="open", db_path=db)
    row = next(r for r in rows if r["prediction_id"] == pid)
    assert row["latest_mark"] == {
        "mark_date": "2026-10-03",
        "cum_return": -0.012,
        "cum_excess": -0.008,
    }


def test_list_predictions_latest_mark_null_for_closed_and_unmarked(db):
    """非 open 行恒 None（即使有历史 marks）；无 marks 的 open 行也 None。"""
    unmarked = _insert(db, symbol="600016.SH", created_at="2026-10-01T11:00:00")
    closed = _insert(db, symbol="600017.SH", created_at="2026-10-01T12:00:00")
    insert_daily_mark(closed, "2026-10-02", cum_return=0.02, db_path=db)
    update_prediction_status(
        closed, {"status": "unresolvable", "resolution_rule": "stale_no_market"}, db_path=db
    )
    rows = {r["prediction_id"]: r for r in list_predictions(db_path=db)}
    assert rows[unmarked]["latest_mark"] is None
    assert rows[closed]["latest_mark"] is None


def test_keyword_band_neutral_label_matches_status_only(db):
    """「带内中性」匹配 resolved_neutral；「中性」只剩方向 neutral 语义（消歧）。"""
    neutral_dir = _insert(
        db, symbol="600015.SH", direction="neutral", created_at="2026-10-01T10:00:00"
    )
    band = _insert(db, symbol="600016.SH", direction="short", created_at="2026-10-01T11:00:00")
    update_prediction_status(
        band, {"status": "resolved_neutral", "resolution_rule": "superseded"}, db_path=db
    )
    band_rows = list_predictions(keyword="带内中性", db_path=db)
    assert [r["prediction_id"] for r in band_rows] == [band]
    dir_rows = list_predictions(keyword="中性", db_path=db)
    assert {r["prediction_id"] for r in dir_rows} == {neutral_dir}


# ── add-watch-trigger-tracking Task 6：session_id / trigger_high / trigger_low 列 ──

_TRIGGER_TRACKING_COLS = {"session_id", "trigger_high", "trigger_low"}


def test_trigger_tracking_columns_migrated(tmp_path):
    """老库（无三列）→ 幂等 ALTER 补列；存量语义 NULL 不回填。"""
    import sqlite3

    from finance_agent.outcome.track_record.model import _migrate_trigger_tracking_columns

    db = tmp_path / "old.db"
    conn = sqlite3.connect(db)
    # 夹具含 stage-a 全列（约束见 _LEGACY_PREDICTIONS_DDL 注释：缺列在建索引处报错）
    conn.executescript(_LEGACY_PREDICTIONS_DDL)
    conn.execute(
        """INSERT INTO predictions (prediction_id, source_type, symbol, direction, horizon_days,
             rationale_snapshot, status, created_at, updated_at)
           VALUES ('p_legacy', 'live', '600519.SH', 'long', 20, '{}', 'open',
                   '2026-10-01T10:00:00', '2026-10-01T10:00:00')"""
    )
    conn.commit()
    pre_cols = {r[1] for r in conn.execute("PRAGMA table_info(predictions)")}
    conn.close()
    # 自证「老库」：三列在迁移前均不存在
    assert not _TRIGGER_TRACKING_COLS & pre_cols

    conn = sqlite3.connect(db)
    try:
        _migrate_trigger_tracking_columns(conn)
        _migrate_trigger_tracking_columns(conn)  # 幂等：重跑不抛异常、不加重复列
        cols = {r[1] for r in conn.execute("PRAGMA table_info(predictions)")}
        assert cols >= _TRIGGER_TRACKING_COLS
        legacy = conn.execute(
            "SELECT session_id, trigger_high, trigger_low FROM predictions"
            " WHERE prediction_id='p_legacy'"
        ).fetchone()
        assert legacy == (None, None, None)  # 存量行不追溯回填
    finally:
        conn.close()


def test_fresh_db_ddl_includes_trigger_tracking_columns(tmp_path):
    """新库 CREATE TABLE 直建三列（不依赖迁移路径）。"""
    import sqlite3

    from finance_agent.outcome.track_record.model import init_track_record_tables

    db = tmp_path / "fresh.db"
    init_track_record_tables(db)
    conn = sqlite3.connect(db)
    try:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(predictions)")}
    finally:
        conn.close()
    assert cols >= _TRIGGER_TRACKING_COLS


def test_insert_prediction_accepts_trigger_tracking_keys(db):
    """insert_prediction 接受新 record 键并原样落库。"""
    pid = _insert(
        db,
        session_id="sess_watch_1",
        trigger_high=24.6,
        trigger_low=22.91,
        direction="neutral",
    )
    row = get_prediction(pid, db_path=db)
    assert row["session_id"] == "sess_watch_1"
    assert row["trigger_high"] == 24.6
    assert row["trigger_low"] == 22.91


def test_insert_prediction_without_trigger_keys_leaves_null(db):
    """未提供新键（旧调用方）→ 列为 NULL，不缺省、不报错。"""
    pid = _insert(db)
    row = get_prediction(pid, db_path=db)
    assert row["session_id"] is None
    assert row["trigger_high"] is None
    assert row["trigger_low"] is None


def test_trigger_tracking_fields_are_write_frozen(db):
    """三列写入即冻结：update_prediction_status 尝试修改 SHALL 失败（FrozenFieldError）。"""
    from finance_agent.outcome.track_record.model import _FROZEN_FIELDS, _MUTABLE_FIELDS

    assert set(_FROZEN_FIELDS) >= _TRIGGER_TRACKING_COLS
    assert not _TRIGGER_TRACKING_COLS & set(_MUTABLE_FIELDS)
    pid = _insert(db, session_id="sess_watch_1")
    with pytest.raises(FrozenFieldError):
        update_prediction_status(pid, {"session_id": "sess_hack"}, db_path=db)


def test_delete_all_track_record_state_clears_seed_surface(db):
    """E2E 全量复位（2026-10-13）：五张造数面表清空且计数正确；二跑幂等。

    背景：战绩页 E2E 套件多个 spec 假设「库内只有自己造的行」，本地重跑残留种子
    （实测 49 条 predictions）致空态/行唯一断言误红；此助手让本地复位与 CI 全新起库
    等价（由 track-record config 的 globalSetup 调用）。
    """
    from finance_agent.outcome.track_record.model import (
        delete_all_track_record_state,
        upsert_equity_point,
        upsert_index_closes,
        upsert_metrics_daily,
    )

    pid = _insert(db)
    insert_daily_mark(pid, "2026-09-01", mark_price=101.0, db_path=db)
    upsert_equity_point("2026-09-01", 1.0, benchmark_nav=1.0, db_path=db)
    upsert_metrics_daily("2026-09-01", {"sample_size": 1}, db_path=db)
    upsert_index_closes([("000300.SH", "2026-09-01", 4000.0)], db_path=db)

    counts = delete_all_track_record_state(db_path=db)
    assert counts == {
        "predictions": 1,
        "daily_marks": 1,
        "equity_curve": 1,
        "agent_metrics_daily": 1,
        "index_closes": 1,
    }
    assert count_predictions(db_path=db) == 0
    # 二跑幂等：全 0，不报错
    assert set(delete_all_track_record_state(db_path=db).values()) == {0}
