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
  close_qfq``、``pct_chg=0``；spot 中存在但最新价为 NaN/0 的票同规则；spot 中
  存在、最新价有效但 OHLC 全 0 且成交量为 0 的「有价无成交」形态（源回填昨收，
  issue #511）同样合成停牌行。退市票
  （master 名单中不再出现）不做正向补行，历史行保持不动。
- D3 状态位（只用 spot 单行信息）：``is_st = "ST" in name``（含 ``*ST``）；
  ``limit_up_sealed = 开=高=低=收 且 pct_chg >= 4.8``；``limit_down_sealed =
  开=高=低=收 且 pct_chg <= -4.8``；``listed_days = (trade_date - list_date).days``
  （自然日；list_date 未知 → NULL，不编造）。
- D4 交易日守卫：全历史交易日历存 ``snapshot_meta.trade_calendar``（JSON 数组）；
  日批先判当日是否在日历内，不在则跳过（不静默写错日期）。
- D5 stale 判定：``expected = 日历中 <= now 的最后一个交易日``（无日历退化最近
  工作日）；``stale = last_error_date == 今天 或 (now >= 18:00 且 last_success_date
  为空或落后于 expected)``；从未成功且从未失败（fresh DB）不告警。

异常：``SnapshotError`` 基类；``SnapshotNotFreshError``（as_of 超出数据范围）；
``SnapshotEmptyError``（该日期无记录）。

本模块不依赖 akshare（拉取与编排见 snapshot_job.py）；连接纪律与
outcome/store.py 同款（WAL + busy_timeout 短连接，DB 路径在调用期读取，
测试经 db_path 显式注入）。
"""

from __future__ import annotations

import json
import math
import os
import sqlite3
from collections.abc import Iterable, Sequence
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

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
  delist_date TEXT,
  delist_source TEXT,
  updated_at TEXT
);
CREATE TABLE IF NOT EXISTS market_ref_daily (
  trade_date TEXT NOT NULL,
  code       TEXT NOT NULL,
  float_cap  REAL,
  total_cap  REAL,
  turnover   REAL,
  source     TEXT,
  updated_at TEXT NOT NULL,
  PRIMARY KEY (trade_date, code)
);
CREATE INDEX IF NOT EXISTS idx_market_ref_code_date
  ON market_ref_daily (code, trade_date);
"""

# market_snapshot 列顺序（写入/读取共用的唯一序列；列名与 Global Constraints 逐字一致）
_SNAPSHOT_COLUMNS: tuple[str, ...] = (
    "trade_date",
    "code",
    "name",
    "open",
    "high",
    "low",
    "close_raw",
    "close_qfq",
    "volume",
    "amount",
    "pct_chg",
    "prev_close",
    "is_st",
    "is_suspended",
    "limit_up_sealed",
    "limit_down_sealed",
    "listed_days",
    "list_date",
)

# read_bars 出口做数值强转的列（评审修正：NULL 混入时保持 float64，消费端不必逐格判型）
_NUMERIC_SNAPSHOT_COLUMNS: tuple[str, ...] = (
    "open",
    "high",
    "low",
    "close_raw",
    "close_qfq",
    "volume",
    "amount",
    "pct_chg",
    "prev_close",
    "listed_days",
)


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
    """短连接（store.py 同款纪律）：busy_timeout，row_factory=Row。

    连接纪律（harden-sqlite-connection-discipline）：journal 模式只在
    ``init_snapshot_db`` 设置一次，每连接不跑 ``PRAGMA journal_mode``（每次
    连接 attach 一次 ``-shm`` 是 incident 053 的放大器）。
    """
    path = Path(db_path) if db_path else _default_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, check_same_thread=False, timeout=15.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=15000")
    return conn


def _connect_ro(db_path: str | Path | None) -> sqlite3.Connection:
    """只读连接（读路径去 DDL 配套）：缺库直接 OperationalError，不创建文件。"""
    path = Path(db_path) if db_path else _default_db_path()
    conn = sqlite3.connect(
        f"file:{path.as_posix()}?mode=ro", uri=True, check_same_thread=False, timeout=5.0
    )
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def init_snapshot_db(db_path: str | Path | None = None) -> Path:
    """幂等建表 + 2 索引（含参考数据侧表），返回实际使用的库路径。

    增量迁移（add-microcap-lane-guardrails）：旧库 ``security_master`` 缺
    ``delist_date`` / ``delist_source`` 列 → ``ALTER TABLE ADD COLUMN`` 补列
    （旧行保留，该列 NULL = 「未退市/退市日未知」——两者以是否出现在退市清单区分，
    见 metrics.md §1.16；``delist_source`` = 退市日语义来源审计留痕）。
    增量迁移（extend-factor-library-coverage）：缺 ``industry_l1`` 列 → 补列
    （NULL = 行业未知；参考数据面回填与周一主表刷新分支写入）。
    """
    path = Path(db_path) if db_path else _default_db_path()
    conn = _connect(path)
    conn.execute("PRAGMA journal_mode=WAL")  # 连接纪律：journal 只在 init 设置（#515）
    try:
        # 连接纪律：journal 模式只在 init 设置一次（库头持久化），见 _connect docstring
        conn.executescript(SNAPSHOT_DDL)
        columns = {row[1] for row in conn.execute("PRAGMA table_info(security_master)")}
        if "delist_date" not in columns:
            conn.execute("ALTER TABLE security_master ADD COLUMN delist_date TEXT")
        if "delist_source" not in columns:
            conn.execute("ALTER TABLE security_master ADD COLUMN delist_source TEXT")
        if "industry_l1" not in columns:
            conn.execute("ALTER TABLE security_master ADD COLUMN industry_l1 TEXT")
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


def set_meta_many(
    mapping: dict[str, str],
    db_path: str | Path | None = None,
    delete_keys: tuple[str, ...] = (),
) -> None:
    """批量写 + 可选同事务删 snapshot_meta 键（单事务单 commit，评审修正）。

    ``delete_keys`` 先删、``mapping`` 后写，同一连接同一提交；任一步失败整体回滚。
    成功路径一次落 ``last_success_date`` 等并清 ``last_error``/``last_error_date``：
    分两步（先 ``delete_meta`` 再写）会在两次提交之间产生「成功已写、错误未清」
    的中间态，状态端点可能闪告警横幅。空 ``mapping`` 且空 ``delete_keys`` → 无操作。
    """
    init_snapshot_db(db_path)
    if not mapping and not delete_keys:
        return
    conn = _connect(db_path)
    try:
        if delete_keys:
            placeholders = ", ".join("?" * len(delete_keys))
            conn.execute(
                # placeholders 仅为 "?" 序列；键值全参数化
                f"DELETE FROM snapshot_meta WHERE key IN ({placeholders})",  # noqa: S608
                list(delete_keys),
            )
        if mapping:
            conn.executemany(
                "INSERT INTO snapshot_meta (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                list(mapping.items()),
            )
        conn.commit()
    finally:
        conn.close()


def delete_meta(keys: Iterable[str], db_path: str | Path | None = None) -> int:
    """删除若干 meta 键（单事务一次删清，返回实际删除行数）。

    独立删键工具；日批成功路径已改用 ``set_meta_many(..., delete_keys=...)``
    的「删+写」单事务（评审修正 2），本函数保留供只需删键的场景使用。
    """
    init_snapshot_db(db_path)
    key_list = [str(key) for key in keys]
    if not key_list:
        return 0
    placeholders = ", ".join("?" * len(key_list))
    conn = _connect(db_path)
    try:
        cur = conn.execute(
            # placeholders 仅为 "?" 序列；键值全参数化
            f"DELETE FROM snapshot_meta WHERE key IN ({placeholders})",  # noqa: S608
            key_list,
        )
        conn.commit()
        return cur.rowcount
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


def reset_snapshot_db(db_path: str | Path | None = None) -> int:
    """清空快照三表全部行（仅 TESTING 造数 / E2E 隔离用），返回删除行数。

    不做 DROP：表结构保留（幂等建表本就在各入口），只删行。
    """
    init_snapshot_db(db_path)
    conn = _connect(db_path)
    try:
        removed = conn.execute("DELETE FROM market_snapshot").rowcount
        removed += conn.execute("DELETE FROM security_master").rowcount
        removed += conn.execute("DELETE FROM snapshot_meta").rowcount
        conn.commit()
        return removed
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


# ── 写入与读取接口（T3）：幂等写 + as_of 防前视 + 空数据显式报错 ─────────────


def _parse_date_param(value: Any, *, param: str) -> date:
    """日期入参解析：严格 ``YYYY-MM-DD``；非规范/不可解析 → ``SnapshotError``（评审修正）。

    严格性理由：日期列是 TEXT 且 SQL 用字符串比较，任何非规范形态（"2026-10-5"、
    "20261012" 等）都会让字典序比较错位而静默读错窗口（前视泄漏风险）；这里一律
    在入口 fail loud，SQL 参数只用 ``isoformat()`` 规范化后的值。
    """
    try:
        parsed = date.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise SnapshotError(f"非规范日期（{param}）: {value!r}，须为 YYYY-MM-DD") from exc
    if parsed.isoformat() != value:
        # date.fromisoformat 在 3.11+ 也接受基本格式（20261012）等变体：拒绝非规范形态
        raise SnapshotError(f"非规范日期（{param}）: {value!r}，须为 YYYY-MM-DD")
    return parsed


def write_snapshot_rows(
    trade_date: str, rows: list[dict], db_path: str | Path | None = None
) -> int:
    """幂等写入某一交易日的快照行（INSERT OR REPLACE），返回写入行数。

    整批同一交易日：行内 trade_date 以参数为准（防止批内日期漂移）。
    """
    init_snapshot_db(db_path)
    if not rows:
        return 0
    cols = ", ".join(_SNAPSHOT_COLUMNS)
    placeholders = ", ".join("?" * len(_SNAPSHOT_COLUMNS))
    payload = [
        tuple(trade_date if col == "trade_date" else row.get(col) for col in _SNAPSHOT_COLUMNS)
        for row in rows
    ]
    conn = _connect(db_path)
    try:
        conn.executemany(
            # 列名/占位符均为模块常量生成，值全参数化（无外部拼接）
            f"INSERT OR REPLACE INTO market_snapshot ({cols}) VALUES ({placeholders})",  # noqa: S608
            payload,
        )
        conn.commit()
        return len(payload)
    finally:
        conn.close()


def write_snapshot_rows_bulk(rows: list[dict], db_path: str | Path | None = None) -> int:
    """多交易日整批 upsert（回填专用）：行内 ``trade_date`` 逐行取用，单事务提交。

    与 ``write_snapshot_rows`` 同列契约与 upsert 语义（INSERT OR REPLACE），差别
    只在提交粒度：本函数一次事务写多日——回填场景每票 ~190 日，逐日提交在 16 进程
    并行下产生提交放大（WAL 写锁争用，实测吞吐降一个量级），本函数把每票收敛为
    一次提交（unify-quant-research-architecture P0-1 运维修复）。日批仍走
    ``write_snapshot_rows`` 的单交易日契约（批内日期漂移防线不变）。
    空批 → 0（不建行、不建库）。
    """
    if not rows:
        return 0
    init_snapshot_db(db_path)
    cols = ", ".join(_SNAPSHOT_COLUMNS)
    placeholders = ", ".join("?" * len(_SNAPSHOT_COLUMNS))
    payload = [tuple(row.get(col) for col in _SNAPSHOT_COLUMNS) for row in rows]
    conn = _connect(db_path)
    try:
        conn.executemany(
            # 列名/占位符均为模块常量生成，值全参数化（无外部拼接）
            f"INSERT OR REPLACE INTO market_snapshot ({cols}) VALUES ({placeholders})",  # noqa: S608
            payload,
        )
        conn.commit()
        return len(payload)
    finally:
        conn.close()


def apply_rebase(
    code: str, factor: float, before_date: str, db_path: str | Path | None = None
) -> int:
    """D1 rebase：该票 ``trade_date < before_date`` 的行 ``close_qfq × factor``。

    ``close_raw`` 不动；返回实际更新行数（乘积四舍五入到 4 位小数，与写入口径一致）。
    ``factor`` 必须有限且 > 0（评审修正）：NaN/0/负值会把整段历史 qfq 写成坏值
    （静默数据事故）→ ``SnapshotError`` 拒绝；``before_date`` 须为规范日期。
    """
    try:
        invalid_factor = not math.isfinite(factor) or factor <= 0
    except TypeError:  # 非数值 factor（None/str 等）
        invalid_factor = True
    if invalid_factor:
        raise SnapshotError(f"非法 rebase factor: {factor!r}（须为有限正数）")
    before = _parse_date_param(before_date, param="before_date")
    init_snapshot_db(db_path)
    conn = _connect(db_path)
    try:
        cur = conn.execute(
            "UPDATE market_snapshot SET close_qfq = ROUND(close_qfq * ?, 4) "
            "WHERE code = ? AND trade_date < ?",
            (factor, code, before.isoformat()),
        )
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()


def latest_snapshot_date(
    db_path: str | Path | None = None, *, before: str | None = None
) -> str | None:
    """快照表最新数据日期（``MAX(trade_date)``）；无行 → None。

    ``before``（规范 ``YYYY-MM-DD``）只统计严格早于该日的行——日批在同日重跑时
    取「上一交易日」行作 rebase 基准/停牌合成源，防止把当日已写行当作历史基准
    而二次复权整段 qfq（T6 评审修正）。非规范日期 → ``SnapshotError``。
    读路径去 DDL（harden-sqlite-connection-discipline）：``?mode=ro`` 连接，
    缺库/缺表返回 None，MUST NOT 建库建表。
    """
    sql = "SELECT MAX(trade_date) FROM market_snapshot"
    params: list[Any] = []
    if before is not None:
        sql += " WHERE trade_date < ?"
        params.append(_parse_date_param(before, param="before").isoformat())
    try:
        conn = _connect_ro(db_path)
    except sqlite3.OperationalError:
        return None  # 缺库（mode=ro 不创建文件）
    try:
        row = conn.execute(sql, params).fetchone()
        return row[0] if row and row[0] is not None else None
    except sqlite3.OperationalError as exc:
        if "no such table" in str(exc).lower():
            return None  # 缺表按空语义，不建表
        raise
    finally:
        conn.close()


# ── 参考数据附加列读取（extend-factor-library-coverage §6）──────────────────

# 旧库缺侧表 / 缺 industry_l1 列 / 缺主表 → 退化为原查询（MUST NOT 误判为空库）
_REF_AUGMENTATION_LIMITS: tuple[str, ...] = (
    "no such table: market_ref_daily",
    "no such column: industry_l1",
    "no such table: security_master",
)


def _universe_select(augmented: bool) -> str:
    """快照读选列：增强形态 LEFT JOIN 侧表与主表（附加参考列）；退化形态原查询。"""
    if not augmented:
        return "SELECT * FROM market_snapshot"
    return (
        "SELECT m.*, r.float_cap AS float_cap, r.total_cap AS total_cap, "
        "r.turnover AS turnover, s.industry_l1 AS industry_l1, "
        "COALESCE(m.listed_days, CAST(julianday(m.trade_date) - julianday(s.list_date) "
        "AS INTEGER)) AS _ref_listed_days "
        "FROM market_snapshot m "
        "LEFT JOIN market_ref_daily r "
        "ON r.trade_date = m.trade_date AND r.code = m.code "
        "LEFT JOIN security_master s ON s.code = m.code"
    )


def _read_snapshot_rows(
    conn: sqlite3.Connection,
    *,
    where: str,
    params: list[Any],
    order_by: str,
    empty_message: str,
) -> list[dict[str, Any]]:
    """单次 SQL 读快照行并附加参考列；旧库缺侧表/列 → 退化为原查询。

    ``where``/``order_by`` 里的 ``{prefix}`` 在增强形态替换为 ``m.``（JOIN 别名），
    退化形态替换为空串。``listed_days`` 为 NULL 时用 ``_ref_listed_days``（主表
    ``list_date`` 派生的自然日差）回填。
    """
    for augmented in (True, False):
        prefix = "m." if augmented else ""
        sql = (
            _universe_select(augmented)
            + where.format(prefix=prefix)
            + " ORDER BY "
            + order_by.format(prefix=prefix)
        )
        try:
            fetched = [dict(row) for row in conn.execute(sql, params).fetchall()]
        except sqlite3.OperationalError as exc:
            message = str(exc).lower()
            if augmented and any(marker in message for marker in _REF_AUGMENTATION_LIMITS):
                continue
            if "no such table" in message:
                raise SnapshotEmptyError(empty_message) from None
            raise
        if augmented:
            for row in fetched:
                derived = row.pop("_ref_listed_days", None)
                if row.get("listed_days") is None:
                    row["listed_days"] = derived
        return fetched
    return []


def read_universe(
    as_of: str, *, include_suspended: bool = True, db_path: str | Path | None = None
) -> list[dict[str, Any]]:
    """``as_of`` 当日全市场快照行（R3 防前视）。

    ``as_of`` 须为规范 ``YYYY-MM-DD``（评审修正：``date.fromisoformat`` 解析后按
    date 对象比较，非规范 → ``SnapshotError``；SQL 参数用规范化字符串）。
    ``as_of > latest`` → ``SnapshotNotFreshError``（消息含最新数据日期，不返回
    任何记录）；该日无行（含空库/缺表/缺库）→ ``SnapshotEmptyError``（不静默
    返回他日数据）。``include_suspended=False`` 时剔除 ``is_suspended=1`` 的行
    （NULL 状态视为未知，同样剔除）。返回按 code 升序。
    读路径去 DDL（harden-sqlite-connection-discipline）：不再调 ``init_snapshot_db``，
    ``?mode=ro`` 连接，缺库/缺表走既有空语义，MUST NOT 建库建表。
    """
    as_of_date = _parse_date_param(as_of, param="as_of")
    latest = latest_snapshot_date(db_path)
    if latest is not None:
        latest_date = _parse_date_param(latest, param="latest_snapshot_date")
        if as_of_date > latest_date:
            raise SnapshotNotFreshError(f"快照最新数据日期为 {latest}，不接受 as_of={as_of}")
    try:
        conn = _connect_ro(db_path)
    except sqlite3.OperationalError:
        raise SnapshotEmptyError(f"该日期无快照记录: {as_of}") from None
    try:
        where = " WHERE {prefix}trade_date = ?"
        if not include_suspended:
            where += " AND {prefix}is_suspended = 0"
        rows = _read_snapshot_rows(
            conn,
            where=where,
            params=[as_of_date.isoformat()],
            order_by="{prefix}code",
            empty_message=f"该日期无快照记录: {as_of}",
        )
    finally:
        conn.close()
    if not rows:
        raise SnapshotEmptyError(f"该日期无快照记录: {as_of}")
    return rows


def read_universe_range(
    start: str,
    end: str,
    *,
    include_suspended: bool = True,
    db_path: str | Path | None = None,
) -> list[dict[str, Any]]:
    """``[start, end]`` 区间全市场快照行（harden-sqlite-connection-discipline ③）。

    单次 ``WHERE trade_date BETWEEN ? AND ?`` 查询取齐多日行（面板加载由逐日
    N 连接收敛为单连接）。口径与 ``read_universe`` 同源：日期经
    ``_parse_date_param`` 严格解析（非规范 → ``SnapshotError``）；``end`` 晚于
    库内最新快照日期 → ``SnapshotNotFreshError``（防前视）；``end < start`` →
    ``SnapshotError``。缺库/缺表 → ``SnapshotEmptyError``（既有空语义，
    MUST NOT 建库建表）；区间内无任何行 → 同样 ``SnapshotEmptyError``。
    ``include_suspended=False`` 剔除停牌行（语义同 ``read_universe``）。
    返回按 ``(trade_date, code)`` 升序，行含 ``trade_date`` 键。
    """
    start_date = _parse_date_param(start, param="start")
    end_date = _parse_date_param(end, param="end")
    if end_date < start_date:
        raise SnapshotError(f"区间非法: end {end} 早于 start {start}")
    try:
        conn = _connect_ro(db_path)
    except sqlite3.OperationalError:
        raise SnapshotEmptyError(f"区间无快照记录: [{start}, {end}]") from None
    try:
        # 防前视与区间查询共用同一条只读连接（latest 单独连一次会翻倍连接数）
        try:
            latest_row = conn.execute("SELECT MAX(trade_date) FROM market_snapshot").fetchone()
        except sqlite3.OperationalError as exc:
            if "no such table" in str(exc).lower():
                raise SnapshotEmptyError(f"区间无快照记录: [{start}, {end}]") from None
            raise
        latest = latest_row[0] if latest_row else None
        if latest is not None:
            latest_date = _parse_date_param(latest, param="latest_snapshot_date")
            if end_date > latest_date:
                raise SnapshotNotFreshError(f"快照最新数据日期为 {latest}，不接受 end={end}")
        where = " WHERE {prefix}trade_date BETWEEN ? AND ?"
        if not include_suspended:
            where += " AND {prefix}is_suspended = 0"
        rows = _read_snapshot_rows(
            conn,
            where=where,
            params=[start_date.isoformat(), end_date.isoformat()],
            order_by="{prefix}trade_date, {prefix}code",
            empty_message=f"区间无快照记录: [{start}, {end}]",
        )
    finally:
        conn.close()
    if not rows:
        raise SnapshotEmptyError(f"区间无快照记录: [{start}, {end}]")
    return rows


def read_bars(
    codes: list[str],
    start: str,
    end: str,
    *,
    as_of: str,
    db_path: str | Path | None = None,
) -> pd.DataFrame:
    """区间日线读取（T3 双保险：``end > as_of`` 与 ``as_of > latest`` 均显式拒绝）。

    ``start``/``end``/``as_of`` 须为规范 ``YYYY-MM-DD``（评审修正：
    ``date.fromisoformat`` 解析后按 date 对象比较，非规范 → ``SnapshotError``；
    SQL 参数用规范化字符串，避免字典序比较错位导致前视泄漏）。
    返回列集合与表列一致（含双复权口径），数值列经 ``pd.to_numeric(errors="coerce")``
    强转（NULL 混入时保持 float64），按 ``(trade_date, code)`` 升序；
    区间/代码无行（含空库）→ ``SnapshotEmptyError``。
    """
    start_date = _parse_date_param(start, param="start")
    end_date = _parse_date_param(end, param="end")
    as_of_date = _parse_date_param(as_of, param="as_of")
    if end_date > as_of_date:
        raise SnapshotNotFreshError(
            f"end={end_date.isoformat()} 晚于 as_of={as_of_date.isoformat()}（禁止读取未来数据）"
        )
    latest = latest_snapshot_date(db_path)
    if latest is None:
        raise SnapshotEmptyError(
            f"快照库为空，区间无记录: {start_date.isoformat()}~{end_date.isoformat()}"
        )
    latest_date = _parse_date_param(latest, param="latest_snapshot_date")
    if as_of_date > latest_date:
        raise SnapshotNotFreshError(f"快照最新数据日期为 {latest}，不接受 as_of={as_of}")
    normalized = [code for code in (_norm_code(c) for c in codes) if code]
    if not normalized:
        raise SnapshotEmptyError(
            f"未指定有效查询代码: {start_date.isoformat()}~{end_date.isoformat()}"
        )
    placeholders = ", ".join("?" * len(normalized))
    conn = _connect(db_path)
    try:
        rows = conn.execute(
            # placeholders 仅为 "?" 序列；代码值全参数化
            f"SELECT * FROM market_snapshot WHERE code IN ({placeholders}) "  # noqa: S608
            "AND trade_date >= ? AND trade_date <= ? ORDER BY trade_date, code",
            [*normalized, start_date.isoformat(), end_date.isoformat()],
        ).fetchall()
        frame = pd.DataFrame([dict(row) for row in rows], columns=list(_SNAPSHOT_COLUMNS))
    finally:
        conn.close()
    if frame.empty:
        raise SnapshotEmptyError(
            f"区间内无快照记录: {start_date.isoformat()}~{end_date.isoformat()}"
        )
    for col in _NUMERIC_SNAPSHOT_COLUMNS:
        frame[col] = pd.to_numeric(frame[col], errors="coerce")
    return frame


def read_close_map(
    dates: Sequence[str], db_path: str | Path | None = None
) -> dict[str, dict[str, float]]:
    """批量读取若干交易日的全市场收盘价（``close_raw``）→ ``{date: {code: close}}``。

    update-outcome-band-volatility-scaled 的同窗随机基准用：只需已完成历史窗口的两个
    横截面，故不设 ``as_of`` 前视门（与 ``read_bars`` 的单标的取数语义不同）。日期须为
    规范 ``YYYY-MM-DD``；空库 → 返回空 dict（调用方按「基准不可用」披露，不阻断页面）。
    """
    normalized: list[str] = []
    for value in dates:
        normalized.append(_parse_date_param(value, param="date").isoformat())
    if not normalized:
        return {}
    placeholders = ", ".join("?" * len(normalized))
    try:
        conn = _connect_ro(db_path)
    except sqlite3.OperationalError:
        return {}
    try:
        rows = conn.execute(
            # placeholders 仅为 "?" 序列；日期值全参数化
            f"SELECT trade_date, code, close_raw FROM market_snapshot "  # noqa: S608
            f"WHERE trade_date IN ({placeholders}) AND close_raw IS NOT NULL",
            normalized,
        ).fetchall()
    finally:
        conn.close()
    out: dict[str, dict[str, float]] = {}
    for trade_date, code, close in rows:
        out.setdefault(str(trade_date), {})[str(code)] = float(close)
    return out


def upsert_security_master(rows: list[dict], db_path: str | Path | None = None) -> int:
    """证券主表 upsert（同 code 覆盖 name/list_date/delist_date/delist_source/updated_at）。

    空 code 行跳过；新值为空（None）时保留已有值（COALESCE），避免部分拉取
    失败把已知字段清空。``delist_date``/``delist_source`` 由退市清单路径
    （``upsert_delisted_masters``）写入；上市清单路径不带这两字段 → 已入库值不被清空。
    """
    init_snapshot_db(db_path)
    now_iso = datetime.now().isoformat()
    payload = []
    for row in rows:
        code = _norm_code(row.get("code"))
        if not code:
            continue
        payload.append(
            (
                code,
                _text(row.get("name")),
                _text(row.get("list_date")),
                _text(row.get("delist_date")),
                _text(row.get("delist_source")),
                _text(row.get("industry_l1")),
                now_iso,
            )
        )
    if not payload:
        return 0
    conn = _connect(db_path)
    try:
        conn.executemany(
            "INSERT INTO security_master "
            "(code, name, list_date, delist_date, delist_source, industry_l1, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(code) DO UPDATE SET "
            "name = COALESCE(excluded.name, security_master.name), "
            "list_date = COALESCE(excluded.list_date, security_master.list_date), "
            "delist_date = COALESCE(excluded.delist_date, security_master.delist_date), "
            "delist_source = COALESCE(excluded.delist_source, security_master.delist_source), "
            "industry_l1 = COALESCE(excluded.industry_l1, security_master.industry_l1), "
            "updated_at = excluded.updated_at",
            payload,
        )
        conn.commit()
        return len(payload)
    finally:
        conn.close()


def update_security_master_industry(
    mapping: dict[str, str], db_path: str | Path | None = None
) -> int:
    """行业门类写入（extend-factor-library-coverage §6）：``{code: industry_l1}``。

    只写非空值（空串/None 跳过，不覆盖已有行业）；返回实际写入行数。行业来源：
    巨潮逐票（``AKShareClient.fetch_industry`` 降级链）与周一主表刷新分支。
    """
    init_snapshot_db(db_path)
    now_iso = datetime.now().isoformat()
    payload: list[tuple[str, str, str]] = []
    for code, industry in mapping.items():
        norm = _norm_code(code)
        value = _text(industry)
        if not norm or not value:
            continue
        payload.append((norm, value, now_iso))
    if not payload:
        return 0
    conn = _connect(db_path)
    try:
        before = conn.total_changes
        conn.executemany(
            "UPDATE security_master SET industry_l1 = ?, updated_at = ? WHERE code = ?",
            [(value, now_iso, code) for code, value, now_iso in payload],
        )
        conn.commit()
        return conn.total_changes - before
    finally:
        conn.close()


def upsert_market_ref_rows(rows: list[dict], db_path: str | Path | None = None) -> int:
    """参考数据侧表幂等 upsert（extend-factor-library-coverage §6）。

    ``rows`` 每行：``trade_date`` / ``code`` / ``float_cap``（元）/ ``total_cap``（元，
    可空）/ ``turnover``（小数）/ ``source``。空 code 或空 trade_date 行跳过；
    同键覆盖（日批重跑幂等）。MUST NOT 修改 ``market_snapshot`` 行。
    """
    init_snapshot_db(db_path)
    now_iso = datetime.now().isoformat()
    payload: list[tuple] = []
    for row in rows:
        code = _norm_code(row.get("code"))
        trade_date = _text(row.get("trade_date"))
        if not code or not trade_date:
            continue
        payload.append(
            (
                trade_date,
                code,
                _num(row.get("float_cap")),
                _num(row.get("total_cap")),
                _num(row.get("turnover")),
                _text(row.get("source")),
                now_iso,
            )
        )
    if not payload:
        return 0
    conn = _connect(db_path)
    try:
        conn.executemany(
            "INSERT INTO market_ref_daily "
            "(trade_date, code, float_cap, total_cap, turnover, source, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(trade_date, code) DO UPDATE SET "
            "float_cap = COALESCE(excluded.float_cap, market_ref_daily.float_cap), "
            "total_cap = COALESCE(excluded.total_cap, market_ref_daily.total_cap), "
            "turnover = COALESCE(excluded.turnover, market_ref_daily.turnover), "
            "source = COALESCE(excluded.source, market_ref_daily.source), "
            "updated_at = excluded.updated_at",
            payload,
        )
        conn.commit()
        return len(payload)
    finally:
        conn.close()


def upsert_delisted_masters(rows: list[dict], db_path: str | Path | None = None) -> int:
    """退市清单落库入口（``AKShareClient.fetch_delist_lists`` 输出形状）→ 返回处理行数。

    与 ``upsert_security_master`` 同表同 upsert 语义（COALESCE 保值、空 code 跳过）；
    本函数是退市清单的**唯一写入口**，供日批主表刷新与运维补数复用。
    抓取侧的 ``source``（``sh_suspend``/``sz_delist``）落库为 ``delist_source``
    （审计留痕：消费方据此区分沪「暂停上市日期」与深「终止上市日期」语义，见 §1.16）；
    显式传入 ``delist_source`` 时以其为准。
    """
    if not rows:
        return 0
    mapped: list[dict] = []
    for row in rows:
        entry = dict(row)
        entry.setdefault("delist_source", row.get("source"))
        mapped.append(entry)
    return upsert_security_master(mapped, db_path=db_path)


def get_security_master(db_path: str | Path | None = None) -> dict[str, dict]:
    """全量证券主表 ``{code: {"name","list_date","delist_date","delist_source","industry_l1"}}``。

    ``build_snapshot_rows`` 的 master 形状（新增键为增量，消费方按需取用）；
    ``delist_date`` 非空 = 已知退市日（语义来源见 ``delist_source``：``sh_suspend``
    = 沪「暂停上市日期」映射、``sz_delist`` = 深「终止上市日期」）；None = 未退市
    或退市日未知。``industry_l1`` = 行业门类（巨潮回填/周一刷新写入；None = 未知）。
    """
    init_snapshot_db(db_path)
    conn = _connect(db_path)
    try:
        master = {
            row["code"]: {
                "name": row["name"],
                "list_date": row["list_date"],
                "delist_date": row["delist_date"],
                "delist_source": row["delist_source"],
                "industry_l1": row["industry_l1"],
            }
            for row in conn.execute(
                "SELECT code, name, list_date, delist_date, delist_source, industry_l1 "
                "FROM security_master"
            ).fetchall()
        }
    finally:
        conn.close()
    return master


def count_delisted_masters(db_path: str | Path | None = None) -> int:
    """主表中 ``delist_date`` 非空的行数（小票通道前置门禁「退市池就绪」判据）。"""
    init_snapshot_db(db_path)
    conn = _connect(db_path)
    try:
        row = conn.execute(
            "SELECT COUNT(*) FROM security_master "
            "WHERE delist_date IS NOT NULL AND TRIM(delist_date) <> ''"
        ).fetchone()
        return int(row[0]) if row else 0
    finally:
        conn.close()


# ── 状态纯函数（T4）：D5 stale 判定（无 IO；API 端点/前端横幅的文案唯一来源）──

_STALE_MESSAGE = "行情快照未更新"
_EXPECTED_AFTER = time(18, 0)
_BEIJING_TZ = ZoneInfo("Asia/Shanghai")


def _to_beijing(now: datetime | None) -> datetime:
    """``now`` 归一到北京时区（评审修正）。

    - ``None`` → 当前北京时间（``datetime.now(ZoneInfo("Asia/Shanghai"))``）；
    - aware 输入 → ``astimezone`` 转北京（UTC 10:30 = 北京 18:30，D5 的 18:00
      边界按北京时间判定）；
    - naive 输入 → 视为已是北京时间本地墙钟（既有调用方惯例），不二次换算。
    """
    if now is None:
        return datetime.now(_BEIJING_TZ)
    if now.tzinfo is not None:
        return now.astimezone(_BEIJING_TZ)
    return now


def compute_snapshot_status(meta: dict[str, str], now: datetime | None = None) -> dict[str, Any]:
    """按 D5 判定快照新鲜度，返回状态端点载荷。

    - ``now`` 取北京时区（评审修正，经 ``_to_beijing``）：None → 当前北京时间；
      aware 先转北京；naive 视为已是北京本地墙钟；
    - ``expected``：日历中 ``<= now.date()`` 的最后一个交易日；无日历/不可解析
      退化「今天是工作日取今天，否则取最近工作日」（不识别节假日，可接受）；
    - ``stale = last_error_date == 今天 或 (now >= 18:00 且 (从未成功 或
      last_success_date < expected))``；
    - 从未尝试（``last_attempt_at`` 不存在，fresh DB）→ 不告警（避免 dev/E2E
      噪音；首次启用后失败即告警）；
    - ``message`` 仅 stale 时非空：逐字「行情快照未更新（数据日期: {latest}）」；
      ``latest`` 为 None 时退化为「行情快照未更新」。
    """
    now = _to_beijing(now)
    latest = meta.get("last_success_date") or None
    last_error = meta.get("last_error") or None
    last_error_date = meta.get("last_error_date") or None

    def _payload(stale: bool) -> dict[str, Any]:
        message = None
        if stale:
            message = f"{_STALE_MESSAGE}（数据日期: {latest}）" if latest else _STALE_MESSAGE
        return {
            "latest_date": latest,
            "stale": stale,
            "message": message,
            "last_error": last_error,
            "last_error_date": last_error_date,
        }

    if "last_attempt_at" not in meta:
        return _payload(False)  # fresh DB：从未尝试，不告警
    if last_error_date == now.date().isoformat():
        return _payload(True)  # 当日失败：立即告警

    if now.time() >= _EXPECTED_AFTER:
        expected = _expected_trade_date(meta.get("trade_calendar"), now.date())
        if latest is None or not _is_on_or_after(latest, expected):
            return _payload(True)
    return _payload(False)


def _expected_trade_date(raw_calendar: str | None, today: date) -> date:
    """日历中 ``<= today`` 的最后一个交易日；无日历/不可解析 → 最近工作日（D5 退化）。"""
    if raw_calendar:
        try:
            decoded = json.loads(raw_calendar)
        except (TypeError, ValueError):
            decoded = None
        dates: list[date] = []
        if isinstance(decoded, list):
            for item in decoded:
                try:
                    dates.append(date.fromisoformat(str(item)))
                except ValueError:
                    continue
        candidates = [d for d in dates if d <= today]
        if candidates:
            return max(candidates)
    day = today
    while day.weekday() >= 5:  # 周六/周日回退至最近工作日
        day -= timedelta(days=1)
    return day


def _is_on_or_after(value: str, expected: date) -> bool:
    """数据日期 >= expected；不可解析 → False（按不新鲜告警，宁可多报不可漏报）。"""
    try:
        return date.fromisoformat(value) >= expected
    except ValueError:
        return False


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
    - spot「无成交」的行按 D2 合成停牌行（有昨行才合成，无价可承则跳过）。
      无成交有两种源形态，任一命中即合成：最新价 NaN/0（缺行同规）；
      最新价有效但 OHLC 全 0 且成交量为 0（源回填昨收的停牌形态，issue #511）；
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
        if _num(raw.get("最新价")) is None or _is_zero_trade_row(raw):
            row = _synthesize_suspended_row(trade_date, code, prev_rows.get(code), master)
        else:
            row = _spot_row(trade_date, code, raw, master)
        if row is not None:
            rows.append(row)

    for code, prev in prev_rows.items():
        if code in seen:
            continue
        if master and code not in master:
            # 已退市：不做正向补行，历史行保持不动。
            # 口径关系（add-microcap-lane-guardrails，见 metrics.md §1.16）：本条是**隐式**
            # 退市判定（不在名单 = 退市），仅服务停牌合成；显式退市判定用 security_master
            # 的 ``delist_date``（池可见性 `list_date ≤ t ≤ delist_date`，见 data/universe.py）。
            # 两者不互相替代：日批 spot 名单不含退市票，隐式口径决定「今天不补行」；
            # delist_date 决定回测/统计池在某历史时点是否可见。
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


def _is_zero_trade_row(raw: Any) -> bool:
    """spot「有价无成交」停牌形态判定（issue #511）。

    东财/新浪对停牌标的的 spot 行可能回填昨收（最新价=昨收而非 NaN/0），此时
    「今开=最高=最低=0 且 成交量为 0」是唯一可判信号；正常交易行 OHLC 有效，
    不会命中。成交量缺失（None）与 0 同判——OHLC 全 0 本身已是无成交。
    """
    open_ = _num(raw.get("今开"))
    high = _num(raw.get("最高"))
    low = _num(raw.get("最低"))
    volume = _num(raw.get("成交量"))
    return open_ == 0.0 and high == 0.0 and low == 0.0 and volume in (None, 0.0)


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


# 公开别名：跨模块消费方（outcome/regime 日批）走 ``norm_code``，不负下划线私有名
norm_code = _norm_code


def _listed_days(trade_date: str, list_date: str | None) -> int | None:
    """上市自然日数 D3；list_date 缺失/不可解析 → None。"""
    if not list_date:
        return None
    try:
        return (date.fromisoformat(trade_date) - date.fromisoformat(list_date)).days
    except ValueError:
        return None
