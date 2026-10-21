"""外资事件打分与事后命中（add-region-split-broker-scoring）。

与内资 event_study 分开：外资用「情绪分 + 市场级 20 日方向命中」，
v1 只结算 target_type=market 的事件，sector/stock 不强行映射。
"""

from __future__ import annotations

import bisect
import logging
import os
from datetime import date
from pathlib import Path
from statistics import mean

from finance_agent.data import research_reports_store as rr_store
from finance_agent.outcome.analyst_claims.scorecard import load_scoring_config

logger = logging.getLogger("finance_agent.outcome.foreign_broker_scoring")

BENCHMARK_CODE = "000300"
DEFAULT_HORIZON_DAYS = 20
RELIABLE_MIN_SAMPLES = 30
_DEFAULT_FOREIGN = {
    "action_weights": {
        "rating_up": 1,
        "target_up": 1,
        "rating_down": -1,
        "target_down": -1,
        "positioning": 0,
        "sentiment": 0,
        "other": 0,
    },
    "confidence_weights": {"high": 1.0, "medium": 0.7, "low": 0.4},
    "horizon_days": 20,
    "benchmark": "000300",
}


def _foreign_config(config: dict | None = None) -> dict:
    payload = config if config is not None else load_scoring_config()
    raw = payload.get("foreign") if isinstance(payload, dict) else None
    if not isinstance(raw, dict):
        return dict(_DEFAULT_FOREIGN)
    return {**_DEFAULT_FOREIGN, **raw}


def event_score(
    action: str | None,
    direction: str | None,
    confidence: str | None,
    config: dict | None = None,
) -> float:
    """确定性情绪分：动作 × 方向 × 置信度；neutral/unknown 为 0。"""
    cfg = _foreign_config(config)
    action_text = str(action or "").strip().lower()
    direction_text = str(direction or "").strip().lower()
    confidence_text = str(confidence or "").strip().lower()
    direction_sign = {"positive": 1.0, "negative": -1.0}.get(direction_text, 0.0)
    if direction_sign == 0:
        return 0.0
    confidence_weight = float(
        (cfg.get("confidence_weights") or {}).get(
            confidence_text, (_DEFAULT_FOREIGN["confidence_weights"])["low"]
        )
    )
    action_weight = float(
        (cfg.get("action_weights") or {}).get(
            action_text, (_DEFAULT_FOREIGN["action_weights"])["other"]
        )
    )
    if action_text in ("rating_up", "target_up", "rating_down", "target_down"):
        return (1.0 if action_weight > 0 else -1.0) * confidence_weight
    if action_text in ("positioning", "sentiment"):
        return direction_sign * confidence_weight
    return 0.0


def _sessions_path(path: str | Path | None) -> Path:
    return Path(path) if path else Path(os.getenv("SESSIONS_DB_PATH", "data/sessions.db"))


def _benchmark_closes(as_of: str, sessions_db_path: str | Path | None = None) -> dict[str, float]:
    path = _sessions_path(sessions_db_path)
    if not path.exists():
        return {}
    import sqlite3

    conn = sqlite3.connect(path, timeout=15)
    conn.row_factory = sqlite3.Row
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
    return {str(row["trade_date"]): float(row["close"]) for row in rows}


def _upsert_outcome(
    event_key: str,
    *,
    status: str,
    entry_date: str | None,
    exit_date: str | None,
    benchmark_return: float | None,
    hit: bool | None,
    reason: str | None,
    db_path: str | Path | None = None,
) -> None:
    rr_store.init_db(db_path)
    conn = rr_store._connect(db_path)
    now = date.today().isoformat()
    try:
        conn.execute(
            """
            INSERT INTO foreign_broker_event_outcomes (
              event_key, status, entry_date, exit_date, benchmark_return, hit,
              reason, resolved_at, updated_at
            ) VALUES (?,?,?,?,?,?,?,?,?)
            ON CONFLICT(event_key) DO UPDATE SET
              status=excluded.status,
              entry_date=excluded.entry_date,
              exit_date=excluded.exit_date,
              benchmark_return=excluded.benchmark_return,
              hit=excluded.hit,
              reason=excluded.reason,
              resolved_at=excluded.resolved_at,
              updated_at=excluded.updated_at
            """,
            (
                event_key,
                status,
                entry_date,
                exit_date,
                benchmark_return,
                None if hit is None else (1 if hit else 0),
                reason,
                now if status in ("resolved", "unresolved") else None,
                now,
            ),
        )
        conn.commit()
    finally:
        conn.close()


def settle_foreign_events(
    *,
    as_of: str | None = None,
    db_path: str | Path | None = None,
    sessions_db_path: str | Path | None = None,
    calendar: list[str] | None = None,
    benchmark_closes: dict[str, float] | None = None,
) -> dict[str, int]:
    as_of_value = as_of or date.today().isoformat()
    rr_store.init_db(db_path)
    close_map = (
        benchmark_closes
        if benchmark_closes is not None
        else _benchmark_closes(as_of_value, sessions_db_path)
    )
    days = sorted(calendar) if calendar is not None else sorted(close_map)
    days = [day for day in days if day <= as_of_value]
    counts = {"settled": 0, "unresolved": 0, "pending": 0}
    if not days:
        return counts

    conn = rr_store._connect(db_path)
    try:
        rows = conn.execute(
            """
            SELECT e.* FROM foreign_broker_events e
            LEFT JOIN foreign_broker_event_outcomes o ON o.event_key = e.event_key
            WHERE o.event_key IS NULL AND e.published_at IS NOT NULL
            """
        ).fetchall()
    finally:
        conn.close()

    for row in rows:
        event = dict(row)
        key = str(event["event_key"])
        if str(event.get("target_type") or "") != "market":
            _upsert_outcome(
                key,
                status="unresolved",
                entry_date=None,
                exit_date=None,
                benchmark_return=None,
                hit=None,
                reason="target_not_market",
                db_path=db_path,
            )
            counts["unresolved"] += 1
            continue
        direction = str(event.get("direction") or "")
        if direction not in ("positive", "negative"):
            _upsert_outcome(
                key,
                status="unresolved",
                entry_date=None,
                exit_date=None,
                benchmark_return=None,
                hit=None,
                reason="no_direction",
                db_path=db_path,
            )
            counts["unresolved"] += 1
            continue
        try:
            published = str(event["published_at"])[:10]
            entry_idx = bisect.bisect_right(days, published)
        except (TypeError, ValueError):
            _upsert_outcome(
                key,
                status="unresolved",
                entry_date=None,
                exit_date=None,
                benchmark_return=None,
                hit=None,
                reason="missing_date",
                db_path=db_path,
            )
            counts["unresolved"] += 1
            continue
        exit_idx = entry_idx + DEFAULT_HORIZON_DAYS
        if exit_idx >= len(days):
            counts["pending"] += 1
            continue
        entry_date = days[entry_idx]
        exit_date = days[exit_idx]
        entry_close = close_map.get(entry_date)
        exit_close = close_map.get(exit_date)
        if not entry_close or exit_close is None or entry_close <= 0:
            _upsert_outcome(
                key,
                status="unresolved",
                entry_date=entry_date,
                exit_date=exit_date,
                benchmark_return=None,
                hit=None,
                reason="missing_benchmark",
                db_path=db_path,
            )
            counts["unresolved"] += 1
            continue
        benchmark_return = float(exit_close) / float(entry_close) - 1.0
        hit = benchmark_return > 0 if direction == "positive" else benchmark_return < 0
        _upsert_outcome(
            key,
            status="resolved",
            entry_date=entry_date,
            exit_date=exit_date,
            benchmark_return=benchmark_return,
            hit=hit,
            reason=None,
            db_path=db_path,
        )
        counts["settled"] += 1
    return counts


def _bucket_stats(rows: list[dict], bucket_type: str, key: str, config: dict) -> dict:
    values = list(rows)
    hits = sum(1 for row in values if row.get("hit"))
    returns = [
        float(row["benchmark_return"]) for row in values if row.get("benchmark_return") is not None
    ]
    scores = [
        float(row["event_score"])
        if row.get("event_score") is not None
        else event_score(row.get("action"), row.get("direction"), row.get("confidence"), config)
        for row in values
    ]
    n = len(values)
    return {
        "region": "foreign",
        "score_family": "sentiment",
        "bucket_type": bucket_type,
        "bucket_key": key,
        "n": n,
        "hits": hits,
        "hit_rate": (hits / n) if n else None,
        "mean_return": mean(returns) if returns else None,
        "net_score": mean(scores) if scores else None,
        "reliable": n >= RELIABLE_MIN_SAMPLES,
    }


def build_foreign_scorecard(
    snapshot_date: str,
    *,
    db_path: str | Path | None = None,
    config: dict | None = None,
) -> list[dict]:
    rr_store.init_db(db_path)
    conn = rr_store._connect(db_path)
    try:
        rows = conn.execute(
            """
            SELECT e.broker, e.action, e.direction, e.confidence, e.event_score,
                   o.hit, o.benchmark_return
            FROM foreign_broker_events e
            JOIN foreign_broker_event_outcomes o ON o.event_key = e.event_key
            WHERE o.status='resolved'
            """
        ).fetchall()
    finally:
        conn.close()
    cfg = _foreign_config(config)
    buckets: dict[tuple[str, str], list[dict]] = {}
    for row in rows:
        item = dict(row)
        for bucket_type, value in (
            ("broker", item.get("broker") or "unknown"),
            ("action", item.get("action") or "unknown"),
            ("direction", item.get("direction") or "unknown"),
        ):
            buckets.setdefault((bucket_type, str(value)), []).append(item)
    return [
        _bucket_stats(members, bucket_type, key, cfg)
        for (bucket_type, key), members in sorted(buckets.items())
    ]


def persist_foreign_scorecard(
    snapshot_date: str, rows: list[dict], *, db_path: str | Path | None = None
) -> int:
    rr_store.init_db(db_path)
    conn = rr_store._connect(db_path)
    now = date.today().isoformat()
    try:
        conn.execute(
            "DELETE FROM foreign_sentiment_scorecards WHERE snapshot_date=?", (snapshot_date,)
        )
        if rows:
            conn.executemany(
                """
                INSERT INTO foreign_sentiment_scorecards (
                  snapshot_date, region, score_family, bucket_type, bucket_key,
                  n, hits, hit_rate, mean_return, net_score, reliable, updated_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                [
                    (
                        snapshot_date,
                        "foreign",
                        "sentiment",
                        str(row["bucket_type"]),
                        str(row["bucket_key"]),
                        int(row.get("n") or 0),
                        int(row.get("hits") or 0),
                        row.get("hit_rate"),
                        row.get("mean_return"),
                        row.get("net_score"),
                        1 if row.get("reliable") else 0,
                        now,
                    )
                    for row in rows
                ],
            )
        conn.commit()
        return len(rows)
    finally:
        conn.close()


def latest_snapshot_date(db_path: str | Path | None = None) -> str | None:
    rr_store.init_db(db_path)
    conn = rr_store._connect(db_path)
    try:
        row = conn.execute("SELECT MAX(snapshot_date) FROM foreign_sentiment_scorecards").fetchone()
    finally:
        conn.close()
    return row[0] if row and row[0] else None


def list_foreign_scorecard(
    *,
    snapshot_date: str | None = None,
    bucket_type: str | None = None,
    bucket_key: str | None = None,
    db_path: str | Path | None = None,
) -> list[dict]:
    rr_store.init_db(db_path)
    date_value = snapshot_date or latest_snapshot_date(db_path)
    if not date_value:
        return []
    sql = "SELECT * FROM foreign_sentiment_scorecards WHERE snapshot_date=?"
    params: list[object] = [date_value]
    if bucket_type:
        sql += " AND bucket_type=?"
        params.append(bucket_type)
    if bucket_key:
        sql += " AND bucket_key=?"
        params.append(bucket_key)
    sql += " ORDER BY bucket_type ASC, bucket_key ASC"
    conn = rr_store._connect(db_path)
    try:
        rows = conn.execute(sql, params).fetchall()
    finally:
        conn.close()
    return [dict(row) for row in rows]
