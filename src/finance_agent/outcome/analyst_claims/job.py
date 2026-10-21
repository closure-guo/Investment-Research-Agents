"""分析师事后检验日批（add-analyst-ex-post-verification）。

顺序：生成主张 → 结算到期 → 生成并落记分卡快照 → 写运行记录。
单条结算失败保持 pending（次日重试），job 记 degraded；生成阶段抛错记 failed。
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import date
from pathlib import Path
from typing import Any

from finance_agent.data import analyst_claims_store as store
from finance_agent.outcome.analyst_claims import extract, scorecard, settle

logger = logging.getLogger("finance_agent.outcome.analyst_claims.job")

JOB_NAME = "analyst_claim_settle_daily"


def run_analyst_claim_settle_daily(
    *,
    as_of: str | None = None,
    db_path: str | Path | None = None,
    snapshot_db_path: str | Path | None = None,
    sessions_db_path: str | Path | None = None,
    calendar: list[str] | None = None,
    stock_prices: Callable[[str, str], dict | None] | None = None,
    benchmark_closes: dict[str, float] | Callable[[str], float | None] | None = None,
    tiers: dict[str, object] | None = None,
) -> dict[str, Any]:
    as_of_value = as_of or date.today().isoformat()
    try:
        generated = extract.generate_claims(db_path=db_path)
        counts = settle.settle_claims(
            as_of=as_of_value,
            claims_db_path=db_path,
            snapshot_db_path=snapshot_db_path,
            sessions_db_path=sessions_db_path,
            calendar=calendar,
            stock_prices=stock_prices,
            benchmark_closes=benchmark_closes,
        )
        rows = scorecard.build_scorecard(as_of_value, db_path=db_path, tiers=tiers)
        scorecard.persist_scorecard(as_of_value, rows, db_path=db_path)
        status = "degraded" if counts["errors"] else "ok"
        detail = store.detail_json(
            generated=generated,
            settled=counts["settled"],
            unresolved=counts["unresolved"],
            pending=counts["pending"],
            errors=counts["errors"],
            scorecard_rows=len(rows),
        )
        store.record_run(
            JOB_NAME,
            as_of_value,
            as_of_value,
            status,
            counts["settled"],
            detail=detail,
            db_path=db_path,
        )
        return {
            "status": status,
            "as_of": as_of_value,
            "generated": generated,
            **counts,
            "scorecard_rows": len(rows),
        }
    except Exception as exc:
        store.record_run(
            JOB_NAME,
            as_of_value,
            as_of_value,
            "failed",
            0,
            detail=store.detail_json(error=str(exc)[:500]),
            db_path=db_path,
        )
        logger.error("分析师事后检验日批失败: %s", exc)
        raise
