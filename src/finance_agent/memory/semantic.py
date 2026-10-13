"""语义记忆（add-decision-memory）：月批提炼候选经验条款 + 人工确认门禁。

存储复用 outcome track-record 库（同 `SESSIONS_DB_PATH`，本 change 文档化决策：
记忆源自该库，择一不建第二库）。状态机 ``pending → confirmed | rejected``；
**未确认条款 SHALL NOT 出现在任何 prompt**——注入侧只读 confirmed 列表，
prompt 组装路径无第二数据源（门禁测试：全组装路径扫描未确认文本零命中）。

本阶段（T1）只含：幂等 DDL、连接、confirmed 读取（注入块的语义段数据源）；
提炼/落库/确认（T2）随后补齐。
"""

from __future__ import annotations

import contextlib
import logging
import os
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

CLAUSE_STATUSES: tuple[str, ...] = ("pending", "confirmed", "rejected")

CLAUSES_DDL = """
CREATE TABLE IF NOT EXISTS memory_clauses (
  clause_id    TEXT PRIMARY KEY,
  text         TEXT NOT NULL,
  status       TEXT NOT NULL DEFAULT 'pending'
               CHECK (status IN ('pending','confirmed','rejected')),
  source       TEXT NOT NULL DEFAULT 'monthly_batch',
  evidence     TEXT,
  created_at   TEXT NOT NULL,
  updated_at   TEXT NOT NULL,
  confirmed_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_memory_clauses_status ON memory_clauses(status);
"""


def _now_iso() -> str:
    """行写入时刻（updated_at 专用；不复用确认日期——语义不同源）。"""
    return datetime.now(UTC).isoformat()


def _db_path(db_path: str | Path | None) -> Path:
    if db_path is not None:
        return Path(db_path)
    return Path(os.getenv("SESSIONS_DB_PATH", "data/sessions.db"))


def _table_exists(conn: sqlite3.Connection) -> bool:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='memory_clauses'"
    ).fetchone()
    return row is not None


def init_clauses(db_path: str | Path | None = None) -> None:
    """幂等建表（写入路径专用；读路径不建表、缺表 → 空）。"""
    conn = _connect(db_path)
    try:
        conn.executescript(CLAUSES_DDL)
        conn.commit()
    finally:
        conn.close()


def _connect(db_path: str | Path | None) -> sqlite3.Connection:
    """连接纪律同 track-record 库（WAL + busy_timeout 短连接，调用期读默认路径）。"""
    from finance_agent.outcome.track_record.model import _connect as _track_record_connect

    return _track_record_connect(_db_path(db_path))


def _read_clauses(
    status: str | None = None, *, db_path: str | Path | None = None
) -> list[dict[str, Any]]:
    """只读条款列表（缺库/缺表 → []，fail-open）；按创建时间升序（稳定）。"""
    path = _db_path(db_path)
    if not path.exists():
        return []
    try:
        conn = _connect(path)
    except Exception:  # noqa: BLE001 -- fail-open
        logger.warning("语义记忆库连接失败（fail-open 空条款）: %s", path, exc_info=True)
        return []
    try:
        if not _table_exists(conn):
            return []
        sql = "SELECT * FROM memory_clauses"
        params: list[Any] = []
        if status:
            sql += " WHERE status = ?"
            params.append(status)
        sql += " ORDER BY created_at ASC, rowid ASC"
        return [dict(r) for r in conn.execute(sql, params).fetchall()]
    except sqlite3.OperationalError:
        logger.warning("语义条款查询失败（fail-open 空条款）", exc_info=True)
        return []
    finally:
        with contextlib.suppress(Exception):
            conn.close()


def list_confirmed_clauses(db_path: str | Path | None = None) -> list[dict[str, Any]]:
    """已确认条款（唯一注入源；未确认永不进入任何 prompt 组装路径）。"""
    return _read_clauses("confirmed", db_path=db_path)


def list_pending_clauses(db_path: str | Path | None = None) -> list[dict[str, Any]]:
    """待人工确认的候选条款（月批产出）。"""
    return _read_clauses("pending", db_path=db_path)
