"""拒绝池复苏重评通道（add-rejected-factor-revival；零 LLM）。

拒绝池（``rejected``）每天记录 IC（add-rejected-factor-pool 日批跟踪），本模块
把「记录」变成「消费者」：滚动 |ICIR| 越过与闸②同值的复苏静默线（
``MIN_ABS_ICIR``）且冷却期外的 rejected 行，经外部导入同一路径（
``run_factor_external_import``，``ledger_source="revival"``）重新过五道闸。契约：

- ``rejected`` 保持终态（状态机零改动）：过闸 = 新 candidate 行入库；再拒 = 既有
  同构 upsert 追加 ``re_rejected`` 事件（冷却自该事件重算，天然防乒乓）；
- 静默线只作提交预筛，五道闸是唯一准入权威（闸②复核 / 闸④撞池 / 闸⑤样本外）；
- 冷却时钟 = 该行 ``created`` / ``re_rejected`` 事件时间戳的最大值
  （``REVIVAL_COOLDOWN_DAYS`` 自然日）；时间戳损坏 → 不冷却（如实放行）；
- 单次运行提交 ≤ ``REVIVAL_DAILY_CAP``（按 |滚动 ICIR| 降序、并列按 factor_id 升序）；
- ``dry_run=True`` 只出预览不提交；无可复苏候选零摘要零面板；面板不可得 fail loud
  （随 external 路径上抛，run_job 记 failed）。
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Any

from finance_agent.factors.constraints import (
    MIN_ABS_ICIR,
    REVIVAL_COOLDOWN_DAYS,
    REVIVAL_DAILY_CAP,
)
from finance_agent.factors.eval_engine import MarketPanel
from finance_agent.factors.external import DEFAULT_IMPORT_SEED, run_factor_external_import
from finance_agent.factors.library import list_events, list_factors, list_ic_series
from finance_agent.factors.tools import IC_WINDOW
from finance_agent.screening.ranker import recent_ic_stats

logger = logging.getLogger(__name__)


def _last_revival_event_at(events: list[dict[str, Any]]) -> str | None:
    """该行 ``created`` / ``re_rejected`` 事件时间戳的最大值（冷却锚点）；无 → ``None``。"""
    stamps = [
        str(event.get("created_at"))
        for event in events
        if str(event.get("event")) in {"created", "re_rejected"} and event.get("created_at")
    ]
    return max(stamps) if stamps else None


def _in_cooldown(last_at: str | None, *, now: datetime, cooldown_days: int) -> bool:
    """冷却判定：``last_at`` 距 ``now`` 不足 ``cooldown_days`` 自然日 → True。

    ``last_at`` 缺失或解析失败 → False（如实放行；事件历史可审计，不虚构冷却）。
    """
    if not last_at:
        return False
    try:
        last = datetime.fromisoformat(last_at)
    except ValueError:
        return False
    return (now - last).days < cooldown_days


def run_factor_revival(
    *,
    dry_run: bool = False,
    db_path: str | Path | None = None,
    snapshot_db_path: str | Path | None = None,
    panel: MarketPanel | None = None,
    seed: int = DEFAULT_IMPORT_SEED,
    cooldown_days: int = REVIVAL_COOLDOWN_DAYS,
    daily_cap: int = REVIVAL_DAILY_CAP,
    now: datetime | None = None,
) -> dict[str, Any]:
    """拒绝池复苏日批入口（ops job ``factor_revival_daily`` 与测试共用）。

    返回摘要：``checked``（rejected 行数）/ ``eligible``（过静默线且冷却外）/
    ``cooldown_skipped`` / ``submitted`` / ``admitted`` / ``re_rejected`` /
    ``errors`` / ``dry_run`` / ``revived``（提交或预览明细）/ ``rejections``。
    """
    current = now or datetime.now()
    rows = list_factors(status="rejected", db_path=db_path)
    stats = recent_ic_stats(rows, window=IC_WINDOW, db_path=db_path) if rows else {}
    events_by_factor: dict[int, list[dict[str, Any]]] = {}
    for event in list_events(db_path=db_path):
        events_by_factor.setdefault(int(event["factor_id"]), []).append(event)

    eligible: list[tuple[float, int, dict[str, Any]]] = []
    cooldown_skipped = 0
    for row in rows:
        factor_id = int(row["id"])
        series = list_ic_series(factor_id, limit=IC_WINDOW, db_path=db_path)
        if len(series) < IC_WINDOW:
            continue
        _mean, icir = stats.get(factor_id, (0.0, 0.0))
        if abs(icir) < MIN_ABS_ICIR:
            continue
        if _in_cooldown(
            _last_revival_event_at(events_by_factor.get(factor_id, [])),
            now=current,
            cooldown_days=cooldown_days,
        ):
            cooldown_skipped += 1
            continue
        eligible.append((abs(icir), factor_id, row))

    eligible.sort(key=lambda item: (-item[0], item[1]))
    selected = eligible[: max(0, daily_cap)]
    summary: dict[str, Any] = {
        "checked": len(rows),
        "eligible": len(eligible),
        "cooldown_skipped": cooldown_skipped,
        "submitted": 0,
        "admitted": 0,
        "re_rejected": 0,
        "errors": 0,
        "dry_run": bool(dry_run),
        "revived": [
            {
                "factor_id": factor_id,
                "expression": str(row.get("expression") or ""),
                "category": str(row.get("category") or ""),
                "rolling_abs_icir": round(abs_icir, 4),
            }
            for abs_icir, factor_id, row in selected
        ],
        "rejections": [],
    }
    if not selected:
        return summary
    if dry_run:
        return summary

    payload = [
        {
            "expression": str(row.get("expression") or ""),
            "hypothesis": str(row.get("hypothesis") or "").strip() or "拒绝池复苏重提",
            "category": str(row.get("category") or "").strip() or "外部-其他",
        }
        for _abs_icir, _factor_id, row in selected
    ]
    import_summary = run_factor_external_import(
        payload,
        panel=panel,
        seed=seed,
        db_path=db_path,
        snapshot_db_path=snapshot_db_path,
        ledger_source="revival",
    )
    summary["submitted"] = len(payload)
    summary["admitted"] = int(import_summary.get("admitted") or 0)
    summary["re_rejected"] = int(import_summary.get("rejected") or 0)
    summary["errors"] = int(import_summary.get("errors") or 0)
    summary["rejections"] = list(import_summary.get("rejections") or [])
    logger.info(
        "factor revival: checked=%s eligible=%s submitted=%s admitted=%s re_rejected=%s",
        summary["checked"],
        summary["eligible"],
        summary["submitted"],
        summary["admitted"],
        summary["re_rejected"],
    )
    return summary
