"""事件线日批薄封装（delta add-event-driven-lane T6；供调度与手动补跑）。

三个 job（scheduler 16:05 / 16:15 / 16:25 触发，均为工作日）：

- ``run_earnings_fetch_daily``：按报告期抓取业绩预告并幂等落库。取数窗口 =
  ``EVENTS_FETCH_PERIODS``（默认 4）个最近季度末（首跑即回填近一年；调大可做全量
  回填，时点纪律由 ``available_at`` 恒等公告日保证）。**失败留痕**：任一期请求失败
  （``sources_seen["earnings_forecast"]`` 记 ``none``）→ 上抛 RuntimeError（ops
  ``run_job`` 记 ``failed`` + 原因），MUST NOT 以零条新增冒充成功；
- ``run_earnings_triage_daily``：扫未分诊（``available_at <= 今日``）→ LLM 分诊
  （快照要素注入，快照不可得则退化为预告要素-only）→ 构建事件观察清单；
- ``run_event_settle_daily``：记账入场（T+1 开盘口径）+ 到期结算，返回两步摘要。

horizon 由 ``EVENTS_HORIZON_DAYS``（默认 5）在**调用期**读取（配置不冻结）。
"""

from __future__ import annotations

import logging
import os
from datetime import date
from pathlib import Path
from typing import Any

from finance_agent.data.akshare_client import AKShareClient, _default_report_period
from finance_agent.outcome.events.journal import (
    DEFAULT_HORIZON_DAYS,
    entry_candidates,
    settle_due,
)
from finance_agent.outcome.events.model import list_untriaged, upsert_forecasts
from finance_agent.outcome.events.triage import GatewayTriageClient, triage_forecasts
from finance_agent.outcome.events.watchlist import build_watchlist
from finance_agent.screening.pipeline import read_snapshot_calendar

logger = logging.getLogger("finance_agent.outcome.events.job")

# 抓取报告期数量默认（EVENTS_FETCH_PERIODS；首跑回填近一年）
_DEFAULT_FETCH_PERIODS = 4
# 报告期候选（季度末 月, 日）
_QUARTER_ENDS: tuple[tuple[int, int], ...] = ((3, 31), (6, 30), (9, 30), (12, 31))
# 快照要素 → 分诊快照上下文（键 = triage.SNAPSHOT_INPUT_FIELDS 同名）
_SNAPSHOT_CTX_COLUMNS: tuple[str, ...] = ("close_raw", "pct_chg", "amount")


def _fetch_periods() -> int:
    raw = os.getenv("EVENTS_FETCH_PERIODS")
    if not raw:
        return _DEFAULT_FETCH_PERIODS
    try:
        value = int(raw)
    except ValueError:
        logger.warning("非法 EVENTS_FETCH_PERIODS=%r，回落默认 %d", raw, _DEFAULT_FETCH_PERIODS)
        return _DEFAULT_FETCH_PERIODS
    return max(1, value)


def horizon_days() -> int:
    """事件线 horizon（``EVENTS_HORIZON_DAYS``，默认 5）；非法值回落默认不炸日批。"""
    raw = os.getenv("EVENTS_HORIZON_DAYS")
    if not raw:
        return DEFAULT_HORIZON_DAYS
    try:
        value = int(raw)
    except ValueError:
        logger.warning("非法 EVENTS_HORIZON_DAYS=%r，回落默认 %d", raw, DEFAULT_HORIZON_DAYS)
        return DEFAULT_HORIZON_DAYS
    if value < 1:
        logger.warning("EVENTS_HORIZON_DAYS=%r 非正，回落默认 %d", raw, DEFAULT_HORIZON_DAYS)
        return DEFAULT_HORIZON_DAYS
    return value


def recent_report_periods(count: int, *, today: date | None = None) -> list[str]:
    """最近 ``count`` 个季度末报告期（升序，含不晚于今日的最近季度末）。"""
    anchor = _default_report_period(today)  # 今日之前最近的季度末（ISO）
    periods: list[str] = []
    cursor_year = int(anchor[:4])
    # 源接口数据自 20081231 起（akshare stock_yjyg_em 文档），下界护栏防死循环
    while len(periods) < count and cursor_year >= 2008:
        for month, day in reversed(_QUARTER_ENDS):
            period = date(cursor_year, month, day)
            if period.isoformat() > anchor:
                continue
            periods.append(period.isoformat())
            if len(periods) >= count:
                break
        cursor_year -= 1
    return sorted(set(periods))


def run_earnings_fetch_daily(*, client: Any = None, today: date | None = None) -> dict[str, Any]:
    """抓取日批：最近 N 个报告期幂等 upsert；任一期失败 → 上抛（失败留痕）。"""
    client = client or AKShareClient()
    periods = recent_report_periods(_fetch_periods(), today=today)
    total_rows: list[dict] = []
    for period in periods:
        total_rows.extend(client.fetch_earnings_forecasts(period))
    failed = "none" in getattr(client, "sources_seen", {}).get("earnings_forecast", set())
    if failed:
        raise RuntimeError(
            f"业绩预告抓取失败（信源缺失/返回失败）: periods={periods}——"
            "本次运行留痕失败原因，不得以零条新增冒充成功"
        )
    upserted = upsert_forecasts(total_rows) if total_rows else 0
    summary = {
        "periods": periods,
        "fetched": len(total_rows),
        "upserted": upserted,
        "sources": sorted(getattr(client, "sources_seen", {}).get("earnings_forecast", set())),
    }
    logger.info("业绩预告抓取完成: %s", summary)
    return summary


def _snapshot_context(
    codes: list[str], *, db_path: str | Path | None = None
) -> dict[str, dict[str, Any]]:
    """标的 → 快照要素（分诊输入；快照不可得 → 空映射，退化为预告要素-only）。"""
    if not codes:
        return {}
    from finance_agent.data.snapshot import latest_snapshot_date, read_universe

    try:
        as_of = latest_snapshot_date(db_path)
        if as_of is None:
            return {}
        wanted = set(codes)
        context: dict[str, dict[str, Any]] = {}
        for row in read_universe(as_of, db_path=db_path):
            code = str(row.get("code") or "")
            if code not in wanted:
                continue
            context[code] = {
                "close": row.get("close_raw"),
                "pct_chg": row.get("pct_chg"),
                "amount": row.get("amount"),
            }
        return context
    except Exception:  # noqa: BLE001 -- 快照不可用不阻断分诊（输入预算退化）
        logger.warning("分诊快照要素取数失败，退化为预告要素-only", exc_info=True)
        return {}


def run_earnings_triage_daily(
    *,
    client: Any = None,
    as_of: str | None = None,
    db_path: str | Path | None = None,
) -> dict[str, Any]:
    """分诊日批：未分诊（时点截断）→ LLM 分诊 → 观察清单；返回合并摘要。"""
    as_of = as_of or date.today().isoformat()
    rows = list_untriaged(as_of=as_of, db_path=db_path)
    if not rows:
        return {"triaged": 0, "skipped": 0, "watchlist_added": 0, "as_of": as_of}
    codes = [str(row.get("code") or "") for row in rows]
    snapshot_ctx = _snapshot_context([code for code in codes if code])
    client = client or GatewayTriageClient()
    result = triage_forecasts(
        rows, client=client, db_path=db_path, as_of=as_of, snapshot_ctx_by_symbol=snapshot_ctx
    )
    added = build_watchlist(db_path=db_path)
    summary = {**result, "watchlist_added": added, "as_of": as_of}
    logger.info("事件分诊日批完成: %s", summary)
    return summary


def run_event_settle_daily(
    *,
    client: Any = None,
    as_of: str | None = None,
    db_path: str | Path | None = None,
    snapshot_db_path: str | Path | None = None,
) -> dict[str, Any]:
    """记账 + 结算日批：入场（T+1 开盘）→ 到期结算；返回 ``{"entry", "settle"}``。"""
    as_of = as_of or date.today().isoformat()
    client = client or AKShareClient()
    calendar = read_snapshot_calendar(snapshot_db_path)
    entry_summary = entry_candidates(
        as_of=as_of,
        kline_client=client,
        snapshot_db_path=snapshot_db_path,
        db_path=db_path,
        horizon_days=horizon_days(),
    )
    settle_summary = settle_due(
        as_of=as_of, kline_client=client, calendar=calendar, db_path=db_path
    )
    summary = {"as_of": as_of, "entry": entry_summary, "settle": settle_summary}
    logger.info("事件记账结算日批完成: %s", summary)
    return summary
