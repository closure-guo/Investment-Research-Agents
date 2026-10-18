"""screening 库：L2 名单快照、L3 triage 决策、日批批次与精选池、移除审计
（add-screening-candidate-llm T1/T2 + add-screening-pipeline T1 + 时效性校验接线）。

表（C5 / pipeline Global Constraints，逐字）：

- ``l2_candidates(trade_date, pool, symbol, composite_score, rank,
  PRIMARY KEY(trade_date, pool, symbol))``：L2 打分名单日快照（按档隔离），幂等 upsert
  （同日重跑覆盖）。**日批重跑走 ``rewrite_day_l2_board``**（当日**该档**榜原子替换：
  旧行不留残——表的读取契约是「行的存在性 ≡ 当日在该档榜内」）；
- ``triage_decisions(trade_date, pool, symbol, verdict, reason, degraded, created_at,
  PRIMARY KEY(trade_date, pool, symbol))``：L3 keep/drop 决策（按档隔离），幂等 upsert；
  日批重跑时当日**该档**旧决策随 ``rewrite_day_l2_board`` 同事务清空后重写。
  **档位隔离（评审 C1）**：16:45 小票通道与 15:40 主档同日各跑一轮时，两表清行/写入
  一律按 ``pool`` 收窄——主档当日榜与决策逐行不变（主档「连续在榜 ≥3 日」门槛与
  评测三腿取样都读主档行）；读侧 ``pool`` 缺省 ``'main'``。
  两表供消融（±L3 粗筛）与前向命中率分层（后续 change）查询，是稳定性门槛查询的
  唯一数据源；
- ``screening_batches(trade_date, pool, status, error, started_at, finished_at,
  l1_passed, l2_top, l3_keep, pool_size, selection_mode, frontier_gate)``：
  日批批次行（``status ∈ {ok, failed}``；``pool ∈ {main, microcap}``——主档/小票通道
  同日各一行，主键 ``(trade_date, pool)``（add-microcap-lane-guardrails）；
  ``frontier_gate`` = 小票通道前置门禁记录字段（主档恒 NULL）；
  失败批次与当日池零行的不变量由 ``record_screening_batch`` 单事务保证，清空**按 pool 收窄**）；
- ``selection_pool(trade_date, pool, symbol, composite_score, rank, consecutive_days,
  l3_verdict, l3_reason, factor_contribs_json, PK(trade_date, pool, symbol))``：
  精选池（keep ∩ 连续在榜门槛达标），同 ``(交易日, 档位)`` 写入为**原子替换**；
  读侧 ``pool`` 缺省 ``'main'``（既有页面/接口口径零变化）；
- ``screening_removals(trade_date, symbol, reason, detail, removed_at,
  PK(trade_date, symbol, reason))``：分析前时效性校验的移除审计行（``trade_date``
  = 校验基准日 = 快照日；同标的多因多行，幂等覆盖）。

稳定性口径（C4）：``consecutive_days_in_top`` 从 ``as_of`` 起向前沿**该档表内交易日
轴**（``l2_candidates`` 的 DISTINCT ``trade_date``，按 ``pool`` 收窄）逐日查该 symbol 是否在榜，
返回尾部连续段长度；``as_of`` 当日不在榜（含当日无任何榜单行）→ 0。表里只有
交易日行 → 非交易日（周末/节假日）天然不在轴内、不打断连续段；中间断档（某交易
日榜单存在但该 symbol 不在）→ 重新计数。``eligible_for_selection`` = 计数 ≥
``min_days``（默认 3，可配置）。

连接纪律与 ``data/snapshot.py`` 同款：WAL + busy_timeout 短连接，库路径调用期读取
``SCREENING_DB_PATH``（默认 ``data/screening.db``），测试经 ``db_path`` 显式注入。
"""

from __future__ import annotations

import json
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
# 注：l2_candidates / triage_decisions 的索引引用 pool 列，**不在**本 DDL 内创建——
# 老库（表已存在、缺 pool 列）上建索引会报 no such column，须待主键重建后由
# ``init_screening_db`` 显式循环补建（见 ``_L2_INDEX_SQL`` / ``_TRIAGE_INDEX_SQL``）。
SCREENING_DDL = """
CREATE TABLE IF NOT EXISTS l2_candidates (
  trade_date      TEXT NOT NULL,
  pool            TEXT NOT NULL DEFAULT 'main',
  symbol          TEXT NOT NULL,
  composite_score REAL NOT NULL,
  rank            INTEGER NOT NULL,
  PRIMARY KEY (trade_date, pool, symbol)
);
CREATE TABLE IF NOT EXISTS triage_decisions (
  trade_date TEXT NOT NULL,
  pool       TEXT NOT NULL DEFAULT 'main',
  symbol     TEXT NOT NULL,
  verdict    TEXT NOT NULL,
  reason     TEXT NOT NULL,
  degraded   INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL,
  PRIMARY KEY (trade_date, pool, symbol)
);
CREATE TABLE IF NOT EXISTS screening_batches (
  trade_date  TEXT NOT NULL,
  pool        TEXT NOT NULL DEFAULT 'main',
  status      TEXT NOT NULL,
  error       TEXT,
  started_at  TEXT NOT NULL,
  finished_at TEXT NOT NULL,
  l1_passed   INTEGER NOT NULL DEFAULT 0,
  l2_top      INTEGER NOT NULL DEFAULT 0,
  l3_keep     INTEGER NOT NULL DEFAULT 0,
  pool_size   INTEGER NOT NULL DEFAULT 0,
  selection_mode TEXT NOT NULL DEFAULT 'factor',
  frontier_gate  TEXT,
  exec_constraints TEXT,
  PRIMARY KEY (trade_date, pool)
);
CREATE TABLE IF NOT EXISTS selection_pool (
  trade_date           TEXT NOT NULL,
  pool                 TEXT NOT NULL DEFAULT 'main',
  symbol               TEXT NOT NULL,
  composite_score      REAL NOT NULL,
  rank                 INTEGER NOT NULL,
  consecutive_days     INTEGER NOT NULL,
  l3_verdict           TEXT NOT NULL,
  l3_reason            TEXT NOT NULL,
  factor_contribs_json TEXT NOT NULL,
  PRIMARY KEY (trade_date, pool, symbol)
);
CREATE INDEX IF NOT EXISTS idx_selection_pool_date_rank
  ON selection_pool (trade_date, rank);
CREATE TABLE IF NOT EXISTS screening_removals (
  trade_date TEXT NOT NULL,
  symbol     TEXT NOT NULL,
  reason     TEXT NOT NULL,
  detail     TEXT NOT NULL,
  removed_at TEXT NOT NULL,
  PRIMARY KEY (trade_date, symbol, reason)
);
"""

# 批次状态（Global Constraints 逐字）：ok / failed
BATCH_STATUSES: tuple[str, ...] = ("ok", "failed")
# 选择模式（add-screening-empty-library-fallback）：factor = 因子打分路径；fallback = 流动性降级
SELECTION_MODES: tuple[str, ...] = ("factor", "fallback")
# 通道档位（add-microcap-lane-guardrails）：main = 主档（默认，既有行为）；microcap = 小票通道
POOL_MAIN = "main"
POOL_MICROCAP = "microcap"
POOLS: tuple[str, ...] = (POOL_MAIN, POOL_MICROCAP)

# 稳定性门槛默认值（Global Constraints：默认连续 3 个交易日）
DEFAULT_MIN_DAYS = 3

# 批次历史窗口（add-daily-ops-page T1）：默认近 20 批次；上限 100 防一次倾倒全表
DEFAULT_BATCH_HISTORY_LIMIT = 20
MAX_BATCH_HISTORY_LIMIT = 100


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


def _table_pk_columns(conn: sqlite3.Connection, table: str) -> list[str]:
    """表主键列名（按 ``PRAGMA table_info`` 的 pk 序号升序）；无主键 → []。"""
    columns = [
        (int(row[5]), str(row[1]))
        for row in conn.execute(f"PRAGMA table_info({table})")  # noqa: S608
    ]
    return [name for pk, name in sorted(columns) if pk > 0]


# 批次/池表重建（加 pool 进主键）的语句序列：列集合与 SCREENING_DDL 逐字一致。
# 逐条执行（不用 executescript——它不隐式包事务）并经 ``_rebuild_in_transaction``
# 显式 BEGIN IMMEDIATE/COMMIT，使 DROP 也可回滚（评审 F3）。
_BATCH_MIGRATE_TABLE = "screening_batches__migrate"
_POOL_MIGRATE_TABLE = "selection_pool__migrate"
_POOL_INDEX_SQL = (
    "CREATE INDEX IF NOT EXISTS idx_selection_pool_date_rank ON selection_pool (trade_date, rank)"
)
_BATCH_TABLE_REBUILD_SQL: tuple[str, ...] = (
    "CREATE TABLE screening_batches__migrate ("
    "trade_date TEXT NOT NULL, pool TEXT NOT NULL DEFAULT 'main', status TEXT NOT NULL, "
    "error TEXT, started_at TEXT NOT NULL, finished_at TEXT NOT NULL, "
    "l1_passed INTEGER NOT NULL DEFAULT 0, l2_top INTEGER NOT NULL DEFAULT 0, "
    "l3_keep INTEGER NOT NULL DEFAULT 0, pool_size INTEGER NOT NULL DEFAULT 0, "
    "selection_mode TEXT NOT NULL DEFAULT 'factor', frontier_gate TEXT, "
    "exec_constraints TEXT, PRIMARY KEY (trade_date, pool))",
    "INSERT INTO screening_batches__migrate (trade_date, pool, status, error, started_at, "
    "finished_at, l1_passed, l2_top, l3_keep, pool_size, selection_mode, frontier_gate, "
    "exec_constraints) "
    "SELECT trade_date, 'main', status, error, started_at, finished_at, l1_passed, l2_top, "
    "l3_keep, pool_size, selection_mode, frontier_gate, exec_constraints FROM screening_batches",
    "DROP TABLE screening_batches",
    "ALTER TABLE screening_batches__migrate RENAME TO screening_batches",
)
_POOL_TABLE_REBUILD_SQL: tuple[str, ...] = (
    "CREATE TABLE selection_pool__migrate ("
    "trade_date TEXT NOT NULL, pool TEXT NOT NULL DEFAULT 'main', symbol TEXT NOT NULL, "
    "composite_score REAL NOT NULL, rank INTEGER NOT NULL, consecutive_days INTEGER NOT NULL, "
    "l3_verdict TEXT NOT NULL, l3_reason TEXT NOT NULL, factor_contribs_json TEXT NOT NULL, "
    "PRIMARY KEY (trade_date, pool, symbol))",
    "INSERT INTO selection_pool__migrate (trade_date, pool, symbol, composite_score, rank, "
    "consecutive_days, l3_verdict, l3_reason, factor_contribs_json) "
    "SELECT trade_date, 'main', symbol, composite_score, rank, consecutive_days, l3_verdict, "
    "l3_reason, factor_contribs_json FROM selection_pool",
    "DROP TABLE selection_pool",
    "ALTER TABLE selection_pool__migrate RENAME TO selection_pool",
    # DROP TABLE 连带删掉旧索引：重建后恢复（与 SCREENING_DDL 同名同形）
    _POOL_INDEX_SQL,
)
# 中断恢复语句（``*__migrate`` 留存 = 上次重建中断）：换入前先过**行数守卫**
# （见 ``_recover_interrupted_rebuild``）——仅 migrate 行数 >= 正式表（或正式表缺失）
# 才视为权威副本并 DROP 正式表后改名换入；否则清理残留、保留正式表。
_L2_INDEX_SQL: tuple[str, ...] = (
    "CREATE INDEX IF NOT EXISTS idx_l2_candidates_date_rank "
    "ON l2_candidates (trade_date, pool, rank)",
    "CREATE INDEX IF NOT EXISTS idx_l2_candidates_symbol_date "
    "ON l2_candidates (symbol, trade_date, pool)",
)
_TRIAGE_INDEX_SQL: tuple[str, ...] = (
    "CREATE INDEX IF NOT EXISTS idx_triage_decisions_symbol_date "
    "ON triage_decisions (symbol, trade_date, pool)",
)
# L2 榜 / triage 决策表重建（加 pool 进主键，评审 C1：小票通道不得覆盖主档当日榜与决策）
_L2_TABLE_REBUILD_SQL: tuple[str, ...] = (
    "CREATE TABLE l2_candidates__migrate ("
    "trade_date TEXT NOT NULL, pool TEXT NOT NULL DEFAULT 'main', symbol TEXT NOT NULL, "
    "composite_score REAL NOT NULL, rank INTEGER NOT NULL, "
    "PRIMARY KEY (trade_date, pool, symbol))",
    "INSERT INTO l2_candidates__migrate (trade_date, pool, symbol, composite_score, rank) "
    "SELECT trade_date, 'main', symbol, composite_score, rank FROM l2_candidates",
    "DROP TABLE l2_candidates",
    "ALTER TABLE l2_candidates__migrate RENAME TO l2_candidates",
    *_L2_INDEX_SQL,
)
_TRIAGE_TABLE_REBUILD_SQL: tuple[str, ...] = (
    "CREATE TABLE triage_decisions__migrate ("
    "trade_date TEXT NOT NULL, pool TEXT NOT NULL DEFAULT 'main', symbol TEXT NOT NULL, "
    "verdict TEXT NOT NULL, reason TEXT NOT NULL, degraded INTEGER NOT NULL DEFAULT 0, "
    "created_at TEXT NOT NULL, PRIMARY KEY (trade_date, pool, symbol))",
    "INSERT INTO triage_decisions__migrate "
    "(trade_date, pool, symbol, verdict, reason, degraded, created_at) "
    "SELECT trade_date, 'main', symbol, verdict, reason, degraded, created_at FROM triage_decisions",
    "DROP TABLE triage_decisions",
    "ALTER TABLE triage_decisions__migrate RENAME TO triage_decisions",
    *_TRIAGE_INDEX_SQL,
)
_RECOVERY_SQL: dict[str, tuple[str, ...]] = {
    "screening_batches": (
        "DROP TABLE IF EXISTS screening_batches",
        "ALTER TABLE screening_batches__migrate RENAME TO screening_batches",
    ),
    "selection_pool": (
        "DROP TABLE IF EXISTS selection_pool",
        "ALTER TABLE selection_pool__migrate RENAME TO selection_pool",
        _POOL_INDEX_SQL,
    ),
    "l2_candidates": (
        "DROP TABLE IF EXISTS l2_candidates",
        "ALTER TABLE l2_candidates__migrate RENAME TO l2_candidates",
        *_L2_INDEX_SQL,
    ),
    "triage_decisions": (
        "DROP TABLE IF EXISTS triage_decisions",
        "ALTER TABLE triage_decisions__migrate RENAME TO triage_decisions",
        *_TRIAGE_INDEX_SQL,
    ),
}


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)
    ).fetchone()
    return row is not None


def _run_statements_in_transaction(conn: sqlite3.Connection, statements: tuple[str, ...]) -> None:
    """显式事务内逐条执行（``BEGIN IMMEDIATE`` … ``COMMIT``）；任一条失败整体 ``ROLLBACK``。

    必须关掉 sqlite3 的 legacy 隐式事务控制（``isolation_level = None``）：默认模式下
    Python 会在 DDL 前隐式提交挂起事务、且 ``executescript`` 不包事务——那样 DROP 之后
    失败会留下「原表已删、migrate 表残留」的半态（评审 F3 复现）。
    """
    restore = conn.isolation_level
    conn.isolation_level = None
    try:
        conn.execute("BEGIN IMMEDIATE")
        try:
            for statement in statements:
                conn.execute(statement)
        except BaseException:
            conn.execute("ROLLBACK")
            raise
        conn.execute("COMMIT")
    finally:
        conn.isolation_level = restore


def _table_row_count(conn: sqlite3.Connection, table: str) -> int:
    """表行数（表名来自模块常量/固定后缀，非外部输入）。"""
    row = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()  # noqa: S608
    return int(row[0]) if row else 0


def _recover_interrupted_rebuild(conn: sqlite3.Connection, table: str) -> bool:
    """残留 ``<table>__migrate`` → 按**行数守卫**恢复/清理；返回是否换入了 migrate 表。

    换入条件（round 3 评审）：正式表缺失 **或** ``rows(migrate) >= rows(real)``。

    - 「INSERT 完整、DROP 后 RENAME 前中断」→ migrate 持权威数据（且正式表缺失或为空壳）
      → 换入；
    - 「CREATE 之后、INSERT 之前中断」→ migrate 为空/更少、正式表仍持全部旧行 →
      **MUST NOT 换入**（否则静默丢历史行），改为 DROP 残留 migrate、保留正式表，
      交由同一次 init 的 ② 列补齐 + ③ 事务化主键重建继续完成迁移（幂等，不二次损坏）。

    不得因为「正式表已存在」一律跳过：中断后的下一次 ``CREATE TABLE IF NOT EXISTS``
    会补出空壳正式表，跳过即静默丢旧行（评审 F3）——故判据是行数而非存在性。
    """
    migrate_table = f"{table}__migrate"
    if not _table_exists(conn, migrate_table):
        return False
    if _table_exists(conn, table) and _table_row_count(conn, migrate_table) < _table_row_count(
        conn, table
    ):
        # 中断早于 INSERT：migrate 不权威 → 清理残留，保留正式表（后续步骤完成迁移）
        _run_statements_in_transaction(conn, (f"DROP TABLE {migrate_table}",))
        return False
    _run_statements_in_transaction(conn, _RECOVERY_SQL[table])
    return True


# 批次表增量列（ALTER 补齐；键 = 列名，值 = ADD COLUMN DDL）。重建 SQL 引用这些列，
# 故 init 顺序固定为「恢复 → 列补齐 → 主键重建」。
_BATCH_COLUMN_MIGRATIONS: tuple[tuple[str, str], ...] = (
    (
        "selection_mode",
        "ALTER TABLE screening_batches ADD COLUMN selection_mode TEXT NOT NULL DEFAULT 'factor'",
    ),
    ("frontier_gate", "ALTER TABLE screening_batches ADD COLUMN frontier_gate TEXT"),
    ("exec_constraints", "ALTER TABLE screening_batches ADD COLUMN exec_constraints TEXT"),
)


def _ensure_batch_columns(conn: sqlite3.Connection) -> None:
    """旧库缺失的批次表列 → 幂等 ALTER 补齐（新增列只在此声明，迁移单点）。"""
    columns = {row[1] for row in conn.execute("PRAGMA table_info(screening_batches)")}
    for name, ddl in _BATCH_COLUMN_MIGRATIONS:
        if name not in columns:
            conn.execute(ddl)


def _rebuild_pool_pk_if_needed(conn: sqlite3.Connection) -> None:
    """旧库主键（单列 ``trade_date``）→ 重建为含 ``pool`` 的复合主键（幂等）。

    ``add-microcap-lane-guardrails``：小票通道与主档同一交易日各写一行批次 →
    旧主键会互相覆盖（且 upsert 会连带清掉对方当日池行）。重建把旧行一律落
    ``pool='main'``（既有批次全是主档产物）；列集合与新建库 DDL 一致。
    已含 ``pool`` 主键（新库/已迁移）→ 无操作。重建为**显式事务**（见
    ``_run_statements_in_transaction``）：中途失败整体回滚，不留「已 DROP 未 RENAME」半态。
    """
    for table, statements in (
        ("screening_batches", _BATCH_TABLE_REBUILD_SQL),
        ("selection_pool", _POOL_TABLE_REBUILD_SQL),
        ("l2_candidates", _L2_TABLE_REBUILD_SQL),
        ("triage_decisions", _TRIAGE_TABLE_REBUILD_SQL),
    ):
        if "pool" not in _table_pk_columns(conn, table):
            _run_statements_in_transaction(conn, statements)
    # 恢复路径可能重建过这些表：索引兜底恢复（幂等）
    for index_sql in (_POOL_INDEX_SQL, *_L2_INDEX_SQL, *_TRIAGE_INDEX_SQL):
        conn.execute(index_sql)


def init_screening_db(db_path: str | Path | None = None) -> Path:
    """幂等建表 + 索引 + 增量迁移，返回实际使用的库路径。

    迁移（顺序固定，三者互相依赖）：
    ① 残留 ``*__migrate``（上次重建中断）→ 按**行数守卫**恢复/清理
       （migrate 权威才换入；migrate 为空/更少则保留正式表并清理残留，
       见 ``_recover_interrupted_rebuild``）；
    ② 批次表缺列 → 幂等 ALTER 补齐（``selection_mode`` / ``frontier_gate`` /
       ``exec_constraints``，见 ``_BATCH_COLUMN_MIGRATIONS``）；
    ③ 旧库主键为单列 ``trade_date`` → 重建为含 ``pool`` 的复合主键
       （add-microcap-lane-guardrails，旧行落 ``pool='main'``；显式事务，失败整体回滚）。
    ②在①之后：恢复可能换入旧版形状的表，列补齐必须在恢复之后重新判定。
    """
    path = Path(db_path) if db_path else _default_db_path()
    conn = _connect(path)
    try:
        conn.executescript(SCREENING_DDL)
        for table in (
            "screening_batches",
            "selection_pool",
            "l2_candidates",
            "triage_decisions",
        ):  # ① 数据优先（行数守卫）
            _recover_interrupted_rebuild(conn, table)
        _ensure_batch_columns(conn)  # ② 列补齐（重建 SQL 引用这些列）
        _rebuild_pool_pk_if_needed(conn)  # ③ 主键重建（幂等）
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


_L2_UPSERT_SQL = (
    "INSERT INTO l2_candidates (trade_date, pool, symbol, composite_score, rank) "
    "VALUES (?, ?, ?, ?, ?) "
    "ON CONFLICT(trade_date, pool, symbol) DO UPDATE SET "
    "composite_score = excluded.composite_score, rank = excluded.rank"
)


def _l2_payload(
    trade_date: str, candidates: Sequence[Candidate], *, pool: str = POOL_MAIN
) -> list[tuple[str, str, str, float, int]]:
    """Candidate 序列 → ``l2_candidates`` payload（逐行 fail loud，坏值不落库）。"""
    payload: list[tuple[str, str, str, float, int]] = []
    for candidate in candidates:
        rank = candidate.rank
        if isinstance(rank, bool) or not isinstance(rank, int) or rank < 1:
            raise ValueError(f"rank 必须是正整数: {rank!r}（symbol={candidate.symbol}）")
        payload.append(
            (
                trade_date,
                pool,
                candidate.symbol,
                _finite_float(candidate.composite_score, "composite_score"),
                rank,
            )
        )
    return payload


def record_l2_candidates(
    trade_date: str,
    candidates: Sequence[Candidate],
    db_path: str | Path | None = None,
    *,
    pool: str = POOL_MAIN,
) -> int:
    """写入某一交易日的 L2 名单（整批同一交易日）；同 ``(trade_date, pool, symbol)`` 幂等覆盖。

    ``pool`` 缺省 ``'main'``（既有调用路径逐字节不变）；小票通道显式传
    ``pool='microcap'``——两档榜单互不覆盖（评审 C1）。

    入参约定：**截断后的 Top-N 名单**（``rank_universe(top_n=…)`` 输出，日批经
    ``pipeline`` 全量迁移）——表内行的**存在性**即「当日在该榜内」，稳定性查询
    （``consecutive_days_in_top``）据此判定在榜；传入未截断全量会虚增在榜天数。

    本函数是**逐行 upsert**（增量写/造数入口）：同日多次写不同 symbol 会累积。
    日批同日重跑须用 ``rewrite_day_l2_board``（当日榜原子替换，旧行不残留）。

    ``composite_score`` 须有限、``rank`` 须为正整数（fail loud，不落坏值）；
    空批 → 0，不建表（无行可写，连库都不碰）。返回写入行数。
    """
    normalized = _parse_trade_date(trade_date)
    if pool not in POOLS:
        raise ValueError(f"非法池 pool {pool!r}；允许: {list(POOLS)}")
    if not candidates:
        return 0
    payload = _l2_payload(normalized, candidates, pool=pool)
    init_screening_db(db_path)
    conn = _connect(db_path)
    try:
        conn.executemany(_L2_UPSERT_SQL, payload)
        conn.commit()
        return len(payload)
    finally:
        conn.close()


def rewrite_day_l2_board(
    trade_date: str,
    candidates: Sequence[Candidate],
    db_path: str | Path | None = None,
    *,
    pool: str = POOL_MAIN,
) -> int:
    """日批重跑入口：当日**该档** L2 榜单**原子替换**（单事务），返回写入行数。

    与 ``record_l2_candidates`` 的区别 = 同日重跑语义：upsert 只保证同键覆盖，本轮
    不再产出的旧行会残留；本函数把「表内行的**存在性** ≡ 当日在该榜内」（C4 稳定性
    查询的唯一依据）做成硬不变量——先清当日 ``l2_candidates`` 旧行再写新名单，
    残留行无法把已出榜标的的 ``consecutive_days_in_top`` 虚增。

    ``triage_decisions`` 当日**该档**旧行**同事务**清除：调用方（日批）紧随其后经
    ``run_triage`` 重写当日决策，旧决策不得跨轮幸存。清行与新行同一事务提交 →
    中途失败回滚后仍是上一轮的行（不会留下「已清未写」的空榜）。

    **档位隔离**（评审 C1）：清行与新行都按 ``pool`` 收窄——16:45 小票通道与 15:40
    主档同日各跑一轮时，MUST NOT 触碰对方当日榜与决策（主档「连续在榜」门槛与
    评测三腿取样都读主档行）。

    空批 → 当日榜替换为空（清旧行、写 0 行；日批「候选为空」即当日无在榜标的）。
    任一坏值（非规范日期/rank/分值）→ 整批不落且不清旧行。
    """
    normalized = _parse_trade_date(trade_date)
    if pool not in POOLS:
        raise ValueError(f"非法池 pool {pool!r}；允许: {list(POOLS)}")
    payload = _l2_payload(normalized, candidates, pool=pool)
    init_screening_db(db_path)
    conn = _connect(db_path)
    try:
        conn.execute(
            "DELETE FROM l2_candidates WHERE trade_date = ? AND pool = ?", (normalized, pool)
        )
        conn.execute(
            "DELETE FROM triage_decisions WHERE trade_date = ? AND pool = ?", (normalized, pool)
        )
        if payload:
            conn.executemany(_L2_UPSERT_SQL, payload)
        conn.commit()
        return len(payload)
    finally:
        conn.close()


def list_l2_candidates(
    trade_date: str, db_path: str | Path | None = None, *, pool: str = POOL_MAIN
) -> list[dict[str, Any]]:
    """某交易日某档 L2 名单（按 rank 升序，同 rank 按 symbol 升序）；无记录 → 空列表。

    ``pool`` 缺省 ``'main'``（既有调用路径/评测取样口径不变）。
    """
    normalized = _parse_trade_date(trade_date)
    if pool not in POOLS:
        raise ValueError(f"非法池 pool {pool!r}；允许: {list(POOLS)}")
    init_screening_db(db_path)
    conn = _connect(db_path)
    try:
        rows = conn.execute(
            "SELECT trade_date, pool, symbol, composite_score, rank FROM l2_candidates "
            "WHERE trade_date = ? AND pool = ? ORDER BY rank, symbol",
            (normalized, pool),
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


# ── 在榜稳定性门槛（C4） ────────────────────────────────────────────────────


def consecutive_days_in_top(
    symbol: str, as_of: str, *, db_path: str | Path | None = None, pool: str = POOL_MAIN
) -> int:
    """``symbol`` 截至 ``as_of``（含）**该档**的连续在榜交易日数（断档重新计数，C4）。

    ``pool`` 缺省 ``'main'``（主档口径逐字节不变）；小票通道显式传 ``pool='microcap'``
    ——两档榜单互不干扰计数（评审 C1）。

    沿**表内交易日轴**（``l2_candidates`` 的 DISTINCT ``trade_date ≤ as_of``）自
    ``as_of`` 向前逐日判定：``as_of`` 当日不在榜（含当日无任何榜单行 / 空库）→ 0；
    遇首个不在榜交易日即停。表里只有交易日行 → 非交易日不打断（不产生缺口）。
    """
    normalized = _parse_trade_date(as_of)
    if pool not in POOLS:
        raise ValueError(f"非法池 pool {pool!r}；允许: {list(POOLS)}")
    init_screening_db(db_path)
    conn = _connect(db_path)
    try:
        date_axis = [
            str(row[0])
            for row in conn.execute(
                "SELECT DISTINCT trade_date FROM l2_candidates "
                "WHERE trade_date <= ? AND pool = ? ORDER BY trade_date DESC",
                (normalized, pool),
            ).fetchall()
        ]
        if not date_axis or date_axis[0] != normalized:
            return 0
        symbol_dates = {
            str(row[0])
            for row in conn.execute(
                "SELECT trade_date FROM l2_candidates "
                "WHERE symbol = ? AND trade_date <= ? AND pool = ?",
                (symbol, normalized, pool),
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
    symbol: str,
    as_of: str,
    *,
    min_days: int = DEFAULT_MIN_DAYS,
    db_path: str | Path | None = None,
    pool: str = POOL_MAIN,
) -> bool:
    """精选池资格：**该档**连续在榜日数 ≥ ``min_days``（默认 3，可配置；须正整数）。

    ``pool`` 缺省 ``'main'``（主档门槛口径逐字节不变）。
    """
    if isinstance(min_days, bool) or not isinstance(min_days, int) or min_days < 1:
        raise ValueError(f"min_days 必须是正整数: {min_days!r}")
    return consecutive_days_in_top(symbol, as_of, db_path=db_path, pool=pool) >= min_days


# ── triage 决策（C5 决策部分） ──────────────────────────────────────────────


def record_triage_decisions(
    trade_date: str,
    decisions: Sequence[TriageDecision],
    db_path: str | Path | None = None,
    *,
    pool: str = POOL_MAIN,
) -> int:
    """批量写入某交易日**该档**的 L3 决策（单连接 executemany + 单次 commit，T1 评审修正）。

    ``pool`` 缺省 ``'main'``（既有调用路径逐字节不变）；小票通道显式传
    ``pool='microcap'``——两档决策互不覆盖（评审 C1）。

    批量路径是唯一写入口：``run_triage`` 每批只调一次本函数，避免逐行
    ``init_screening_db`` + 新建连接 + 独立 commit 的 N 倍开销。全部 ``verdict``
    先校验（合同单一来源；任一非法 → ``ValueError``，整批不落）；同
    ``(trade_date, symbol)`` **幂等**覆盖（upsert，``created_at`` 为本次写入时刻）；
    空批 → 0，不建表。返回写入行数。
    """
    normalized = _parse_trade_date(trade_date)
    if pool not in POOLS:
        raise ValueError(f"非法池 pool {pool!r}；允许: {list(POOLS)}")
    if not decisions:
        return 0
    payload: list[tuple[str, str, str, str, str, int, str]] = []
    now = datetime.now().isoformat()
    for decision in decisions:
        if decision.verdict not in VERDICTS:
            raise ValueError(f"非法 verdict {decision.verdict!r}；允许: {list(VERDICTS)}")
        payload.append(
            (
                normalized,
                pool,
                decision.symbol,
                decision.verdict,
                decision.reason,
                int(decision.degraded),
                now,
            )
        )
    init_screening_db(db_path)
    conn = _connect(db_path)
    try:
        conn.executemany(
            "INSERT INTO triage_decisions "
            "(trade_date, pool, symbol, verdict, reason, degraded, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(trade_date, pool, symbol) DO UPDATE SET "
            "verdict = excluded.verdict, reason = excluded.reason, "
            "degraded = excluded.degraded, created_at = excluded.created_at",
            payload,
        )
        conn.commit()
        return len(payload)
    finally:
        conn.close()


def record_triage_decision(
    trade_date: str,
    decision: TriageDecision,
    db_path: str | Path | None = None,
    *,
    pool: str = POOL_MAIN,
) -> None:
    """写入某标的某交易日的 L3 决策；同 ``(trade_date, symbol)`` **幂等**覆盖（upsert）。

    ``verdict`` 必须 ∈ ``candidate_llm.VERDICTS``（合同单一来源），非法值 fail loud
    （不落坏值）；``created_at`` 为写入时刻（同日重跑覆盖为最新）。单行写入是
    ``record_triage_decisions`` 的薄封装——批量路径是唯一实现，语义恒一致。
    """
    record_triage_decisions(trade_date, [decision], db_path=db_path, pool=pool)


def list_triage_decisions(
    *,
    trade_date: str | None = None,
    symbol: str | None = None,
    db_path: str | Path | None = None,
    pool: str = POOL_MAIN,
) -> list[dict[str, Any]]:
    """某档 L3 决策列表（按 trade_date、symbol 升序）；``trade_date``/``symbol`` 可选过滤。

    ``pool`` 缺省 ``'main'``（既有调用路径/评测取样口径不变）；小票通道显式传
    ``pool='microcap'``。``degraded`` 为 SQLite 0/1 原值（与 snapshot/library 读出口径
    一致，不隐式转 bool）。
    """
    if pool not in POOLS:
        raise ValueError(f"非法池 pool {pool!r}；允许: {list(POOLS)}")
    init_screening_db(db_path)
    sql = (
        "SELECT trade_date, pool, symbol, verdict, reason, degraded, created_at "
        "FROM triage_decisions"
    )
    conditions: list[str] = ["pool = ?"]
    params: list[Any] = [pool]
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


# ── 日批批次与精选池（add-screening-pipeline T1；S1 落库口径） ──────────────


def _parse_json_object(raw: Any) -> dict[str, Any] | None:
    """JSON 文本 → dict；缺失/坏 JSON/非对象 → None（防御性读取，不抛）。"""
    if not raw:
        return None
    try:
        decoded = json.loads(raw)
    except (TypeError, ValueError):
        return None
    return decoded if isinstance(decoded, dict) else None


def _exec_constraints_json(value: Any) -> str | None:
    """建议执行约束 dict → JSON 文本（None 透传；非空 dict 之外 → ``ValueError``）。"""
    if value is None:
        return None
    if not isinstance(value, dict) or not value:
        raise ValueError(f"exec_constraints 必须是 None 或非空 dict: {value!r}")
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"exec_constraints 不可 JSON 序列化: {exc}") from exc


def _batch_row(row: sqlite3.Row) -> dict[str, Any]:
    """批次行 → dict（``exec_constraints`` JSON 解析回 dict/None）。"""
    entry = dict(row)
    entry["exec_constraints"] = _parse_json_object(entry.get("exec_constraints"))
    return entry


def _non_negative_int(value: Any, field_name: str) -> int:
    """计数入参：非负整数（bool 除外）→ int；否则 fail loud。"""
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{field_name} 必须是非负整数: {value!r}")
    return value


def _pool_row_payload(
    trade_date: str, row: dict[str, Any], *, pool: str = POOL_MAIN
) -> tuple[Any, ...]:
    """精选池行 dict → SQLite payload（字段 fail loud，坏值不入库）。

    输出列序与 ``selection_pool`` 表列一致：``(trade_date, pool, symbol, ...)``。
    """
    if not isinstance(row, dict):
        raise ValueError(f"池行必须是 dict: {row!r}")
    symbol = str(row.get("symbol") or "").strip()
    if not symbol:
        raise ValueError(f"池行 symbol 不能为空: {row!r}")
    verdict = row.get("l3_verdict")
    if verdict not in VERDICTS:
        raise ValueError(f"非法 l3_verdict {verdict!r}；允许: {list(VERDICTS)}")
    reason = row.get("l3_reason")
    if not isinstance(reason, str) or not reason.strip():
        raise ValueError(f"池行 l3_reason 不能为空: {row!r}（symbol={symbol}）")
    rank = row.get("rank")
    if isinstance(rank, bool) or not isinstance(rank, int) or rank < 1:
        raise ValueError(f"池行 rank 必须是正整数: {rank!r}（symbol={symbol}）")
    consecutive_days = row.get("consecutive_days")
    if (
        isinstance(consecutive_days, bool)
        or not isinstance(consecutive_days, int)
        or consecutive_days < 1
    ):
        raise ValueError(
            f"池行 consecutive_days 必须是正整数: {consecutive_days!r}（symbol={symbol}）"
        )
    contribs = row.get("factor_contribs") or []
    try:
        contribs_json = json.dumps(contribs, ensure_ascii=False, sort_keys=True)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"池行 factor_contribs 不可 JSON 序列化: {exc}") from exc
    return (
        trade_date,
        pool,
        symbol,
        _finite_float(row.get("composite_score"), "composite_score"),
        rank,
        consecutive_days,
        verdict,
        reason,
        contribs_json,
    )


def record_screening_batch(
    trade_date: str,
    *,
    status: str,
    started_at: str,
    finished_at: str,
    error: str | None = None,
    l1_passed: int = 0,
    l2_top: int = 0,
    l3_keep: int = 0,
    pool_rows: Sequence[dict[str, Any]] = (),
    selection_mode: str = "factor",
    pool: str = POOL_MAIN,
    frontier_gate: str | None = None,
    exec_constraints: dict[str, Any] | None = None,
    db_path: str | Path | None = None,
) -> int:
    """批次行 + 当日精选池**单事务**写入（S1 落库口径；fail-closed 不变量）。

    - ``status`` 逐字 ∈ {``"ok"``, ``"failed"``}；``selection_mode`` 逐字 ∈
      {``"factor"``, ``"fallback"``}（add-screening-empty-library-fallback）；
      ``pool`` 逐字 ∈ {``"main"``, ``"microcap"``}（add-microcap-lane-guardrails：
      通道档位标注；同 ``trade_date`` 两档各一行，主键 ``(trade_date, pool)``）；
      ``pool_size`` 恒 = ``len(pool_rows)``（不独立入参，避免与池行数漂移）；
      同 ``(trade_date, pool)`` upsert 覆盖（幂等重跑）；
    - ``frontier_gate`` = 前置门禁记录字段（``None`` = 未评估；主档恒 None）；
      非空字符串（``ready`` / ``observation_only:missing=<要点>``），空串 → ``ValueError``；
      ``exec_constraints`` = 建议执行约束的**记录字段**（JSON 对象，如
      ``{"suggested_single_cap": 0.05, "slippage_tier_bump": 1}``；主档恒 None），
      非 None 且非空 dict → 序列化落库；
    - **失败批次传 ``pool_rows=()`` → 该档当日池行先清空**：失败日零池行是不变量，不被
      同日旧行/部分写入破坏；批次行与池行同一事务提交（ok 与池同生共死）。
      清空**按 pool 收窄**——小票通道批次不得动主档当日池行；
    - 池行字段 fail loud（见 ``_pool_row_payload``）；同批重复 ``symbol`` → ``ValueError``。

    返回写入池行数（= ``pool_size``）。
    """
    normalized = _parse_trade_date(trade_date)
    if status not in BATCH_STATUSES:
        raise ValueError(f"非法批次 status {status!r}；允许: {list(BATCH_STATUSES)}")
    if selection_mode not in SELECTION_MODES:
        raise ValueError(f"非法 selection_mode {selection_mode!r}；允许: {list(SELECTION_MODES)}")
    if pool not in POOLS:
        raise ValueError(f"非法批次 pool {pool!r}；允许: {list(POOLS)}")
    if frontier_gate is not None and (
        not isinstance(frontier_gate, str) or not frontier_gate.strip()
    ):
        raise ValueError(f"frontier_gate 必须是 None 或非空字符串: {frontier_gate!r}")
    constraints_json = _exec_constraints_json(exec_constraints)
    if not isinstance(started_at, str) or not started_at:
        raise ValueError(f"started_at 不能为空: {started_at!r}")
    if not isinstance(finished_at, str) or not finished_at:
        raise ValueError(f"finished_at 不能为空: {finished_at!r}")
    if error is not None and not isinstance(error, str):
        raise ValueError(f"error 必须是字符串或 None: {error!r}")
    counts = [
        _non_negative_int(l1_passed, "l1_passed"),
        _non_negative_int(l2_top, "l2_top"),
        _non_negative_int(l3_keep, "l3_keep"),
    ]
    payload = [_pool_row_payload(normalized, row, pool=pool) for row in pool_rows]
    symbols = [row[2] for row in payload]
    duplicated = sorted({symbol for symbol in symbols if symbols.count(symbol) > 1})
    if duplicated:
        raise ValueError(f"池行 symbol 重复: {duplicated}（每标的至多一行）")
    init_screening_db(db_path)
    conn = _connect(db_path)
    try:
        # 单事务：本档池行原子替换（失败批次 → 清空）与批次行 upsert 同生共死；
        # DELETE 按 (trade_date, pool) 收窄 → 不触碰另一档当日池行
        conn.execute(
            "DELETE FROM selection_pool WHERE trade_date = ? AND pool = ?", (normalized, pool)
        )
        conn.execute(
            "INSERT INTO screening_batches "
            "(trade_date, pool, status, error, started_at, finished_at, l1_passed, l2_top, "
            "l3_keep, pool_size, selection_mode, frontier_gate, exec_constraints) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(trade_date, pool) DO UPDATE SET "
            "status = excluded.status, error = excluded.error, "
            "started_at = excluded.started_at, finished_at = excluded.finished_at, "
            "l1_passed = excluded.l1_passed, l2_top = excluded.l2_top, "
            "l3_keep = excluded.l3_keep, pool_size = excluded.pool_size, "
            "selection_mode = excluded.selection_mode, "
            "frontier_gate = excluded.frontier_gate, "
            "exec_constraints = excluded.exec_constraints",
            (
                normalized,
                pool,
                status,
                error,
                started_at,
                finished_at,
                *counts,
                len(payload),
                selection_mode,
                frontier_gate,
                constraints_json,
            ),
        )
        if payload:
            conn.executemany(
                "INSERT INTO selection_pool "
                "(trade_date, pool, symbol, composite_score, rank, consecutive_days, "
                "l3_verdict, l3_reason, factor_contribs_json) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                payload,
            )
        conn.commit()
        return len(payload)
    finally:
        conn.close()


def list_selection_pool(
    trade_date: str, db_path: str | Path | None = None, *, pool: str = POOL_MAIN
) -> list[dict[str, Any]]:
    """某交易日某档精选池（rank 升序，同 rank 按 symbol 升序）；无记录 → 空列表。

    ``pool`` 缺省 ``'main'``（既有调用路径/接口口径不变）；小票通道读侧显式传
    ``pool='microcap'``。``factor_contribs`` 由 ``factor_contribs_json`` 解析回列表
    （坏 JSON 防御性回退 ``[]``）。
    """
    normalized = _parse_trade_date(trade_date)
    if pool not in POOLS:
        raise ValueError(f"非法池 pool {pool!r}；允许: {list(POOLS)}")
    init_screening_db(db_path)
    conn = _connect(db_path)
    try:
        rows = conn.execute(
            "SELECT trade_date, pool, symbol, composite_score, rank, consecutive_days, "
            "l3_verdict, l3_reason, factor_contribs_json FROM selection_pool "
            "WHERE trade_date = ? AND pool = ? ORDER BY rank, symbol",
            (normalized, pool),
        ).fetchall()
    finally:
        conn.close()
    rows_out: list[dict[str, Any]] = []
    for row in rows:
        entry = dict(row)
        raw = entry.pop("factor_contribs_json", None)
        try:
            parsed = json.loads(raw) if raw else []
        except (TypeError, ValueError):
            parsed = []
        entry["factor_contribs"] = parsed if isinstance(parsed, list) else []
        rows_out.append(entry)
    return rows_out


def latest_screening_batch(
    db_path: str | Path | None = None, *, pool: str = POOL_MAIN
) -> dict[str, Any] | None:
    """最近一次日批尝试（该档 ``MAX(trade_date)`` 行，含 failed）；无 → None。

    ``pool`` 缺省 ``'main'``（既有页面/接口口径不变）；小票通道行经
    ``pool='microcap'`` 显式读取。
    """
    if pool not in POOLS:
        raise ValueError(f"非法批次 pool {pool!r}；允许: {list(POOLS)}")
    init_screening_db(db_path)
    conn = _connect(db_path)
    try:
        row = conn.execute(
            "SELECT trade_date, pool, status, error, started_at, finished_at, "
            "l1_passed, l2_top, l3_keep, pool_size, selection_mode, frontier_gate, "
            "exec_constraints FROM screening_batches WHERE pool = ? "
            "ORDER BY trade_date DESC LIMIT 1",
            (pool,),
        ).fetchone()
        return _batch_row(row) if row is not None else None
    finally:
        conn.close()


def latest_successful_batch(
    db_path: str | Path | None = None, *, pool: str = POOL_MAIN
) -> dict[str, Any] | None:
    """最近一次成功批次（该档 ``status='ok'`` 且 ``trade_date`` 最大）；无 → None。"""
    if pool not in POOLS:
        raise ValueError(f"非法批次 pool {pool!r}；允许: {list(POOLS)}")
    init_screening_db(db_path)
    conn = _connect(db_path)
    try:
        row = conn.execute(
            "SELECT trade_date, pool, status, error, started_at, finished_at, "
            "l1_passed, l2_top, l3_keep, pool_size, selection_mode, frontier_gate, "
            "exec_constraints FROM screening_batches WHERE pool = ? AND status = 'ok' "
            "ORDER BY trade_date DESC LIMIT 1",
            (pool,),
        ).fetchone()
        return _batch_row(row) if row is not None else None
    finally:
        conn.close()


def list_batches(
    limit: int = DEFAULT_BATCH_HISTORY_LIMIT,
    db_path: str | Path | None = None,
    *,
    pool: str = POOL_MAIN,
) -> list[dict[str, Any]]:
    """近 ``limit`` 条批次历史（该档 ``trade_date`` 倒序，含失败行）；空库 → 空列表。

    字段与服务端「当日运维」页逐字对齐：状态 / 错误（失败原因原样，不改写不归类）/
    起止时间 / 各层计数（``l1_passed``/``l2_top``/``l3_keep``）/ 池数（``pool_size``）/
    档位（``pool``）/ 门禁（``frontier_gate``）。``pool`` 缺省 ``'main'``——既有页面
    口径零变化（小票通道行不混入）；小票通道读侧显式传 ``pool='microcap'``。
    ``limit`` 须正整数（bool 为非整数，fail loud）——**上/下界钳制在端点层**，本函数
    只做严格校验（与 ``_non_negative_int`` 等写入口径一致，不静默改正调用方入参）。
    """
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
        raise ValueError(f"limit 必须是正整数: {limit!r}")
    if pool not in POOLS:
        raise ValueError(f"非法批次 pool {pool!r}；允许: {list(POOLS)}")
    init_screening_db(db_path)
    conn = _connect(db_path)
    try:
        rows = conn.execute(
            "SELECT trade_date, pool, status, error, started_at, finished_at, "
            "l1_passed, l2_top, l3_keep, pool_size, selection_mode, frontier_gate, "
            "exec_constraints FROM screening_batches WHERE pool = ? "
            "ORDER BY trade_date DESC LIMIT ?",
            (pool, limit),
        ).fetchall()
        return [_batch_row(row) for row in rows]
    finally:
        conn.close()


def reset_screening_db(db_path: str | Path | None = None) -> dict[str, int]:
    """清空 screening 五表（E2E ``screening_reset`` 造数用）；返回各表删除行数。"""
    init_screening_db(db_path)
    conn = _connect(db_path)
    tables = (
        "l2_candidates",
        "triage_decisions",
        "screening_batches",
        "selection_pool",
        "screening_removals",
    )
    removed: dict[str, int] = {}
    try:
        for table in tables:
            cursor = conn.execute(f"DELETE FROM {table}")  # noqa: S608 -- 表名来自模块常量
            removed[table] = int(cursor.rowcount)
        conn.commit()
        return removed
    finally:
        conn.close()


# ── 分析前移除审计（时效性校验，spec「信号时效性校验」） ────────────────────


def record_screening_removals(
    trade_date: str,
    removals: Sequence[dict[str, Any]],
    db_path: str | Path | None = None,
) -> int:
    """写入某基准日（= 校验所用快照日）的分析前移除审计行；返回写入行数。

    入参 = ``pipeline.check_signal_freshness`` 的 findings（``{symbol, reason,
    detail}``）；同一标的可多因多行（主键 ``(trade_date, symbol, reason)``），
    同键幂等覆盖（``removed_at`` 为本次写入时刻）。字段 fail loud（空 symbol/
    空 reason/非字符串 detail → ``ValueError``，整批先校验后写）；空批 → 0。
    """
    normalized = _parse_trade_date(trade_date)
    if not removals:
        return 0
    payload: list[tuple[str, str, str, str, str]] = []
    now = datetime.now().isoformat()
    for removal in removals:
        if not isinstance(removal, dict):
            raise ValueError(f"移除行必须是 dict: {removal!r}")
        symbol = str(removal.get("symbol") or "").strip()
        if not symbol:
            raise ValueError(f"移除行 symbol 不能为空: {removal!r}")
        reason = removal.get("reason")
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError(f"移除行 reason 不能为空: {removal!r}（symbol={symbol}）")
        detail = removal.get("detail")
        if not isinstance(detail, str):
            raise ValueError(f"移除行 detail 必须是字符串: {removal!r}（symbol={symbol}）")
        payload.append((normalized, symbol, reason, detail, now))
    init_screening_db(db_path)
    conn = _connect(db_path)
    try:
        conn.executemany(
            "INSERT INTO screening_removals (trade_date, symbol, reason, detail, removed_at) "
            "VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(trade_date, symbol, reason) DO UPDATE SET "
            "detail = excluded.detail, removed_at = excluded.removed_at",
            payload,
        )
        conn.commit()
        return len(payload)
    finally:
        conn.close()


def list_screening_removals(
    *, trade_date: str | None = None, db_path: str | Path | None = None
) -> list[dict[str, Any]]:
    """移除审计行（按 trade_date、symbol、reason 升序）；``trade_date`` 可选过滤。

    库不存在/为空 → 空列表（读写同库，E2E reset 后读取恒可重入）。
    """
    init_screening_db(db_path)
    sql = "SELECT trade_date, symbol, reason, detail, removed_at FROM screening_removals"
    params: list[Any] = []
    if trade_date is not None:
        sql += " WHERE trade_date = ?"
        params.append(_parse_trade_date(trade_date))
    sql += " ORDER BY trade_date, symbol, reason"
    conn = _connect(db_path)
    try:
        rows = conn.execute(sql, params).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()
