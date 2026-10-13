"""screening 库：L2 名单快照与 L3 triage 决策持久化（add-screening-candidate-llm T1/T2）。

表（C5，逐字）：

- ``triage_decisions(trade_date, symbol, verdict, reason, degraded, created_at,
  PRIMARY KEY(trade_date, symbol))``：L3 keep/drop 决策，幂等 upsert（同日重跑覆盖）；
  供消融（±L3 粗筛）与前向命中率分层统计查询（后续 change）。

连接纪律与 ``data/snapshot.py`` 同款：WAL + busy_timeout 短连接，库路径调用期读取
``SCREENING_DB_PATH``（默认 ``data/screening.db``），测试经 ``db_path`` 显式注入。
"""

from __future__ import annotations

import os
import sqlite3
from datetime import date, datetime
from pathlib import Path

from finance_agent.screening.candidate_llm import VERDICTS, TriageDecision

# 表 DDL（幂等 CREATE IF NOT EXISTS；列名与 C5 逐字一致）
SCREENING_DDL = """
CREATE TABLE IF NOT EXISTS triage_decisions (
  trade_date TEXT NOT NULL,
  symbol     TEXT NOT NULL,
  verdict    TEXT NOT NULL,
  reason     TEXT NOT NULL,
  degraded   INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL,
  PRIMARY KEY (trade_date, symbol)
);
CREATE INDEX IF NOT EXISTS idx_triage_decisions_symbol_date
  ON triage_decisions (symbol, trade_date);
"""


# ── 连接纪律（与 data/snapshot.py 同款） ────────────────────────────────────


def _default_db_path() -> Path:
    """默认 screening 库路径；调用期读取 env（测试/SOP 经 SCREENING_DB_PATH 覆盖）。"""
    return Path(os.getenv("SCREENING_DB_PATH", "data/screening.db"))


def _connect(db_path: str | Path | None) -> sqlite3.Connection:
    """短连接（snapshot.py 同款纪律）：WAL + busy_timeout，row_factory=Row。"""
    path = Path(db_path) if db_path else _default_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, check_same_thread=False, timeout=15.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=15000")
    return conn


def init_screening_db(db_path: str | Path | None = None) -> Path:
    """幂等建表，返回实际使用的库路径。"""
    path = Path(db_path) if db_path else _default_db_path()
    conn = _connect(path)
    try:
        conn.executescript(SCREENING_DDL)
        conn.commit()
    finally:
        conn.close()
    return path


def _parse_trade_date(value: str) -> str:
    """交易日严格规范 ``YYYY-MM-DD``（字典序比较/序列依赖规范形态）。"""
    try:
        parsed = date.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"非规范交易日: {value!r}，须为 YYYY-MM-DD") from exc
    if parsed.isoformat() != value:
        raise ValueError(f"非规范交易日: {value!r}，须为 YYYY-MM-DD")
    return value


# ── triage 决策（C5 决策部分） ──────────────────────────────────────────────


def record_triage_decision(
    trade_date: str, decision: TriageDecision, db_path: str | Path | None = None
) -> None:
    """写入某标的某交易日的 L3 决策；同 ``(trade_date, symbol)`` **幂等**覆盖（upsert）。

    ``verdict`` 必须 ∈ ``candidate_llm.VERDICTS``（合同单一来源），非法值 fail loud
    （不落坏值）；``created_at`` 为写入时刻（同日重跑覆盖为最新）。
    """
    normalized = _parse_trade_date(trade_date)
    if decision.verdict not in VERDICTS:
        raise ValueError(f"非法 verdict {decision.verdict!r}；允许: {list(VERDICTS)}")
    init_screening_db(db_path)
    now = datetime.now().isoformat()
    conn = _connect(db_path)
    try:
        conn.execute(
            "INSERT INTO triage_decisions "
            "(trade_date, symbol, verdict, reason, degraded, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(trade_date, symbol) DO UPDATE SET "
            "verdict = excluded.verdict, reason = excluded.reason, "
            "degraded = excluded.degraded, created_at = excluded.created_at",
            (
                normalized,
                decision.symbol,
                decision.verdict,
                decision.reason,
                int(decision.degraded),
                now,
            ),
        )
        conn.commit()
    finally:
        conn.close()
