"""可转债抓取→落库同步层（delta add-convertible-bond-coverage T1/T2）。

四个同步入口（口径见 docs/evals/metrics.md §1.17）：

- ``sync_cb_master``：``bond_zh_cov`` → ``cb_master`` 幂等 upsert，并**在覆盖前**检出
  转股价相对上次落库发生变化的代码集合（下修有界接入的触发面——下修必致转股价变化，
  每日量级 0~10 个；缺值与新增代码不算变化，MUST NOT 猜测）；
- ``sync_cb_quotes``：``bond_zh_hs_cov_spot`` → ``cb_quotes``（``交易日+代码`` 唯一）；
  ``trade_date`` 必须在快照库交易日历内，否则跳过（MUST NOT 拿前一交易日价位冒充当日）；
  日历不可得（快照库空/未建）→ 退化为按 ``trade_date`` 落库（日历缺失不阻断日批）；
- ``sync_cb_events``：``bond_cb_redeem_jsl`` → ``cb_events``（``event_type='强赎'``）；
  **公告日 = 首次观测日**（源无公告日字段）：首次见到该 ``(code, event_type)`` 的
  ``as_of`` 即公告日，跨日重跑复用既有行的 ``announce_date`` → 键稳定、幂等零重复；
- ``sync_cb_adj_events``：``bond_cb_adj_logs_jsl``（**逐代码**，下修历史）→ ``cb_events``
  （``event_type='下修'``）；仅对调用方给定的代码集合查询（变化触发，日调用量有界），
  且只收 ``股东大会日 ≥ as_of − window_days`` 的记录（逐代码接口返回全量历史，
  历史条目一次性重放不得涌入清单）；公告日 = 股东大会日（源真实日期，非首观测）。

失败纪律（明细类既有约定）：抓取失败 → 空列表 + ERROR 留痕（``sources_seen[<源>]``
记 ``none``），**不落任何行、不清空既有数据**；同步层据 ``none`` 标记在返回摘要里报
``source_failed``（日批据此上抛留痕，MUST NOT 以零条新增冒充成功）。逐代码下修查询的
单代码失败记 ``failed_codes`` 且不阻断其余代码（次要通道，如实留痕）。
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Sequence
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from finance_agent.data.akshare_client import AKShareClient
from finance_agent.outcome.events.cb_model import (
    CB_EVENT_TYPE_ADJ,
    CB_EVENT_TYPE_REDEEM,
    cb_event_key,
    find_cb_event,
    list_cb_masters,
    upsert_cb_events,
    upsert_cb_masters,
    upsert_cb_quotes,
)

logger = logging.getLogger("finance_agent.outcome.events.cb_sync")

# 下修事件准入窗口（自然日）：股东大会日 ≥ as_of − 本值才落库
# （逐代码接口返回全量历史；旧条目一次性重放不得涌入观察清单）
CB_ADJ_EVENT_WINDOW_DAYS = 30

# 源标签（sources_seen 键；与抓取层一致）
_SOURCE_MASTER = "cb_master"
_SOURCE_QUOTES = "cb_quotes"
_SOURCE_REDEEM = "cb_redeem"
_SOURCE_ADJ = "cb_adj"


def _source_snapshot(client: Any, tag: str) -> set[str]:
    """取某源当前标签集合（``sources_seen`` 缺位 → 空集，fake 客户端兼容）。"""
    return set(getattr(client, "sources_seen", {}).get(tag, set()))


def _failed_since(client: Any, tag: str, before: set[str]) -> bool:
    """本次调用是否**新增**失败标记（实时差集归因，同事件线抓取口径）。"""
    return "none" in (_source_snapshot(client, tag) - before)


def _last_call_failed(client: Any) -> bool:
    """逐代码抓取的单次调用失败判据（``last_cb_adj_failed``；缺位 → False）。

    ``sources_seen`` 差集在逐代码循环里无法区分「重复失败」与「本次无记录」，
    故逐代码路径以客户端显式留痕的**单次调用**成败为准（先例 ``last_spot_source``）。
    """
    return getattr(client, "last_cb_adj_failed", None) is True


def sync_cb_master(*, db_path: str | Path | None = None, client: Any = None) -> dict[str, Any]:
    """标的同步：``bond_zh_cov`` → ``cb_master``（幂等）；返回摘要。

    ``convert_price_changed`` = 相对上次落库值发生变化的代码（升序；覆盖前比较）。
    """
    client = client or AKShareClient()
    before = _source_snapshot(client, _SOURCE_MASTER)
    rows = client.fetch_cb_master() or []
    sources = sorted(_source_snapshot(client, _SOURCE_MASTER))
    if _failed_since(client, _SOURCE_MASTER, before):
        logger.error("可转债标的同步失败（信源缺失，本次不落行）: sources=%s", sources)
        return {
            "fetched": 0,
            "upserted": 0,
            "convert_price_changed": [],
            "source_failed": True,
            "sources": sources,
        }
    previous = {
        str(row["code"]): row.get("convert_price") for row in list_cb_masters(db_path=db_path)
    }
    changed = sorted(
        str(row["code"])
        for row in rows
        if _price_changed(previous.get(str(row["code"])), row.get("convert_price"))
    )
    upserted = upsert_cb_masters(rows, db_path=db_path) if rows else 0
    logger.info(
        "可转债标的同步完成: fetched=%d upserted=%d convert_price_changed=%s",
        len(rows),
        upserted,
        changed,
    )
    return {
        "fetched": len(rows),
        "upserted": upserted,
        "convert_price_changed": changed,
        "source_failed": False,
        "sources": sources,
    }


def _price_changed(previous: Any, current: Any) -> bool:
    """转股价是否变化（任一侧缺失 → 不算变化：MUST NOT 由缺值猜测下修）。"""
    if previous is None or current is None:
        return False
    try:
        return abs(float(previous) - float(current)) > 1e-9
    except (TypeError, ValueError):
        return False


def sync_cb_quotes(
    *,
    db_path: str | Path | None = None,
    client: Any = None,
    trade_date: str | None = None,
    calendar: Sequence[str] | None = None,
) -> dict[str, Any]:
    """日行情同步：``bond_zh_hs_cov_spot`` → ``cb_quotes``（``交易日+代码`` 唯一）。

    ``calendar`` 给定时做交易日校验：非交易日 → 跳过落库（``skipped_reason=
    non_trading_day``，且不发起抓取）；``calendar=None``（日历不可得）→ 退化为按
    ``trade_date`` 落库并 WARN（缺日历不阻断日批）。
    """
    trade_date = trade_date or date.today().isoformat()
    if calendar is not None and trade_date not in set(calendar):
        logger.warning(
            "可转债行情跳过：%s 不在快照交易日历内（非交易日，不用过期价冒充当日）", trade_date
        )
        return {
            "fetched": 0,
            "upserted": 0,
            "trade_date": trade_date,
            "skipped_reason": "non_trading_day",
            "source_failed": False,
            "sources": [],
        }
    if calendar is None:
        logger.warning("快照交易日历不可得：可转债行情按 %s 落库（缺日历不阻断日批）", trade_date)
    client = client or AKShareClient()
    before = _source_snapshot(client, _SOURCE_QUOTES)
    rows = client.fetch_cb_quotes() or []
    sources = sorted(_source_snapshot(client, _SOURCE_QUOTES))
    if _failed_since(client, _SOURCE_QUOTES, before):
        logger.error("可转债行情同步失败（信源缺失，本次不落行）: sources=%s", sources)
        return {
            "fetched": 0,
            "upserted": 0,
            "trade_date": trade_date,
            "skipped_reason": None,
            "source_failed": True,
            "sources": sources,
        }
    payload = [{**dict(row), "trade_date": trade_date} for row in rows]
    upserted = upsert_cb_quotes(payload, db_path=db_path) if payload else 0
    logger.info(
        "可转债行情同步完成: trade_date=%s fetched=%d upserted=%d", trade_date, len(rows), upserted
    )
    return {
        "fetched": len(rows),
        "upserted": upserted,
        "trade_date": trade_date,
        "skipped_reason": None,
        "source_failed": False,
        "sources": sources,
    }


def sync_cb_events(
    *, db_path: str | Path | None = None, client: Any = None, as_of: str | None = None
) -> dict[str, Any]:
    """强赎事件同步：``bond_cb_redeem_jsl`` → ``cb_events``（``event_type='强赎'``）。

    公告日 = 首次观测日：既有 ``(code, 强赎)`` 行存在 → 复用其 ``announce_date``
    （键稳定、跨日重跑零重复）；否则取本次 ``as_of``。
    """
    as_of = as_of or date.today().isoformat()
    client = client or AKShareClient()
    before = _source_snapshot(client, _SOURCE_REDEEM)
    rows = client.fetch_cb_redeem_events() or []
    sources = sorted(_source_snapshot(client, _SOURCE_REDEEM))
    if _failed_since(client, _SOURCE_REDEEM, before):
        logger.error("强赎事件同步失败（信源缺失，本次不落行）: sources=%s", sources)
        return {"fetched": 0, "upserted": 0, "source_failed": True, "sources": sources}
    payload: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        code = str(item.get("code") or "")
        existing = find_cb_event(code, CB_EVENT_TYPE_REDEEM, db_path=db_path) if code else None
        announce_date = str(
            (existing or {}).get("announce_date") or item.pop("announce_date", None) or as_of
        )
        payload.append(
            {
                **item,
                "event_key": cb_event_key(code, CB_EVENT_TYPE_REDEEM, announce_date),
                "code": code,
                "event_type": CB_EVENT_TYPE_REDEEM,
                "announce_date": announce_date,
            }
        )
    upserted = upsert_cb_events(payload, db_path=db_path) if payload else 0
    logger.info("强赎事件同步完成: as_of=%s fetched=%d upserted=%d", as_of, len(rows), upserted)
    return {
        "fetched": len(rows),
        "upserted": upserted,
        "source_failed": False,
        "sources": sources,
    }


def sync_cb_adj_events(
    *,
    db_path: str | Path | None = None,
    client: Any = None,
    codes: Iterable[str] = (),
    as_of: str | None = None,
    window_days: int = CB_ADJ_EVENT_WINDOW_DAYS,
) -> dict[str, Any]:
    """下修事件同步（逐代码，变化触发有界接入）；返回摘要。

    ``codes`` = 由 ``sync_cb_master`` 的 ``convert_price_changed`` 驱动的候选代码
    （空 → 不发起任何查询）。仅收 ``股东大会日 ≥ as_of − window_days`` 的记录；
    公告日 = 股东大会日（源真实日期）。单代码查询失败 → 记 ``failed_codes``（不阻断）。
    """
    as_of = as_of or date.today().isoformat()
    codes = [str(code) for code in codes]
    if not codes:
        return {"queried": 0, "fetched": 0, "upserted": 0, "failed_codes": []}
    client = client or AKShareClient()
    horizon = _days_before(as_of, window_days)
    payload: list[dict[str, Any]] = []
    failed: list[str] = []
    fetched = 0
    for code in codes:
        try:
            rows = client.fetch_cb_adj_logs(code) or []
        except Exception:  # noqa: BLE001 - 单代码失败不阻断其余代码（如实留痕）
            logger.exception("下修历史抓取异常: code=%s", code)
            failed.append(code)
            continue
        if _last_call_failed(client):
            logger.error("下修历史抓取失败（信源缺失，本次不落行）: code=%s", code)
            failed.append(code)
            continue
        fetched += len(rows)
        for row in rows:
            meeting = str(row.get("meeting_date") or "")
            if not meeting or meeting < horizon or meeting > as_of:
                continue  # 窗口外历史条目 / 未来记录：不入清单
            payload.append(
                {
                    "event_key": cb_event_key(code, CB_EVENT_TYPE_ADJ, meeting),
                    "code": code,
                    "event_type": CB_EVENT_TYPE_ADJ,
                    "announce_date": meeting,
                    "trigger_price": None,
                    "trigger_ratio": None,
                    "count_text": None,
                    "last_trade_date": None,
                    "remain_scale": None,
                    "raw_json": row.get("raw_json"),
                }
            )
    upserted = upsert_cb_events(payload, db_path=db_path) if payload else 0
    if failed:
        logger.error("下修事件同步部分失败: failed_codes=%s", failed)
    logger.info(
        "下修事件同步完成: queried=%d fetched=%d admitted=%d upserted=%d failed=%d",
        len(codes),
        fetched,
        len(payload),
        upserted,
        len(failed),
    )
    return {"queried": len(codes), "fetched": fetched, "upserted": upserted, "failed_codes": failed}


def _days_before(as_of: str, days: int) -> str:
    """``as_of`` 前 ``days`` 个自然日的 ISO 日期（窗口下界；非法 as_of → 上抛）。"""
    return (date.fromisoformat(as_of) - timedelta(days=max(0, int(days)))).isoformat()
