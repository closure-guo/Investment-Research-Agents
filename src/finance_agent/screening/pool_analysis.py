"""精选池自动深度分析编排（add-auto-pool-analysis-daily-brief）。

职责边界：

- **本模块只做同步编排**：读当日成功池、截断、时效性校验、建会话、写幂等表、调用
  ``enqueue``。真正的队列补位与管线执行在 API 进程的主事件循环里（``AnalyzeQueue`` +
  ``PipelineRunner``），由 ``api.submit_pool_analysis_batch`` 桥接。这样自动批次与人工
  批次共用同一套 SSE/journal 与并发上限。
- **池新鲜度**：名单取自最近成功批次，且批次日须为「当日或前一交易日」（按快照
  交易日历判定；无历不猜，退回仅当日）。只认当日会把深夜补跑出的池永久挡在门外
  （issue #456 缺陷 B：10-21~23 连续三天池分析空转）；更旧的池仍 ``skipped_stale_pool``
  ——选股连续失败多日时不得拿过期池冒充当日结果。
- **幂等键 = (trade_date, symbol)**：同一天重复跑（手动补跑/重试）不重复建会话；若上次只
  是入队失败，复用原 ``session_id`` 重新入队，而不是再建一个会话；已 ``completed`` 的
  组合不重复烧分析。
- **失败留痕**：建会话失败 / 入队失败分别记 ``session_failed`` / ``dispatch_failed``，不静默
  吞掉；当日汇报会读到这些行。``reconcile_pool_analysis_runs`` 在每次编排前把已终结
  会话的终态回写到运行行（issue #456 缺陷 A：会话完成而行停 ``queued``，运维侧无法
  据此判断链路是否在工作）。
"""

from __future__ import annotations

import logging
import os
import sqlite3
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

from finance_agent.screening.freshness import screening_freshness_removals
from finance_agent.screening.pipeline import latest_successful_pool, read_snapshot_calendar
from finance_agent.session_store import (
    append_chat,
    append_session_event,
    create_session,
    get_session,
    set_pipeline_anchor,
    update_session_for_clarify,
)

logger = logging.getLogger("finance_agent.screening.pool_analysis")

AUTO_POOL_DISPLAY_SUFFIX = "自动选股分析"
DEFAULT_MAX_SYMBOLS = 10
MAX_SYMBOLS_CAP = 100

_SCHEMA = """
CREATE TABLE IF NOT EXISTS pool_analysis_runs (
  trade_date TEXT NOT NULL,
  symbol TEXT NOT NULL,
  session_id TEXT NOT NULL,
  status TEXT NOT NULL,
  error TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  PRIMARY KEY (trade_date, symbol)
)
"""


def _default_db_path() -> Path:
    """screening 库路径（调用期读 env，测试经 SCREENING_DB_PATH 覆盖）。"""
    return Path(os.getenv("SCREENING_DB_PATH", "data/screening.db"))


def _connect(db_path: str | Path | None = None) -> sqlite3.Connection:
    path = Path(db_path) if db_path else _default_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, check_same_thread=False, timeout=15.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=15000")
    return conn


def init_pool_analysis_runs(db_path: str | Path | None = None) -> None:
    with _connect(db_path) as conn:
        conn.execute(_SCHEMA)
        conn.commit()


def _now() -> str:
    return datetime.now().isoformat()


def record_pool_analysis_run(
    trade_date: str,
    symbol: str,
    session_id: str,
    status: str = "queued",
    error: str | None = None,
    *,
    db_path: str | Path | None = None,
) -> None:
    """写入/更新一条自动分析运行行（同 (trade_date, symbol) 幂等 upsert）。"""
    init_pool_analysis_runs(db_path)
    now = _now()
    with _connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO pool_analysis_runs
                (trade_date, symbol, session_id, status, error, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(trade_date, symbol) DO UPDATE SET
                session_id = excluded.session_id,
                status = excluded.status,
                error = excluded.error,
                updated_at = excluded.updated_at
            """,
            (trade_date, symbol, session_id, status, error, now, now),
        )
        conn.commit()


def get_pool_analysis_run(
    trade_date: str, symbol: str, *, db_path: str | Path | None = None
) -> dict[str, Any] | None:
    init_pool_analysis_runs(db_path)
    with _connect(db_path) as conn:
        row = conn.execute(
            "SELECT trade_date, symbol, session_id, status, error, created_at, updated_at "
            "FROM pool_analysis_runs WHERE trade_date = ? AND symbol = ?",
            (trade_date, symbol),
        ).fetchone()
    return dict(row) if row else None


def list_pool_analysis_runs(
    trade_date: str, *, db_path: str | Path | None = None
) -> list[dict[str, Any]]:
    init_pool_analysis_runs(db_path)
    with _connect(db_path) as conn:
        rows = conn.execute(
            "SELECT trade_date, symbol, session_id, status, error, created_at, updated_at "
            "FROM pool_analysis_runs WHERE trade_date = ? ORDER BY symbol",
            (trade_date,),
        ).fetchall()
    return [dict(row) for row in rows]


def create_analysis_session(symbol: str, *, display_name: str, source: str | None = None) -> str:
    """创建独立分析会话（序列与人工端点一致；source 仅自动批次写入）。"""
    session_id = create_session(
        stock_code="",
        stock_name="",
        display_name=display_name,
        status="clarifying",
        session_type="analysis",
    )
    append_chat(session_id, "user", f"对 {symbol} 进行深度分析")
    set_pipeline_anchor(session_id)
    update_session_for_clarify(
        session_id,
        stock_code=symbol,
        stock_name="",
        status="running",
    )
    event: dict[str, Any] = {
        "type": "session_created",
        "session_id": session_id,
        "display_name": display_name,
        "timestamp": _now(),
    }
    if source:
        event["source"] = source
    append_session_event(session_id, event)
    return session_id


def _resolve_max_symbols(max_symbols: int | None) -> int:
    if max_symbols is None:
        raw = os.getenv("AUTO_POOL_ANALYSIS_MAX_SYMBOLS", str(DEFAULT_MAX_SYMBOLS))
        try:
            value = int(raw)
        except (TypeError, ValueError):
            logger.warning(
                "AUTO_POOL_ANALYSIS_MAX_SYMBOLS 非法（%r），回退 %d", raw, DEFAULT_MAX_SYMBOLS
            )
            value = DEFAULT_MAX_SYMBOLS
        max_symbols = value
    return max(1, min(MAX_SYMBOLS_CAP, int(max_symbols)))


# 会话终态 → 运行行状态的映射（对账用；在跑态不在映射内、保持原行状态）
_SESSION_TERMINAL_STATUS: dict[str, str] = {
    "completed": "completed",
    "failed": "failed",
    "interrupted": "failed",
}


def reconcile_pool_analysis_runs(
    trade_date: str | None = None, *, db_path: str | Path | None = None
) -> int:
    """把已终结会话的终态回写到 ``pool_analysis_runs`` 行，返回回写行数。

    ``trade_date=None`` 扫全表（历史僵尸行一并治愈）；否则只扫该分析日。
    只碰 ``queued`` / ``dispatch_failed`` 且带 ``session_id`` 的行：会话 ``completed``
    → 行 ``completed``；``failed``/``interrupted`` → 行 ``failed``（error 取会话的
    ``failure_reason``）。在跑态（running/clarifying）保持原样。幂等：终态行不再进
    本函数的候选集。读会话失败只记日志（对账是自愈动作，不得阻断编排）。
    """
    healed = 0
    try:
        if trade_date is None:
            init_pool_analysis_runs(db_path)
            with _connect(db_path) as conn:
                rows = [
                    dict(r)
                    for r in conn.execute(
                        "SELECT trade_date, symbol, session_id, status FROM pool_analysis_runs"
                    ).fetchall()
                ]
        else:
            rows = list_pool_analysis_runs(trade_date, db_path=db_path)
        for row in rows:
            if row["status"] not in ("queued", "dispatch_failed"):
                continue
            session_id = str(row["session_id"] or "")
            if not session_id:
                continue
            session = get_session(session_id)
            if session is None:
                continue
            mapped = _SESSION_TERMINAL_STATUS.get(str(session.get("status") or ""))
            if mapped is None:
                continue
            error = str(session.get("failure_reason") or "") or None if mapped == "failed" else None
            run_day = str(row["trade_date"])
            record_pool_analysis_run(
                run_day, row["symbol"], session_id, mapped, error, db_path=db_path
            )
            healed += 1
    except Exception:  # noqa: BLE001 - 对账失败不阻断编排
        logger.exception("池分析运行行对账失败（忽略）")
        return healed
    if healed:
        logger.warning("池分析运行行对账回写 %d 条（%s）", healed, trade_date)
    return healed


def _accept_batch_date(batch_date: str, day: str, calendar: list[str] | None) -> bool:
    """批次日可接受 = 当日，或（有历且）恰为 ``day`` 的前一交易日。

    无历不猜：日历缺失/不可解析时退回「仅当日」的旧行为（fail-closed）。
    """
    if batch_date == day:
        return True
    if not calendar:
        return False
    earlier = [d for d in calendar if d < day]
    return bool(earlier) and earlier[-1] == batch_date


def prepare_pool_analysis_batch(
    *,
    as_of: str | None = None,
    max_symbols: int | None = None,
    enqueue: Callable[[str, str], bool] | None = None,
    db_path: str | Path | None = None,
    screening_db_path: str | Path | None = None,
) -> dict[str, Any]:
    """读取当日池并准备自动分析批次；返回可审计摘要。

    ``enqueue(symbol, session_id)`` 返回 True 表示已交给队列；False/异常记
    ``dispatch_failed`` 并保留 ``session_id`` 供手动补跑复用。
    """
    day = as_of or datetime.now().strftime("%Y-%m-%d")
    cap = _resolve_max_symbols(max_symbols)
    result: dict[str, Any] = {
        "trade_date": day,
        "batch_date": None,
        "stale_batch": None,
        "pool_size": 0,
        "selected": 0,
        "started": 0,
        "started_symbols": [],
        "skipped_existing": 0,
        "reconciled": 0,
        "removed": [],
        "dispatch_failed": 0,
    }
    # 全表对账（含历史僵尸行）；结果计数随摘要披露
    result["reconciled"] = reconcile_pool_analysis_runs(None, db_path=db_path)

    successful = latest_successful_pool(db_path=screening_db_path)
    if successful is None:
        result["status"] = "skipped_empty_pool"
        return result

    batch = successful.get("batch") or {}
    batch_date = str(batch.get("trade_date") or "")
    pool = list(successful.get("pool") or [])
    result["batch_date"] = batch_date
    result["pool_size"] = len(pool)

    if not _accept_batch_date(batch_date, day, read_snapshot_calendar()):
        result["status"] = "skipped_stale_pool"
        return result
    if batch_date != day:
        result["stale_batch"] = batch_date
        logger.warning(
            "池分析接受前一交易日的补跑池（批次 %s，分析日 %s）；时效性重校验照常执行",
            batch_date,
            day,
        )
    if not pool:
        result["status"] = "skipped_empty_pool"
        return result

    selected = sorted(
        pool,
        key=lambda row: (int(row.get("rank") or 10**9), str(row.get("symbol") or "")),
    )[:cap]
    result["selected"] = len(selected)

    removed = screening_freshness_removals(selected, batch_date)
    result["removed"] = removed
    removed_symbols = {str(item.get("symbol") or "") for item in removed}

    for row in selected:
        symbol = str(row.get("symbol") or "").strip()
        if not symbol or symbol in removed_symbols:
            continue
        existing = get_pool_analysis_run(day, symbol, db_path=db_path)
        if existing is not None and existing.get("status") in ("queued", "completed"):
            # queued = 已建会话待入队/在跑；completed = 当日已完成（对账后可见）——
            # 两者都不重复建会话、不重复烧分析（issue #456）。
            result["skipped_existing"] += 1
            continue
        session_id = str(existing.get("session_id") or "") if existing else ""
        if not session_id:
            try:
                session_id = create_analysis_session(
                    symbol,
                    display_name=f"{symbol} {AUTO_POOL_DISPLAY_SUFFIX}",
                    source="auto_pool",
                )
            except Exception as exc:  # noqa: BLE001 -- 单标的会话失败不阻断整批
                logger.exception("自动选股分析建会话失败 %s", symbol)
                record_pool_analysis_run(
                    day, symbol, "", "session_failed", str(exc), db_path=db_path
                )
                result["dispatch_failed"] += 1
                continue
            record_pool_analysis_run(day, symbol, session_id, "queued", None, db_path=db_path)

        if enqueue is None:
            record_pool_analysis_run(
                day,
                symbol,
                session_id,
                "dispatch_failed",
                "enqueue unavailable",
                db_path=db_path,
            )
            result["dispatch_failed"] += 1
            continue
        try:
            dispatched = bool(enqueue(symbol, session_id))
        except Exception as exc:  # noqa: BLE001 -- 入队失败留痕，可手动补跑复用 session
            logger.exception("自动选股分析入队失败 %s", symbol)
            record_pool_analysis_run(
                day, symbol, session_id, "dispatch_failed", str(exc), db_path=db_path
            )
            result["dispatch_failed"] += 1
            continue
        if not dispatched:
            record_pool_analysis_run(
                day,
                symbol,
                session_id,
                "dispatch_failed",
                "enqueue returned false",
                db_path=db_path,
            )
            result["dispatch_failed"] += 1
            continue
        record_pool_analysis_run(day, symbol, session_id, "queued", None, db_path=db_path)
        result["started"] += 1
        result["started_symbols"].append(symbol)

    result["status"] = "ok"
    return result


def _submit(**kwargs: Any) -> dict[str, Any]:
    """惰性导入 api，避免 outcome/screening ↔ api 的导入期环。"""
    from finance_agent import api

    return api.submit_pool_analysis_batch(**kwargs)


def run_pool_analysis_daily() -> dict[str, Any]:
    """日批入口：开关/上限解析后，把批次准备提交到 API 主事件循环。"""
    if os.getenv("AUTO_POOL_ANALYSIS_ENABLED", "1").strip() == "0":
        return {"status": "skipped_disabled"}
    return _submit(max_symbols=_resolve_max_symbols(None))
