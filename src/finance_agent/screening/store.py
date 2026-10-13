"""screening 库：L2 名单快照、L3 triage 决策与在榜稳定性查询（add-screening-candidate-llm T1/T2）。

表（C5，逐字）：

- ``l2_candidates(trade_date, symbol, composite_score, rank, PRIMARY KEY(trade_date, symbol))``：
  L2 打分名单日快照，幂等 upsert（同日重跑覆盖）；
- ``triage_decisions(trade_date, symbol, verdict, reason, degraded, created_at,
  PRIMARY KEY(trade_date, symbol))``：L3 keep/drop 决策，幂等 upsert（同日重跑覆盖）。
  两表供消融（±L3 粗筛）与前向命中率分层（后续 change）查询，是稳定性门槛查询的
  唯一数据源。

稳定性口径（C4）：``consecutive_days_in_top`` 从 ``as_of`` 起向前沿**表内交易日
轴**（``l2_candidates`` 的 DISTINCT ``trade_date``）逐日查该 symbol 是否在榜，
返回尾部连续段长度；``as_of`` 当日不在榜（含当日无任何榜单行）→ 0。表里只有
交易日行 → 非交易日（周末/节假日）天然不在轴内、不打断连续段；中间断档（某交易
日榜单存在但该 symbol 不在）→ 重新计数。``eligible_for_selection`` = 计数 ≥
``min_days``（默认 3，可配置）。

连接纪律与 ``data/snapshot.py`` 同款：WAL + busy_timeout 短连接，库路径调用期读取
``SCREENING_DB_PATH``（默认 ``data/screening.db``），测试经 ``db_path`` 显式注入。
"""

from __future__ import annotations

import math
import os
import sqlite3
from collections.abc import Sequence
from datetime import date, datetime
from pathlib import Path
from typing import Any

from finance_agent.screening.candidate_llm import VERDICTS, TriageDecision
from finance_agent.screening.ranker import Candidate

# 表 DDL（幂等 CREATE IF NOT EXISTS；列名与 C5 逐字一致）
SCREENING_DDL = """
CREATE TABLE IF NOT EXISTS l2_candidates (
  trade_date      TEXT NOT NULL,
  symbol          TEXT NOT NULL,
  composite_score REAL NOT NULL,
  rank            INTEGER NOT NULL,
  PRIMARY KEY (trade_date, symbol)
);
CREATE INDEX IF NOT EXISTS idx_l2_candidates_date_rank
  ON l2_candidates (trade_date, rank);
CREATE INDEX IF NOT EXISTS idx_l2_candidates_symbol_date
  ON l2_candidates (symbol, trade_date);
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

# 稳定性门槛默认值（Global Constraints：默认连续 3 个交易日）
DEFAULT_MIN_DAYS = 3


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
    """幂等建两表 + 索引，返回实际使用的库路径。"""
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


def _finite_float(value: Any, field: str) -> float:
    """数值入参 → float；None/NaN/±inf/不可解析 → ``ValueError``（不落坏值）。"""
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"{field} 必须是有限数值: {value!r}") from None
    if not math.isfinite(number):
        raise ValueError(f"{field} 必须是有限数值: {value!r}")
    return number


# ── L2 名单快照（C5） ───────────────────────────────────────────────────────


def record_l2_candidates(
    trade_date: str, candidates: Sequence[Candidate], db_path: str | Path | None = None
) -> int:
    """写入某一交易日的 L2 名单（整批同一交易日）；同 ``(trade_date, symbol)`` 幂等覆盖。

    ``composite_score`` 须有限、``rank`` 须为正整数（fail loud，不落坏值）；
    空批 → 0（不建表除外——入口仍幂等建表）。返回写入行数。
    """
    normalized = _parse_trade_date(trade_date)
    if not candidates:
        return 0
    payload: list[tuple[str, str, float, int]] = []
    for candidate in candidates:
        rank = candidate.rank
        if isinstance(rank, bool) or not isinstance(rank, int) or rank < 1:
            raise ValueError(f"rank 必须是正整数: {rank!r}（symbol={candidate.symbol}）")
        payload.append(
            (
                normalized,
                candidate.symbol,
                _finite_float(candidate.composite_score, "composite_score"),
                rank,
            )
        )
    init_screening_db(db_path)
    conn = _connect(db_path)
    try:
        conn.executemany(
            "INSERT INTO l2_candidates (trade_date, symbol, composite_score, rank) "
            "VALUES (?, ?, ?, ?) "
            "ON CONFLICT(trade_date, symbol) DO UPDATE SET "
            "composite_score = excluded.composite_score, rank = excluded.rank",
            payload,
        )
        conn.commit()
        return len(payload)
    finally:
        conn.close()


def list_l2_candidates(trade_date: str, db_path: str | Path | None = None) -> list[dict[str, Any]]:
    """某交易日 L2 名单（按 rank 升序，同 rank 按 symbol 升序）；无记录 → 空列表。"""
    normalized = _parse_trade_date(trade_date)
    init_screening_db(db_path)
    conn = _connect(db_path)
    try:
        rows = conn.execute(
            "SELECT trade_date, symbol, composite_score, rank FROM l2_candidates "
            "WHERE trade_date = ? ORDER BY rank, symbol",
            (normalized,),
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


# ── 在榜稳定性门槛（C4） ────────────────────────────────────────────────────


def consecutive_days_in_top(symbol: str, as_of: str, *, db_path: str | Path | None = None) -> int:
    """``symbol`` 截至 ``as_of``（含）的连续在榜交易日数（断档重新计数，C4）。

    沿**表内交易日轴**（``l2_candidates`` 的 DISTINCT ``trade_date ≤ as_of``）自
    ``as_of`` 向前逐日判定：``as_of`` 当日不在榜（含当日无任何榜单行 / 空库）→ 0；
    遇首个不在榜交易日即停。表里只有交易日行 → 非交易日不打断（不产生缺口）。
    """
    normalized = _parse_trade_date(as_of)
    init_screening_db(db_path)
    conn = _connect(db_path)
    try:
        date_axis = [
            str(row[0])
            for row in conn.execute(
                "SELECT DISTINCT trade_date FROM l2_candidates WHERE trade_date <= ? "
                "ORDER BY trade_date DESC",
                (normalized,),
            ).fetchall()
        ]
        if not date_axis or date_axis[0] != normalized:
            return 0
        symbol_dates = {
            str(row[0])
            for row in conn.execute(
                "SELECT trade_date FROM l2_candidates WHERE symbol = ? AND trade_date <= ?",
                (symbol, normalized),
            ).fetchall()
        }
    finally:
        conn.close()
    streak = 0
    for trade_date in date_axis:
        if trade_date not in symbol_dates:
            break
        streak += 1
    return streak


def eligible_for_selection(
    symbol: str, as_of: str, *, min_days: int = DEFAULT_MIN_DAYS, db_path: str | Path | None = None
) -> bool:
    """精选池资格：连续在榜日数 ≥ ``min_days``（默认 3，可配置；``min_days`` 须正整数）。"""
    if isinstance(min_days, bool) or not isinstance(min_days, int) or min_days < 1:
        raise ValueError(f"min_days 必须是正整数: {min_days!r}")
    return consecutive_days_in_top(symbol, as_of, db_path=db_path) >= min_days


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


def list_triage_decisions(
    *,
    trade_date: str | None = None,
    symbol: str | None = None,
    db_path: str | Path | None = None,
) -> list[dict[str, Any]]:
    """L3 决策列表（按 trade_date、symbol 升序）；``trade_date``/``symbol`` 可选过滤。

    ``degraded`` 为 SQLite 0/1 原值（与 snapshot/library 读出口径一致，不隐式转 bool）。
    """
    init_screening_db(db_path)
    sql = "SELECT trade_date, symbol, verdict, reason, degraded, created_at FROM triage_decisions"
    conditions: list[str] = []
    params: list[Any] = []
    if trade_date is not None:
        conditions.append("trade_date = ?")
        params.append(_parse_trade_date(trade_date))
    if symbol is not None:
        conditions.append("symbol = ?")
        params.append(symbol)
    if conditions:
        sql += " WHERE " + " AND ".join(conditions)
    sql += " ORDER BY trade_date, symbol"
    conn = _connect(db_path)
    try:
        rows = conn.execute(sql, params).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()
