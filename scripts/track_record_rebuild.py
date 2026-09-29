"""wipe + 重建 daily_marks / equity_curve / agent_metrics_daily（delta update-track-record-data-integrity）。

三张 derived 表可随时从 predictions + 行情重算；predictions / audit_log 不动。
重算前自动备份 <db>.bak-rebuild。建议先拷贝生产库演练：

  cp data/sessions.db /tmp/sessions-rebuild-check.db
  uv run python scripts/track_record_rebuild.py --db-path /tmp/sessions-rebuild-check.db --yes
"""

from __future__ import annotations

import argparse
import os
import shutil
import sqlite3
import sys

from finance_agent.outcome.track_record.marking import run_daily_marking
from finance_agent.outcome.track_record.model import (
    get_latest_metrics,
    init_track_record_tables,
    list_equity_curve,
)

_DERIVED_TABLES = ("daily_marks", "equity_curve", "agent_metrics_daily")


def _resolve_db(args_db: str | None) -> str:
    if args_db:
        return args_db
    return os.getenv("SESSIONS_DB_PATH", "data/sessions.db")


def main() -> None:
    ap = argparse.ArgumentParser(description="wipe + 重建战绩 derived 三表")
    ap.add_argument(
        "--db-path",
        default=None,
        help="目标 SQLite 路径（缺省 SESSIONS_DB_PATH 或 data/sessions.db）",
    )
    ap.add_argument("--kline-days", type=int, default=280)
    ap.add_argument("--yes", action="store_true", help="确认清空三张 derived 表并重建")
    args = ap.parse_args()
    if not args.yes:
        sys.exit(
            "拒绝执行：将清空 daily_marks/equity_curve/agent_metrics_daily，需 --yes 确认（建议先拷贝库演练）"
        )
    db = _resolve_db(args.db_path)
    shutil.copy2(db, db + ".bak-rebuild")  # 重算前自动备份
    init_track_record_tables(db)
    conn = sqlite3.connect(db)
    try:
        for table in _DERIVED_TABLES:
            conn.execute(f"DELETE FROM {table}")  # noqa: S608 — 表名来自模块内固定白名单
        conn.commit()
    finally:
        conn.close()
    result = run_daily_marking(db_path=db, kline_days=args.kline_days)
    curve = list_equity_curve(db_path=db)
    latest = get_latest_metrics(db_path=db)
    print("rebuild:", result)
    print(
        "equity points:",
        len(curve),
        "first:",
        curve[0] if curve else None,
        "last:",
        curve[-1] if curve else None,
    )
    print("latest metrics:", latest)
    if latest:
        annual, vol, sharpe = (
            latest.get("annual_return"),
            latest.get("volatility"),
            latest.get("sharpe"),
        )
        if (
            (annual is not None and abs(annual) > 2.0)
            or (vol is not None and vol > 2.0)
            or (sharpe is not None and abs(sharpe) > 5.0)
        ):
            print("⚠️ 指标量级仍异常（年化/波动/夏普超常识范围）——按 incident 032 纪律先归因再处置")


if __name__ == "__main__":
    main()
