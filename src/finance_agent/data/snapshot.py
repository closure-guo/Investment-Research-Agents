"""全市场日频行情快照存储（add-market-snapshot）。

口径规则 D1–D5（计划 docs/superpowers/plans/2026-10-13-add-market-snapshot.md，
实施者不得自行改口径）：

- D1 复权基准（rebase）：日批写入当日行时 ``close_qfq == close_raw``（前复权
  以最新日为基准）；除权除息检测 ``k = 交易所昨收(spot prev_close) / 该票最近
  一条历史行的 close_raw``，``abs(k-1) > 1e-4`` 时把该票**所有历史行**
  ``close_qfq`` 乘 k（历史 close_raw 不动）；无历史行（新股次日）不做 rebase。
  只有统一基准的 qfq 才能让跨日收益率在除权日不跳变。
- D2 停牌/缺行合成：spot 表（``stock_zh_a_spot_em``）不含停牌标的；「昨日有行、
  今日 spot 无、且非退市」的票合成为停牌行：``open=high=low=close_raw=昨行
  close_raw``、``volume=amount=0``、``is_suspended=1``、``close_qfq=昨行
  close_qfq``、``pct_chg=0``；spot 中存在但最新价为 NaN 的票同规则。退市票
  （master 名单中不再出现）不做正向补行，历史行保持不动。
- D3 状态位（只用 spot 单行信息）：``is_st = "ST" in name``（含 ``*ST``）；
  ``limit_up_sealed = 开=高=低=收 且 pct_chg >= 4.8``；``limit_down_sealed =
  开=高=低=收 且 pct_chg <= -4.8``；``listed_days = (trade_date - list_date).days``
  （自然日；list_date 未知 → NULL，不编造）。
- D4 交易日守卫：全历史交易日历存 ``snapshot_meta.trade_calendar``（JSON 数组）；
  日批先判当日是否在日历内，不在则跳过（不静默写错日期）。
- D5 stale 判定：``expected = 日历中 <= now 的最后一个交易日``（无日历退化最近
  工作日）；``stale = last_error_date == 今天 或 (now >= 18:00 且 last_success_date
  落后于 expected)``；从未成功且从未失败（fresh DB）不告警。

异常：``SnapshotError`` 基类；``SnapshotNotFreshError``（as_of 超出数据范围）；
``SnapshotEmptyError``（该日期无记录）。

本模块不依赖 akshare（拉取与编排见 snapshot_job.py）；连接纪律与
outcome/store.py 同款（WAL + busy_timeout 短连接，DB 路径在调用期读取，
测试经 db_path 显式注入）。
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

# 三表 DDL 集中定义（幂等 CREATE IF NOT EXISTS；列名与 Global Constraints 逐字一致）。
SNAPSHOT_DDL = """
CREATE TABLE IF NOT EXISTS market_snapshot (
  trade_date        TEXT NOT NULL,
  code              TEXT NOT NULL,
  name              TEXT,
  open              REAL,
  high              REAL,
  low               REAL,
  close_raw         REAL,
  close_qfq         REAL,
  volume            REAL,
  amount            REAL,
  pct_chg           REAL,
  prev_close        REAL,
  is_st             INTEGER,
  is_suspended      INTEGER,
  limit_up_sealed   INTEGER,
  limit_down_sealed INTEGER,
  listed_days       INTEGER,
  list_date         TEXT,
  PRIMARY KEY (trade_date, code)
);
CREATE INDEX IF NOT EXISTS idx_market_snapshot_code_date
  ON market_snapshot (code, trade_date);
CREATE INDEX IF NOT EXISTS idx_market_snapshot_date ON market_snapshot (trade_date);
CREATE TABLE IF NOT EXISTS snapshot_meta (
  key   TEXT PRIMARY KEY,
  value TEXT
);
CREATE TABLE IF NOT EXISTS security_master (
  code       TEXT PRIMARY KEY,
  name       TEXT,
  list_date  TEXT,
  updated_at TEXT
);
"""


class SnapshotError(Exception):
    """快照库异常基类。"""


class SnapshotNotFreshError(SnapshotError):
    """as_of 超出快照最新数据日期（防前视读取拒绝）。"""


class SnapshotEmptyError(SnapshotError):
    """目标日期在快照库中无任何记录。"""


def _default_db_path() -> Path:
    """默认快照库路径；调用期读取 env（测试/SOP 经 SNAPSHOT_DB_PATH 覆盖）。"""
    return Path(os.getenv("SNAPSHOT_DB_PATH", "data/market_snapshot.db"))


def _connect(db_path: str | Path | None) -> sqlite3.Connection:
    """短连接（store.py 同款纪律）：WAL + busy_timeout，row_factory=Row。"""
    path = Path(db_path) if db_path else _default_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, check_same_thread=False, timeout=15.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=15000")
    return conn


def init_snapshot_db(db_path: str | Path | None = None) -> Path:
    """幂等建 3 表 + 2 索引，返回实际使用的库路径。"""
    path = Path(db_path) if db_path else _default_db_path()
    conn = _connect(path)
    try:
        conn.executescript(SNAPSHOT_DDL)
        conn.commit()
    finally:
        conn.close()
    return path


def set_meta(key: str, value: str, db_path: str | Path | None = None) -> None:
    """写入 snapshot_meta 键值（同键覆盖）。"""
    conn = _connect(db_path)
    try:
        conn.execute(
            "INSERT INTO snapshot_meta (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
        conn.commit()
    finally:
        conn.close()


def get_meta(key: str, db_path: str | Path | None = None) -> str | None:
    """读取单个 meta 键；缺失返回 None。"""
    conn = _connect(db_path)
    try:
        row = conn.execute("SELECT value FROM snapshot_meta WHERE key = ?", (key,)).fetchone()
        return row[0] if row else None
    finally:
        conn.close()


def get_all_meta(db_path: str | Path | None = None) -> dict[str, str]:
    """读取全部 meta 键值（状态判定纯函数的输入）。"""
    conn = _connect(db_path)
    try:
        rows = conn.execute("SELECT key, value FROM snapshot_meta").fetchall()
        return {row[0]: row[1] for row in rows}
    finally:
        conn.close()
