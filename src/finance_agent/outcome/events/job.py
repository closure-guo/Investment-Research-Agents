"""事件线日批薄封装（delta add-event-driven-lane T6；供调度与手动补跑）。

三个 job（scheduler 16:05 / 16:15 / 16:25 触发，均为工作日）：

- ``run_earnings_fetch_daily``：按报告期抓取业绩预告并幂等落库。取数窗口 =
  ``EVENTS_FETCH_PERIODS``（默认 4）个最近季度末（首跑即回填近一年；调大可做全量
  回填，时点纪律由 ``available_at`` 恒等公告日保证）。**失败留痕**：任一期请求失败
  （``sources_seen["earnings_forecast"]`` 记 ``none``）→ 成功期先幂等落库（不丢已成功
  数据），再上抛 RuntimeError（异常文案带 ``upserted`` 与失败期列表；ops ``run_job``
  记 ``failed`` + 原因），MUST NOT 以零条新增冒充成功；
- ``run_earnings_triage_daily``：扫未分诊（``available_at <= 今日``）→ LLM 分诊
  （快照要素**逐行时点化**注入：每行取 ≤ 该行 ``available_at`` 的最近快照日字段；
  无覆盖行不注入快照字段，缺失计数入摘要 ``snapshot_ctx_missing``）→ 构建事件观察清单；
- ``run_event_settle_daily``：记账入场（T+1 开盘口径，交易日历识别停牌缺口）+ 到期
  结算，返回两步摘要。

horizon 由 ``EVENTS_HORIZON_DAYS``（默认 5）在**调用期**读取（配置不冻结）。
"""

from __future__ import annotations

import logging
import os
from collections.abc import Mapping, Sequence
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from finance_agent.data.akshare_client import AKShareClient, _default_report_period
from finance_agent.outcome.events.journal import (
    DEFAULT_HORIZON_DAYS,
    entry_candidates,
    settle_due,
)
from finance_agent.outcome.events.model import (
    forecast_key,
    list_untriaged,
    upsert_forecasts,
)
from finance_agent.outcome.events.triage import GatewayTriageClient, triage_forecasts
from finance_agent.outcome.events.watchlist import build_watchlist
from finance_agent.screening.pipeline import read_snapshot_calendar

logger = logging.getLogger("finance_agent.outcome.events.job")

# 抓取报告期数量默认（EVENTS_FETCH_PERIODS；首跑回填近一年）
_DEFAULT_FETCH_PERIODS = 4
# 报告期候选（季度末 月, 日）
_QUARTER_ENDS: tuple[tuple[int, int], ...] = ((3, 31), (6, 30), (9, 30), (12, 31))


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
    """抓取日批：最近 N 个报告期幂等 upsert；任一期失败 → 成功期先落库再上抛。

    逐期失败检测 = 该次调用新增 ``sources_seen["earnings_forecast"]`` 的 ``none``
    标记（实时差集归因；同批次内重复失败会再上抛，失败期列表以可归因者为准）；
    失败期**之外**的已抓行先 ``upsert_forecasts``（不丢已成功数据），随后上抛
    RuntimeError（文案带 ``upserted`` 与失败期列表）。
    """
    client = client or AKShareClient()
    periods = recent_report_periods(_fetch_periods(), today=today)
    seen: set[str] = getattr(client, "sources_seen", {}).setdefault("earnings_forecast", set())
    total_rows: list[dict] = []
    failed_periods: list[str] = []
    for period in periods:
        seen_before = set(seen)
        rows = client.fetch_earnings_forecasts(period) or []
        if "none" in (seen - seen_before):
            failed_periods.append(period)  # 本次调用新增 none = 该期请求失败
            continue
        total_rows.extend(rows)
    upserted = upsert_forecasts(total_rows) if total_rows else 0
    summary = {
        "periods": periods,
        "fetched": len(total_rows),
        "upserted": upserted,
        "failed_periods": failed_periods,
        "sources": sorted(seen),
    }
    if failed_periods or "none" in seen:
        logger.error("业绩预告抓取失败（已成功期照常落库）: %s", summary)
        raise RuntimeError(
            f"业绩预告抓取失败（信源缺失/返回失败）: failed_periods={failed_periods} "
            f"upserted={upserted} periods={periods}——本次运行留痕失败原因，"
            "不得以零条新增冒充成功"
        )
    logger.info("业绩预告抓取完成: %s", summary)
    return summary


def _snapshot_day_at_or_before(asof: str) -> str | None:
    """``≤ asof`` 的最近快照日（无覆盖 → None；非规范日期 → 上抛，由调用方按缺失处置）。"""
    from finance_agent.data.snapshot import latest_snapshot_date

    next_day = (date.fromisoformat(asof) + timedelta(days=1)).isoformat()
    return latest_snapshot_date(before=next_day)


def _snapshot_fields(snapshot_row: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """快照行 → 分诊快照要素（键 = ``triage.SNAPSHOT_INPUT_FIELDS`` 同名）。

    只取快照表实际可得列（``close``←``close_raw`` / ``pct_chg`` / ``amount``）；
    该股该日无行 → None（不注入、不编造）。
    """
    if snapshot_row is None:
        return None
    return {
        "close": snapshot_row.get("close_raw"),
        "pct_chg": snapshot_row.get("pct_chg"),
        "amount": snapshot_row.get("amount"),
    }


def _snapshot_context(
    rows: Sequence[Mapping[str, Any]],
) -> tuple[dict[str, dict[str, Any]], int]:
    """逐行时点化快照要素 → ``(forecast_key → ctx, 缺失计数)``。

    每行取「**≤ 该行** ``available_at`` 的最近快照日」的该股快照字段（消除回填批次
    共用单一快照日的前视选择偏差；日解析与当日全市场行读取按日缓存）。该日快照库无
    覆盖 / 无该股行 / 快照不可读（老公告、空库）→ 该行**不注入快照字段**（仅预告要素），
    计入缺失计数（批次摘要 ``snapshot_ctx_missing`` 留痕）。
    """
    from finance_agent.data.snapshot import read_universe

    context: dict[str, dict[str, Any]] = {}
    missing = 0
    day_by_asof: dict[str, str | None] = {}
    rows_by_day: dict[str, dict[str, dict[str, Any]]] = {}
    warned = False
    for row in rows:
        code = str(row.get("code") or "")
        asof = str(row.get("available_at") or row.get("announce_date") or "")
        fields: dict[str, Any] | None = None
        if code and asof:
            try:
                if asof not in day_by_asof:
                    day_by_asof[asof] = _snapshot_day_at_or_before(asof)
                day = day_by_asof[asof]
                if day is not None:
                    if day not in rows_by_day:
                        rows_by_day[day] = {
                            str(item.get("code") or ""): item for item in read_universe(day)
                        }
                    fields = _snapshot_fields(rows_by_day[day].get(code))
            except Exception:  # noqa: BLE001 -- 快照不可用不阻断分诊（输入预算退化）
                if not warned:
                    logger.warning("分诊快照要素取数失败，退化为预告要素-only", exc_info=True)
                    warned = True
        if fields is None:
            missing += 1
        else:
            context[forecast_key(row)] = fields
    return context, missing


def run_earnings_triage_daily(
    *,
    client: Any = None,
    as_of: str | None = None,
    db_path: str | Path | None = None,
) -> dict[str, Any]:
    """分诊日批：未分诊（时点截断）→ 逐行时点快照 → LLM 分诊 → 观察清单。"""
    as_of = as_of or date.today().isoformat()
    rows = list_untriaged(as_of=as_of, db_path=db_path)
    if not rows:
        return {
            "triaged": 0,
            "skipped": 0,
            "watchlist_added": 0,
            "snapshot_ctx_missing": 0,
            "as_of": as_of,
        }
    snapshot_ctx, snapshot_missing = _snapshot_context(rows)
    client = client or GatewayTriageClient()
    result = triage_forecasts(
        rows,
        client=client,
        db_path=db_path,
        as_of=as_of,
        snapshot_ctx_by_key=snapshot_ctx,
    )
    added = build_watchlist(db_path=db_path)
    summary = {
        **result,
        "watchlist_added": added,
        "snapshot_ctx_missing": snapshot_missing,
        "as_of": as_of,
    }
    logger.info("事件分诊日批完成: %s", summary)
    return summary


def run_event_settle_daily(
    *,
    client: Any = None,
    as_of: str | None = None,
    db_path: str | Path | None = None,
    snapshot_db_path: str | Path | None = None,
) -> dict[str, Any]:
    """记账 + 结算日批：入场（T+1 开盘，日历识别停牌缺口）→ 到期结算。"""
    as_of = as_of or date.today().isoformat()
    client = client or AKShareClient()
    calendar = read_snapshot_calendar(snapshot_db_path)
    entry_summary = entry_candidates(
        as_of=as_of,
        kline_client=client,
        snapshot_db_path=snapshot_db_path,
        db_path=db_path,
        horizon_days=horizon_days(),
        calendar=calendar,
    )
    settle_summary = settle_due(
        as_of=as_of, kline_client=client, calendar=calendar, db_path=db_path
    )
    summary = {"as_of": as_of, "entry": entry_summary, "settle": settle_summary}
    logger.info("事件记账结算日批完成: %s", summary)
    return summary
