"""全市场日频行情快照存储（add-market-snapshot）。

口径规则 D1–D5（计划 docs/superpowers/plans/2026-10-13-add-market-snapshot.md，
实施者不得自行改口径）：

- D1 复权基准（rebase）：日批写入当日行时 ``close_qfq == close_raw``（前复权
  以最新日为基准）；除权除息检测 ``k = 交易所昨收(spot prev_close) / 该票最近
  一条历史行的 close_qfq``（close_qfq 是受维护的当前基准序列，若用 close_raw
  对已修订历史会二次复权：停牌股在除息后复牌时历史行已被前次 rebase 修订），
  ``abs(k-1) > 1e-4`` 时把该票**所有历史行** ``close_qfq`` 乘 k（历史
  close_raw 不动）；无历史行（新股次日）不做 rebase。
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

import math
import os
import sqlite3
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd

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
  value TEXT NOT NULL
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
    """写入 snapshot_meta 键值（同键覆盖；入口幂等建表，与 ops/model 同款纪律）。"""
    init_snapshot_db(db_path)
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
    """读取单个 meta 键；缺失返回 None（入口幂等建表，空库不抛错）。"""
    init_snapshot_db(db_path)
    conn = _connect(db_path)
    try:
        row = conn.execute("SELECT value FROM snapshot_meta WHERE key = ?", (key,)).fetchone()
        return row[0] if row else None
    finally:
        conn.close()


def get_all_meta(db_path: str | Path | None = None) -> dict[str, str]:
    """读取全部 meta 键值（状态判定纯函数的输入；入口幂等建表，空库返回 {}）。"""
    init_snapshot_db(db_path)
    conn = _connect(db_path)
    try:
        rows = conn.execute("SELECT key, value FROM snapshot_meta").fetchall()
        return {row[0]: row[1] for row in rows}
    finally:
        conn.close()


# ── 行构造纯函数（无 IO；输入 spot 均为 stock_zh_a_spot_em 原样 DataFrame） ──


def build_snapshot_rows(
    *,
    trade_date: str,
    spot: pd.DataFrame,
    prev_rows: dict[str, dict],
    master: dict[str, dict],
) -> list[dict[str, Any]]:
    """当日 spot + 昨日快照 + 证券主表 → 当日快照行列表（键与表列名逐字一致）。

    - spot 有效行 → 正常行（D3 状态位；D1 当日 close_qfq = close_raw）；
    - spot 最新价 NaN 的行按 D2 合成停牌行（有昨行才合成，无价可承则跳过）；
    - 昨日有行、今日 spot 无、且仍在 master 名单（master 为空视为名单未知，
      保守保留）的票合成停牌行；master 名单中已消失的票视为退市 → 不补行。
    """
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for _, raw in spot.iterrows():
        code = _norm_code(raw.get("代码"))
        if not code:
            continue
        seen.add(code)
        if _num(raw.get("最新价")) is None:
            row = _synthesize_suspended_row(trade_date, code, prev_rows.get(code), master)
        else:
            row = _spot_row(trade_date, code, raw, master)
        if row is not None:
            rows.append(row)

    for code, prev in prev_rows.items():
        if code in seen:
            continue
        if master and code not in master:
            continue  # 已退市：不做正向补行，历史行保持不动
        row = _synthesize_suspended_row(trade_date, code, prev, master)
        if row is not None:
            rows.append(row)
    return rows


def compute_rebase_factors(spot: pd.DataFrame, prev_rows: dict[str, dict]) -> dict[str, float]:
    """按 D1 检测除权除息，返回需要 rebase 的 ``{code: k}``。

    ``k = spot 昨收 / 该票最近历史行 close_qfq``；仅当存在有效历史基准且
    ``abs(k-1) > 1e-4`` 时返回（无历史行/历史行无 close_qfq 的新股次日不设
    基准，避免误乘）。用 close_qfq 而非 close_raw 作基准：close_qfq 是受维护
    的当前基准序列，对已修订历史用 close_raw 会二次复权。
    """
    factors: dict[str, float] = {}
    for _, raw in spot.iterrows():
        code = _norm_code(raw.get("代码"))
        prev = prev_rows.get(code)
        if prev is None:
            continue
        base = _num(prev.get("close_qfq"))
        prev_close = _num(raw.get("昨收"))
        if base is None or base == 0 or prev_close is None:
            continue
        k = prev_close / base
        if abs(k - 1.0) > 1e-4:
            factors[code] = k
    return factors


def _spot_row(trade_date: str, code: str, raw: Any, master: dict[str, dict]) -> dict[str, Any]:
    """spot 单行 → 快照行（D3 状态位；close_qfq 当日锚定 close_raw）。"""
    master_row = master.get(code) or {}
    name = _text(raw.get("名称")) or _text(master_row.get("name"))
    open_ = _num(raw.get("今开"))
    high = _num(raw.get("最高"))
    low = _num(raw.get("最低"))
    close = _num(raw.get("最新价"))
    pct_chg = _num(raw.get("涨跌幅"))
    flat = open_ is not None and open_ == high == low == close
    list_date = _text(master_row.get("list_date"))
    return {
        "trade_date": trade_date,
        "code": code,
        "name": name,
        "open": open_,
        "high": high,
        "low": low,
        "close_raw": close,
        "close_qfq": close,
        "volume": _num(raw.get("成交量")),
        "amount": _num(raw.get("成交额")),
        "pct_chg": pct_chg,
        "prev_close": _num(raw.get("昨收")),
        "is_st": 1 if "ST" in (name or "") else 0,
        "is_suspended": 0,
        "limit_up_sealed": 1 if flat and pct_chg is not None and pct_chg >= 4.8 else 0,
        "limit_down_sealed": 1 if flat and pct_chg is not None and pct_chg <= -4.8 else 0,
        "listed_days": _listed_days(trade_date, list_date),
        "list_date": list_date,
    }


def _synthesize_suspended_row(
    trade_date: str, code: str, prev: dict | None, master: dict[str, dict]
) -> dict[str, Any] | None:
    """D2 停牌合成行；无昨行或昨行无 close_raw（无价可承）→ None（不编造价格）。"""
    if prev is None:
        return None
    close = _num(prev.get("close_raw"))
    if close is None:
        return None
    master_row = master.get(code) or {}
    name = _text(master_row.get("name")) or _text(prev.get("name"))
    list_date = _text(master_row.get("list_date")) or _text(prev.get("list_date"))
    qfq = _num(prev.get("close_qfq"))
    return {
        "trade_date": trade_date,
        "code": code,
        "name": name,
        "open": close,
        "high": close,
        "low": close,
        "close_raw": close,
        "close_qfq": qfq if qfq is not None else close,
        "volume": 0.0,
        "amount": 0.0,
        "pct_chg": 0.0,
        "prev_close": close,
        "is_st": 1 if "ST" in (name or "") else 0,
        "is_suspended": 1,
        "limit_up_sealed": 0,
        "limit_down_sealed": 0,
        "listed_days": _listed_days(trade_date, list_date),
        "list_date": list_date,
    }


def _num(value: Any) -> float | None:
    """spot/DB 原始值 → float；None/NaN/inf/不可解析 → None（不编造数值）。"""
    if value is None:
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(out):  # NaN / +inf / -inf 一律视为缺失
        return None
    return out


def _text(value: Any) -> str | None:
    """原始值 → 去空白字符串；None/NaN/pd.NA/NaT/空串 → None（不产生 '<NA>'）。"""
    if value is None:
        return None
    try:
        if pd.isna(value):  # 标量缺失（NaN/pd.NA/NaT）；array-like 走 except
            return None
    except (TypeError, ValueError):
        pass
    text = str(value).strip()
    return text or None


def _norm_code(value: Any) -> str:
    """代码归一为 6 位列字符串（float 整数值去掉 ".0"；数字输入补前导零）；空/NaN → ""。"""
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return ""
    if isinstance(value, float) and value.is_integer():
        value = int(value)  # 600519.0 → "600519"（避免 ".0" 尾巴破坏代码列）
    code = str(value).strip()
    if code.isdigit() and len(code) < 6:
        code = code.zfill(6)
    return code


def _listed_days(trade_date: str, list_date: str | None) -> int | None:
    """上市自然日数 D3；list_date 缺失/不可解析 → None。"""
    if not list_date:
        return None
    try:
        return (date.fromisoformat(trade_date) - date.fromisoformat(list_date)).days
    except ValueError:
        return None
