"""决策分布读数：按日统计 buy/sell/watch，识别连续零买入（看门狗）。

动机：系统在 581 个分析会话里一次「买入」都没给出，却没有任何指标报警——
决策分布本身必须成为被观测对象。日报据此在连续零买入时显式喊出来。

口径：
- 动作取自 ``predictions.rationale_snapshot`` 的 ``action`` 字段（与日报同源）；
- 缺字段/坏 JSON → 计入 ``other``，不静默丢行；
- ``zero_buy_streak`` 从最新一天往前数，遇到有买入的一天即停（按日，不按自然日补齐）；
- 库/表不存在 → 返回空结构（读不到不等于异常，调用方按「无数据」处理）。
"""

from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from typing import Any

_ACTIONS = ("buy", "sell", "watch")


def action_from_snapshot(raw: object) -> str | None:
    """从 rationale_snapshot（JSON 字符串或已解析 dict）取 action。"""
    if not raw:
        return None
    try:
        payload = json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    action = payload.get("action")
    return str(action) if action else None


def _db_path(db_path: str | Path | None) -> Path:
    return (
        Path(db_path)
        if db_path is not None
        else Path(os.getenv("SESSIONS_DB_PATH", "data/sessions.db"))
    )


def compute_decision_mix(days: int = 10, db_path: str | Path | None = None) -> dict[str, Any]:
    """按日统计决策动作分布，并给出「连续零买入」天数。

    Args:
        days: 只统计最近 N 个有决策的交易日（<=0 表示全部）。
        db_path: sessions 库路径；缺省读 SESSIONS_DB_PATH，再退 data/sessions.db。

    Returns:
        {"days": [{"date": str, "counts": {...}}], "zero_buy_streak": int, "total": {...}}
    """
    conn = sqlite3.connect(_db_path(db_path))
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT created_at, rationale_snapshot FROM predictions ORDER BY created_at ASC"
        ).fetchall()
    except sqlite3.OperationalError:
        rows = []
    finally:
        conn.close()

    per_day: dict[str, dict[str, int]] = {}
    for row in rows:
        ts = str(row["created_at"] or "")
        if len(ts) < 10:
            continue
        counts = per_day.setdefault(ts[:10], {"buy": 0, "sell": 0, "watch": 0, "other": 0})
        action = action_from_snapshot(row["rationale_snapshot"])
        counts[action if action in _ACTIONS else "other"] += 1

    ordered = sorted(per_day)[-days:] if days > 0 else sorted(per_day)

    streak = 0
    for day in reversed(ordered):
        if per_day[day]["buy"]:
            break
        streak += 1

    total = {k: sum(per_day[d][k] for d in ordered) for k in ("buy", "sell", "watch", "other")}
    return {
        "days": [{"date": d, "counts": per_day[d]} for d in ordered],
        "zero_buy_streak": streak,
        "total": total,
    }
