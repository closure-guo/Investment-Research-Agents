"""历史分析回填 predictions（add-user-feedback / add-track-record 运维修复）。

背景：ReAct 深模式落库挂点缺失 + pydantic 访问缺陷导致历史深度分析未进
predictions（历史战绩空）。本脚本扫描已完成会话的报告 markdown，解析「交易决策」
段（方向/置信度/入场价等），回填 predictions。幂等：已有同 session 标记的跳过。

delta update-decision-settlement-contract：与 ingest 同语义——参考价不可得不再标
unresolvable，存档 + WARN 且状态保持 open（判定不依赖参考价，结算入场价由行情派生）；
horizon_days 显式写配置默认值（不依赖表级默认）。

用法：
    uv run python scripts/backfill_predictions_from_reports.py [--db data/sessions.db] [--dry-run]
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
import uuid
from datetime import datetime
from pathlib import Path

from finance_agent.outcome.track_record.judgment import DEFAULT_HORIZON_DAYS

DEFAULT_DB = os.getenv("SESSIONS_DB_PATH", "data/sessions.db")

_DIRECTION_MAP = {"buy": "long", "sell": "short", "hold": "neutral", "watch": "neutral"}


def _parse_decision(md: str) -> dict | None:
    """从报告 markdown 解析「交易决策」段：方向/置信度/入场价/止损价/目标价。"""
    m = re.search(r"交易决策\s*\n+(.{0,800})", md, re.S)
    if not m:
        return None
    section = m.group(1)
    dm = re.search(r"\*\*方向\*\*[:：]\s*(buy|sell|hold|watch)", section)
    if not dm:
        return None
    out: dict = {"action": dm.group(1)}
    cm = re.search(r"\*\*置信度\*\*[:：]\s*([\d.]+)\s*%", section)
    if cm:
        out["confidence"] = round(float(cm.group(1)) / 100, 3)
    for field, key in (
        ("入场价", "entry_price"),
        ("止损价", "stop_loss"),
        ("目标价", "target_price"),
    ):
        pm = re.search(rf"\*\*{field}\**[:：]\s*([\d.]+)", section)
        if pm:
            out[key] = float(pm.group(1))
    return out


def backfill(db_path: str, dry_run: bool = False) -> int:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        sessions = conn.execute(
            "SELECT session_id, stock_code, stock_name, report_markdown, created_at "
            "FROM sessions WHERE status='completed' AND report_markdown IS NOT NULL "
            "ORDER BY created_at"
        ).fetchall()
        inserted = 0
        for s in sessions:
            # 幂等：该 session 已回填过则跳过（快照含 session 标记）
            exists = conn.execute(
                "SELECT COUNT(*) FROM predictions WHERE rationale_snapshot LIKE ?",
                (f'%"backfill_session": "{s["session_id"]}"%',),
            ).fetchone()[0]
            if exists:
                continue
            if not s["report_markdown"]:
                continue
            decision = _parse_decision(s["report_markdown"])
            if decision is None:
                print(f"  [SKIP] {s['session_id']}({s['stock_code']}) 报告无「交易决策」段")
                continue
            action = decision["action"]
            direction = _DIRECTION_MAP[action]
            entry_price = decision.get("entry_price")
            # 参考价不可得：存档 + WARN，状态保持 open（判定不依赖参考价）
            status = "open"
            if entry_price is None:
                print(f"  [WARN] {s['stock_code']} 报告无入场价（参考价不可得），存档 open")
            snapshot = json.dumps(
                {
                    "backfill_session": s["session_id"],
                    "action": action,
                    "source": "report_markdown",
                },
                ensure_ascii=False,
            )
            now = s["created_at"] or datetime.now().isoformat()
            if dry_run:
                print(f"  [DRY] {s['stock_code']} {action}→{direction} status={status}")
                inserted += 1
                continue
            conn.execute(
                """INSERT INTO predictions (
                     prediction_id, source_type, symbol, symbol_name, direction,
                     entry_price, target_price, horizon_days, confidence, benchmark,
                     rationale_snapshot, langfuse_trace_id, status, created_at, updated_at,
                     resolution_rule
                   ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    f"p_{uuid.uuid4().hex[:12]}",
                    "live",
                    f"{s['stock_code']}.SH"
                    if str(s["stock_code"]).startswith("6")
                    else f"{s['stock_code']}.SZ",
                    s["stock_name"],
                    direction,
                    entry_price,
                    decision.get("target_price"),
                    DEFAULT_HORIZON_DAYS,
                    decision.get("confidence"),
                    "000300.SH",
                    snapshot,
                    None,
                    status,
                    now,
                    now,
                    None,
                ),
            )
            inserted += 1
            print(f"  [OK] {s['stock_code']} {action}→{direction} status={status}")
        if not dry_run:
            conn.commit()
        print(f"\n回填 {inserted} 条")
        return inserted
    finally:
        conn.close()


def main() -> int:
    p = argparse.ArgumentParser(description="历史分析回填 predictions")
    p.add_argument("--db", default=DEFAULT_DB)
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()
    if not Path(args.db).exists():
        print(f"[ERROR] DB 不存在: {args.db}", file=sys.stderr)
        return 1
    backfill(args.db, dry_run=args.dry_run)
    return 0


if __name__ == "__main__":
    sys.exit(main())
