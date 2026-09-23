"""cohort 跑批记账（delta add-forward-paper-trading-cohort）。

独立于 predictions（观点表零改动）；同库（data/sessions.db）便于 join 导出。
幂等键 = (universe_version, ticker, trade_date, run_seq)：同日重复触发跳过（不写行），
显式 force 以 run_seq 递增另记一行。db_path 调用期注入（SESSIONS_DB_PATH 缺省）。
"""

from __future__ import annotations

import os
import sqlite3
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

COHORT_RUNS_DDL = """
CREATE TABLE IF NOT EXISTS cohort_runs (
  run_id            TEXT PRIMARY KEY,
  universe_version  TEXT NOT NULL,
  ticker            TEXT NOT NULL,
  trade_date        TEXT NOT NULL,
  session_id        TEXT,
  langfuse_trace_id TEXT,
  status            TEXT NOT NULL CHECK (status IN ('success','failure','skipped')),
  failure_reason    TEXT,
  run_seq           INTEGER NOT NULL DEFAULT 0,
  llm_calls         INTEGER,
  tokens_prompt     INTEGER,
  tokens_completion INTEGER,
  tokens_total      INTEGER,
  trigger_time      TEXT NOT NULL,
  created_at        TEXT NOT NULL,
  UNIQUE (universe_version, ticker, trade_date, run_seq)
);
CREATE INDEX IF NOT EXISTS idx_cohort_runs_key ON cohort_runs(universe_version, trade_date);
"""


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


def init_cohort_runs(db_path: str | Path | None = None) -> None:
    """建 cohort_runs 表（若缺）；幂等，可重复调用。"""
    conn = _connect(db_path)
    try:
        conn.executescript(COHORT_RUNS_DDL)
        conn.commit()
    finally:
        conn.close()


def insert_cohort_run(record: dict[str, Any], db_path: str | Path | None = None) -> str:
    """插入一条跑批记账；``run_id`` / ``created_at`` 默认服务端生成。返回 run_id。

    ``record["run_id"]`` 若显式提供则**覆盖**服务端生成值（实现为
    ``record.get("run_id") or uuid4().hex``）；runner 从不传该键，实际恒为
    服务端生成。``created_at`` 恒服务端生成（不接受 record 覆盖）。

    同 key 同 run_seq 重复插入由 UNIQUE 约束拒绝（sqlite3.IntegrityError）。
    """
    run_id = record.get("run_id") or uuid.uuid4().hex
    created_at = datetime.now().isoformat()
    conn = _connect(db_path)
    try:
        conn.execute(
            """INSERT INTO cohort_runs (
                 run_id, universe_version, ticker, trade_date, session_id,
                 langfuse_trace_id, status, failure_reason, run_seq, llm_calls,
                 tokens_prompt, tokens_completion, tokens_total, trigger_time, created_at
               ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                run_id,
                record["universe_version"],
                record["ticker"],
                record["trade_date"],
                record.get("session_id"),
                record.get("langfuse_trace_id"),
                record["status"],
                record.get("failure_reason"),
                int(record.get("run_seq", 0)),
                record.get("llm_calls"),
                record.get("tokens_prompt"),
                record.get("tokens_completion"),
                record.get("tokens_total"),
                record["trigger_time"],
                created_at,
            ),
        )
        conn.commit()
    finally:
        conn.close()
    return run_id


def list_cohort_runs(
    *,
    universe_version: str | None = None,
    trade_date: str | None = None,
    since: str | None = None,
    db_path: str | Path | None = None,
) -> list[dict[str, Any]]:
    """列出记账行（可选过滤）；ORDER BY trade_date, ticker, run_seq。

    `trade_date` 为精确匹配日；`since` 为 trade_date 下界（含），供时间窗导出。
    """
    clauses: list[str] = []
    params: list[Any] = []
    if universe_version:
        clauses.append("universe_version = ?")
        params.append(universe_version)
    if trade_date:
        clauses.append("trade_date = ?")
        params.append(trade_date)
    if since:
        clauses.append("trade_date >= ?")
        params.append(since)
    sql = "SELECT * FROM cohort_runs"
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY trade_date, ticker, run_seq"
    conn = _connect(db_path)
    try:
        rows = conn.execute(sql, params).fetchall()  # noqa: S608 — 子句为固定字面量 + 值参数化
        return [dict(r) for r in rows]
    finally:
        conn.close()
