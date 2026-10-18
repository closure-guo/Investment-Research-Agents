"""``regime_daily`` 存储层（delta add-crowding-regime-positioning T1）。

新库 ``data/regime.db``（env ``REGIME_DB_PATH`` 调用期读，测试/E2E 注入隔离路径），
与 ``outcome/track_record/model.py`` 同款 SQLite 纪律（WAL + busy_timeout 短连接）。

表 ``regime_daily``：``trade_date`` 唯一（同日重跑幂等覆盖=INSERT OR REPLACE）；
指标与分位列取自 design.md「存储与运维」逐字（分位 null 如实落库，不折算 0）；
``cap_source``（design 修订：市值取数源留痕 ``spot|tencent``）经 ``_ensure_columns``
补列，老库无列时自动 ALTER。
"""

from __future__ import annotations

import os
import sqlite3
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from finance_agent.outcome.regime.indicators import INDICATOR_KEYS

REGIME_DDL = """
CREATE TABLE IF NOT EXISTS regime_daily (
  trade_date            TEXT PRIMARY KEY,
  small_cap_share       REAL,
  up_count              INTEGER,
  down_count            INTEGER,
  flat_count            INTEGER,
  limit_up_count        INTEGER,
  limit_down_count      INTEGER,
  total_amount          REAL,
  small_cap_share_pctile REAL,
  total_amount_pctile   REAL,
  computed_at           TEXT NOT NULL,
  cap_source            TEXT
);
"""

# 建表后补列（``CREATE TABLE IF NOT EXISTS`` 不改已存在老库）：名称 + 列类型
_ADDED_COLUMNS: tuple[tuple[str, str], ...] = (("cap_source", "TEXT"),)

# 分位列（触发指标分位；写入映射用，跨模块契约）
PCTILE_COLUMNS: tuple[str, ...] = ("small_cap_share_pctile", "total_amount_pctile")

# 历史读取上限（防无界查询；沿用 ops.list_job_runs 的钳制惯例）
_MAX_HISTORY_LIMIT = 1000


def _default_db_path() -> Path:
    """默认 regime 库路径；**调用期**读取 env（测试/SOP 经 REGIME_DB_PATH 覆盖）。"""
    return Path(os.getenv("REGIME_DB_PATH", "data/regime.db"))


def _connect(db_path: str | Path | None) -> sqlite3.Connection:
    """短连接（track_record/model.py 同款纪律）：WAL + busy_timeout，row_factory=Row。"""
    path = Path(db_path) if db_path else _default_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, check_same_thread=False, timeout=15.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=15000")
    return conn


def _ensure_columns(conn: sqlite3.Connection) -> None:
    """老库补列（幂等）：``cap_source`` 为 design 修订新增，老库缺列时 ALTER 补上
    （写入列名固定 → 不补会让 upsert 直接炸 ``no such column``）。"""
    existing = {row["name"] for row in conn.execute("PRAGMA table_info(regime_daily)")}
    for name, column_type in _ADDED_COLUMNS:
        if name not in existing:
            conn.execute(f"ALTER TABLE regime_daily ADD COLUMN {name} {column_type}")  # noqa: S608


def init_regime_db(db_path: str | Path | None = None) -> Path:
    """幂等建表（含老库补列），返回实际使用的库路径。"""
    path = Path(db_path) if db_path else _default_db_path()
    conn = _connect(path)
    try:
        conn.executescript(REGIME_DDL)
        _ensure_columns(conn)
        conn.commit()
    finally:
        conn.close()
    return path


def upsert_regime_daily(
    trade_date: str,
    indicators: Mapping[str, Any],
    pctiles: Mapping[str, Any],
    *,
    computed_at: str,
    cap_source: str | None = None,
    db_path: str | Path | None = None,
) -> None:
    """落一行当日指标（``trade_date`` 唯一，同日重跑覆盖）。

    ``indicators`` 须覆盖 ``INDICATOR_KEYS`` 全键（缺键 → ``ValueError``，不静默写
    NULL）；``pctiles`` 缺键或值为 None → 该列 NULL（= 样本不足 ``insufficient_history``，
    如实落库，不折算）。``cap_source`` = 市值取数源留痕（``spot`` / ``tencent``；
    未传 → NULL，不编造来源）。
    """
    row: dict[str, Any] = {}
    for key in INDICATOR_KEYS:
        if key not in indicators:
            raise ValueError(f"指标缺 {key}（regime_daily 写入不完整）")
        row[key] = indicators[key]
    for key in PCTILE_COLUMNS:
        row[key] = pctiles.get(key)
    row["trade_date"] = trade_date
    row["computed_at"] = computed_at
    row["cap_source"] = cap_source

    columns = ", ".join(row)
    placeholders = ", ".join("?" * len(row))
    init_regime_db(db_path)
    conn = _connect(db_path)
    try:
        conn.execute(
            # 列名来自固定键序列（非外部拼接），值全参数化
            f"INSERT OR REPLACE INTO regime_daily ({columns}) VALUES ({placeholders})",  # noqa: S608
            list(row.values()),
        )
        conn.commit()
    finally:
        conn.close()


def latest_regime(db_path: str | Path | None = None) -> dict[str, Any] | None:
    """最大交易日一行（无行 → None；入口幂等建表，空库不抛错）。"""
    init_regime_db(db_path)
    conn = _connect(db_path)
    try:
        row = conn.execute("SELECT * FROM regime_daily ORDER BY trade_date DESC LIMIT 1").fetchone()
        return dict(row) if row is not None else None
    finally:
        conn.close()


def list_regime_history(
    limit: int = 250, db_path: str | Path | None = None
) -> list[dict[str, Any]]:
    """最近 ``limit`` 行，**时间轴升序**（旧→新；曲线展示与分位窗口共用）。

    ``limit`` 夹取 ``[1, 1000]``；空库 → ``[]``。
    """
    limit = max(1, min(int(limit), _MAX_HISTORY_LIMIT))
    init_regime_db(db_path)
    conn = _connect(db_path)
    try:
        rows = conn.execute(
            "SELECT * FROM regime_daily ORDER BY trade_date DESC LIMIT ?", (limit,)
        ).fetchall()
    finally:
        conn.close()
    return [dict(row) for row in reversed(rows)]
