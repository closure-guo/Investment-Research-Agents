"""事件观察清单构建（delta add-event-driven-lane T5）。

口径（docs/evals/metrics.md §1.15 + spec「事件观察清单与深度分析接线」）：
``verdict=outperform`` 且清单无条目的分诊结果进入观察清单（``source="earnings"``）；
``in_line``/``risk`` 一律不入。清单去重由 ``model.insert_watchlist`` 的
``forecast_key`` 唯一约束兜底——**重跑零重复**（已存在的条目不重置状态/session）。
"""

from __future__ import annotations

import logging
from pathlib import Path

from finance_agent.outcome.events.model import _connect, init_events_db, insert_watchlist

logger = logging.getLogger("finance_agent.outcome.events.watchlist")

# 清单来源标签（观察清单条目与事件记账同源标识）
WATCHLIST_SOURCE = "earnings"


def build_watchlist(*, db_path: str | Path | None = None) -> int:
    """把未入清单的 ``outperform`` 分诊结论写入观察清单，返回新增条数（幂等）。"""
    init_events_db(db_path)
    conn = _connect(db_path)
    try:
        rows = conn.execute(
            """SELECT t.forecast_key FROM event_triage t
               LEFT JOIN event_watchlist w ON w.forecast_key = t.forecast_key
               WHERE t.verdict = 'outperform' AND w.forecast_key IS NULL
               ORDER BY t.triaged_at ASC, t.forecast_key ASC"""
        ).fetchall()
    finally:
        conn.close()
    added = 0
    for row in rows:
        key = str(row["forecast_key"])
        if insert_watchlist(key, source=WATCHLIST_SOURCE, db_path=db_path) is not None:
            added += 1
    if added:
        logger.info("事件观察清单新增 %d 条（outperform）", added)
    return added
