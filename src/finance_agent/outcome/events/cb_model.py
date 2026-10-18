"""可转债标的与条款事件存储层（delta add-convertible-bond-coverage T1/T2）。

**并库**：cb 三表并入事件库 ``data/events.db``（env ``EVENTS_DB_PATH`` 调用期读），
DDL 由 ``model.init_events_db`` 的迁移链一并应用（``init_cb_db`` 亦可单独幂等调用，
供只需 cb 表的调用方）。连接纪律与事件四表同款（WAL + busy_timeout 短连接）。

三表（口径见 docs/evals/metrics.md §1.17）：
- ``cb_master(code PK)``：标的要素。源无「剩余规模」列 → ``remain_scale`` 恒 NULL
  （MUST NOT 以发行规模冒充）；``premium_rate`` 单位 %、``convert_price`` 单位元。
- ``cb_quotes(trade_date, code) PK``：日行情。``close``/``amount`` 单位元（源单位原样，
  不换算）；停牌/零成交行由抓取层跳过（MUST NOT 折算 0）。
- ``cb_events(event_key PK)``：条款事件（``event_key = code|event_type|announce_date``）。
  唯一键 = 事件类型 + 代码 + 公告日；同键重跑刷新为最新观测（幂等零重复）。
  状态推导为**读时纯函数**（``cb_status.derive_redeem_status``），本层不做判定。

增量迁移：``event_watchlist.note``（清单规则化注记载体，cbb 条目倒计时/理由落此列）
——既有库经 ``PRAGMA table_info`` 判缺 + ``ALTER TABLE ADD COLUMN``（先例
``data/snapshot.py`` 的 ``security_master`` 增量列），新库直接由 DDL 建齐。
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable, Mapping
from datetime import datetime
from pathlib import Path
from typing import Any

from finance_agent.outcome.events.model import _connect, init_events_db

CB_DDL = """
CREATE TABLE IF NOT EXISTS cb_master (
  code          TEXT PRIMARY KEY,
  name          TEXT,
  stock_code    TEXT,
  convert_price REAL,
  premium_rate  REAL,
  remain_scale  REAL,
  rating        TEXT,
  updated_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS cb_quotes (
  trade_date TEXT NOT NULL,
  code       TEXT NOT NULL,
  close      REAL,
  amount     REAL,
  updated_at TEXT NOT NULL,
  PRIMARY KEY (trade_date, code)
);
CREATE INDEX IF NOT EXISTS idx_cb_quotes_code ON cb_quotes(code, trade_date);

CREATE TABLE IF NOT EXISTS cb_events (
  event_key       TEXT PRIMARY KEY,
  code            TEXT NOT NULL,
  event_type      TEXT NOT NULL,
  announce_date   TEXT NOT NULL,
  trigger_price   REAL,
  trigger_ratio   REAL,
  count_text      TEXT,
  last_trade_date TEXT,
  remain_scale    REAL,
  raw_json        TEXT,
  updated_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_cb_events_code ON cb_events(code, event_type);
"""

# 事件类型（v1 实现范围：强赎 + 下修；回售源缺失，见 metrics §1.17）
CB_EVENT_TYPE_REDEEM = "强赎"
CB_EVENT_TYPE_ADJ = "下修"

_CB_MASTER_COLUMNS: tuple[str, ...] = (
    "code",
    "name",
    "stock_code",
    "convert_price",
    "premium_rate",
    "remain_scale",
    "rating",
    "updated_at",
)
_CB_QUOTE_COLUMNS: tuple[str, ...] = ("trade_date", "code", "close", "amount", "updated_at")
_CB_EVENT_COLUMNS: tuple[str, ...] = (
    "event_key",
    "code",
    "event_type",
    "announce_date",
    "trigger_price",
    "trigger_ratio",
    "count_text",
    "last_trade_date",
    "remain_scale",
    "raw_json",
    "updated_at",
)


def cb_event_key(code: str, event_type: str, announce_date: str) -> str:
    """事件行 → 稳定复合键 ``code|event_type|announce_date``（清单/审计外键）。"""
    return f"{code}|{event_type}|{announce_date}"


def _ensure_watchlist_note_column(conn: sqlite3.Connection) -> None:
    """``event_watchlist.note`` 增量迁移（既有库判缺补列；新库由 DDL 建齐）。"""
    columns = {str(row[1]) for row in conn.execute("PRAGMA table_info(event_watchlist)")}
    if columns and "note" not in columns:
        conn.execute("ALTER TABLE event_watchlist ADD COLUMN note TEXT")


def init_cb_db(db_path: str | Path | None = None) -> Path:
    """幂等建 cb 三表（+ 事件清单注记列增量迁移），返回实际使用的库路径。"""
    from finance_agent.outcome.events.model import _default_db_path

    path = Path(db_path) if db_path else _default_db_path()
    conn = _connect(path)
    try:
        conn.executescript(CB_DDL)
        _ensure_watchlist_note_column(conn)
        conn.commit()
    finally:
        conn.close()
    return path


def upsert_cb_masters(
    rows: Iterable[Mapping[str, Any]],
    *,
    db_path: str | Path | None = None,
    updated_at: str | None = None,
) -> int:
    """幂等写入标的（``ON CONFLICT(code) DO UPDATE`` 覆盖为最新值）；返回写入行数。

    空输入 → 0 且不建库（与 ``upsert_forecasts`` 同款）。``updated_at`` 缺省取当前时刻。
    """
    payload = [dict(row) for row in rows]
    if not payload:
        return 0
    init_cb_db(db_path)
    stamp = updated_at or datetime.now().isoformat()
    conn = _connect(db_path)
    try:
        columns = ", ".join(_CB_MASTER_COLUMNS)
        placeholders = ", ".join("?" * len(_CB_MASTER_COLUMNS))
        updates = ", ".join(f"{col}=excluded.{col}" for col in _CB_MASTER_COLUMNS if col != "code")
        sql = (
            f"INSERT INTO cb_master ({columns}) VALUES ({placeholders}) "  # noqa: S608
            f"ON CONFLICT(code) DO UPDATE SET {updates}"
        )
        for row in payload:
            conn.execute(
                sql,
                [stamp if col == "updated_at" else row.get(col) for col in _CB_MASTER_COLUMNS],
            )
        conn.commit()
        return len(payload)
    finally:
        conn.close()


def upsert_cb_quotes(
    rows: Iterable[Mapping[str, Any]],
    *,
    db_path: str | Path | None = None,
    updated_at: str | None = None,
) -> int:
    """幂等写入日行情（``ON CONFLICT(trade_date, code)`` 覆盖为最新值）；返回写入行数。"""
    payload = [dict(row) for row in rows]
    if not payload:
        return 0
    init_cb_db(db_path)
    stamp = updated_at or datetime.now().isoformat()
    conn = _connect(db_path)
    try:
        columns = ", ".join(_CB_QUOTE_COLUMNS)
        placeholders = ", ".join("?" * len(_CB_QUOTE_COLUMNS))
        updates = ", ".join(
            f"{col}=excluded.{col}"
            for col in _CB_QUOTE_COLUMNS
            if col not in ("trade_date", "code")
        )
        sql = (
            f"INSERT INTO cb_quotes ({columns}) VALUES ({placeholders}) "  # noqa: S608
            f"ON CONFLICT(trade_date, code) DO UPDATE SET {updates}"
        )
        for row in payload:
            conn.execute(
                sql,
                [stamp if col == "updated_at" else row.get(col) for col in _CB_QUOTE_COLUMNS],
            )
        conn.commit()
        return len(payload)
    finally:
        conn.close()


def upsert_cb_events(
    rows: Iterable[Mapping[str, Any]],
    *,
    db_path: str | Path | None = None,
    updated_at: str | None = None,
) -> int:
    """幂等写入条款事件（``ON CONFLICT(event_key)`` 刷新为最新观测）；返回写入行数。

    ``event_key`` 缺省由 ``(code, event_type, announce_date)`` 合成（调用方亦可显式传）。
    """
    payload: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        item.setdefault(
            "event_key",
            cb_event_key(
                str(item.get("code") or ""),
                str(item.get("event_type") or ""),
                str(item.get("announce_date") or ""),
            ),
        )
        payload.append(item)
    if not payload:
        return 0
    init_cb_db(db_path)
    stamp = updated_at or datetime.now().isoformat()
    conn = _connect(db_path)
    try:
        columns = ", ".join(_CB_EVENT_COLUMNS)
        placeholders = ", ".join("?" * len(_CB_EVENT_COLUMNS))
        updates = ", ".join(
            f"{col}=excluded.{col}" for col in _CB_EVENT_COLUMNS if col != "event_key"
        )
        sql = (
            f"INSERT INTO cb_events ({columns}) VALUES ({placeholders}) "  # noqa: S608
            f"ON CONFLICT(event_key) DO UPDATE SET {updates}"
        )
        for row in payload:
            conn.execute(
                sql,
                [stamp if col == "updated_at" else row.get(col) for col in _CB_EVENT_COLUMNS],
            )
        conn.commit()
        return len(payload)
    finally:
        conn.close()


def get_cb_master(code: str, *, db_path: str | Path | None = None) -> dict[str, Any] | None:
    """单条标的；未知代码 → ``None``。"""
    init_events_db(db_path)
    conn = _connect(db_path)
    try:
        row = conn.execute("SELECT * FROM cb_master WHERE code=?", (code,)).fetchone()
        return dict(row) if row is not None else None
    finally:
        conn.close()


def list_cb_masters(*, db_path: str | Path | None = None) -> list[dict[str, Any]]:
    """全部标的，按代码升序（遍历顺序确定）。"""
    init_events_db(db_path)
    conn = _connect(db_path)
    try:
        return [dict(row) for row in conn.execute("SELECT * FROM cb_master ORDER BY code ASC")]
    finally:
        conn.close()


def get_cb_quote(
    code: str, *, trade_date: str | None = None, db_path: str | Path | None = None
) -> dict[str, Any] | None:
    """单条行情；``trade_date`` 缺省取该代码**最新交易日**行（清单现价口径）。"""
    init_events_db(db_path)
    conn = _connect(db_path)
    try:
        if trade_date is None:
            row = conn.execute(
                "SELECT * FROM cb_quotes WHERE code=? ORDER BY trade_date DESC LIMIT 1", (code,)
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT * FROM cb_quotes WHERE code=? AND trade_date=?", (code, trade_date)
            ).fetchone()
        return dict(row) if row is not None else None
    finally:
        conn.close()


def find_cb_event(
    code: str, event_type: str, *, db_path: str | Path | None = None
) -> dict[str, Any] | None:
    """按 ``(代码, 事件类型)`` 找既有事件（首次观测日复用：跨日重跑键稳定的依据）。

    同代码同类型可能存在多条（不同公告日的历史事件）→ 取公告日**最早**一条
    （该类型的首次观测），保证键复用确定。
    """
    init_events_db(db_path)
    conn = _connect(db_path)
    try:
        row = conn.execute(
            "SELECT * FROM cb_events WHERE code=? AND event_type=? "
            "ORDER BY announce_date ASC LIMIT 1",
            (code, event_type),
        ).fetchone()
        return dict(row) if row is not None else None
    finally:
        conn.close()


def list_cb_events(*, db_path: str | Path | None = None) -> list[dict[str, Any]]:
    """全部条款事件，按 ``(公告日, 代码, 事件类型)`` 升序（遍历顺序确定）。"""
    init_events_db(db_path)
    conn = _connect(db_path)
    try:
        return [
            dict(row)
            for row in conn.execute(
                "SELECT * FROM cb_events ORDER BY announce_date ASC, code ASC, event_type ASC"
            )
        ]
    finally:
        conn.close()
