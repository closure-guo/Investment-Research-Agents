"""事件观察清单构建（delta add-event-driven-lane T5；add-convertible-bond-coverage 扩展）。

口径（docs/evals/metrics.md §1.15 / §1.17 + spec「事件观察清单与深度分析接线」）：

- **业绩预告分支**（``source="earnings"``）：``verdict=outperform`` 且清单无条目的分诊结果
  进入观察清单；``in_line``/``risk`` 一律不入。去重由 ``model.insert_watchlist`` 的
  ``forecast_key`` 唯一约束兜底——**重跑零重复**（已存在的条目不重置状态/session）。
- **条款事件分支**（``source="cbb"``，add-convertible-bond-coverage）：``cb_events`` 全部
  条目进清单，标注**规则化**（状态 + 天计数 + 距最后交易日；**零 LLM 调用**，MUST NOT
  依赖分诊）。状态经纯函数 ``cb_status.derive_redeem_status``（强赎）或源行要素文本
  （下修）推导，结果落 ``event_watchlist.note``（JSON：``state`` / ``call_countdown_days``
  / ``reason``）；四要素其余三键（现价/溢价率/剩余规模）由 ``model.list_watchlist``
  读取时联表取数（``cb_quotes`` 最新交易日 / ``cb_master`` / 事件行）。
  每日重跑**刷新**既有 cbb 条目的注记（倒计时随交易日递减），但**不动**
  ``status``/``session_id``/``created_at``。
"""

from __future__ import annotations

import json
import logging
from datetime import date
from pathlib import Path
from typing import Any

from finance_agent.outcome.events.cb_model import (
    CB_EVENT_TYPE_ADJ,
    list_cb_events,
)
from finance_agent.outcome.events.cb_status import (
    STATE_ANNOUNCED,
    STATE_COUNTING,
    STATE_TRIGGERED,
    STATE_UNDETERMINED,
    derive_redeem_status,
)
from finance_agent.outcome.events.model import (
    _connect,
    init_events_db,
    insert_watchlist,
    update_watchlist_note,
)

logger = logging.getLogger("finance_agent.outcome.events.watchlist")

# 清单来源标签（观察清单条目与事件记账同源标识）
WATCHLIST_SOURCE = "earnings"
WATCHLIST_SOURCE_CBB = "cbb"

# 状态 → 规则化理由文案（清单条目 ``reason`` 前缀；零 LLM）
_STATE_LABELS = {
    STATE_COUNTING: "强赎天计数进行中",
    STATE_TRIGGERED: "已满足强赎条件",
    STATE_ANNOUNCED: "已公告强赎",
    STATE_UNDETERMINED: "强赎状态不可判定",
}


def build_watchlist(
    *,
    db_path: str | Path | None = None,
    as_of: str | None = None,
    calendar: list[str] | None = None,
) -> int:
    """把未入清单的 ``outperform`` 分诊结论 + 全部条款事件写入观察清单，返回新增条数。

    ``as_of``/``calendar`` 仅供条款事件分支的状态推导（交易日历推算倒计时）；缺省
    ``as_of`` = 今日、``calendar`` = 空（状态照判、倒计时如实 null + note）。
    """
    added = _build_earnings_watchlist(db_path=db_path)
    added += _build_cbb_watchlist(db_path=db_path, as_of=as_of, calendar=calendar)
    return added


def _build_earnings_watchlist(*, db_path: str | Path | None = None) -> int:
    """业绩预告分支：``verdict=outperform`` 且未入清单者（既有口径，逐字不变）。"""
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


def _build_cbb_watchlist(
    *,
    db_path: str | Path | None = None,
    as_of: str | None = None,
    calendar: list[str] | None = None,
) -> int:
    """条款事件分支：全部事件入清单（规则化标注，零 LLM），注记每日刷新。"""
    rows = list_cb_events(db_path=db_path)
    if not rows:
        return 0
    anchor = as_of or date.today().isoformat()
    days = [str(day) for day in calendar or []]
    added = 0
    for row in rows:
        key = str(row["event_key"])
        note = json.dumps(cbb_annotation(row, as_of=anchor, calendar=days), ensure_ascii=False)
        if (
            insert_watchlist(key, source=WATCHLIST_SOURCE_CBB, note=note, db_path=db_path)
            is not None
        ):
            added += 1
        else:
            update_watchlist_note(key, note=note, db_path=db_path)
    logger.info("事件观察清单新增 %d 条（条款事件 cbb，规则化标注，零 LLM）", added)
    return added


def cbb_annotation(event: dict[str, Any], *, as_of: str, calendar: list[str]) -> dict[str, Any]:
    """条款事件行 → 规则化注记载荷（``state`` / ``call_countdown_days`` / ``reason``）。

    - 强赎事件：状态经 ``cb_status.derive_redeem_status`` 推导（``强赎状态`` 取自源行留痕
      ``raw_json``；留痕损坏 → 不可判定并写明原因，MUST NOT 猜测）；倒计时 = 交易日个数；
    - 下修事件：无强赎倒计时（``state``/``call_countdown_days`` 为 null），理由取源行要素
      （下修前/后转股价、生效日）。
    """
    if str(event.get("event_type") or "") == CB_EVENT_TYPE_ADJ:
        return {"state": None, "call_countdown_days": None, "reason": _adj_reason(event)}
    status_text, readable = _redeem_status_from_raw(event.get("raw_json"))
    if not readable:
        return {
            "state": STATE_UNDETERMINED,
            "call_countdown_days": None,
            "reason": "强赎状态不可判定：源行留痕损坏，强赎状态不可读（MUST NOT 猜测）",
        }
    status = derive_redeem_status(
        {**event, "redeem_status": status_text}, as_of=as_of, calendar=calendar
    )
    return {
        "state": status["state"],
        "call_countdown_days": status["trading_days_to_last"],
        "reason": _redeem_reason(status),
    }


def _redeem_status_from_raw(raw_json: Any) -> tuple[str | None, bool]:
    """源行留痕 → ``(强赎状态文本, 是否可读)``；留痕缺位/损坏 → ``(None, False)``。"""
    if raw_json is None:
        return None, False
    try:
        parsed = json.loads(str(raw_json))
    except ValueError:
        logger.warning("条款事件源行留痕损坏（状态不可读，按不可判定处理）: %.80s", raw_json)
        return None, False
    if not isinstance(parsed, dict):
        return None, False
    raw_status = parsed.get("强赎状态")
    return ("" if raw_status is None else str(raw_status).strip()), True


def _redeem_reason(status: dict[str, Any]) -> str:
    """推导结果 → 规则化理由文本（状态 + 天计数 + 距最后交易日 + 不可判定注记）。"""
    parts = [_STATE_LABELS.get(str(status["state"]), str(status["state"]))]
    if status["days_done"] is not None and status["days_needed"] is not None:
        parts.append(f"天计数 {status['days_done']}/{status['days_needed']}")
    if status["trading_days_to_last"] is not None:
        parts.append(f"距最后交易日 {status['trading_days_to_last']} 个交易日")
    if status["note"]:
        parts.append(str(status["note"]))
    return "；".join(parts)


def _adj_reason(event: dict[str, Any]) -> str:
    """下修事件 → 规则化理由文本（要素取源行留痕；缺要素如实从简，不编造）。"""
    parts = ["下修（转股价调整）"]
    raw = _parse_raw(event.get("raw_json"))
    before, after = raw.get("下修前转股价"), raw.get("下修后转股价")
    if before is not None and after is not None:
        parts.append(f"转股价 {before} → {after}")
    effective = raw.get("新转股价生效日期")
    if effective:
        parts.append(f"生效 {effective}")
    return "；".join(parts)


def _parse_raw(raw_json: Any) -> dict[str, Any]:
    """源行留痕 → dict（缺位/损坏 → 空 dict，理由文本从简）。"""
    if raw_json is None:
        return {}
    try:
        parsed = json.loads(str(raw_json))
    except ValueError:
        return {}
    return parsed if isinstance(parsed, dict) else {}
