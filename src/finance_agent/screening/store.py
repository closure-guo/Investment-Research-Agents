"""screening 库：L2 名单快照、L3 triage 决策、日批批次与精选池、移除审计
（add-screening-candidate-llm T1/T2 + add-screening-pipeline T1 + 时效性校验接线）。

表（C5 / pipeline Global Constraints，逐字）：

- ``l2_candidates(trade_date, symbol, composite_score, rank, PRIMARY KEY(trade_date, symbol))``：
  L2 打分名单日快照，幂等 upsert（同日重跑覆盖）；
- ``triage_decisions(trade_date, symbol, verdict, reason, degraded, created_at,
  PRIMARY KEY(trade_date, symbol))``：L3 keep/drop 决策，幂等 upsert（同日重跑覆盖）。
  两表供消融（±L3 粗筛）与前向命中率分层（后续 change）查询，是稳定性门槛查询的
  唯一数据源；
- ``screening_batches(trade_date PK, status, error, started_at, finished_at,
  l1_passed, l2_top, l3_keep, pool_size)``：日批批次行（``status ∈ {ok, failed}``；
  失败批次与当日池零行的不变量由 ``record_screening_batch`` 单事务保证）；
- ``selection_pool(trade_date, symbol, composite_score, rank, consecutive_days,
  l3_verdict, l3_reason, factor_contribs_json, PK(trade_date, symbol))``：
  精选池（keep ∩ 连续在榜门槛达标），同日写入为**原子替换**；
- ``screening_removals(trade_date, symbol, reason, detail, removed_at,
  PK(trade_date, symbol, reason))``：分析前时效性校验的移除审计行（``trade_date``
  = 校验基准日 = 快照日；同标的多因多行，幂等覆盖）。

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
CREATE TABLE IF NOT EXISTS screening_batches (
  trade_date  TEXT PRIMARY KEY,
  status      TEXT NOT NULL,
  error       TEXT,
  started_at  TEXT NOT NULL,
  finished_at TEXT NOT NULL,
  l1_passed   INTEGER NOT NULL DEFAULT 0,
  l2_top      INTEGER NOT NULL DEFAULT 0,
  l3_keep     INTEGER NOT NULL DEFAULT 0,
  pool_size   INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS selection_pool (
  trade_date           TEXT NOT NULL,
  symbol               TEXT NOT NULL,
  composite_score      REAL NOT NULL,
  rank                 INTEGER NOT NULL,
  consecutive_days     INTEGER NOT NULL,
  l3_verdict           TEXT NOT NULL,
  l3_reason            TEXT NOT NULL,
  factor_contribs_json TEXT NOT NULL,
  PRIMARY KEY (trade_date, symbol)
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

    入参约定：**截断后的 Top-N 名单**（``rank_universe(top_n=…)`` 输出，日批经
    ``pipeline`` 全量迁移）——表内行的**存在性**即「当日在该榜内」，稳定性查询
    （``consecutive_days_in_top``）据此判定在榜；传入未截断全量会虚增在榜天数。

    ``composite_score`` 须有限、``rank`` 须为正整数（fail loud，不落坏值）；
    空批 → 0，不建表（无行可写，连库都不碰）。返回写入行数。
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


def record_triage_decisions(
    trade_date: str, decisions: Sequence[TriageDecision], db_path: str | Path | None = None
) -> int:
    """批量写入某交易日的 L3 决策（单连接 executemany + 单次 commit，T1 评审修正）。

    批量路径是唯一写入口：``run_triage`` 每批只调一次本函数，避免逐行
    ``init_screening_db`` + 新建连接 + 独立 commit 的 N 倍开销。全部 ``verdict``
    先校验（合同单一来源；任一非法 → ``ValueError``，整批不落）；同
    ``(trade_date, symbol)`` **幂等**覆盖（upsert，``created_at`` 为本次写入时刻）；
    空批 → 0，不建表。返回写入行数。
    """
    normalized = _parse_trade_date(trade_date)
    if not decisions:
        return 0
    payload: list[tuple[str, str, str, str, int, str]] = []
    now = datetime.now().isoformat()
    for decision in decisions:
        if decision.verdict not in VERDICTS:
            raise ValueError(f"非法 verdict {decision.verdict!r}；允许: {list(VERDICTS)}")
        payload.append(
            (
                normalized,
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
            "(trade_date, symbol, verdict, reason, degraded, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(trade_date, symbol) DO UPDATE SET "
            "verdict = excluded.verdict, reason = excluded.reason, "
            "degraded = excluded.degraded, created_at = excluded.created_at",
            payload,
        )
        conn.commit()
        return len(payload)
    finally:
        conn.close()


def record_triage_decision(
    trade_date: str, decision: TriageDecision, db_path: str | Path | None = None
) -> None:
    """写入某标的某交易日的 L3 决策；同 ``(trade_date, symbol)`` **幂等**覆盖（upsert）。

    ``verdict`` 必须 ∈ ``candidate_llm.VERDICTS``（合同单一来源），非法值 fail loud
    （不落坏值）；``created_at`` 为写入时刻（同日重跑覆盖为最新）。单行写入是
    ``record_triage_decisions`` 的薄封装——批量路径是唯一实现，语义恒一致。
    """
    record_triage_decisions(trade_date, [decision], db_path=db_path)


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


# ── 日批批次与精选池（add-screening-pipeline T1；S1 落库口径） ──────────────


def _non_negative_int(value: Any, field_name: str) -> int:
    """计数入参：非负整数（bool 除外）→ int；否则 fail loud。"""
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{field_name} 必须是非负整数: {value!r}")
    return value


def _pool_row_payload(trade_date: str, row: dict[str, Any]) -> tuple[Any, ...]:
    """精选池行 dict → SQLite payload（字段 fail loud，坏值不入库）。"""
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
    db_path: str | Path | None = None,
) -> int:
    """批次行 + 当日精选池**单事务**写入（S1 落库口径；fail-closed 不变量）。

    - ``status`` 逐字 ∈ {``"ok"``, ``"failed"``}；``pool_size`` 恒 = ``len(pool_rows)``
      （不独立入参，避免与池行数漂移）；同 ``trade_date`` upsert 覆盖（幂等重跑）；
    - **失败批次传 ``pool_rows=()`` → 当日池行先清空**：失败日零池行是不变量，不被
      同日旧行/部分写入破坏；批次行与池行同一事务提交（ok 与池同生共死）；
    - 池行字段 fail loud（见 ``_pool_row_payload``）；同批重复 ``symbol`` → ``ValueError``。

    返回写入池行数（= ``pool_size``）。
    """
    normalized = _parse_trade_date(trade_date)
    if status not in BATCH_STATUSES:
        raise ValueError(f"非法批次 status {status!r}；允许: {list(BATCH_STATUSES)}")
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
    payload = [_pool_row_payload(normalized, row) for row in pool_rows]
    symbols = [row[1] for row in payload]
    duplicated = sorted({symbol for symbol in symbols if symbols.count(symbol) > 1})
    if duplicated:
        raise ValueError(f"池行 symbol 重复: {duplicated}（每标的至多一行）")
    init_screening_db(db_path)
    conn = _connect(db_path)
    try:
        # 单事务：池行原子替换（失败批次 → 清空）与批次行 upsert 同生共死
        conn.execute("DELETE FROM selection_pool WHERE trade_date = ?", (normalized,))
        conn.execute(
            "INSERT INTO screening_batches "
            "(trade_date, status, error, started_at, finished_at, l1_passed, l2_top, l3_keep, pool_size) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(trade_date) DO UPDATE SET "
            "status = excluded.status, error = excluded.error, "
            "started_at = excluded.started_at, finished_at = excluded.finished_at, "
            "l1_passed = excluded.l1_passed, l2_top = excluded.l2_top, "
            "l3_keep = excluded.l3_keep, pool_size = excluded.pool_size",
            (normalized, status, error, started_at, finished_at, *counts, len(payload)),
        )
        if payload:
            conn.executemany(
                "INSERT INTO selection_pool "
                "(trade_date, symbol, composite_score, rank, consecutive_days, "
                "l3_verdict, l3_reason, factor_contribs_json) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                payload,
            )
        conn.commit()
        return len(payload)
    finally:
        conn.close()


def list_selection_pool(trade_date: str, db_path: str | Path | None = None) -> list[dict[str, Any]]:
    """某交易日精选池（rank 升序，同 rank 按 symbol 升序）；无记录 → 空列表。

    ``factor_contribs`` 由 ``factor_contribs_json`` 解析回列表（坏 JSON 防御性回退 ``[]``）。
    """
    normalized = _parse_trade_date(trade_date)
    init_screening_db(db_path)
    conn = _connect(db_path)
    try:
        rows = conn.execute(
            "SELECT trade_date, symbol, composite_score, rank, consecutive_days, "
            "l3_verdict, l3_reason, factor_contribs_json FROM selection_pool "
            "WHERE trade_date = ? ORDER BY rank, symbol",
            (normalized,),
        ).fetchall()
    finally:
        conn.close()
    pool: list[dict[str, Any]] = []
    for row in rows:
        entry = dict(row)
        raw = entry.pop("factor_contribs_json", None)
        try:
            parsed = json.loads(raw) if raw else []
        except (TypeError, ValueError):
            parsed = []
        entry["factor_contribs"] = parsed if isinstance(parsed, list) else []
        pool.append(entry)
    return pool


def latest_screening_batch(db_path: str | Path | None = None) -> dict[str, Any] | None:
    """最近一次日批尝试（``MAX(trade_date)`` 行，含 failed）；空库 → None。"""
    init_screening_db(db_path)
    conn = _connect(db_path)
    try:
        row = conn.execute(
            "SELECT trade_date, status, error, started_at, finished_at, "
            "l1_passed, l2_top, l3_keep, pool_size FROM screening_batches "
            "ORDER BY trade_date DESC LIMIT 1"
        ).fetchone()
        return dict(row) if row is not None else None
    finally:
        conn.close()


def latest_successful_batch(db_path: str | Path | None = None) -> dict[str, Any] | None:
    """最近一次成功批次（``status='ok'`` 且 ``trade_date`` 最大）；无 → None。"""
    init_screening_db(db_path)
    conn = _connect(db_path)
    try:
        row = conn.execute(
            "SELECT trade_date, status, error, started_at, finished_at, "
            "l1_passed, l2_top, l3_keep, pool_size FROM screening_batches "
            "WHERE status = 'ok' ORDER BY trade_date DESC LIMIT 1"
        ).fetchone()
        return dict(row) if row is not None else None
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
