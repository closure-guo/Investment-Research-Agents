"""predictions 数据模型(add-track-record):append-only + 冻结字段守卫 + decision_log 迁移。

与 outcome/store.py 同款 SQLite(WAL + busy_timeout 短连接),db_path 调用期注入。
冻结铁律:方向/入场价/快照/创建时间写入后不可改;判定结果只经 update_prediction_status
更新 status/resolved_at/exit_price/raw_return/excess_return/resolution_rule/
avoidance_status/settle_entry_price。
"""

from __future__ import annotations

import contextlib
import json
import os
import sqlite3
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from finance_agent.outcome.track_record.judgment import (
    DEFAULT_HORIZON_DAYS,
    MAX_HORIZON_DAYS,
    direction_for_action,
)

PREDICTIONS_DDL = """
CREATE TABLE IF NOT EXISTS predictions (
  prediction_id     TEXT PRIMARY KEY,
  source_type       TEXT NOT NULL CHECK (source_type IN ('backtest','live')),
  symbol            TEXT NOT NULL,
  symbol_name       TEXT,
  direction         TEXT NOT NULL CHECK (direction IN ('long','short','neutral')),
  entry_price       REAL,
  target_price      REAL,
  horizon_days      INTEGER NOT NULL DEFAULT 20,
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
  avoidance_status  TEXT,
  settle_entry_price REAL,
  updated_at        TEXT NOT NULL,
  version_seq       INTEGER,
  snapshot_hash     TEXT
);
CREATE INDEX IF NOT EXISTS idx_predictions_status ON predictions(status);
CREATE INDEX IF NOT EXISTS idx_predictions_symbol ON predictions(symbol);
"""

PREDICTIONS_STATUSES = (
    "open",
    "resolved_win",
    "resolved_loss",
    "resolved_neutral",
    # neutral 回避判定的生命周期终态（结果本身在 avoidance_status 列）
    "avoidance",
    "unresolvable",
)
_FROZEN_FIELDS = (
    "direction",
    "entry_price",
    "target_price",
    "rationale_snapshot",
    "created_at",
    "source_type",
    "symbol",
)
_MUTABLE_FIELDS = (
    "status",
    "resolved_at",
    "exit_price",
    "raw_return",
    "excess_return",
    "resolution_rule",
    "updated_at",
    "avoidance_status",
    "settle_entry_price",
)


class FrozenFieldError(Exception):
    """尝试修改冻结字段(append-only 铁律)。"""


def _default_db_path() -> Path:
    return Path(os.getenv("SESSIONS_DB_PATH", "data/sessions.db"))


def _connect(db_path: str | Path | None) -> sqlite3.Connection:
    path = Path(db_path) if db_path else _default_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, check_same_thread=False, timeout=15.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=15000")
    return conn


def init_predictions(db_path: str | Path | None = None) -> None:
    conn = _connect(db_path)
    try:
        conn.executescript(PREDICTIONS_DDL)
        conn.commit()
    finally:
        conn.close()


# ── add-track-record-stage-b：盯市/净值/日批指标 三表（幂等 DDL）──
TRACK_RECORD_EXTRA_DDL = """
CREATE TABLE IF NOT EXISTS daily_marks (
  mark_id          TEXT PRIMARY KEY,
  prediction_id    TEXT NOT NULL,
  mark_date        TEXT NOT NULL,
  mark_price       REAL,
  cum_return       REAL,
  cum_excess       REAL,
  benchmark_price  REAL,
  UNIQUE(prediction_id, mark_date)
);
CREATE INDEX IF NOT EXISTS idx_daily_marks_pred ON daily_marks(prediction_id);
CREATE INDEX IF NOT EXISTS idx_daily_marks_date ON daily_marks(mark_date);

CREATE TABLE IF NOT EXISTS equity_curve (
  curve_date    TEXT PRIMARY KEY,
  agent_nav     REAL NOT NULL,
  benchmark_nav REAL,
  daily_return  REAL,
  trades_count  INTEGER
);

CREATE TABLE IF NOT EXISTS agent_metrics_daily (
  metric_date   TEXT PRIMARY KEY,
  sample_size   INTEGER NOT NULL DEFAULT 0,
  settled       INTEGER NOT NULL DEFAULT 0,
  win_rate      REAL,
  avg_excess    REAL,
  annual_return REAL,
  volatility    REAL,
  sharpe        REAL,
  max_drawdown  REAL,
  risk_score    INTEGER,
  risk_label    TEXT,
  beta          REAL,
  jensen_alpha  REAL,
  segment_json  TEXT NOT NULL DEFAULT '{}'
);

-- ── add-track-record-stage-c ──
CREATE TABLE IF NOT EXISTS agents (
  agent_id          TEXT PRIMARY KEY,
  model_version     TEXT NOT NULL,
  strategy_version  TEXT,
  version_seq       INTEGER NOT NULL,
  retired_at        TEXT,
  created_at        TEXT NOT NULL,
  note              TEXT,
  UNIQUE(version_seq)
);

CREATE TABLE IF NOT EXISTS audit_log (
  log_id        INTEGER PRIMARY KEY AUTOINCREMENT,
  prediction_id TEXT NOT NULL,
  action        TEXT NOT NULL,
  old_status    TEXT,
  new_status    TEXT,
  detail        TEXT,
  source        TEXT,
  created_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_audit_pred ON audit_log(prediction_id);

-- ── add-index-performance-compare ──
CREATE TABLE IF NOT EXISTS index_closes (
  index_code TEXT NOT NULL,
  trade_date TEXT NOT NULL,
  close      REAL NOT NULL,
  PRIMARY KEY (index_code, trade_date)
);

-- ── update-risk-free-rate-source ──
CREATE TABLE IF NOT EXISTS risk_free_rates (
  rate_date TEXT PRIMARY KEY,
  rate      REAL NOT NULL,
  source    TEXT NOT NULL
);
"""


def init_track_record_tables(db_path: str | Path | None = None) -> None:
    """建 predictions（若缺）与全部 stage-b/c 表；幂等，可重复调用。"""
    conn = _connect(db_path)
    try:
        conn.executescript(PREDICTIONS_DDL)
        conn.executescript(TRACK_RECORD_EXTRA_DDL)
        _migrate_stage_c_columns(conn)
        _migrate_settlement_contract_columns(conn)
        _migrate_metrics_beta_alpha_columns(conn)
        conn.commit()
    finally:
        conn.close()


# stages-c 迁移列：旧库 ALTER TABLE 补列（幂等：先查 PRAGMA table_info）
_STAGE_C_COLUMNS = (
    ("version_seq", "ALTER TABLE predictions ADD COLUMN version_seq INTEGER"),
    ("snapshot_hash", "ALTER TABLE predictions ADD COLUMN snapshot_hash TEXT"),
)


def _migrate_stage_c_columns(conn: sqlite3.Connection) -> None:
    cols = {r[1] for r in conn.execute("PRAGMA table_info(predictions)").fetchall()}
    for col, ddl in _STAGE_C_COLUMNS:
        if col not in cols:
            conn.execute(ddl)


# ── update-decision-settlement-contract：回避判定 + 派生结算入场价（幂等迁移）──
_SETTLEMENT_CONTRACT_COLUMNS = (
    ("avoidance_status", "ALTER TABLE predictions ADD COLUMN avoidance_status TEXT"),
    ("settle_entry_price", "ALTER TABLE predictions ADD COLUMN settle_entry_price REAL"),
)


def _migrate_settlement_contract_columns(conn: sqlite3.Connection) -> None:
    cols = {r[1] for r in conn.execute("PRAGMA table_info(predictions)").fetchall()}
    for col, ddl in _SETTLEMENT_CONTRACT_COLUMNS:
        if col not in cols:
            conn.execute(ddl)


# ── add-portfolio-beta-alpha:指标快照 β/α 列(幂等迁移)──
_METRICS_BETA_ALPHA_COLUMNS = (
    ("beta", "ALTER TABLE agent_metrics_daily ADD COLUMN beta REAL"),
    ("jensen_alpha", "ALTER TABLE agent_metrics_daily ADD COLUMN jensen_alpha REAL"),
)


def _migrate_metrics_beta_alpha_columns(conn: sqlite3.Connection) -> None:
    cols = {r[1] for r in conn.execute("PRAGMA table_info(agent_metrics_daily)").fetchall()}
    for col, ddl in _METRICS_BETA_ALPHA_COLUMNS:
        if col not in cols:
            conn.execute(ddl)


def compute_snapshot_hash(rationale_snapshot: Any) -> str:
    """SHA-256(canonical JSON)。dict 递归排序键、ensure_ascii=False，稳定跨运行。"""
    import hashlib

    def _canon(v: Any) -> Any:
        if isinstance(v, dict):
            return {str(k): _canon(x) for k, x in sorted(v.items(), key=lambda kv: str(kv[0]))}
        if isinstance(v, list):
            return [_canon(x) for x in v]
        return v

    canonical = json.dumps(_canon(rationale_snapshot), ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


# ── agents：模型/策略版本登记与活跃版本（P6 分段封存）──
def register_agent(
    model_version: str,
    strategy_version: str | None = None,
    note: str | None = None,
    db_path: str | Path | None = None,
) -> dict[str, Any]:
    """登记新版本 agent：旧活跃版本 retired_at 落时间戳，新版本接任活跃。"""
    conn = _connect(db_path)
    try:
        now = datetime.now().isoformat()
        active = conn.execute("SELECT * FROM agents WHERE retired_at IS NULL").fetchone()
        if active is not None:
            conn.execute(
                "UPDATE agents SET retired_at=? WHERE agent_id=?", (now, active["agent_id"])
            )
        agent_id = f"ag_{uuid.uuid4().hex[:12]}"
        seq_row = conn.execute(
            "SELECT COALESCE(MAX(version_seq), 0) + 1 AS n FROM agents"
        ).fetchone()
        version_seq = int(seq_row["n"])
        conn.execute(
            """INSERT INTO agents (agent_id, model_version, strategy_version, version_seq,
                 retired_at, created_at, note)
               VALUES (?, ?, ?, ?, NULL, ?, ?)""",
            (agent_id, model_version, strategy_version, version_seq, now, note),
        )
        conn.commit()
        return {
            "agent_id": agent_id,
            "model_version": model_version,
            "strategy_version": strategy_version,
            "version_seq": version_seq,
            "retired_at": None,
            "created_at": now,
        }
    finally:
        conn.close()


def get_active_agent(db_path: str | Path | None = None) -> dict[str, Any] | None:
    conn = _connect(db_path)
    try:
        row = conn.execute("SELECT * FROM agents WHERE retired_at IS NULL").fetchone()  # noqa: S608
        return dict(row) if row else None
    finally:
        conn.close()


def list_agents(db_path: str | Path | None = None) -> list[dict[str, Any]]:
    conn = _connect(db_path)
    try:
        rows = conn.execute("SELECT * FROM agents ORDER BY version_seq ASC").fetchall()  # noqa: S608
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_agent(version_seq: int, db_path: str | Path | None = None) -> dict[str, Any] | None:
    conn = _connect(db_path)
    try:
        row = conn.execute("SELECT * FROM agents WHERE version_seq=?", (version_seq,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


# ── audit_log：状态变更留痕（不可变事实流）──
def append_audit(
    prediction_id: str,
    action: str,
    *,
    old_status: str | None = None,
    new_status: str | None = None,
    detail: str | None = None,
    source: str = "system",
    db_path: str | Path | None = None,
) -> None:
    conn = _connect(db_path)
    try:
        conn.execute(
            """INSERT INTO audit_log (prediction_id, action, old_status, new_status,
                 detail, source, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                prediction_id,
                action,
                old_status,
                new_status,
                detail,
                source,
                datetime.now().isoformat(),
            ),
        )
        conn.commit()
    finally:
        conn.close()


def list_audit(prediction_id: str, db_path: str | Path | None = None) -> list[dict[str, Any]]:
    conn = _connect(db_path)
    try:
        rows = conn.execute(
            "SELECT * FROM audit_log WHERE prediction_id=? ORDER BY log_id ASC",
            (prediction_id,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def integrity_check(db_path: str | Path | None = None) -> dict[str, Any]:
    """快照哈希完整性校验：逐条重算 rationale_snapshot 哈希比对 snapshot_hash。

    篡改（hash 不一致）不自动修复，写审计日志（action='integrity_mismatch'）并
    返回 mismatches 清单供告警。幂等：重复执行仅追加审计，不改数据。
    """
    conn = _connect(db_path)
    try:
        rows = conn.execute(
            "SELECT prediction_id, rationale_snapshot, snapshot_hash, status FROM predictions"
        ).fetchall()  # noqa: S608
    finally:
        conn.close()

    mismatches: list[dict[str, Any]] = []
    for r in rows:
        expected = r["snapshot_hash"]
        if not expected:
            continue  # 旧数据无哈希列，不判篡改
        try:
            actual = compute_snapshot_hash(json.loads(r["rationale_snapshot"]))
        except (json.JSONDecodeError, TypeError):
            actual = compute_snapshot_hash(r["rationale_snapshot"])
        if actual != expected:
            mismatches.append(
                {"prediction_id": r["prediction_id"], "expected": expected, "actual": actual}
            )
            append_audit(
                r["prediction_id"],
                "integrity_mismatch",
                detail=f"期望 {expected[:12]}… 实际 {actual[:12]}…",
                source="integrity-check",
                db_path=db_path,
            )
    return {"checked": len(rows), "mismatches": mismatches, "mismatch_count": len(mismatches)}


# ── daily_marks：盯市（upsert 幂等；同一 (prediction_id, mark_date) 覆盖重写）──
def insert_daily_mark(
    prediction_id: str,
    mark_date: str,
    mark_price: float | None = None,
    cum_return: float | None = None,
    cum_excess: float | None = None,
    benchmark_price: float | None = None,
    db_path: str | Path | None = None,
) -> None:
    conn = _connect(db_path)
    try:
        conn.execute(
            """INSERT INTO daily_marks (mark_id, prediction_id, mark_date, mark_price,
                 cum_return, cum_excess, benchmark_price)
               VALUES (?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(prediction_id, mark_date) DO UPDATE SET
                 mark_price=excluded.mark_price,
                 cum_return=excluded.cum_return,
                 cum_excess=excluded.cum_excess,
                 benchmark_price=excluded.benchmark_price""",
            (
                f"m_{prediction_id}_{mark_date}",
                prediction_id,
                mark_date,
                mark_price,
                cum_return,
                cum_excess,
                benchmark_price,
            ),
        )
        conn.commit()
    finally:
        conn.close()


def list_daily_marks(
    db_path: str | Path | None = None,
    prediction_id: str | None = None,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    conn = _connect(db_path)
    try:
        sql = "SELECT * FROM daily_marks"  # noqa: S608 — 无外部拼接
        if prediction_id:
            sql += " WHERE prediction_id=?"
        sql += " ORDER BY mark_date ASC"
        if limit is not None:
            sql += " LIMIT ?"
        params: list[Any] = ([prediction_id] if prediction_id else []) + (
            [limit] if limit is not None else []
        )
        rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def prediction_ids_by_direction(direction: str, db_path: str | Path | None = None) -> set[str]:
    """按方向取 prediction_id 集合（组合聚合排除 neutral 用，incident 032 根因 A）。"""
    conn = _connect(db_path)
    try:
        rows = conn.execute(
            "SELECT prediction_id FROM predictions WHERE direction=?", (direction,)
        ).fetchall()
        return {r["prediction_id"] for r in rows}
    finally:
        conn.close()


# ── equity_curve：组合净值（同日期覆盖；幂等）──
def upsert_equity_point(
    curve_date: str,
    agent_nav: float,
    benchmark_nav: float | None = None,
    daily_return: float | None = None,
    trades_count: int | None = None,
    db_path: str | Path | None = None,
) -> None:
    conn = _connect(db_path)
    try:
        conn.execute(
            """INSERT INTO equity_curve (curve_date, agent_nav, benchmark_nav, daily_return, trades_count)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(curve_date) DO UPDATE SET
                 agent_nav=excluded.agent_nav,
                 benchmark_nav=excluded.benchmark_nav,
                 daily_return=excluded.daily_return,
                 trades_count=excluded.trades_count""",
            (curve_date, agent_nav, benchmark_nav, daily_return, trades_count),
        )
        conn.commit()
    finally:
        conn.close()


def list_equity_curve(db_path: str | Path | None = None) -> list[dict[str, Any]]:
    conn = _connect(db_path)
    try:
        rows = conn.execute(
            "SELECT curve_date, agent_nav, benchmark_nav, daily_return, trades_count FROM equity_curve ORDER BY curve_date ASC"
        ).fetchall()  # noqa: S608
        return [dict(r) for r in rows]
    finally:
        conn.close()


# ── index_closes:指数收盘(add-index-performance-compare;展示层对比用,判定链不读)──
def upsert_index_closes(
    rows: list[tuple[str, str, float]],
    db_path: str | Path | None = None,
) -> int:
    """批量幂等写入指数收盘(INSERT OR REPLACE,单事务),返回写入行数。"""
    if not rows:
        return 0
    conn = _connect(db_path)
    try:
        conn.executemany(
            "INSERT OR REPLACE INTO index_closes (index_code, trade_date, close) VALUES (?, ?, ?)",
            rows,
        )
        conn.commit()
        return len(rows)
    finally:
        conn.close()


def list_index_closes(
    index_code: str,
    end: str | None = None,
    db_path: str | Path | None = None,
) -> list[dict[str, Any]]:
    """按指数读收盘序列(trade_date ASC),end 为含右边界;无 start(基期回退需全历史)。"""
    conn = _connect(db_path)
    try:
        if end is None:
            cur = conn.execute(
                "SELECT index_code, trade_date, close FROM index_closes"
                " WHERE index_code = ? ORDER BY trade_date ASC",
                (index_code,),
            )
        else:
            cur = conn.execute(
                "SELECT index_code, trade_date, close FROM index_closes"
                " WHERE index_code = ? AND trade_date <= ? ORDER BY trade_date ASC",
                (index_code, end),
            )
        return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


# ── risk_free_rates:无风险利率日频序列(update-risk-free-rate-source;夏普/α 消费)──
def upsert_risk_free_rates(
    rows: list[tuple[str, float, str]],
    db_path: str | Path | None = None,
) -> int:
    """幂等写入日频无风险利率（rate_date, rate 年化小数, source）；同日覆盖。"""
    if not rows:
        return 0
    conn = _connect(db_path)
    try:
        conn.executemany(
            "INSERT OR REPLACE INTO risk_free_rates (rate_date, rate, source) VALUES (?, ?, ?)",
            rows,
        )
        conn.commit()
        return len(rows)
    finally:
        conn.close()


def list_risk_free_rates(db_path: str | Path | None = None) -> list[dict[str, Any]]:
    """全部无风险利率记录，按 rate_date 升序。"""
    conn = _connect(db_path)
    try:
        cur = conn.execute("SELECT rate_date, rate, source FROM risk_free_rates ORDER BY rate_date")
        return [
            {"rate_date": str(r[0]), "rate": float(r[1]), "source": str(r[2])}
            for r in cur.fetchall()
        ]
    finally:
        conn.close()


def earliest_mark_date(db_path: str | Path | None = None) -> str | None:
    """daily_marks 最早 mark_date；空表返回 None（sync 回填区间起点用）。"""
    conn = _connect(db_path)
    try:
        row = conn.execute("SELECT MIN(mark_date) FROM daily_marks").fetchone()
        return str(row[0]) if row and row[0] else None
    finally:
        conn.close()


# ── agent_metrics_daily：日批指标快照（同日覆盖）──
_METRICS_COLUMNS = (
    "sample_size",
    "settled",
    "win_rate",
    "avg_excess",
    "annual_return",
    "volatility",
    "sharpe",
    "max_drawdown",
    "risk_score",
    "risk_label",
    "beta",
    "jensen_alpha",
)


def upsert_metrics_daily(
    metric_date: str,
    metrics: dict[str, Any],
    db_path: str | Path | None = None,
) -> None:
    """写入/覆盖当日指标快照。metrics 缺省列按默认置空（全列静态 upsert）。"""
    values = []
    for c in _METRICS_COLUMNS:
        v = metrics.get(c)
        if v is None and c in ("sample_size", "settled"):  # NOT NULL 整数列兜底 0
            v = 0
        values.append(v)
    conn = _connect(db_path)
    try:
        conn.execute(
            """INSERT INTO agent_metrics_daily (metric_date, sample_size, settled,
                 win_rate, avg_excess, annual_return, volatility, sharpe,
                 max_drawdown, risk_score, risk_label, beta, jensen_alpha)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(metric_date) DO UPDATE SET
                 sample_size=excluded.sample_size,
                 settled=excluded.settled,
                 win_rate=excluded.win_rate,
                 avg_excess=excluded.avg_excess,
                 annual_return=excluded.annual_return,
                 volatility=excluded.volatility,
                 sharpe=excluded.sharpe,
                 max_drawdown=excluded.max_drawdown,
                 risk_score=excluded.risk_score,
                 risk_label=excluded.risk_label,
                 beta=excluded.beta,
                 jensen_alpha=excluded.jensen_alpha""",
            [metric_date, *values],
        )
        conn.commit()
    finally:
        conn.close()


def get_latest_metrics(db_path: str | Path | None = None) -> dict[str, Any] | None:
    conn = _connect(db_path)
    try:
        row = conn.execute(
            "SELECT * FROM agent_metrics_daily ORDER BY metric_date DESC LIMIT 1"
        ).fetchone()  # noqa: S608
        return dict(row) if row else None
    finally:
        conn.close()


def latest_equity_date(db_path: str | Path | None = None) -> str | None:
    """净值数据最新日期（总览 portfolio.as_of 用，incident 032 诚实性）。"""
    conn = _connect(db_path)
    try:
        row = conn.execute("SELECT MAX(curve_date) AS d FROM equity_curve").fetchone()
        return row["d"] if row else None
    finally:
        conn.close()


def list_metric_dates(db_path: str | Path | None = None) -> list[str]:
    """agent_metrics_daily 全部 metric_date 升序（as-of 历史重算遍历用）。"""
    conn = _connect(db_path)
    try:
        return [
            str(r[0])
            for r in conn.execute(
                "SELECT metric_date FROM agent_metrics_daily ORDER BY metric_date"
            )
        ]
    finally:
        conn.close()


def update_metrics_daily_columns(
    metric_date: str,
    columns: dict[str, float | None],
    db_path: str | Path | None = None,
) -> dict[str, Any]:
    """部分列 UPDATE agent_metrics_daily 指定行；返回前后对照 dict。

    列名白名单由调用方约束（recompute_rf_metrics_history 仅传
    sharpe/beta/jensen_alpha）；行不存在时原样返回不含对照。
    """
    if not columns:
        return {"metric_date": metric_date}
    conn = _connect(db_path)
    try:
        col_names = ", ".join(columns)
        old = conn.execute(
            f"SELECT {col_names} FROM agent_metrics_daily WHERE metric_date = ?",  # noqa: S608
            (metric_date,),
        ).fetchone()
        if old is None:
            return {"metric_date": metric_date}
        sets = ", ".join(f"{c} = ?" for c in columns)
        conn.execute(
            f"UPDATE agent_metrics_daily SET {sets} WHERE metric_date = ?",  # noqa: S608
            (*columns.values(), metric_date),
        )
        conn.commit()
        row: dict[str, Any] = {"metric_date": metric_date}
        for c, o in zip(columns, old, strict=True):
            row[f"old_{c}"] = o
            row[f"new_{c}"] = columns[c]
        return row
    finally:
        conn.close()


def insert_prediction(
    record: dict[str, Any], db_path: str | Path | None = None, status: str = "open"
) -> str:
    """插入一条观点;created_at 服务端生成;快照 JSON 序列化后冻结。

    status 默认 open。参考价(entry_price)不可得不再标 unresolvable:存档 + WARN、
    状态保持 open——判定不依赖参考价,结算入场价届时由行情派生 settle_entry_price
    (delta update-decision-settlement-contract)。真不可判定(长期无行情/停牌退市)
    由判定 job 落 status='unresolvable' + resolution_rule 说明,计入样本但不计入胜率。
    """
    prediction_id = record.get("prediction_id") or f"p_{uuid.uuid4().hex[:12]}"
    created_at = record.get("created_at") or record.get("timestamp") or datetime.now().isoformat()
    version_seq = record.get("version_seq")
    if version_seq is None:
        # 版本关联为增强字段：老库（未建 agents 表）不阻断落库
        try:
            active = get_active_agent(db_path=db_path)
            version_seq = active["version_seq"] if active else None
        except sqlite3.OperationalError:
            version_seq = None
    snapshot = record.get("rationale_snapshot") or {}
    snapshot_hash = record.get("snapshot_hash") or compute_snapshot_hash(snapshot)
    conn = _connect(db_path)
    try:
        conn.execute(
            """INSERT INTO predictions (
                 prediction_id, source_type, symbol, symbol_name, direction,
                 entry_price, target_price, horizon_days, confidence, benchmark,
                 rationale_snapshot, langfuse_trace_id, status, created_at, updated_at,
                 resolution_rule, version_seq, snapshot_hash
               ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                prediction_id,
                record["source_type"],
                record["symbol"],
                record.get("symbol_name"),
                record["direction"],
                record.get("entry_price"),
                record.get("target_price"),
                int(record.get("horizon_days", DEFAULT_HORIZON_DAYS)),
                record.get("confidence"),
                record.get("benchmark", "000300.SH"),
                json.dumps(snapshot, ensure_ascii=False),
                record.get("langfuse_trace_id"),
                status,
                created_at,
                created_at,
                record.get("resolution_rule"),
                version_seq,
                snapshot_hash,
            ),
        )
        conn.commit()
    finally:
        conn.close()
    return prediction_id


def update_prediction_status(
    prediction_id: str,
    resolved: dict[str, Any],
    db_path: str | Path | None = None,
    source: str = "system",
) -> None:
    """判定结果更新。尝试改冻结字段抛 FrozenFieldError;幂等由调用方保证。

    stage-c：任何状态变更写审计日志（old/new status + source）。
    """
    frozen_hits = [f for f in _FROZEN_FIELDS if f in resolved]
    if frozen_hits:
        raise FrozenFieldError(f"attempt to mutate frozen field(s): {frozen_hits}")
    conn = _connect(db_path)
    try:
        row = conn.execute(
            "SELECT * FROM predictions WHERE prediction_id=?", (prediction_id,)
        ).fetchone()
        if row is None:
            return
        fields = [f for f in _MUTABLE_FIELDS if f in resolved]
        if not fields:
            return
        sets = ", ".join(f"{f}=?" for f in fields)
        params = [resolved[f] for f in fields]
        # 列名来自固定常量元组 _MUTABLE_FIELDS，值已参数化
        sql = f"UPDATE predictions SET {sets} WHERE prediction_id=?"  # noqa: S608
        conn.execute(sql, (*params, prediction_id))
        old_status = row["status"]
        new_status = resolved.get("status")
        if new_status is not None and new_status != old_status:
            # 老库无 audit_log 表：审计为增强字段，缺表不阻断判定
            with contextlib.suppress(sqlite3.OperationalError):
                conn.execute(
                    """INSERT INTO audit_log (prediction_id, action, old_status, new_status,
                         detail, source, created_at)
                       VALUES (?, 'status_change', ?, ?, ?, ?, ?)""",
                    (
                        prediction_id,
                        old_status,
                        new_status,
                        resolved.get("resolution_rule"),
                        source,
                        datetime.now().isoformat(),
                    ),
                )
        conn.commit()
    finally:
        conn.close()


# 排序白名单：仅允许这些列参与 ORDER BY（防注入）
_SORT_WHITELIST = {
    "created_at": "created_at",
    "symbol": "symbol",
    "direction": "direction",
    "status": "status",
    "entry_price": "entry_price",
    "exit_price": "exit_price",
    "raw_return": "raw_return",
    "excess_return": "excess_return",
}

# 关键字 → 方向/状态映射（与前端 DIRECTION_LABEL/STATUS_LABEL 一致）
_DIRECTION_LABELS = {"看多": "long", "看空": "short", "中性": "neutral"}
_STATUS_LABELS = {
    "进行中": "open",
    "命中": "resolved_win",
    "未中": "resolved_loss",
    "中性": "resolved_neutral",
    "回避": "avoidance",
    "不可判定": "unresolvable",
}


def _prediction_filters(
    ticker: str | None = None,
    status: str | None = None,
    source_type: str | None = None,
    keyword: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
) -> tuple[list[str], list[Any]]:
    """构造 predictions 查询的 WHERE 子句与参数（list/count 共用）。"""
    clauses: list[str] = []
    params: list[Any] = []
    if ticker:
        clauses.append("symbol LIKE ?")
        params.append(f"%{ticker}%")
    if status:
        if status == "resolved":
            # update-prediction-log-tabs:组值——全部非 open 终态(含未来新增)
            clauses.append("status != 'open'")
        else:
            clauses.append("status = ?")
            params.append(status)
    if source_type:
        clauses.append("source_type = ?")
        params.append(source_type)
    if keyword:
        kw = f"%{keyword}%"
        label_parts = ["(symbol LIKE ? OR symbol_name LIKE ?)"]
        params += [kw, kw]
        if keyword in _DIRECTION_LABELS:
            label_parts.append("direction = ?")
            params.append(_DIRECTION_LABELS[keyword])
        if keyword in _STATUS_LABELS:
            label_parts.append("status = ?")
            params.append(_STATUS_LABELS[keyword])
        clauses.append("(" + " OR ".join(label_parts) + ")")
    if date_from or date_to:
        date_parts: list[str] = []
        if date_from:
            date_parts.append("date(created_at) >= ?")
            params.append(date_from)
        if date_to:
            date_parts.append("date(created_at) <= ?")
            params.append(date_to)
        clauses.append("(" + " AND ".join(date_parts) + ")")
    return clauses, params


def list_predictions(
    ticker: str | None = None,
    status: str | None = None,
    source_type: str | None = None,
    limit: int = 50,
    offset: int = 0,
    db_path: str | Path | None = None,
    sort_by: str | None = None,
    sort_dir: str | None = None,
    keyword: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
) -> list[dict[str, Any]]:
    limit = max(1, min(int(limit), 100))
    conn = _connect(db_path)
    try:
        clauses, params = _prediction_filters(
            ticker, status, source_type, keyword, date_from, date_to
        )
        sql = "SELECT * FROM predictions"
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        col = _SORT_WHITELIST.get(sort_by or "created_at", "created_at")
        direction = "ASC" if (sort_dir or "desc").lower() == "asc" else "DESC"
        sql += f" ORDER BY {col} {direction} LIMIT ? OFFSET ?"
        params += [limit, offset]
        rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def count_predictions(
    ticker: str | None = None,
    status: str | None = None,
    source_type: str | None = None,
    keyword: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    db_path: str | Path | None = None,
) -> int:
    """返回过滤后观点总数（供 API 分页 total 使用）。"""
    conn = _connect(db_path)
    try:
        clauses, params = _prediction_filters(
            ticker, status, source_type, keyword, date_from, date_to
        )
        sql = "SELECT COUNT(*) FROM predictions"
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        return int(conn.execute(sql, params).fetchone()[0])
    finally:
        conn.close()


def get_prediction(prediction_id: str, db_path: str | Path | None = None) -> dict[str, Any] | None:
    conn = _connect(db_path)
    try:
        row = conn.execute(
            "SELECT * FROM predictions WHERE prediction_id=?", (prediction_id,)
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def prediction_stats(
    source_type: str | None = None,
    db_path: str | Path | None = None,
    version_seq: int | None = None,
    horizon_days: int | None = None,
) -> dict[str, Any]:
    """胜率/超额聚合。胜率只基于 long/short 的 resolved_win/resolved_loss;
    neutral/unresolvable 不进分母。avg_excess 人口 = long/short 三态
    (resolved_win/loss/neutral,排除 unresolvable)——消除 ±2% 中性带截断偏差
    (口径 metrics.md §1.9①);neutral 方向观点不属于主指标人口,一律排除。

    horizon_days 缺省不过滤(全量存量);传值只统计该窗口(头条口径 DEFAULT_HORIZON_DAYS),
    切点前后已结算行不混入同一读数(口径切点分段)。
    stage-c：version_seq 过滤用于分段封存（P6），缺省统计全部版本。
    """
    conn = _connect(db_path)
    try:
        cond = ""
        params: list[Any] = []
        if source_type:
            cond += " AND source_type=?"
            params.append(source_type)
        if version_seq is not None:
            cond += " AND version_seq=?"
            params.append(version_seq)
        if horizon_days is not None:
            cond += " AND horizon_days=?"
            params.append(horizon_days)
        where = " WHERE 1=1" + cond
        src_cond = cond  # 除 COUNT(*) 无 WHERE 的查询外，条件部分相同
        ls = " AND direction IN ('long','short')"  # 主指标人口限可执行决策(§1.9①)
        # where/src_cond 均为固定字面量拼装 + 值参数化
        q_total = f"SELECT COUNT(*) FROM predictions{where}"  # noqa: S608
        q_open = f"SELECT COUNT(*) FROM predictions WHERE status='open'{src_cond}"  # noqa: S608
        q_win = f"SELECT COUNT(*) FROM predictions WHERE status='resolved_win'{ls}{src_cond}"  # noqa: S608
        q_loss = f"SELECT COUNT(*) FROM predictions WHERE status='resolved_loss'{ls}{src_cond}"  # noqa: S608
        q_avg = f"SELECT AVG(excess_return) FROM predictions WHERE status IN ('resolved_win','resolved_loss','resolved_neutral'){ls}{src_cond}"  # noqa: S608
        q_counts = f"SELECT status, COUNT(*) FROM predictions{where} GROUP BY status"  # noqa: S608
        total = int(conn.execute(q_total, params).fetchone()[0])
        open_count = int(conn.execute(q_open, params).fetchone()[0])
        wins = int(conn.execute(q_win, params).fetchone()[0])
        losses = int(conn.execute(q_loss, params).fetchone()[0])
        settled = wins + losses
        win_rate = round(wins / settled, 4) if settled else None
        avg_exc = conn.execute(q_avg, params).fetchone()[0]
        avg_excess = round(float(avg_exc), 4) if avg_exc is not None else None
        return {
            "total": total,
            "open": open_count,
            "settled": settled,
            "win_rate": win_rate,
            "avg_excess": avg_excess,
            "status_counts": dict(conn.execute(q_counts, params).fetchall()),
        }
    finally:
        conn.close()


def avoidance_stats(
    source_type: str | None = None,
    db_path: str | Path | None = None,
    version_seq: int | None = None,
) -> dict[str, Any]:
    """neutral 观点回避正确率：avoidance_win/(avoidance_win+avoidance_loss)。

    avoidance_neutral 与未判定(avoidance_status IS NULL)不进分母、不计 settled;
    分母为 0 → avoidance_rate=None。作为独立辅助指标(§1.9②),SHALL NOT 混入胜率。
    """
    conn = _connect(db_path)
    try:
        cond = ""
        params: list[Any] = []
        if source_type:
            cond += " AND source_type=?"
            params.append(source_type)
        if version_seq is not None:
            cond += " AND version_seq=?"
            params.append(version_seq)
        rows = conn.execute(
            f"SELECT avoidance_status, COUNT(*) FROM predictions WHERE avoidance_status IS NOT NULL{cond} GROUP BY avoidance_status",  # noqa: S608
            params,
        ).fetchall()
        counts = dict(rows)
        wins = int(counts.get("avoidance_win", 0))
        losses = int(counts.get("avoidance_loss", 0))
        settled = wins + losses
        return {
            "avoidance_win": wins,
            "avoidance_loss": losses,
            "avoidance_neutral": int(counts.get("avoidance_neutral", 0)),
            "settled": settled,
            "avoidance_rate": round(wins / settled, 4) if settled else None,
        }
    finally:
        conn.close()


def migrate_decision_log(db_path: str | Path | None = None) -> int:
    """一次性迁移 decision_log → predictions(方向映射 + 最小快照)。幂等:重复执行跳过已迁移。

    首迁时给 decision_log 加 prediction_id_migrated 列作为迁移标记;之后重复执行只迁未迁移行。
    """
    conn = _connect(db_path)
    migrated = 0
    try:
        try:
            rows = conn.execute(
                "SELECT * FROM decision_log WHERE prediction_id_migrated IS NULL"
            ).fetchall()
        except sqlite3.OperationalError:
            conn.execute("ALTER TABLE decision_log ADD COLUMN prediction_id_migrated TEXT")
            conn.commit()
            rows = conn.execute(
                "SELECT * FROM decision_log WHERE prediction_id_migrated IS NULL"
            ).fetchall()
        for r in rows:
            action = r["action"]
            direction = direction_for_action(action)
            pid = f"p_{uuid.uuid4().hex[:12]}"
            snapshot = json.dumps(
                {
                    "migrated_from": "decision_log",
                    "decision_id": r["decision_id"],
                    "action": action,
                },
                ensure_ascii=False,
            )
            conn.execute(
                """INSERT INTO predictions (
                     prediction_id, source_type, symbol, symbol_name, direction,
                     entry_price, target_price, horizon_days, confidence, benchmark,
                     rationale_snapshot, langfuse_trace_id, status, created_at, updated_at
                   ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?, 'open', ?, ?)""",
                (
                    pid,
                    "live",
                    f"{r['ticker']}.SH"
                    if str(r["ticker"]).startswith("6")
                    else f"{r['ticker']}.SZ",
                    r["name"],
                    direction,
                    float(r["entry_price"]),
                    r["target_price"],
                    # 存量不追溯：迁移行按其原语义窗口（252），不吃新默认窗口 20
                    MAX_HORIZON_DAYS,
                    r["confidence"],
                    "000300.SH",
                    snapshot,
                    r["langfuse_trace_id"],
                    r["timestamp"],
                    r["timestamp"],
                ),
            )
            conn.execute(
                "UPDATE decision_log SET prediction_id_migrated=? WHERE decision_id=?",
                (pid, r["decision_id"]),
            )
            migrated += 1
        conn.commit()
    finally:
        conn.close()
    return migrated


def recent_directions(
    stock_code: str,
    window_days: int = 5,
    db_path: str | Path | None = None,
    today: str | None = None,
) -> list[dict[str, Any]]:
    """近窗历史决策方向序列（add-decision-hysteresis 滞回数据源，只读）。

    返回窗口内该标的按时间升序的 {"date", "direction", "confidence"} 列表；
    symbol 映射与 ingest 同款（6 开头 .SH、否则 .SZ）。fail-open：DB 异常/库
    不可用返回 []——滞回不生效，MUST NOT 因历史查询炸决策管线。
    """
    symbol = f"{stock_code}.SH" if str(stock_code).startswith("6") else f"{stock_code}.SZ"
    today = today or datetime.now().strftime("%Y-%m-%d")
    try:
        conn = _connect(db_path)
    except Exception:  # noqa: BLE001 -- fail-open（查询不可用 ≠ 决策不可行）
        return []
    try:
        rows = conn.execute(
            """SELECT substr(created_at, 1, 10) AS d, direction, confidence
               FROM predictions
               WHERE symbol = ? AND d >= date(?, ?)
               ORDER BY created_at ASC""",
            (symbol, today, f"-{int(window_days)} days"),
        ).fetchall()
        return [
            {"date": d, "direction": direction, "confidence": confidence}
            for d, direction, confidence in rows
        ]
    except Exception:  # noqa: BLE001 -- fail-open 同上
        return []
    finally:
        with contextlib.suppress(Exception):
            conn.close()
