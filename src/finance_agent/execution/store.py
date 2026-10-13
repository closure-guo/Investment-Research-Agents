"""交易执行层落库（add-signal-portfolio-risk-gate G2）：signals + hysteresis_events。

连接纪律与 ``data/snapshot.py`` / ``outcome/store.py`` 同款：WAL + busy_timeout 短连接，
DB 路径调用期读取 env ``EXECUTION_DB_PATH``（默认 ``data/execution.db``），测试经
``db_path`` 显式注入。

- ``signals``：Signal 契约落库表，PK=signal_id（确定性 id → 同日同标的重跑幂等
  upsert，不翻倍）；
- ``hysteresis_events``：滞回判定记录（applied 非空的判定逐条落库，供审计——
  delta spec decision-hysteresis「滞回判定记录落库」）。

模块级只依赖 stdlib：``Signal`` 类型经 TYPE_CHECKING / 函数内 import 引入，
避免 ``signals.py``（转换入口）与 ``store.py``（落库）的模块级循环依赖。
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from finance_agent.execution.signals import Signal

# 两表 DDL 集中定义（幂等 CREATE IF NOT EXISTS）。
EXECUTION_DDL = """
CREATE TABLE IF NOT EXISTS signals (
  signal_id    TEXT PRIMARY KEY,
  symbol       TEXT NOT NULL,
  direction    TEXT NOT NULL,
  strength     REAL NOT NULL,
  horizon_days INTEGER NOT NULL,
  source       TEXT NOT NULL,
  decision_id  TEXT NOT NULL,
  created_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_signals_symbol_created
  ON signals (symbol, created_at);
CREATE TABLE IF NOT EXISTS hysteresis_events (
  event_id    INTEGER PRIMARY KEY AUTOINCREMENT,
  signal_id   TEXT NOT NULL,
  detail_json TEXT NOT NULL,
  created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_hysteresis_events_signal
  ON hysteresis_events (signal_id);
"""

_SIGNAL_COLUMNS = (
    "signal_id",
    "symbol",
    "direction",
    "strength",
    "horizon_days",
    "source",
    "decision_id",
    "created_at",
)

_SELECT_SIGNALS = (
    "SELECT signal_id, symbol, direction, strength, horizon_days, source, decision_id,"
    " created_at FROM signals"
)
_ORDER_SIGNALS = " ORDER BY created_at ASC, symbol ASC"


def _default_db_path() -> Path:
    """默认执行库路径；调用期读取 env（测试/SOP 经 EXECUTION_DB_PATH 覆盖）。"""
    return Path(os.getenv("EXECUTION_DB_PATH", "data/execution.db"))


def _connect(db_path: str | Path | None) -> sqlite3.Connection:
    """短连接（snapshot 同款纪律）：WAL + busy_timeout，row_factory=Row。"""
    path = Path(db_path) if db_path else _default_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, check_same_thread=False, timeout=15.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=15000")
    return conn


def init_execution_db(db_path: str | Path | None = None) -> Path:
    """幂等建 signals + hysteresis_events 两表与索引，返回实际使用的库路径。"""
    path = Path(db_path) if db_path else _default_db_path()
    conn = _connect(path)
    try:
        conn.executescript(EXECUTION_DDL)
        conn.commit()
    finally:
        conn.close()
    return path


def record_signal(signal: Signal, *, db_path: str | Path | None = None) -> None:
    """Signal 落库（幂等 upsert by signal_id；同日重跑覆盖不翻倍）。"""
    init_execution_db(db_path)
    conn = _connect(db_path)
    try:
        conn.execute(
            """INSERT INTO signals
                 (signal_id, symbol, direction, strength, horizon_days, source,
                  decision_id, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(signal_id) DO UPDATE SET
                 symbol=excluded.symbol,
                 direction=excluded.direction,
                 strength=excluded.strength,
                 horizon_days=excluded.horizon_days,
                 source=excluded.source,
                 decision_id=excluded.decision_id,
                 created_at=excluded.created_at""",
            (
                signal.signal_id,
                signal.symbol,
                signal.direction,
                signal.strength,
                signal.horizon_days,
                signal.source,
                signal.decision_id,
                signal.created_at,
            ),
        )
        conn.commit()
    finally:
        conn.close()


def list_signals(
    *,
    trade_date: str | None = None,
    symbol: str | None = None,
    db_path: str | Path | None = None,
) -> list[Signal]:
    """按交易日（created_at 前 10 位）/标的过滤，返回 created_at、symbol 升序的 Signal。"""
    from finance_agent.execution.signals import Signal

    init_execution_db(db_path)
    if trade_date is not None and symbol is not None:
        sql = _SELECT_SIGNALS + " WHERE substr(created_at, 1, 10) = ? AND symbol = ?"
        params: tuple[Any, ...] = (str(trade_date), str(symbol))
    elif trade_date is not None:
        sql = _SELECT_SIGNALS + " WHERE substr(created_at, 1, 10) = ?"
        params = (str(trade_date),)
    elif symbol is not None:
        sql = _SELECT_SIGNALS + " WHERE symbol = ?"
        params = (str(symbol),)
    else:
        sql = _SELECT_SIGNALS
        params = ()
    conn = _connect(db_path)
    try:
        rows = conn.execute(sql + _ORDER_SIGNALS, params).fetchall()
    finally:
        conn.close()
    return [
        Signal(
            signal_id=str(row["signal_id"]),
            symbol=str(row["symbol"]),
            direction=str(row["direction"]),  # type: ignore[arg-type]  # 写入侧已过枚举校验
            strength=float(row["strength"]),
            horizon_days=int(row["horizon_days"]),
            source=str(row["source"]),
            decision_id=str(row["decision_id"]),
            created_at=str(row["created_at"]),
        )
        for row in rows
    ]


def record_hysteresis_event(
    signal_id: str,
    detail_json: str,
    created_at: str,
    *,
    db_path: str | Path | None = None,
) -> None:
    """滞回判定记录落库（detail_json 为 JSON 字符串，序列化归调用方）。"""
    init_execution_db(db_path)
    conn = _connect(db_path)
    try:
        conn.execute(
            "INSERT INTO hysteresis_events (signal_id, detail_json, created_at) VALUES (?, ?, ?)",
            (str(signal_id), str(detail_json), str(created_at)),
        )
        conn.commit()
    finally:
        conn.close()


def list_hysteresis_events(
    *, signal_id: str | None = None, db_path: str | Path | None = None
) -> list[dict[str, Any]]:
    """按 signal_id 过滤的判定记录（event_id 升序；detail_json 保持原始字符串）。"""
    init_execution_db(db_path)
    conn = _connect(db_path)
    try:
        if signal_id is None:
            rows = conn.execute(
                "SELECT event_id, signal_id, detail_json, created_at FROM hysteresis_events"
                " ORDER BY event_id ASC"
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT event_id, signal_id, detail_json, created_at FROM hysteresis_events"
                " WHERE signal_id = ? ORDER BY event_id ASC",
                (str(signal_id),),
            ).fetchall()
    finally:
        conn.close()
    return [
        {
            "event_id": int(row["event_id"]),
            "signal_id": str(row["signal_id"]),
            "detail_json": str(row["detail_json"]),
            "created_at": str(row["created_at"]),
        }
        for row in rows
    ]
