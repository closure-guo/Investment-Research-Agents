"""事件独立记账与结算（delta add-event-driven-lane T6）。

口径权威 ``docs/evals/metrics.md`` §1.15 + design.md「记账口径」：

- **入场**：对 ``verdict='outperform'`` 且未入场者，在**可得时点（``available_at``，
  缺省回落 ``announce_date``）次一交易日开盘价**入场（kline 取数显式
  ``adjust=SETTLEMENT_ADJUST``（hfq 后复权），与执行链同口径）；
- **不可成交顺延**：入场候选日读当日快照行（``read_universe(as_of=entry_date)``）——
  ``is_suspended=1`` 或 ``limit_up_sealed=1`` 顺延至下一交易日并留痕
  ``note=deferred:<原因>@<日期>``。涨停一字板挡买沿用执行链**买入侧**门禁语义
  （``risk_gate``/``matching``：买 + ``limit_up_sealed`` 拒绝；跌停板可买，不挡）；
  停牌真实形态是「该交易日无 kline 行」——选定入场日后按交易日历识别
  ``available_at → 入场日`` 之间缺失的交易日 = 停牌日，同样落
  ``deferred:suspended@<日期>``（多日逗号合并注记）；
  快照行缺失/快照库不可读（历史回填场景）→ 退化为 **kline OHLC 判板**
  （``sealed_flags_from_ohlc``，与快照 D3 同源阈值 ±4.8%；停牌无 kline 行天然顺延），
  并在日志注明口径降级；
- **horizon**：默认 5 个交易日（job 层经 ``EVENTS_HORIZON_DAYS`` 可配置）；
- **结算**：结算日 = 入场日之后第 ``horizon`` 个交易日（交易日历取快照 meta
  ``trade_calendar`` 容错版；不可得时退化为该标的 kline 交易日序列）；结算价 = 该日
  **收盘价**，``raw_return = 结算价 / 入场价 − 1``（round 6——与判定链同款浮点噪声处置）；
  该日无行（停牌）→ 递延至复牌首日收盘（行驱动顺延，同 ``outcome/settle.py`` 纪律）
  并留痕 ``note=deferred:<复牌日>@<原结算日>``；
- **参考价缺失**（kline 取数失败 / 窗口未覆盖）→ 保持 ``open`` + 留痕
  ``note=settle_pending:<原因>``，MUST NOT 以 0 或估算值结算；
- **留痕拼接**：结算阶段的注记与入场顺延注记**拼接不覆盖**（``;`` 分隔，重试不叠写
  同款 pending 段）。
"""

from __future__ import annotations

import logging
import math
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import pandas as pd

from finance_agent.data.akshare_client import SETTLEMENT_ADJUST
from finance_agent.execution.matching import sealed_flags_from_ohlc
from finance_agent.outcome.events.model import (
    forecast_key,
    insert_journal,
    list_open_journal,
    list_outperform_without_journal,
    settle_journal,
    update_journal_note,
)

logger = logging.getLogger("finance_agent.outcome.events.journal")

# 事件线 horizon 默认（独立于观点线 T+20；job 层 EVENTS_HORIZON_DAYS 可配置）
DEFAULT_HORIZON_DAYS = 5

# kline 取数天数护栏（自然日 → 交易日窗口，fetch_kline 内部 ×2 展开）
_KLINE_MIN_DAYS = 30
_KLINE_MAX_DAYS = 1000

# 留痕前缀（日记 note；与 brief 逐字：``deferred:xxx`` / 参考价缺失重试）
_DEFERRED_PREFIX = "deferred:"
_PENDING_PREFIX = "settle_pending:"


def _finite(value: Any) -> float | None:
    """数值强转；None/NaN/inf/不可解析 → None（不编造价格）。"""
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _positive(value: Any) -> float | None:
    """有限正数（价格口径：0/负/NaN 视为缺失，不编造）。"""
    number = _finite(value)
    if number is None or number <= 0:
        return None
    return number


def _code(symbol: str) -> str:
    """``'600519.SH'`` → ``'600519'``（快照/kline 取数用 6 位裸码）。"""
    return str(symbol).split(".")[0]


def _symbol_for(code: str) -> str:
    """6 位裸码 → 库内 symbol 后缀（交易所映射与 ``AKShareClient._to_sina_symbol`` 同源）。

    92/4/8 号段（北交所）→ ``.BJ``，6/9 → ``.SH``，其余 → ``.SZ``（与 predictions
    的 6→SH 简化口径相比更准；北交所预告不再被误标 ``.SZ``）。
    """
    if code.startswith("92") or code.startswith(("4", "8")):
        return f"{code}.BJ"
    if code.startswith(("6", "9")):
        return f"{code}.SH"
    return f"{code}.SZ"


def _norm_kline(frame: pd.DataFrame | None) -> pd.DataFrame | None:
    """kline 日期列归一 ``YYYY-MM-DD`` 字符串并升序排列；空/None → None。"""
    if frame is None or frame.empty:
        return None
    df = frame.copy()
    df["日期"] = df["日期"].astype(str).str[:10]
    return df.sort_values("日期").reset_index(drop=True)


def _kline_days(span_days: int) -> int:
    """按需要覆盖的自然日跨度估算取数交易日数（含 horizon 余量，护栏内钳制）。"""
    return max(_KLINE_MIN_DAYS, min(int(span_days) // 2 + span_days + 10, _KLINE_MAX_DAYS))


def _fetch_kline(client: Any, code: str, span_days: int) -> pd.DataFrame | None:
    """取 kline（结算口径 hfq）；异常/空 → None（调用方按缺失留痕，不编造）。"""
    try:
        frame = client.fetch_kline(code, days=_kline_days(span_days), adjust=SETTLEMENT_ADJUST)
    except Exception:  # noqa: BLE001 -- 取数失败按缺失处置（保持 open + 留痕）
        logger.warning("kline 取数失败，本标的按缺失处置: %s", code, exc_info=True)
        return None
    return _norm_kline(frame)


def _snapshot_row(entry_date: str, code: str, snapshot_db_path: str | Path | None) -> dict | None:
    """读当日快照行（不可成交判定）；不可读/无该代码行 → ``None``（退化为 OHLC 判板）。"""
    from finance_agent.data.snapshot import SnapshotError, read_universe

    try:
        rows = read_universe(entry_date, db_path=snapshot_db_path)
    except SnapshotError:
        return None
    except Exception:  # noqa: BLE001 -- 快照不可读不阻断记账（退化为 OHLC 判板）
        logger.warning("快照读取失败（%s），退化为 kline OHLC 判板", entry_date, exc_info=True)
        return None
    for row in rows:
        if str(row.get("code") or "") == code:
            return row
    return None


def _tradable_on(
    *,
    day: str,
    code: str,
    kline_row: pd.Series,
    prev_close: float | None,
    snapshot_db_path: str | Path | None,
) -> tuple[bool, str | None]:
    """入场候选日可成交性：``(可成交, 不可成交原因)``。

    快照行可得 → ``is_suspended`` / ``limit_up_sealed``（买入侧门禁语义：跌停板可买不挡）；
    快照行缺失 → kline OHLC 判板（开=高=低=收 且涨幅达阈值 → 顺延），口径降级记日志。
    """
    snap = _snapshot_row(day, code, snapshot_db_path)
    if snap is not None:
        if snap.get("is_suspended"):
            return False, "suspended"
        if snap.get("limit_up_sealed"):
            return False, "limit_up_sealed"
        return True, None
    # 快照行缺失：退化为 kline OHLC 判板（历史回填/快照窗口未覆盖该日）
    open_ = _positive(kline_row.get("开盘"))
    close = _positive(kline_row.get("收盘"))
    high = _positive(kline_row.get("最高"))
    low = _positive(kline_row.get("最低"))
    if open_ is None or close is None or high is None or low is None:
        logger.warning("kline OHLC 不完整（%s/%s），按不可成交顺延（fail-closed）", code, day)
        return False, "ohlc_missing"
    up_sealed, _down_sealed = sealed_flags_from_ohlc(open_, high, low, close, prev_close)
    if up_sealed:
        logger.info("快照行缺失，按 kline OHLC 判得一字涨停板（%s/%s）→ 顺延", code, day)
        return False, "limit_up_sealed_ohlc"
    return True, None


def _suspension_gap_days(
    calendar: Sequence[str] | None,
    symbol_days: set[str],
    *,
    anchor: str,
    entry_day: str,
) -> list[str]:
    """``anchor → entry_day`` 之间（不含两端）市场交易日中无该标的 kline 行者 = 停牌日。

    停牌真实形态是「该交易日无 kline 行」，行驱动遍历天然看不见；选定入场日后用
    市场交易日历识别缺口补留痕。``calendar`` 不可得（None）→ 空（无法识别，不猜）。
    """
    if not calendar:
        return []
    gaps = {
        day
        for day in (str(item)[:10] for item in calendar)
        if anchor < day < entry_day and day not in symbol_days
    }
    return sorted(gaps)


def _format_deferrals(deferrals: Sequence[tuple[str, str]]) -> str | None:
    """顺延留痕 → ``note=deferred:<原因>@<日期>[,...]``（按日期升序，多日合并）。"""
    if not deferrals:
        return None
    ordered = sorted(deferrals, key=lambda item: item[0])
    return f"{_DEFERRED_PREFIX}{','.join(f'{reason}@{day}' for day, reason in ordered)}"


def _merge_note(existing: Any, new: str) -> str:
    """note 拼接（不覆盖）：既有注记保留，同款段跳过（重试不叠写同款 pending）。"""
    parts = [part for part in str(existing or "").split(";") if part]
    if new not in parts:
        parts.append(new)
    return ";".join(parts)


def entry_candidates(
    *,
    as_of: str,
    kline_client: Any,
    snapshot_db_path: str | Path | None = None,
    db_path: str | Path | None = None,
    horizon_days: int = DEFAULT_HORIZON_DAYS,
    calendar: Sequence[str] | None = None,
) -> dict[str, Any]:
    """对 ``outperform`` 且未入场者在其可得时点次一交易日开盘入场（幂等）。

    返回 ``{"entered", "deferred", "not_due", "errors"}``：``deferred`` 计入发生过
    顺延的入场；``not_due`` 为次一交易日尚未到（或不晚于 as_of 无可成交日）；单标的
    异常跳过并计入 ``errors``（不阻断整批）。``calendar``（市场交易日历，升序）用于
    识别停牌缺口日（该交易日无 kline 行 → 留痕 ``deferred:suspended@<日期>``）。
    """
    summary = {
        "entered": 0,
        "deferred": 0,
        "not_due": 0,
        "errors": 0,
        "window_truncated": 0,
    }
    for forecast in list_outperform_without_journal(db_path=db_path):
        try:
            _enter_one(
                forecast,
                as_of=as_of,
                kline_client=kline_client,
                snapshot_db_path=snapshot_db_path,
                db_path=db_path,
                horizon_days=horizon_days,
                calendar=calendar,
                summary=summary,
            )
        except Exception:  # noqa: BLE001 -- 单标的异常跳过，不阻断整批
            summary["errors"] += 1
            logger.warning("事件入场失败（跳过该标的）: %s", forecast.get("code"), exc_info=True)
    if summary["entered"] or summary["deferred"]:
        logger.info("事件记账入场：%s", summary)
    return summary


def _enter_one(
    forecast: Mapping[str, Any],
    *,
    as_of: str,
    kline_client: Any,
    snapshot_db_path: str | Path | None,
    db_path: str | Path | None,
    horizon_days: int,
    calendar: Sequence[str] | None,
    summary: dict[str, int],
) -> None:
    """单条入场：定位可得时点后的首个可成交交易日 → 开盘价入场（顺延留痕）。"""
    anchor = str(forecast.get("available_at") or forecast.get("announce_date") or "")
    code = str(forecast.get("code") or "")
    if not anchor or not code:
        summary["not_due"] += 1
        return
    span = max((pd.Timestamp(as_of) - pd.Timestamp(anchor)).days, 30)
    requested_days = _kline_days(span + 30)
    frame = _fetch_kline(kline_client, code, span_days=span + 30)
    if frame is None:
        summary["errors"] += 1
        logger.warning("入场取数失败，保持未入场（下次重试）: %s", code)
        return
    # N5：窗口被 _KLINE_MAX_DAYS 截断且未覆盖 anchor → 无法区分「窗户外停牌」与
    # 「窗口未覆盖」，按未就绪处理；MUST NOT 把窗口首行当次一交易日入场，也
    # MUST NOT 把窗口外交易日误判为停牌。
    frame_start = str(frame["日期"].iloc[0]) if not frame.empty else ""
    if requested_days >= _KLINE_MAX_DAYS and frame_start > anchor:
        summary["window_truncated"] += 1
        logger.warning(
            "kline 窗口未覆盖可得时点（%s anchor=%s frame_start=%s days=%s），"
            "按未就绪处理，不判停牌",
            code,
            anchor,
            frame_start,
            requested_days,
        )
        return
    later = frame[frame["日期"] > anchor]
    if later.empty:
        summary["not_due"] += 1  # 次一交易日尚未到（或 K 线窗口未覆盖可得时点）
        return
    closes = {str(row["日期"]): _positive(row.get("收盘")) for _, row in frame.iterrows()}
    ordered_days = [str(day) for day in frame["日期"]]
    symbol_days = set(ordered_days)
    deferrals: list[tuple[str, str]] = []  # (日期, 原因)
    for _, row in later.iterrows():
        day = str(row["日期"])
        if day > as_of:
            break  # 不晚于 as_of 的可成交日：等未来运行再入场
        open_price = _positive(row.get("开盘"))
        if open_price is None:
            summary["errors"] += 1
            logger.warning("kline 开盘价缺失（%s/%s），保持未入场", code, day)
            return
        # 昨收：kline 无「昨收」列时取上一交易日收盘（OHLC 判板退化的前收口径）
        index = ordered_days.index(day)
        prev_close = closes.get(ordered_days[index - 1]) if index > 0 else None
        tradable, reason = _tradable_on(
            day=day,
            code=code,
            kline_row=row,
            prev_close=prev_close,
            snapshot_db_path=snapshot_db_path,
        )
        if not tradable:
            deferrals.append((day, reason or "ineligible"))
            continue
        # 停牌缺口日（该交易日无 kline 行）并入留痕：锚点 → 入场日之间
        for gap_day in _suspension_gap_days(calendar, symbol_days, anchor=anchor, entry_day=day):
            deferrals.append((gap_day, "suspended"))
        insert_journal(
            forecast_key=forecast_key(forecast),
            symbol=_symbol_for(code),
            entry_date=day,
            entry_price=open_price,
            horizon_days=horizon_days,
            note=_format_deferrals(deferrals),
            db_path=db_path,
        )
        summary["entered"] += 1
        if deferrals:
            summary["deferred"] += 1
        return
    summary["not_due"] += 1
    if deferrals:
        logger.info("入场候选 %s 全部顺延（未到可成交日）: %s", code, deferrals)


def settle_due(
    *,
    as_of: str,
    kline_client: Any,
    calendar: Sequence[str] | None,
    db_path: str | Path | None = None,
) -> dict[str, Any]:
    """结算到期的 open 记账（到期 = 入场日之后第 horizon 个交易日）。

    ``calendar``：市场交易日历（升序 ``YYYY-MM-DD``；快照 meta 容错版，可为 None →
    退化为该标的 kline 交易日序列）。返回 ``{"settled", "deferred", "pending",
    "not_due", "errors"}``；参考价缺失保持 open + 留痕（MUST NOT 折算 0）。
    """
    summary = {"settled": 0, "deferred": 0, "pending": 0, "not_due": 0, "errors": 0}
    market_days = [str(day)[:10] for day in (calendar or [])]
    for journal in list_open_journal(db_path=db_path):
        try:
            _settle_one(
                journal,
                as_of=as_of,
                kline_client=kline_client,
                market_days=market_days,
                db_path=db_path,
                summary=summary,
            )
        except Exception:  # noqa: BLE001 -- 单条异常跳过（保持 open），不阻断整批
            summary["errors"] += 1
            logger.warning("事件结算失败（保持 open）: id=%s", journal.get("id"), exc_info=True)
    if summary["settled"] or summary["pending"] or summary["deferred"]:
        logger.info("事件结算：%s", summary)
    return summary


def _resolve_settle_date(
    entry_date: str, horizon_days: int, symbol_days: list[str], market_days: list[str]
) -> str | None:
    """结算日 = 入场日之后第 horizon 个交易日（市场日历优先，退化用标的 kline 日序列）。"""
    for days in (market_days, symbol_days):
        if entry_date not in days:
            continue
        index = days.index(entry_date) + horizon_days
        if index < len(days):
            return days[index]
    return None


def _settle_one(
    journal: dict[str, Any],
    *,
    as_of: str,
    kline_client: Any,
    market_days: list[str],
    db_path: str | Path | None,
    summary: dict[str, int],
) -> None:
    """单条结算：解析结算日 → 取收盘价 → 落终态；缺失一律留痕保持 open。"""
    row_id = int(journal["id"])
    symbol = str(journal.get("symbol") or "")
    entry_date = str(journal.get("entry_date") or "")
    entry_price = _positive(journal.get("entry_price"))
    horizon = int(journal.get("horizon_days") or DEFAULT_HORIZON_DAYS)
    existing_note = journal.get("note")  # 入场顺延注记：结算留痕拼接不覆盖
    if not symbol or not entry_date or entry_price is None:
        summary["pending"] += 1
        update_journal_note(
            row_id,
            note=_merge_note(existing_note, f"{_PENDING_PREFIX}invalid_row"),
            db_path=db_path,
        )
        return
    span = max((pd.Timestamp(as_of) - pd.Timestamp(entry_date)).days, 30)
    frame = _fetch_kline(kline_client, _code(symbol), span_days=span + horizon * 2)
    if frame is None:
        summary["errors"] += 1
        update_journal_note(
            row_id, note=_merge_note(existing_note, f"{_PENDING_PREFIX}no_kline"), db_path=db_path
        )
        logger.warning("结算取数失败，保持 open（下次重试）: %s", symbol)
        return
    symbol_days = [str(day) for day in frame["日期"]]
    settle_date = _resolve_settle_date(entry_date, horizon, symbol_days, market_days)
    if settle_date is None:
        summary["not_due"] += 1
        return
    if settle_date > as_of:
        summary["not_due"] += 1
        return
    # 结算日收盘价：该日无行（停牌）→ 递延至复牌首日收盘（行驱动顺延）
    at_or_after = frame[frame["日期"] >= settle_date]
    if at_or_after.empty:
        summary["pending"] += 1
        update_journal_note(
            row_id,
            note=_merge_note(existing_note, f"{_PENDING_PREFIX}no_price@{settle_date}"),
            db_path=db_path,
        )
        logger.warning("结算日无行情（%s/%s），保持 open + 留痕重试", symbol, settle_date)
        return
    exit_row = at_or_after.iloc[0]
    exit_date = str(exit_row["日期"])
    exit_price = _positive(exit_row.get("收盘"))
    if exit_price is None:
        summary["pending"] += 1
        update_journal_note(
            row_id,
            note=_merge_note(existing_note, f"{_PENDING_PREFIX}no_price@{settle_date}"),
            db_path=db_path,
        )
        return
    note = existing_note
    if exit_date != settle_date:
        note = _merge_note(existing_note, f"{_DEFERRED_PREFIX}{exit_date}@{settle_date}")
        summary["deferred"] += 1
    settle_journal(
        row_id,
        settle_date=exit_date,
        settle_price=exit_price,
        raw_return=round(exit_price / entry_price - 1.0, 6),
        note=note,
        db_path=db_path,
    )
    summary["settled"] += 1
