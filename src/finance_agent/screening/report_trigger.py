"""研报触发关联分析编排（add-report-triggered-analysis）。

边界：本模块只做同步编排（筛选、限额、幂等、建会话、投递），真正的管线执行由
``AnalyzeQueue`` 在主事件循环里完成；默认关闭，dry-run 零副作用。
"""

from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
from collections.abc import Callable
from datetime import date, datetime
from pathlib import Path
from typing import Any

from finance_agent.data import research_reports_store as rr_store
from finance_agent.screening.freshness import screening_freshness_removals
from finance_agent.screening.pipeline import latest_successful_pool
from finance_agent.screening.pool_analysis import create_analysis_session

logger = logging.getLogger("finance_agent.screening.report_trigger")

DEFAULT_MAX_SYMBOLS = 3
DEFAULT_MAX_AGE_DAYS = 7
DEFAULT_DAILY_MAX = 10
DEFAULT_SCOPE = "pool,watchlist,holdings"
_STATUS_SKIP = (
    "stale",
    "out_of_scope",
    "duplicate",
    "already_analyzed_today",
    "daily_cap",
    "per_report_cap",
    "freshness",
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS report_analysis_runs (
  doc_key    TEXT NOT NULL,
  symbol     TEXT NOT NULL,
  session_id TEXT,
  status     TEXT NOT NULL,
  reason     TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  PRIMARY KEY (doc_key, symbol)
)
"""


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _screening_path(path: str | Path | None) -> Path:
    return Path(path) if path else Path(os.getenv("SCREENING_DB_PATH", "data/screening.db"))


def _research_path(path: str | Path | None) -> Path:
    return (
        Path(path)
        if path
        else Path(os.getenv("RESEARCH_REPORTS_DB_PATH", "data/research_reports.db"))
    )


def _connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=15.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=15000")
    return conn


def init_db(db_path: str | Path | None = None) -> None:
    with _connect(_screening_path(db_path)) as conn:
        conn.execute(_SCHEMA)
        conn.commit()


def record_run(
    doc_key: str,
    symbol: str,
    session_id: str | None,
    status: str,
    reason: str | None,
    *,
    db_path: str | Path | None = None,
) -> None:
    init_db(db_path)
    now = _now()
    conn = _connect(_screening_path(db_path))
    try:
        conn.execute(
            """
            INSERT INTO report_analysis_runs (
              doc_key, symbol, session_id, status, reason, created_at, updated_at
            ) VALUES (?,?,?,?,?,?,?)
            ON CONFLICT(doc_key, symbol) DO UPDATE SET
              session_id=COALESCE(excluded.session_id, report_analysis_runs.session_id),
              status=excluded.status,
              reason=excluded.reason,
              updated_at=excluded.updated_at
            """,
            (doc_key, symbol, session_id, status, reason, now, now),
        )
        conn.commit()
    finally:
        conn.close()


def get_run(doc_key: str, symbol: str, *, db_path: str | Path | None = None) -> dict | None:
    init_db(db_path)
    conn = _connect(_screening_path(db_path))
    try:
        row = conn.execute(
            "SELECT * FROM report_analysis_runs WHERE doc_key=? AND symbol=?",
            (doc_key, symbol),
        ).fetchone()
    finally:
        conn.close()
    return dict(row) if row else None


def _env_int(name: str, default: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default
    return value if value >= 0 else default


def trigger_enabled() -> bool:
    return os.getenv("REPORT_TRIGGER_ENABLED", "0").strip().lower() in ("1", "true", "yes")


def resolve_scope(
    scope: str | None = None,
    *,
    screening_db_path: str | Path | None = None,
    events_db_path: str | Path | None = None,
    execution_db_path: str | Path | None = None,
) -> tuple[set[str], dict[str, int]]:
    """范围并集：精选池 / 事件观察清单 / 持仓；单来源失败降级为空集。"""
    names = [
        item.strip()
        for item in (scope or os.getenv("REPORT_TRIGGER_SCOPE", DEFAULT_SCOPE)).split(",")
        if item.strip()
    ]
    symbols: set[str] = set()
    detail: dict[str, int] = {}
    for name in names:
        found: set[str] = set()
        try:
            if name == "pool":
                successful = latest_successful_pool(db_path=screening_db_path)
                found = {
                    str(row.get("symbol") or "")
                    for row in (successful or {}).get("pool") or []
                    if re.fullmatch(r"\d{6}", str(row.get("symbol") or ""))
                }
            elif name == "watchlist":
                from finance_agent.outcome.events.model import list_watchlist

                found = {
                    str(row.get("code") or "")
                    for row in list_watchlist(db_path=events_db_path)
                    if re.fullmatch(r"\d{6}", str(row.get("code") or ""))
                }
            elif name == "holdings":
                from finance_agent.execution.store import list_positions

                found = {
                    str(row.get("symbol") or "")
                    for row in list_positions(db_path=execution_db_path)
                    if float(row.get("qty") or 0) > 0
                    and re.fullmatch(r"\d{6}", str(row.get("symbol") or ""))
                }
            else:
                logger.warning("未知 REPORT_TRIGGER_SCOPE 项（忽略）: %s", name)
        except Exception as exc:  # noqa: BLE001 - 单来源失败不阻断其他来源
            logger.warning("研报触发范围来源失败 %s: %s", name, exc)
        detail[name] = len(found)
        symbols.update(found)
    return symbols, detail


def _today_used(day: str, screening_db_path: str | Path | None) -> int:
    conn = _connect(_screening_path(screening_db_path))
    try:
        report_rows = conn.execute(
            "SELECT COUNT(*) FROM report_analysis_runs WHERE substr(created_at, 1, 10)=?",
            (day,),
        ).fetchone()[0]
        pool_exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='pool_analysis_runs'"
        ).fetchone()
        pool_rows = 0
        if pool_exists:
            pool_rows = conn.execute(
                "SELECT COUNT(*) FROM pool_analysis_runs WHERE trade_date=?", (day,)
            ).fetchone()[0]
    finally:
        conn.close()
    return int(report_rows) + int(pool_rows)


def _already_today(sym: str, day: str, doc_key: str, screening_db_path: str | Path | None) -> bool:
    conn = _connect(_screening_path(screening_db_path))
    try:
        row = conn.execute(
            "SELECT 1 FROM report_analysis_runs"
            " WHERE symbol=? AND substr(created_at, 1, 10)=? AND doc_key<>? LIMIT 1",
            (sym, day, doc_key),
        ).fetchone()
        if row:
            return True
        pool_exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='pool_analysis_runs'"
        ).fetchone()
        if pool_exists:
            row = conn.execute(
                "SELECT 1 FROM pool_analysis_runs WHERE trade_date=? AND symbol=? LIMIT 1",
                (day, sym),
            ).fetchone()
            if row:
                return True
    finally:
        conn.close()
    return False


def _parse_stocks(value: Any) -> list[str]:
    if isinstance(value, list):
        data = value
    else:
        try:
            data = json.loads(value or "[]")
        except (TypeError, json.JSONDecodeError):
            return []
    codes: list[str] = []
    for item in data or []:
        if not isinstance(item, dict):
            continue
        code = str(item.get("code") or "").strip()
        if re.fullmatch(r"\d{6}", code) and code not in codes:
            codes.append(code)
    return codes


def _report_date(row: dict, research_db_path: str | Path | None) -> str | None:
    value = str(row.get("report_date") or "").strip()[:10]
    if value:
        return value
    doc = rr_store.get_document(str(row.get("doc_key") or ""), db_path=research_db_path)
    return str((doc or {}).get("publish_date") or "").strip()[:10] or None


def prepare_report_analysis_batch(
    doc_keys: list[str] | None = None,
    *,
    dry_run: bool = False,
    enqueue: Callable[[str, str], bool] | None = None,
    as_of: str | None = None,
    research_db_path: str | Path | None = None,
    screening_db_path: str | Path | None = None,
    events_db_path: str | Path | None = None,
    execution_db_path: str | Path | None = None,
    scope_symbols: set[str] | None = None,
    scope: str | None = None,
    max_symbols: int | None = None,
    max_age_days: int | None = None,
    daily_max: int | None = None,
) -> dict[str, Any]:
    """筛选关联标的并（非 dry-run 时）建会话入队；返回可审计摘要。"""
    day = as_of or date.today().isoformat()
    limit_symbols = (
        max_symbols
        if max_symbols is not None
        else _env_int("REPORT_TRIGGER_MAX_SYMBOLS", DEFAULT_MAX_SYMBOLS)
    )
    age_limit = (
        max_age_days
        if max_age_days is not None
        else _env_int("REPORT_TRIGGER_MAX_AGE_DAYS", DEFAULT_MAX_AGE_DAYS)
    )
    day_limit = (
        daily_max
        if daily_max is not None
        else _env_int("REPORT_TRIGGER_DAILY_MAX", DEFAULT_DAILY_MAX)
    )
    init_db(screening_db_path)
    if scope_symbols is None:
        symbols_in_scope, scope_detail = resolve_scope(
            scope,
            screening_db_path=screening_db_path,
            events_db_path=events_db_path,
            execution_db_path=execution_db_path,
        )
    else:
        symbols_in_scope = set(scope_symbols)
        scope_detail = {"explicit": len(symbols_in_scope)}

    if doc_keys is None:
        rows = rr_store.list_extractions(limit=200, db_path=research_db_path)
        input_scope = "all_extractions"
    else:
        rows = [
            row
            for row in (rr_store.get_extraction(key, db_path=research_db_path) for key in doc_keys)
            if row
        ]
        input_scope = "explicit_doc_keys"
    rows = [row for row in rows if row]

    # 输入为空时显式披露原因：深度抽取只覆盖人工投喂 PDF，API 采集的研报只有元数据。
    # 不披露会让「库内没有抽取」与「跑过但无候选」在摘要里长得一样，运维无法区分。
    notice: str | None = None
    if not rows:
        notice = (
            "no_extractions: 深度抽取仅覆盖人工投喂 PDF（当前库内 0 条），本次无候选；"
            "API 采集的研报只落元数据"
            if input_scope == "all_extractions"
            else "doc_not_found: 指定文档无抽取结果（尚未投喂或抽取失败）"
        )

    skipped = dict.fromkeys(_STATUS_SKIP, 0)
    result: dict[str, Any] = {
        "status": "dry_run" if dry_run else "ok",
        "input_scope": input_scope,
        "notice": notice,
        "as_of": day,
        "docs_scanned": len(rows),
        "candidates": 0,
        "triggered": 0,
        "failed": 0,
        "would_trigger": [],
        "skipped": skipped,
        "scope": scope_detail,
        "removed": [],
    }
    used_today = _today_used(day, screening_db_path)

    candidate_codes = {code for row in rows for code in _parse_stocks(row.get("stocks_json"))}
    removals = screening_freshness_removals(
        [{"symbol": symbol} for symbol in sorted(candidate_codes)], day
    )
    removed_symbols = {str(item.get("symbol") or "") for item in removals}
    result["removed"] = removals

    for row in rows:
        doc_key = str(row.get("doc_key") or "")
        codes = _parse_stocks(row.get("stocks_json"))
        if not doc_key or not codes:
            continue
        date_text = _report_date(row, research_db_path)
        try:
            report_date = date.fromisoformat(str(date_text)[:10])
        except (TypeError, ValueError):
            skipped["stale"] += 1
            continue
        if (date.fromisoformat(day) - report_date).days > age_limit:
            skipped["stale"] += 1
            continue
        result["candidates"] += len(codes)
        per_doc = 0
        for symbol in codes:
            if symbol not in symbols_in_scope:
                skipped["out_of_scope"] += 1
                continue
            if symbol in removed_symbols:
                skipped["freshness"] += 1
                continue
            if per_doc >= limit_symbols:
                skipped["per_report_cap"] += 1
                continue
            existing = get_run(doc_key, symbol, db_path=screening_db_path)
            session_id = ""
            if existing is not None:
                if existing.get("status") == "dispatch_failed" and existing.get("session_id"):
                    session_id = str(existing["session_id"])
                else:
                    skipped["duplicate"] += 1
                    continue
            if _already_today(symbol, day, doc_key, screening_db_path):
                skipped["already_analyzed_today"] += 1
                continue
            if used_today >= day_limit:
                skipped["daily_cap"] += 1
                continue
            if dry_run:
                result["would_trigger"].append({"doc_key": doc_key, "symbol": symbol})
                per_doc += 1
                continue
            if not session_id:
                try:
                    session_id = create_analysis_session(
                        symbol,
                        display_name=f"{symbol} 研报触发分析",
                        source="report_trigger",
                    )
                except Exception as exc:  # noqa: BLE001 - 单标的失败不阻断整批
                    logger.exception("研报触发建会话失败 %s", symbol)
                    record_run(
                        doc_key,
                        symbol,
                        "",
                        "session_failed",
                        str(exc),
                        db_path=screening_db_path,
                    )
                    result["failed"] += 1
                    continue
                record_run(
                    doc_key,
                    symbol,
                    session_id,
                    "queued",
                    None,
                    db_path=screening_db_path,
                )
            if enqueue is None or not enqueue(symbol, session_id):
                record_run(
                    doc_key,
                    symbol,
                    session_id,
                    "dispatch_failed",
                    "enqueue unavailable",
                    db_path=screening_db_path,
                )
                result["triggered"] += 1
                result["failed"] += 1
                continue
            result["triggered"] += 1
            used_today += 1
            per_doc += 1
    return result


def run_report_trigger_daily(
    enqueue: Callable[[str, str], bool] | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    """日批入口：默认关闭时只计算 would-trigger 摘要（不建会话、不写触发记录）。"""
    if not trigger_enabled():
        result = prepare_report_analysis_batch(doc_keys=None, dry_run=True, enqueue=None, **kwargs)
        result["status"] = "disabled"
        logger.info("研报触发分析已关闭；would_trigger=%s", result.get("would_trigger"))
        return result
    return prepare_report_analysis_batch(doc_keys=None, enqueue=enqueue, **kwargs)
