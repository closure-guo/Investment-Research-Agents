"""强赎状态推导**纯函数**（delta add-convertible-bond-coverage T2/T3）。

口径（docs/evals/metrics.md §1.17 + spec「条款事件落库与状态推导」）：确定性规则推导，
**零 LLM、零 IO、同输入同输出**；要素缺失如实标注不可判定，MUST NOT 猜测。状态推导
在读时进行（清单构建/人工核对都走本函数），落库层不做判定。

状态 = f(天计数, 强赎状态, 交易日历, as_of)：
- ``announced``：源 ``强赎状态 == "已公告强赎"``（生命周期序 counting → triggered →
  announced，故已公告优先级最高）；**此状态要求 last_trade_date 可解析**，缺失 →
  ``undetermined``（spec 场景「缺少最后交易日的强赎公告 → 不可判定」）；
- ``triggered``：天计数可解析且 ``days_done >= days_needed``（含边界）且
  ``as_of >= announce_date``；
- ``counting``：天计数可解析但未达标（含 ``as_of < announce_date``）；
- ``undetermined``：announce_date 缺失/不可解析，或非「已公告强赎」而天计数缺失/
  不可解析；``note`` 指明缺失字段。``公告不强赎`` 无专用状态位 → 沿用天计数判定
  （不强赎事实保留于源行 raw_json，不误读为「已公告强赎」）。

倒计时 ``trading_days_to_last`` = 交易日历中 ``as_of < d <= last_trade_date`` 的交易日
个数（``last_trade_date <= as_of`` → 0，含边界不含当日）；日历不可得或未覆盖
``last_trade_date`` → ``None`` + note（MUST NOT 按残缺日历少报，MUST NOT 猜自然日）。
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from datetime import date
from typing import Any

# 天计数文本「已触发天数/所需天数 | 上限天数」（样本 ``"12/15 | 30"``；兼容半/全角竖线、
# 任意空白与前后缀文案——源里同列混有「临近到期 <span>…」等到期提醒）
_COUNT_RE = re.compile(r"(\d+)\s*/\s*(\d+)")

# 源状态取值（集思录 ``强赎状态``；空串 = 未见公告）
_STATUS_ANNOUNCED = "已公告强赎"

# 状态枚举（brief 冻结）
STATE_COUNTING = "counting"
STATE_TRIGGERED = "triggered"
STATE_ANNOUNCED = "announced"
STATE_UNDETERMINED = "undetermined"


def parse_count_text(count_text: Any) -> tuple[int | None, int | None]:
    """天计数文本 → ``(已触发天数, 所需天数)``；不可解析 → ``(None, None)``。"""
    if count_text is None:
        return None, None
    match = _COUNT_RE.search(str(count_text))
    if match is None:
        return None, None
    return int(match.group(1)), int(match.group(2))


def _iso_or_none(value: Any) -> str | None:
    """规范 ISO 日期（``datetime.date`` / ``YYYY-MM-DD`` 串）；其余 → None（如实缺失）。"""
    if value is None:
        return None
    if isinstance(value, date):
        return value.isoformat()
    try:
        return date.fromisoformat(str(value)).isoformat()
    except ValueError:
        return None


def _status_text(event: Mapping[str, Any]) -> str:
    """源状态文本（``redeem_status``；兼容源行中文键 ``强赎状态``）→ 去空白。"""
    raw = event.get("redeem_status")
    if raw is None:
        raw = event.get("强赎状态")
    return "" if raw is None else str(raw).strip()


def _trading_days_between(as_of: str, last_trade_date: str, calendar: Sequence[str]) -> int | None:
    """``as_of`` 之后（不含）至 ``last_trade_date``（含）的交易日个数。

    ``calendar`` 为交易日升序列表；空日历 / 未覆盖 ``last_trade_date`` → ``None``
    （不按残缺日历少报，也不猜自然日）。``last_trade_date <= as_of`` → 0。
    """
    days = [str(day) for day in calendar]
    if not days:
        return None
    if last_trade_date <= as_of:
        return 0
    if last_trade_date > max(days):
        return None
    return sum(1 for day in days if as_of < day <= last_trade_date)


def derive_redeem_status(
    event: Mapping[str, Any], *, as_of: str, calendar: Sequence[str]
) -> dict[str, Any]:
    """强赎事件行 → 确定性状态推导（见模块 docstring 的规则表）。

    返回 ``{"state", "days_done", "days_needed", "trading_days_to_last", "note"}``；
    ``note`` 为 ``None`` 或指明缺失字段/降级原因的说明文本。``as_of`` 非规范 ISO →
    ``ValueError``（调用方缺陷，不静默降级）。
    """
    as_of_iso = _iso_or_none(as_of)
    if as_of_iso is None:
        raise ValueError(f"as_of 非规范 ISO 日期: {as_of!r}")
    as_of = as_of_iso

    announce_date = _iso_or_none(event.get("announce_date"))
    last_trade_date = _iso_or_none(event.get("last_trade_date"))
    days_done, days_needed = parse_count_text(event.get("count_text"))
    announced = _status_text(event) == _STATUS_ANNOUNCED

    countdown: int | None = None
    if last_trade_date is not None:
        countdown = _trading_days_between(as_of, last_trade_date, calendar)

    notes: list[str] = []
    if last_trade_date is not None and countdown is None:
        notes.append("交易日历不可用" if not list(calendar) else "交易日历未覆盖 last_trade_date")

    if announce_date is None:
        notes.insert(0, "缺 announce_date（无法时点判定）")
        state = STATE_UNDETERMINED
    elif announced:
        if last_trade_date is None:
            notes.insert(0, "已公告强赎但缺 last_trade_date（不可判定剩余交易日）")
            state = STATE_UNDETERMINED
            countdown = None
        else:
            state = STATE_ANNOUNCED
    elif days_done is None or days_needed is None:
        notes.insert(0, "count_text 缺失或不可解析（天计数不可判定）")
        if last_trade_date is None:
            notes.insert(1, "缺 last_trade_date")
        state = STATE_UNDETERMINED
    elif as_of >= announce_date and days_done >= days_needed:
        state = STATE_TRIGGERED
    else:
        state = STATE_COUNTING

    return {
        "state": state,
        "days_done": days_done,
        "days_needed": days_needed,
        "trading_days_to_last": countdown,
        "note": "；".join(notes) if notes else None,
    }
