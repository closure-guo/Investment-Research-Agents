"""Outcome 读数健康检查（spec evaluation「Outcome 读数收口纪律」第①步）。

四项读数 + 快照完整性：结算成功率 / 行情缺失率 / 记账完整率 / 污染护栏。
主检查（passed 判据）：结算成功率 ≥0.90、行情缺失率 ≤0.10、integrity 零不一致。
告警（不阻断，供人工终裁入口）：trace 缺失行数、重复 snapshot_hash 组数（incident 031 指纹形态）。
只读：不写 predictions；integrity_check 例外——按既有契约写 integrity_mismatch 审计。
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
from pathlib import Path
from typing import Any

from finance_agent.outcome.track_record.model import integrity_check

MIN_SETTLEMENT_SUCCESS_RATE = 0.90
MAX_MARKET_MISSING_RATE = 0.10


def _db_path(db_path: str | Path | None) -> Path:
    return Path(db_path) if db_path else Path(os.getenv("SESSIONS_DB_PATH", "data/sessions.db"))


def collect_outcome_health(
    db_path: str | Path | None = None,
    *,
    source_type: str | None = "live",
    since: str | None = None,
) -> dict[str, Any]:
    path = _db_path(db_path)
    conn = sqlite3.connect(path, timeout=15.0)
    conn.row_factory = sqlite3.Row
    try:
        where, params = " WHERE 1=1", []
        if source_type:
            where += " AND source_type=?"
            params.append(source_type)
        if since:
            where += " AND created_at>=?"
            params.append(since)
        counts = dict(
            conn.execute(
                f"SELECT status, COUNT(*) FROM predictions{where} GROUP BY status",  # noqa: S608
                params,
            ).fetchall()
        )
        total = sum(counts.values())
        resolved = sum(
            counts.get(s, 0) for s in ("resolved_win", "resolved_loss", "resolved_neutral")
        )
        unresolvable = counts.get("unresolvable", 0)
        settleable = resolved + unresolvable
        success_rate = round(resolved / settleable, 4) if settleable else None
        missing_rate = round(unresolvable / settleable, 4) if settleable else None
        price_missing = int(
            conn.execute(
                f"SELECT COUNT(*) FROM predictions{where} AND entry_price IS NULL",  # noqa: S608
                params,
            ).fetchone()[0]
        )
        trace_missing = int(
            conn.execute(
                f"SELECT COUNT(*) FROM predictions{where}"  # noqa: S608
                " AND (langfuse_trace_id IS NULL OR langfuse_trace_id='')",
                params,
            ).fetchone()[0]
        )
        dup_groups = conn.execute(
            f"SELECT snapshot_hash, COUNT(*) c FROM predictions{where}"  # noqa: S608
            " AND snapshot_hash IS NOT NULL GROUP BY snapshot_hash HAVING c>1",
            params,
        ).fetchall()
    finally:
        conn.close()

    integrity = integrity_check(path)
    checks = {
        "settlement_success": success_rate is not None
        and success_rate >= MIN_SETTLEMENT_SUCCESS_RATE,
        "market_missing": missing_rate is None or missing_rate <= MAX_MARKET_MISSING_RATE,
        "integrity": integrity["mismatch_count"] == 0,
    }
    warnings: list[str] = []
    if trace_missing:
        warnings.append(
            f"trace 缺失 {trace_missing} 行（非阻断；Langfuse 离线期可预期，异常时人工核对）"
        )
    if dup_groups:
        warnings.append(
            f"重复 snapshot_hash 组 {len(dup_groups)} 个（疑似测试泄漏指纹，须人工核对，incident 031）"
        )
    return {
        "db": str(path),
        "source_type": source_type,
        "since": since,
        "total": total,
        "status_counts": counts,
        "settled_resolved": resolved,
        "unresolvable": unresolvable,
        "settlement_success_rate": success_rate,
        "market_missing_rate": missing_rate,
        "entry_price_missing": price_missing,
        "bookkeeping_completeness": round(1 - price_missing / total, 4) if total else None,
        "integrity_checked": integrity["checked"],
        "integrity_mismatches": integrity["mismatch_count"],
        "trace_missing": trace_missing,
        "duplicate_snapshot_groups": len(dup_groups),
        "checks": checks,
        "warnings": warnings,
        "passed": all(checks.values()),
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="outcome 读数健康检查（只读）")
    ap.add_argument("--db", default=None)
    ap.add_argument("--source-type", default="live")
    ap.add_argument("--since", default=None)
    args = ap.parse_args(argv)
    report = collect_outcome_health(args.db, source_type=args.source_type or None, since=args.since)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
