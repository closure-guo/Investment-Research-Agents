"""逐主张结算（add-analyst-ex-post-verification）。

口径：入场 = 发布日次一交易日开盘（复权换算），出场 = 入场后第 H 个交易日收盘；
基准默认沪深 300（``index_closes``，与判定链同源）。停牌/缺行情/缺基准记
``unresolved`` 并留原因，不填前值。
"""

from __future__ import annotations

import bisect
import logging
import os
import sqlite3
from collections.abc import Callable
from datetime import date
from pathlib import Path

from finance_agent.data import analyst_claims_store as store

logger = logging.getLogger("finance_agent.outcome.analyst_claims.settle")

BENCHMARK_CODE = "000300"
DEFAULT_SNAPSHOT_DB = "data/market_snapshot.db"
DEFAULT_SESSIONS_DB = "data/sessions.db"


def _snapshot_path(path: str | Path | None) -> Path:
    return Path(path) if path else Path(os.getenv("SNAPSHOT_DB_PATH", DEFAULT_SNAPSHOT_DB))


def _sessions_path(path: str | Path | None) -> Path:
    return Path(path) if path else Path(os.getenv("SESSIONS_DB_PATH", DEFAULT_SESSIONS_DB))


def _connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def _default_calendar(as_of: str, snapshot_db_path: str | Path | None = None) -> list[str]:
    path = _snapshot_path(snapshot_db_path)
    if not path.exists():
        return []
    conn = _connect(path)
    try:
        rows = conn.execute(
            "SELECT DISTINCT trade_date FROM market_snapshot"
            " WHERE trade_date <= ? ORDER BY trade_date ASC",
            (as_of,),
        ).fetchall()
    finally:
        conn.close()
    return [str(r["trade_date"]) for r in rows]


def _default_stock_prices() -> Callable[[str, str], dict | None]:
    cache: dict[tuple[str, str], dict | None] = {}

    def provider(code: str, trade_date: str) -> dict | None:
        key = (code, trade_date)
        if key in cache:
            return cache[key]
        path = _snapshot_path(None)
        if not path.exists():
            cache[key] = None
            return None
        conn = _connect(path)
        try:
            row = conn.execute(
                "SELECT open, close_qfq, close_raw, is_suspended FROM market_snapshot"
                " WHERE code=? AND trade_date=?",
                (code, trade_date),
            ).fetchone()
        finally:
            conn.close()
        cache[key] = dict(row) if row else None
        return cache[key]

    return provider


def _default_benchmark(as_of: str, sessions_db_path: str | Path | None = None) -> dict[str, float]:
    path = _sessions_path(sessions_db_path)
    if not path.exists():
        return {}
    conn = _connect(path)
    try:
        exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='index_closes'"
        ).fetchone()
        if not exists:
            return {}
        rows = conn.execute(
            "SELECT trade_date, close FROM index_closes"
            " WHERE index_code=? AND trade_date <= ? ORDER BY trade_date ASC",
            (BENCHMARK_CODE, as_of),
        ).fetchall()
    finally:
        conn.close()
    return {str(r["trade_date"]): float(r["close"]) for r in rows}


def _benchmark_value(
    benchmark: dict[str, float] | Callable[[str], float | None], trade_date: str
) -> float | None:
    if callable(benchmark):
        return benchmark(trade_date)
    return benchmark.get(trade_date)


def _adjusted_open(bar: dict) -> float | None:
    open_price = bar.get("open")
    if open_price is None:
        return None
    close_qfq = bar.get("close_qfq")
    close_raw = bar.get("close_raw")
    try:
        if close_qfq is not None and close_raw not in (None, 0):
            return float(open_price) * float(close_qfq) / float(close_raw)
    except (TypeError, ValueError, ZeroDivisionError):
        pass
    try:
        return float(open_price)
    except (TypeError, ValueError):
        return None


def _exit_close(bar: dict) -> float | None:
    value = bar.get("close_qfq")
    if value is None:
        value = bar.get("close_raw")
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def settle_claims(
    *,
    as_of: str | None = None,
    claims_db_path: str | Path | None = None,
    snapshot_db_path: str | Path | None = None,
    sessions_db_path: str | Path | None = None,
    calendar: list[str] | None = None,
    stock_prices: Callable[[str, str], dict | None] | None = None,
    benchmark_closes: dict[str, float] | Callable[[str], float | None] | None = None,
) -> dict[str, int]:
    """结算所有到期主张；返回 {settled, unresolved, pending, errors}。"""
    as_of_value = as_of or date.today().isoformat()
    days = (
        sorted(calendar)
        if calendar is not None
        else _default_calendar(as_of_value, snapshot_db_path)
    )
    days = [d for d in days if d <= as_of_value]
    price_provider = stock_prices or _default_stock_prices()
    benchmark = (
        benchmark_closes
        if benchmark_closes is not None
        else _default_benchmark(as_of_value, sessions_db_path)
    )

    counts = {"settled": 0, "unresolved": 0, "pending": 0, "errors": 0}
    if not days:
        counts["pending"] = len(
            store.list_pending_claims(as_of=as_of_value, db_path=claims_db_path)
        )
        return counts

    for claim in store.list_pending_claims(as_of=as_of_value, db_path=claims_db_path):
        try:
            published_at = str(claim["published_at"])
            entry_idx = bisect.bisect_right(days, published_at)
            exit_idx = entry_idx + int(claim["horizon_days"] or 20)
            if exit_idx >= len(days):
                counts["pending"] += 1
                continue
            entry_date = days[entry_idx]
            exit_date = days[exit_idx]

            entry_bar = price_provider(str(claim["code"]), entry_date)
            if entry_bar is None:
                _unresolve(claim, "missing_price", claims_db_path)
                counts["unresolved"] += 1
                continue
            if entry_bar.get("is_suspended"):
                _unresolve(claim, "suspended_entry", claims_db_path)
                counts["unresolved"] += 1
                continue
            entry_price = _adjusted_open(entry_bar)
            if entry_price is None or entry_price <= 0:
                _unresolve(claim, "missing_price", claims_db_path)
                counts["unresolved"] += 1
                continue

            exit_bar = price_provider(str(claim["code"]), exit_date)
            if exit_bar is None:
                _unresolve(claim, "missing_price", claims_db_path)
                counts["unresolved"] += 1
                continue
            if exit_bar.get("is_suspended"):
                _unresolve(claim, "suspended_exit", claims_db_path)
                counts["unresolved"] += 1
                continue
            exit_price = _exit_close(exit_bar)
            if exit_price is None or exit_price <= 0:
                _unresolve(claim, "missing_price", claims_db_path)
                counts["unresolved"] += 1
                continue

            bench_entry = _benchmark_value(benchmark, entry_date)
            bench_exit = _benchmark_value(benchmark, exit_date)
            if bench_entry is None or bench_exit is None or bench_entry <= 0:
                _unresolve(claim, "benchmark_missing", claims_db_path)
                counts["unresolved"] += 1
                continue

            raw_return = exit_price / entry_price - 1.0
            benchmark_return = float(bench_exit) / float(bench_entry) - 1.0
            store.upsert_outcome(
                str(claim["claim_key"]),
                status="resolved",
                entry_date=entry_date,
                entry_price=entry_price,
                exit_date=exit_date,
                exit_price=exit_price,
                raw_return=raw_return,
                benchmark_return=benchmark_return,
                excess_return=raw_return - benchmark_return,
                reason=None,
                db_path=claims_db_path,
            )
            counts["settled"] += 1
        except Exception as exc:  # noqa: BLE001 - 单条失败不阻断其他主张
            counts["errors"] += 1
            logger.warning(
                "主张结算失败（保持 pending，次日重试） %s: %s",
                claim.get("claim_key"),
                exc,
            )
    return counts


def _unresolve(claim: dict, reason: str, db_path: str | Path | None) -> None:
    store.upsert_outcome(
        str(claim["claim_key"]),
        status="unresolved",
        entry_date=None,
        entry_price=None,
        exit_date=None,
        exit_price=None,
        raw_return=None,
        benchmark_return=None,
        excess_return=None,
        reason=reason,
        db_path=db_path,
    )
