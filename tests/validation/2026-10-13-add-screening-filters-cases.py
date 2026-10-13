"""add-screening-filters 人工核对用例表（50 例，替代 live 快照核对）。

背景：spec 任务 3.2 要求「2026-09 全月过滤 + 抽 50 例」，但本机东财 IP 封禁
（issue #325）拿不到真实快照数据，无法执行 live 全月过滤 → 以本表的**手工构造
50 例**（六码 × 正反 × 边界：59/60/61 交易日、均额恰等于阈值、一字封死 vs
触板未封死、DATA_MISSING 变体、多因同单枚举顺序）替代；期望原因码为逐例**手算
硬编码**（不从被测函数推导），运行后逐行比对。

触板未封死口径：快照 D3 只在「开=高=低=收 且幅度达 ±4.8%」时置 sealed 位；
「high 触板但 low < high」在快照里 sealed 位为 False → 本表以 sealed=False 表达。

运行：uv run python tests/validation/2026-10-13-add-screening-filters-cases.py
（全对 exit 0；任一不符打印明细并 exit 1）
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from finance_agent.screening.filters import (
    CandidateRecord,
    RejectReason,
    build_candidate_record,
    filter_universe,
)

# 默认阈值口径（与 constraints.py 逐字一致；表内期望值据此手算）
THRESHOLD = 50_000_000.0


@dataclass(frozen=True)
class _Case:
    """单个核对用例：构造输入 + 手算期望（原因码 tuple，空 = 通过）。"""

    label: str
    record: CandidateRecord
    expected: tuple[RejectReason, ...]


def _calendar(n: int, start: str = "2026-09-01") -> list[str]:
    """n 个连续「交易日」（跳过周末）的 ISO 日期列表（起点 2026-09-01 周二）。"""
    days: list[str] = []
    day = date.fromisoformat(start)
    while len(days) < n:
        if day.weekday() < 5:
            days.append(day.isoformat())
        day += timedelta(days=1)
    return days


def _rec(**overrides: Any) -> CandidateRecord:
    """「全通过」基线记录（60 交易日 / 恰等于阈值），按需覆盖字段。"""
    base: dict[str, Any] = {
        "code": "600000",
        "name": "用例标的",
        "is_st": False,
        "is_suspended": False,
        "limit_up_sealed": False,
        "limit_down_sealed": False,
        "trading_days_listed": 60,
        "avg_amount_20d": THRESHOLD,
    }
    base.update(overrides)
    return CandidateRecord(**base)


_CAL_59 = _calendar(59)
_CAL_60 = _calendar(60)
_CAL_61 = _calendar(61)
_AMOUNTS_EXACT = [THRESHOLD] * 20
_AMOUNTS_SHORT = [THRESHOLD] * 19

# 50 例期望值全部手算：枚举声明顺序 ST_FLAG → SUSPENDED → IPO_RECENT →
# LIMIT_LOCKED → ILLIQUID → DATA_MISSING；「低于阈值」才 ILLIQUID；恰好等于通过。
CASES: tuple[_Case, ...] = (
    # A. 通过基线（正）、触板未封死
    _Case("A01 全字段干净：60日/恰等于阈值", _rec(), ()),
    _Case(
        "A02 老股高流动性：250日/1亿",
        _rec(trading_days_listed=250, avg_amount_20d=100_000_000.0),
        (),
    ),
    _Case(
        "A03 60日边界通过 + 阈值+0.01",
        _rec(trading_days_listed=60, avg_amount_20d=THRESHOLD + 0.01),
        (),
    ),
    _Case(
        "A04 涨停触板未封死（sealed=False）+ 60日/恰阈值",
        _rec(limit_up_sealed=False, limit_down_sealed=False),
        (),
    ),
    _Case(
        "A05 跌停触板未封死 + 61日/阈值+0.01",
        _rec(
            trading_days_listed=61,
            avg_amount_20d=THRESHOLD + 0.01,
            limit_down_sealed=False,
        ),
        (),
    ),
    # B. ST_FLAG 正反
    _Case("B06 ST 正：is_st=True", _rec(is_st=True), (RejectReason.ST_FLAG,)),
    _Case("B07 ST 反：is_st=False 显式", _rec(is_st=False), ()),
    _Case(
        "B08 ST + 停牌（枚举声明顺序）",
        _rec(is_st=True, is_suspended=True),
        (RejectReason.ST_FLAG, RejectReason.SUSPENDED),
    ),
    _Case(
        "B09 ST + 上市59日",
        _rec(is_st=True, trading_days_listed=59),
        (RejectReason.ST_FLAG, RejectReason.IPO_RECENT),
    ),
    # C. SUSPENDED 正反
    _Case(
        "C10 停牌正：is_suspended=True",
        _rec(is_suspended=True),
        (RejectReason.SUSPENDED,),
    ),
    _Case("C11 停牌反：False 显式", _rec(is_suspended=False), ()),
    _Case(
        "C12 停牌 + 上市59日",
        _rec(is_suspended=True, trading_days_listed=59),
        (RejectReason.SUSPENDED, RejectReason.IPO_RECENT),
    ),
    # D. IPO_RECENT 59/60/61 边界
    _Case(
        "D13 上市59日边界剔除",
        _rec(trading_days_listed=59),
        (RejectReason.IPO_RECENT,),
    ),
    _Case("D14 上市60日边界通过（满 60 保留）", _rec(trading_days_listed=60), ()),
    _Case("D15 上市61日通过", _rec(trading_days_listed=61), ()),
    _Case(
        "D16 59日 + 均额低于阈值",
        _rec(trading_days_listed=59, avg_amount_20d=THRESHOLD - 0.01),
        (RejectReason.IPO_RECENT, RejectReason.ILLIQUID),
    ),
    _Case(
        "D17 59日 + 停牌 + 一字涨停 + 均额低于",
        _rec(
            is_suspended=True,
            trading_days_listed=59,
            limit_up_sealed=True,
            avg_amount_20d=THRESHOLD - 0.01,
        ),
        (
            RejectReason.SUSPENDED,
            RejectReason.IPO_RECENT,
            RejectReason.LIMIT_LOCKED,
            RejectReason.ILLIQUID,
        ),
    ),
    _Case(
        "D18 60日边界 + 一字跌停",
        _rec(limit_down_sealed=True),
        (RejectReason.LIMIT_LOCKED,),
    ),
    _Case("D19 61日 + ST", _rec(is_st=True, trading_days_listed=61), (RejectReason.ST_FLAG,)),
    # E. LIMIT_LOCKED 封死 vs 触板
    _Case(
        "E20 一字涨停封死",
        _rec(limit_up_sealed=True),
        (RejectReason.LIMIT_LOCKED,),
    ),
    _Case(
        "E21 一字跌停封死",
        _rec(limit_down_sealed=True),
        (RejectReason.LIMIT_LOCKED,),
    ),
    _Case(
        "E22 双封位同真 → 只报一次",
        _rec(limit_up_sealed=True, limit_down_sealed=True),
        (RejectReason.LIMIT_LOCKED,),
    ),
    _Case(
        "E23 触板未封死：sealed 位均 False",
        _rec(limit_up_sealed=False, limit_down_sealed=False),
        (),
    ),
    _Case(
        "E24 一字涨停 + 59日",
        _rec(limit_up_sealed=True, trading_days_listed=59),
        (RejectReason.IPO_RECENT, RejectReason.LIMIT_LOCKED),
    ),
    # F. ILLIQUID 阈值边界
    _Case("F25 均额恰等于阈值（不剔除）", _rec(avg_amount_20d=THRESHOLD), ()),
    _Case(
        "F26 均额 = 阈值 − 0.01",
        _rec(avg_amount_20d=THRESHOLD - 0.01),
        (RejectReason.ILLIQUID,),
    ),
    _Case("F27 均额 = 阈值 + 0.01", _rec(avg_amount_20d=THRESHOLD + 0.01), ()),
    _Case("F28 均额 = 0", _rec(avg_amount_20d=0.0), (RejectReason.ILLIQUID,)),
    _Case(
        "F29 均额低于 + 停牌",
        _rec(is_suspended=True, avg_amount_20d=THRESHOLD - 0.01),
        (RejectReason.SUSPENDED, RejectReason.ILLIQUID),
    ),
    _Case("F30 均额 = 1e15（巨额）", _rec(avg_amount_20d=1e15), ()),
    _Case(
        "F31 均额 = 阈值的一半",
        _rec(avg_amount_20d=THRESHOLD / 2),
        (RejectReason.ILLIQUID,),
    ),
    # G. DATA_MISSING 变体
    _Case(
        "G32 上市天数缺失（None）",
        _rec(trading_days_listed=None),
        (RejectReason.DATA_MISSING,),
    ),
    _Case(
        "G33 均额缺失（None）",
        _rec(avg_amount_20d=None),
        (RejectReason.DATA_MISSING,),
    ),
    _Case(
        "G34 两项派生量都缺失 → 只报一次",
        _rec(trading_days_listed=None, avg_amount_20d=None),
        (RejectReason.DATA_MISSING,),
    ),
    _Case(
        "G35 天数缺失 + ST",
        _rec(is_st=True, trading_days_listed=None),
        (RejectReason.ST_FLAG, RejectReason.DATA_MISSING),
    ),
    _Case(
        "G36 均额缺失 + 停牌",
        _rec(is_suspended=True, avg_amount_20d=None),
        (RejectReason.SUSPENDED, RejectReason.DATA_MISSING),
    ),
    _Case(
        "G37 天数缺失 + 一字涨停",
        _rec(limit_up_sealed=True, trading_days_listed=None),
        (RejectReason.LIMIT_LOCKED, RejectReason.DATA_MISSING),
    ),
    _Case(
        "G38 五码全命中（数据齐全，无 DATA_MISSING）",
        _rec(
            is_st=True,
            is_suspended=True,
            trading_days_listed=59,
            limit_up_sealed=True,
            avg_amount_20d=THRESHOLD - 0.01,
        ),
        (
            RejectReason.ST_FLAG,
            RejectReason.SUSPENDED,
            RejectReason.IPO_RECENT,
            RejectReason.LIMIT_LOCKED,
            RejectReason.ILLIQUID,
        ),
    ),
    _Case(
        "G39 ST+停牌+天数缺失+双封+均额缺失",
        _rec(
            is_st=True,
            is_suspended=True,
            trading_days_listed=None,
            limit_up_sealed=True,
            limit_down_sealed=True,
            avg_amount_20d=None,
        ),
        (
            RejectReason.ST_FLAG,
            RejectReason.SUSPENDED,
            RejectReason.LIMIT_LOCKED,
            RejectReason.DATA_MISSING,
        ),
    ),
    _Case(
        "G40 天数缺失 + 均额低于阈值",
        _rec(trading_days_listed=None, avg_amount_20d=THRESHOLD - 0.01),
        (RejectReason.ILLIQUID, RejectReason.DATA_MISSING),
    ),
    _Case(
        "G41 59日 + 均额缺失",
        _rec(trading_days_listed=59, avg_amount_20d=None),
        (RejectReason.IPO_RECENT, RejectReason.DATA_MISSING),
    ),
    # H. 构造链（build_candidate_record → 派生量由 F1/F2 纯函数计算）
    _Case(
        "H42 构造链：60日日历 + 20笔恰阈值 → 通过",
        build_candidate_record(
            row={"code": "600100", "list_date": _CAL_60[0]},
            amounts=_AMOUNTS_EXACT,
            calendar=_CAL_60,
            as_of=_CAL_60[-1],
        ),
        (),
    ),
    _Case(
        "H43 构造链：19笔成交额（历史不足）→ DATA_MISSING",
        build_candidate_record(
            row={"code": "600100", "list_date": _CAL_60[0]},
            amounts=_AMOUNTS_SHORT,
            calendar=_CAL_60,
            as_of=_CAL_60[-1],
        ),
        (RejectReason.DATA_MISSING,),
    ),
    _Case(
        "H44 构造链：59日日历 → IPO_RECENT",
        build_candidate_record(
            row={"code": "600100", "list_date": _CAL_59[0]},
            amounts=_AMOUNTS_EXACT,
            calendar=_CAL_59,
            as_of=_CAL_59[-1],
        ),
        (RejectReason.IPO_RECENT,),
    ),
    _Case(
        "H45 构造链：25笔取最后20笔（前 5 笔 10 亿不参与）→ ILLIQUID",
        build_candidate_record(
            row={"code": "600100", "list_date": _CAL_60[0]},
            amounts=[1_000_000_000.0] * 5 + [THRESHOLD - 0.01] * 20,
            calendar=_CAL_60,
            as_of=_CAL_60[-1],
        ),
        (RejectReason.ILLIQUID,),
    ),
    _Case(
        "H46 构造链：61日日历 + 快照行 is_st=1 → ST_FLAG",
        build_candidate_record(
            row={"code": "600100", "is_st": 1, "list_date": _CAL_61[0]},
            amounts=_AMOUNTS_EXACT,
            calendar=_CAL_61,
            as_of=_CAL_61[-1],
        ),
        (RejectReason.ST_FLAG,),
    ),
    _Case(
        "H47 构造链：行 list_date=None → DATA_MISSING",
        build_candidate_record(
            row={"code": "600100", "list_date": None},
            amounts=_AMOUNTS_EXACT,
            calendar=_CAL_60,
            as_of=_CAL_60[-1],
        ),
        (RejectReason.DATA_MISSING,),
    ),
    _Case(
        "H48 构造链：list_date 不在日历（日历未覆盖上市日）→ DATA_MISSING",
        build_candidate_record(
            row={"code": "600100", "list_date": "2026-08-31"},
            amounts=_AMOUNTS_EXACT,
            calendar=_CAL_60,
            as_of=_CAL_60[-1],
        ),
        (RejectReason.DATA_MISSING,),
    ),
    _Case(
        "H49 构造链：as_of 早于 list_date → DATA_MISSING",
        build_candidate_record(
            row={"code": "600100", "list_date": _CAL_60[-1]},
            amounts=_AMOUNTS_EXACT,
            calendar=_CAL_60,
            as_of=_CAL_60[0],
        ),
        (RejectReason.DATA_MISSING,),
    ),
    _Case(
        "H50 构造链：快照行 limit_up_sealed=1 → LIMIT_LOCKED",
        build_candidate_record(
            row={"code": "600100", "limit_up_sealed": 1, "list_date": _CAL_60[0]},
            amounts=_AMOUNTS_EXACT,
            calendar=_CAL_60,
            as_of=_CAL_60[-1],
        ),
        (RejectReason.LIMIT_LOCKED,),
    ),
)


def _fmt(reasons: tuple[RejectReason, ...]) -> str:
    """原因码 tuple → 展示串（空 = 通过）。"""
    return "()" if not reasons else "(" + ", ".join(reason.value for reason in reasons) + ")"


def main() -> int:
    """逐例跑 filter_universe 并比对硬编码期望；返回进程退出码。"""
    if len(CASES) != 50:
        print(f"[表结构错误] 用例数 {len(CASES)} != 50")
        return 1
    mismatches: list[str] = []
    coverage: dict[RejectReason, int] = dict.fromkeys(RejectReason, 0)
    print("序号 | 结论 | 用例 | 期望原因 | 实际原因")
    print("-" * 96)
    for index, case in enumerate(CASES, start=1):
        expected = case.expected
        actual = filter_universe([case.record])[0].reasons
        ok = actual == expected
        for reason in expected:
            coverage[reason] += 1
        print(
            f"{index:03d}  | {'PASS' if ok else 'FAIL'} | {case.label}"
            f" | {_fmt(expected)} | {_fmt(actual)}"
        )
        if not ok:
            mismatches.append(case.label)
    print("-" * 96)
    matched = len(CASES) - len(mismatches)
    print(f"匹配 {matched}/{len(CASES)}；不一致 {len(mismatches)}")
    print(
        "六码覆盖（期望命中例数）："
        + "；".join(f"{reason.value}={coverage[reason]}" for reason in RejectReason)
    )
    if mismatches:
        print("不一致用例：" + "；".join(mismatches))
        return 1
    print("全部 50 例与手算期望一致")
    return 0


if __name__ == "__main__":
    sys.exit(main())
