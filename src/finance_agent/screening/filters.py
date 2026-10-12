"""选股漏斗 L1 确定性硬过滤纯函数（add-screening-filters，零 IO）。

计划 docs/superpowers/plans/2026-10-13-add-screening-filters.md 关键设计决策
F1–F4（实施者不得自行改口径）：

- F1 上市交易天数：只用交易日历口径（禁用自然日近似——快照 D3 的 ``listed_days``
  是自然日，仅作参考），``trading_days_listed`` 统计 ``list_date <= d <= as_of``
  的日历天数（含首日 = 1）；``list_date`` 缺失/不可解析/不在日历、``as_of``
  早于 ``list_date`` → None → ``DATA_MISSING``（fail-closed：宁可漏选不可误选）。
- F2 流动性：``avg_amount_20d`` 取按日期升序序列的最后至多 ``history_days``
  个成交额均值（``history_days=None`` → 调用期读 ``constraints.MIN_LIQUIDITY_HISTORY_DAYS``）；
  历史长度不足（快照积累期）或判定窗口含非有限值 → None → ``DATA_MISSING``；
  判定「均值 < 阈值」为 ``ILLIQUID``（恰好等于阈值不剔除——「低于」语义）。
  窗口外的非有限值不影响近 20 日判定（窗口即 F2 口径的数据范围）。
- F3 一字板：只看 ``limit_up_sealed``/``limit_down_sealed``（快照 D3 口径：
  开=高=低=收且涨跌幅达 ±4.8%）；「盘中触板未封死」（快照 sealed 位为 False）
  不剔除。
- F4 阈值单一来源：本模块 ``from finance_agent.screening import constraints``，
  ``filter_universe`` 在**调用期**读 ``constraints.DEFAULT_THRESHOLDS``（模块
  属性，可被 monkeypatch），``thresholds=`` 参数可显式覆盖；默认阈值不得在导入期
  固化到本模块。改阈值只改 ``constraints.py``。

剔除返回**全部**命中原因码（确定性顺序 = ``RejectReason`` 声明顺序），不做
「首个原因短路」。

快照输入约定：状态位列由 ``data/snapshot.py`` D3 写入 0/1（NULL 视为「未标记」
False）；``build_candidate_record`` 负责行 dict → ``CandidateRecord`` 的映射，
过滤函数只吃结构化 ``CandidateRecord`` 序列，便于单测与后续 pipeline 复用。
"""

from __future__ import annotations

import enum
import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

from finance_agent.screening import constraints
from finance_agent.screening.constraints import FilterThresholds


class RejectReason(enum.StrEnum):
    """剔除原因码（结构化契约，值与 spec 逐字一致，供前端展示与风控复用）。"""

    ST_FLAG = "ST_FLAG"
    SUSPENDED = "SUSPENDED"
    IPO_RECENT = "IPO_RECENT"
    LIMIT_LOCKED = "LIMIT_LOCKED"
    ILLIQUID = "ILLIQUID"
    # 数据不可得时的 fail-closed 码（spec「至少包含」五项之外的扩展项）
    DATA_MISSING = "DATA_MISSING"


@dataclass(frozen=True)
class CandidateRecord:
    """过滤层单标的输入（纯数据载体，由快照行 + 派生量构造）。

    ``trading_days_listed``/``avg_amount_20d`` 为 None 表示数据不可得（→
    ``DATA_MISSING``）；状态位为快照 D3 的 0/1 布尔化结果。
    """

    code: str
    name: str = ""
    is_st: bool = False
    is_suspended: bool = False
    limit_up_sealed: bool = False
    limit_down_sealed: bool = False
    trading_days_listed: int | None = None
    avg_amount_20d: float | None = None


@dataclass(frozen=True)
class FilterDecision:
    """单标的过滤结论；``reasons`` 为空 tuple 表示通过。"""

    code: str
    passed: bool
    reasons: tuple[RejectReason, ...]


def _parse_iso(value: object) -> date | None:
    """严格解析 ``YYYY-MM-DD``（规范形态）；缺失/不可解析/非规范 → None。

    与 ``data/snapshot.py::_parse_date_param`` 同纪律：只接受 ``date.fromisoformat``
    可解析且 ``isoformat()`` 回写一致的形态，任何变体（``"2026-9-1"``、
    ``"20260901"``）一律视为不可得，不让非规范形态在天数计数中静默漂移。
    """
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        parsed = date.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.isoformat() == text else None


def trading_days_listed(list_date: str | None, as_of: str, calendar: Sequence[str]) -> int | None:
    """F1：``calendar`` 中 ``list_date <= d <= as_of`` 的天数（含首日 = 1）。

    日历必须覆盖 ``list_date``（真实交易日历必然包含上市日）；未覆盖 →
    None（fail-closed，不猜天数）。``list_date`` 缺失/不可解析、``as_of``
    不可解析、``as_of`` 早于 ``list_date``、日历为空 → None。日历中的不可解析项
    跳过（与快照日历解析同纪律）。
    """
    start = _parse_iso(list_date)
    end = _parse_iso(as_of)
    if start is None or end is None or end < start:
        return None
    count = 0
    list_date_in_calendar = False
    for entry in calendar:
        day = _parse_iso(entry)
        if day is None:
            continue
        if day == start:
            list_date_in_calendar = True
        if start <= day <= end:
            count += 1
    if not list_date_in_calendar:
        return None
    return count


def avg_amount_20d(amounts: Sequence[float], *, history_days: int | None = None) -> float | None:
    """F2：序列（按日期升序）最后至多 ``history_days`` 个 ``amount`` 的均值。

    ``history_days=None`` → 调用期读 ``constraints.MIN_LIQUIDITY_HISTORY_DAYS``；
    显式传入时以传入值为准（``build_candidate_record`` 经此消费 ``FilterThresholds``
    的 ``min_liquidity_history_days``）。长度不足（含空序列）或 ``history_days <= 0``
    （无效配置）或判定窗口含非有限值（NaN/±inf）→ None（→ ``DATA_MISSING``，
    快照积累期约 20 个交易日）。
    """
    history = history_days if history_days is not None else constraints.MIN_LIQUIDITY_HISTORY_DAYS
    if history <= 0 or len(amounts) < history:
        return None
    window = [float(value) for value in amounts[-history:]]
    if not all(math.isfinite(value) for value in window):
        return None
    return sum(window) / len(window)


def filter_universe(
    records: Sequence[CandidateRecord], *, thresholds: FilterThresholds | None = None
) -> list[FilterDecision]:
    """对候选记录逐标的判定通过/剔除，返回与输入同序的结论列表。

    每个剔除结论携带**全部**命中原因码，顺序 = ``RejectReason`` 声明顺序
    （ST → 停牌 → 次新 → 一字板 → 流动性 → 数据缺失），不做首个原因短路。
    派生量 ``trading_days_listed``/``avg_amount_20d`` 为 None **或非有限值**
    （直接构造的记录可携带 NaN/±inf；比较语义下会漏过）→ ``DATA_MISSING``
    （fail-closed，不产出 IPO_RECENT/ILLIQUID）。阈值于调用期取
    ``constraints.DEFAULT_THRESHOLDS``（F4：模块属性可被 monkeypatch）；
    ``thresholds=`` 显式传入时优先。注意本函数只消费已算好的派生量，不读
    ``min_liquidity_history_days``（该字段由 ``build_candidate_record`` 消费）。
    """
    active = thresholds if thresholds is not None else constraints.DEFAULT_THRESHOLDS
    decisions: list[FilterDecision] = []
    for record in records:
        reasons: list[RejectReason] = []
        if record.is_st:
            reasons.append(RejectReason.ST_FLAG)
        if record.is_suspended:
            reasons.append(RejectReason.SUSPENDED)
        missing_data = False
        days = record.trading_days_listed
        if days is None or not math.isfinite(days):
            missing_data = True
        elif days < active.min_trading_days:
            reasons.append(RejectReason.IPO_RECENT)
        if record.limit_up_sealed or record.limit_down_sealed:
            reasons.append(RejectReason.LIMIT_LOCKED)
        avg_amount = record.avg_amount_20d
        if avg_amount is None or not math.isfinite(avg_amount):
            missing_data = True
        elif avg_amount < active.min_avg_amount_20d:
            reasons.append(RejectReason.ILLIQUID)
        if missing_data:
            reasons.append(RejectReason.DATA_MISSING)
        decisions.append(
            FilterDecision(code=record.code, passed=not reasons, reasons=tuple(reasons))
        )
    return decisions


def build_candidate_record(
    *,
    row: dict,
    amounts: Sequence[float],
    calendar: Sequence[str],
    as_of: str,
    thresholds: FilterThresholds | None = None,
) -> CandidateRecord:
    """快照行 dict + 近段成交额 + 交易日历 → 过滤输入 ``CandidateRecord``。

    - 状态位：快照 D3 写入 0/1；NULL/缺失按 False（「未标记」，不参与
      ``DATA_MISSING``——只有 F1/F2 派生量不可得才升级为数据缺失）；
    - ``trading_days_listed``/``avg_amount_20d`` 分别经 F1/F2 纯函数派生
      （None 即数据不可得）；``avg_amount_20d`` 的历史长度取调用期
      ``thresholds.min_liquidity_history_days``（``thresholds=None`` → 调用期
      读 ``constraints.DEFAULT_THRESHOLDS``，F4 同纪律，可被 monkeypatch）；
    - ``code`` 缺失/None → ``""``（快照行 code 恒为六位文本）。
    """
    active = thresholds if thresholds is not None else constraints.DEFAULT_THRESHOLDS
    return CandidateRecord(
        code=str(row.get("code") or "").strip(),
        name=str(row.get("name") or "").strip(),
        is_st=bool(row.get("is_st")),
        is_suspended=bool(row.get("is_suspended")),
        limit_up_sealed=bool(row.get("limit_up_sealed")),
        limit_down_sealed=bool(row.get("limit_down_sealed")),
        trading_days_listed=trading_days_listed(row.get("list_date"), as_of, calendar),
        avg_amount_20d=avg_amount_20d(amounts, history_days=active.min_liquidity_history_days),
    )
