"""分析师事后检验存储（add-analyst-ex-post-verification）。

三张表，独立于 ``research_reports`` 的业务表但共用同一个库：

- ``analyst_claims``：事件式卖方主张（评级变动 / EPS 修正 / 目标价变动）；
- ``analyst_claim_outcomes``：逐主张结算结果（pending/resolved/unresolved）；
- ``analyst_scorecard_snapshots``：按日分桶记分卡与机构权重快照。

连接纪律沿用 ``research_reports_store``：WAL + busy_timeout + 短连接 + 调用期读 env。
"""

from __future__ import annotations

import json
import os
import sqlite3
from collections.abc import Iterable
from datetime import datetime
from pathlib import Path
from typing import Any

DEFAULT_DB_PATH = "data/research_reports.db"

ANALYST_CLAIMS_DDL = """
CREATE TABLE IF NOT EXISTS analyst_claims (
  claim_key        TEXT PRIMARY KEY,
  claim_type       TEXT NOT NULL,
  code             TEXT NOT NULL,
  org              TEXT NOT NULL,
  analyst          TEXT,
  published_at     TEXT NOT NULL,
  rating           TEXT,
  prev_rating      TEXT,
  target_price     REAL,
  prev_target_price REAL,
  eps_year         INTEGER,
  eps_value        REAL,
  prev_eps_value   REAL,
  horizon_days     INTEGER NOT NULL,
  industry         TEXT,
  region           TEXT NOT NULL DEFAULT 'domestic',
  depth_class      TEXT,
  source           TEXT NOT NULL,
  created_at       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ac_code_date ON analyst_claims(code, published_at);
CREATE INDEX IF NOT EXISTS idx_ac_org_date ON analyst_claims(org, published_at);
CREATE INDEX IF NOT EXISTS idx_ac_type_date ON analyst_claims(claim_type, published_at);

CREATE TABLE IF NOT EXISTS analyst_claim_outcomes (
  claim_key        TEXT PRIMARY KEY,
  status           TEXT NOT NULL DEFAULT 'pending',
  entry_date       TEXT,
  entry_price      REAL,
  exit_date        TEXT,
  exit_price       REAL,
  raw_return       REAL,
  benchmark_return REAL,
  excess_return    REAL,
  reason           TEXT,
  resolved_at      TEXT,
  updated_at       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_aco_status ON analyst_claim_outcomes(status);

CREATE TABLE IF NOT EXISTS analyst_scorecard_snapshots (
  snapshot_date  TEXT NOT NULL,
  bucket_type    TEXT NOT NULL,
  bucket_key     TEXT NOT NULL,
  n              INTEGER NOT NULL,
  wins           INTEGER NOT NULL,
  win_rate       REAL,
  mean_excess    REAL,
  median_excess  REAL,
  reliable       INTEGER NOT NULL,
  weight         REAL,
  tier           TEXT,
  region         TEXT NOT NULL DEFAULT 'domestic',
  score_family   TEXT NOT NULL DEFAULT 'event_study',
  weighted_mean_excess REAL,
  deep_n         INTEGER,
  updated_at     TEXT NOT NULL,
  PRIMARY KEY (snapshot_date, bucket_type, bucket_key)
);
CREATE INDEX IF NOT EXISTS idx_ass_type ON analyst_scorecard_snapshots(bucket_type);
"""


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _default_db_path() -> Path:
    return Path(os.getenv("RESEARCH_REPORTS_DB_PATH", DEFAULT_DB_PATH))


def _connect(db_path: str | Path | None) -> sqlite3.Connection:
    path = Path(db_path) if db_path else _default_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def init_db(db_path: str | Path | None = None) -> Path:
    path = Path(db_path) if db_path else _default_db_path()
    conn = _connect(path)
    try:
        conn.executescript(ANALYST_CLAIMS_DDL)
        _ensure_columns(conn)
        conn.commit()
    finally:
        conn.close()
    return path


# add-region-split-broker-scoring：旧库补 region/score_family/depth 派生列。
_CLAIM_MIGRATIONS: dict[str, dict[str, str]] = {
    "analyst_claims": {
        "region": "TEXT NOT NULL DEFAULT 'domestic'",
        "depth_class": "TEXT",
    },
    "analyst_scorecard_snapshots": {
        "region": "TEXT NOT NULL DEFAULT 'domestic'",
        "score_family": "TEXT NOT NULL DEFAULT 'event_study'",
        "weighted_mean_excess": "REAL",
        "deep_n": "INTEGER",
    },
}


def _ensure_columns(conn: sqlite3.Connection) -> None:
    for table, columns in _CLAIM_MIGRATIONS.items():
        existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
        for name, ddl in columns.items():
            if name not in existing:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}")  # noqa: S608


def upsert_claims(rows: Iterable[dict], db_path: str | Path | None = None) -> int:
    """幂等写入主张；同键已存在不覆盖（首次生成即事实）。返回处理行数。"""
    init_db(db_path)
    now = _now_iso()
    payload: list[tuple] = []
    for row in rows:
        claim_key = str(row.get("claim_key") or "").strip()
        code = str(row.get("code") or "").strip()
        org = str(row.get("org") or "").strip()
        published_at = str(row.get("published_at") or "").strip()
        claim_type = str(row.get("claim_type") or "").strip()
        if not claim_key or not code or not org or not published_at or not claim_type:
            continue
        payload.append(
            (
                claim_key,
                claim_type,
                code,
                org,
                (row.get("analyst") or None),
                published_at,
                (row.get("rating") or None),
                (row.get("prev_rating") or None),
                row.get("target_price"),
                row.get("prev_target_price"),
                row.get("eps_year"),
                row.get("eps_value"),
                row.get("prev_eps_value"),
                int(row.get("horizon_days") or 20),
                (row.get("industry") or None),
                (row.get("region") or "domestic"),
                (row.get("depth_class") or None),
                (row.get("source") or "unknown"),
                now,
            )
        )
    if not payload:
        return 0
    conn = _connect(db_path)
    try:
        conn.executemany(
            """
            INSERT OR IGNORE INTO analyst_claims (
              claim_key, claim_type, code, org, analyst, published_at, rating,
              prev_rating, target_price, prev_target_price, eps_year, eps_value,
              prev_eps_value, horizon_days, industry, region, depth_class, source, created_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            payload,
        )
        conn.commit()
        return len(payload)
    finally:
        conn.close()


def upsert_outcome(
    claim_key: str,
    *,
    status: str,
    entry_date: str | None,
    entry_price: float | None,
    exit_date: str | None,
    exit_price: float | None,
    raw_return: float | None,
    benchmark_return: float | None,
    excess_return: float | None,
    reason: str | None,
    db_path: str | Path | None = None,
) -> None:
    """写入/覆盖一条结算结果。status ∈ pending/resolved/unresolved。"""
    if status not in ("pending", "resolved", "unresolved"):
        raise ValueError(f"非法结算状态: {status!r}")
    init_db(db_path)
    now = _now_iso()
    conn = _connect(db_path)
    try:
        conn.execute(
            """
            INSERT INTO analyst_claim_outcomes (
              claim_key, status, entry_date, entry_price, exit_date, exit_price,
              raw_return, benchmark_return, excess_return, reason, resolved_at, updated_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(claim_key) DO UPDATE SET
              status=excluded.status,
              entry_date=excluded.entry_date,
              entry_price=excluded.entry_price,
              exit_date=excluded.exit_date,
              exit_price=excluded.exit_price,
              raw_return=excluded.raw_return,
              benchmark_return=excluded.benchmark_return,
              excess_return=excluded.excess_return,
              reason=excluded.reason,
              resolved_at=excluded.resolved_at,
              updated_at=excluded.updated_at
            """,
            (
                claim_key,
                status,
                entry_date,
                entry_price,
                exit_date,
                exit_price,
                raw_return,
                benchmark_return,
                excess_return,
                reason,
                now if status in ("resolved", "unresolved") else None,
                now,
            ),
        )
        conn.commit()
    finally:
        conn.close()


def list_pending_claims(
    *, as_of: str | None = None, db_path: str | Path | None = None
) -> list[dict]:
    """返回尚未结算（无结果行或 status=pending）且已发布的主张。"""
    init_db(db_path)
    conn = _connect(db_path)
    try:
        sql = (
            "SELECT c.* FROM analyst_claims c"
            " LEFT JOIN analyst_claim_outcomes o ON o.claim_key = c.claim_key"
            " WHERE (o.claim_key IS NULL OR o.status = 'pending')"
        )
        params: list[Any] = []
        if as_of:
            sql += " AND c.published_at <= ?"
            params.append(as_of)
        sql += " ORDER BY c.published_at ASC, c.claim_key ASC"
        rows = conn.execute(sql, params).fetchall()
    finally:
        conn.close()
    return [dict(row) for row in rows]


def list_resolved(db_path: str | Path | None = None) -> list[dict]:
    """返回已结算主张 + 结果（用于记分卡）。"""
    init_db(db_path)
    conn = _connect(db_path)
    try:
        rows = conn.execute(
            """
            SELECT c.*, o.status AS outcome_status, o.entry_date, o.entry_price,
                   o.exit_date, o.exit_price, o.raw_return, o.benchmark_return,
                   o.excess_return, o.reason
            FROM analyst_claims c
            JOIN analyst_claim_outcomes o ON o.claim_key = c.claim_key
            WHERE o.status = 'resolved'
            ORDER BY c.published_at ASC, c.claim_key ASC
            """
        ).fetchall()
    finally:
        conn.close()
    return [dict(row) for row in rows]


def get_outcome(claim_key: str, db_path: str | Path | None = None) -> dict | None:
    init_db(db_path)
    conn = _connect(db_path)
    try:
        row = conn.execute(
            "SELECT * FROM analyst_claim_outcomes WHERE claim_key=?", (claim_key,)
        ).fetchone()
    finally:
        conn.close()
    return dict(row) if row else None


def replace_scorecard(
    snapshot_date: str, rows: Iterable[dict], db_path: str | Path | None = None
) -> int:
    """同日同键幂等替换记分卡快照。"""
    init_db(db_path)
    now = _now_iso()
    payload = [
        (
            snapshot_date,
            str(row["bucket_type"]),
            str(row["bucket_key"]),
            int(row.get("n") or 0),
            int(row.get("wins") or 0),
            row.get("win_rate"),
            row.get("mean_excess"),
            row.get("median_excess"),
            1 if row.get("reliable") else 0,
            row.get("weight"),
            row.get("tier"),
            row.get("region") or "domestic",
            row.get("score_family") or "event_study",
            row.get("weighted_mean_excess"),
            row.get("deep_n"),
            now,
        )
        for row in rows
    ]
    conn = _connect(db_path)
    try:
        conn.execute(
            "DELETE FROM analyst_scorecard_snapshots WHERE snapshot_date=?", (snapshot_date,)
        )
        if payload:
            conn.executemany(
                """
                INSERT INTO analyst_scorecard_snapshots (
                  snapshot_date, bucket_type, bucket_key, n, wins, win_rate,
                  mean_excess, median_excess, reliable, weight, tier, region,
                  score_family, weighted_mean_excess, deep_n, updated_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                payload,
            )
        conn.commit()
        return len(payload)
    finally:
        conn.close()


def latest_snapshot_date(db_path: str | Path | None = None) -> str | None:
    init_db(db_path)
    conn = _connect(db_path)
    try:
        row = conn.execute("SELECT MAX(snapshot_date) FROM analyst_scorecard_snapshots").fetchone()
    finally:
        conn.close()
    return row[0] if row and row[0] else None


def list_scorecard(
    *,
    snapshot_date: str | None = None,
    bucket_type: str | None = None,
    bucket_key: str | None = None,
    db_path: str | Path | None = None,
) -> list[dict]:
    """读取记分卡；未指定日期时取最新快照。"""
    init_db(db_path)
    date_value = snapshot_date or latest_snapshot_date(db_path)
    if not date_value:
        return []
    sql = "SELECT * FROM analyst_scorecard_snapshots WHERE snapshot_date=?"
    params: list[Any] = [date_value]
    if bucket_type:
        sql += " AND bucket_type=?"
        params.append(bucket_type)
    if bucket_key:
        sql += " AND bucket_key=?"
        params.append(bucket_key)
    sql += " ORDER BY bucket_type ASC, bucket_key ASC"
    conn = _connect(db_path)
    try:
        rows = conn.execute(sql, params).fetchall()
    finally:
        conn.close()
    return [dict(row) for row in rows]


def record_run(
    job: str,
    window_start: str | None,
    window_end: str | None,
    status: str,
    rows: int,
    detail: str | None = None,
    db_path: str | Path | None = None,
) -> None:
    """复用 ``collection_runs`` 记录运行；不存在时由 research_reports_store 建表。"""
    from finance_agent.data import research_reports_store

    research_reports_store.record_run(
        job, window_start, window_end, status, rows, detail=detail, db_path=db_path
    )


def detail_json(**kwargs: Any) -> str:
    return json.dumps(kwargs, ensure_ascii=False, sort_keys=True)
