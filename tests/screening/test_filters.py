"""screening/filters.py L1 硬过滤单测（add-screening-filters Task 1）。

计划 docs/superpowers/plans/2026-10-13-add-screening-filters.md 关键设计决策：

- F1 上市交易天数：交易日历口径（禁用自然日近似）；list_date 缺失/不可解析/
  不在日历/as_of 早于 list_date → None（→ ``DATA_MISSING``）；
- F2 流动性：最后至多 20 个成交额均值；历史不足 20 或窗口含非有限值 → None
  （→ ``DATA_MISSING``）；「平均 < 阈值」才剔除（恰好等于不剔除）；
- F3 一字板：只看快照 ``limit_up_sealed``/``limit_down_sealed``（D3 口径），
  「盘中触板未封死」不剔除；
- F4 阈值单点来源：``filter_universe`` 调用期读 ``constraints.DEFAULT_THRESHOLDS``
  （模块属性），显式 ``thresholds=`` 参数可覆盖。

剔除返回全部命中原因码，顺序 = ``RejectReason`` 声明顺序，不做首个原因短路。
"""

from __future__ import annotations

from datetime import date, timedelta

from finance_agent.screening.constraints import (
    DEFAULT_THRESHOLDS,
    MIN_AVG_AMOUNT_20D,
    MIN_LIQUIDITY_HISTORY_DAYS,
    MIN_TRADING_DAYS,
    FilterThresholds,
)
from finance_agent.screening.filters import (
    CandidateRecord,
    RejectReason,
    avg_amount_20d,
    build_candidate_record,
    filter_universe,
    trading_days_listed,
)

# 阈值常量（逐字）在测试里独立手写，不复用被测模块生成期望值（除 F4 场景外）。
_THRESHOLD = 50_000_000.0


def _calendar(n: int, start: str = "2026-09-01") -> list[str]:
    """生成 n 个连续「交易日」（跳过周末）的 ISO 日期列表（起点 2026-09-01 周二）。"""
    days: list[str] = []
    day = date.fromisoformat(start)
    while len(days) < n:
        if day.weekday() < 5:
            days.append(day.isoformat())
        day += timedelta(days=1)
    return days


def _rec(**overrides: object) -> CandidateRecord:
    """构造「全通过」候选记录（60 交易日 / 恰等于阈值），再按需覆盖字段。"""
    base: dict[str, object] = {
        "code": "600000",
        "name": "测试标的",
        "is_st": False,
        "is_suspended": False,
        "limit_up_sealed": False,
        "limit_down_sealed": False,
        "trading_days_listed": 60,
        "avg_amount_20d": _THRESHOLD,
    }
    base.update(overrides)
    return CandidateRecord(**base)  # type: ignore[arg-type]


# ── Task 1: 约束常量（单一来源，逐字） ───────────────────────────────────────


def test_constraints_single_source_verbatim():
    """常量口径逐字：60 交易日 / 5000 万 / 20 日历史；DEFAULT_THRESHOLDS 同源。"""
    assert MIN_TRADING_DAYS == 60
    assert MIN_AVG_AMOUNT_20D == 50_000_000.0
    assert MIN_LIQUIDITY_HISTORY_DAYS == 20
    assert (
        FilterThresholds(
            min_trading_days=60,
            min_avg_amount_20d=50_000_000.0,
            min_liquidity_history_days=20,
        )
        == DEFAULT_THRESHOLDS
    )


# ── Task 1: 每规则正反 ───────────────────────────────────────────────────────


def test_clean_record_passes():
    """全字段干净 → 通过，reasons 为空 tuple。"""
    decision = filter_universe([_rec()])[0]
    assert decision.passed is True
    assert decision.reasons == ()
    assert decision.code == "600000"


def test_st_flag_positive_and_negative():
    """ST 正（is_st=True → ST_FLAG）/ 反（False → 通过）。"""
    hit = filter_universe([_rec(is_st=True)])[0]
    assert hit.passed is False
    assert hit.reasons == (RejectReason.ST_FLAG,)
    assert filter_universe([_rec(is_st=False)])[0].passed is True


def test_suspended_positive_and_negative():
    """停牌正（is_suspended=True → SUSPENDED）/ 反（False → 通过）。"""
    hit = filter_universe([_rec(is_suspended=True)])[0]
    assert hit.reasons == (RejectReason.SUSPENDED,)
    assert filter_universe([_rec(is_suspended=False)])[0].passed is True


def test_limit_up_sealed_rejected():
    """一字涨停（sealed_up=True，封死）→ LIMIT_LOCKED。"""
    decision = filter_universe([_rec(limit_up_sealed=True)])[0]
    assert decision.reasons == (RejectReason.LIMIT_LOCKED,)


def test_limit_down_sealed_rejected():
    """一字跌停（sealed_down=True，封死）→ LIMIT_LOCKED（与涨停同码）。"""
    decision = filter_universe([_rec(limit_down_sealed=True)])[0]
    assert decision.reasons == (RejectReason.LIMIT_LOCKED,)


def test_touched_but_not_sealed_passes():
    """盘中触板未封死：快照 sealed 位为 False（high 触及但 low < high）→ 通过。"""
    decision = filter_universe([_rec(limit_up_sealed=False, limit_down_sealed=False)])[0]
    assert decision.passed is True
    assert decision.reasons == ()


def test_both_sealed_flags_single_reason():
    """涨跌停双封位同真（数据兜底形态）→ LIMIT_LOCKED 只出现一次。"""
    decision = filter_universe([_rec(limit_up_sealed=True, limit_down_sealed=True)])[0]
    assert decision.reasons == (RejectReason.LIMIT_LOCKED,)


def test_multi_reason_enum_declaration_order():
    """同单多因：ST + 停牌 → (ST_FLAG, SUSPENDED)（枚举声明顺序）。"""
    decision = filter_universe([_rec(is_st=True, is_suspended=True)])[0]
    assert decision.reasons == (RejectReason.ST_FLAG, RejectReason.SUSPENDED)


def test_all_five_rules_hit_in_order():
    """五条规则同时命中 → 原因码按声明顺序全集（无 DATA_MISSING）。"""
    record = _rec(
        is_st=True,
        is_suspended=True,
        trading_days_listed=59,
        limit_up_sealed=True,
        avg_amount_20d=_THRESHOLD - 0.01,
    )
    decision = filter_universe([record])[0]
    assert decision.reasons == (
        RejectReason.ST_FLAG,
        RejectReason.SUSPENDED,
        RejectReason.IPO_RECENT,
        RejectReason.LIMIT_LOCKED,
        RejectReason.ILLIQUID,
    )


# ── Task 1: 上市天数边界 ─────────────────────────────────────────────────────


def test_ipo_recent_boundary_59_60_61():
    """边界：59 日剔除（IPO_RECENT）；60 / 61 日通过（含 60 边界，「满 60」保留）。"""
    assert filter_universe([_rec(trading_days_listed=59)])[0].reasons == (RejectReason.IPO_RECENT,)
    assert filter_universe([_rec(trading_days_listed=60)])[0].passed is True
    assert filter_universe([_rec(trading_days_listed=61)])[0].passed is True


def test_trading_days_none_is_data_missing():
    """上市天数为 None（数据不可得）→ DATA_MISSING（fail-closed）。"""
    decision = filter_universe([_rec(trading_days_listed=None)])[0]
    assert decision.reasons == (RejectReason.DATA_MISSING,)


# ── Task 1: 流动性边界 ───────────────────────────────────────────────────────


def test_illiquid_at_threshold_passes():
    """「低于」语义：平均成交额恰等于阈值 → 不剔除。"""
    assert filter_universe([_rec(avg_amount_20d=_THRESHOLD)])[0].passed is True


def test_illiquid_just_below_threshold_rejected():
    """阈值 − 0.01 → ILLIQUID。"""
    decision = filter_universe([_rec(avg_amount_20d=_THRESHOLD - 0.01)])[0]
    assert decision.reasons == (RejectReason.ILLIQUID,)


def test_avg_amount_none_is_data_missing():
    """成交额均值为 None（历史不足/数据不可得）→ DATA_MISSING。"""
    decision = filter_universe([_rec(avg_amount_20d=None)])[0]
    assert decision.reasons == (RejectReason.DATA_MISSING,)


def test_both_none_data_missing_once():
    """上市天数与均额同时缺失 → DATA_MISSING 只出现一次。"""
    decision = filter_universe([_rec(trading_days_listed=None, avg_amount_20d=None)])[0]
    assert decision.reasons == (RejectReason.DATA_MISSING,)


def test_data_missing_ordered_after_other_reasons():
    """DATA_MISSING 排在声明顺序末位，与已命中原因共存（不吞并其他原因）。"""
    st_missing = filter_universe([_rec(is_st=True, trading_days_listed=None)])[0]
    assert st_missing.reasons == (RejectReason.ST_FLAG, RejectReason.DATA_MISSING)
    sealed_missing = filter_universe([_rec(limit_down_sealed=True, avg_amount_20d=None)])[0]
    assert sealed_missing.reasons == (RejectReason.LIMIT_LOCKED, RejectReason.DATA_MISSING)


# ── Task 1: filter_universe 批量契约 ─────────────────────────────────────────


def test_filter_universe_preserves_input_order_and_empty():
    """输出与输入同序、同长；空输入 → 空输出。"""
    records = [
        _rec(code="000001"),
        _rec(code="600519", is_st=True),
        _rec(code="300750", trading_days_listed=59),
    ]
    decisions = filter_universe(records)
    assert [d.code for d in decisions] == ["000001", "600519", "300750"]
    assert [d.passed for d in decisions] == [True, False, False]
    assert filter_universe([]) == []


def test_filter_universe_only_emits_enum_members():
    """混合输入下所有原因码均为 RejectReason 成员（枚举封闭）。"""
    records = [
        _rec(),
        _rec(is_st=True),
        _rec(is_suspended=True),
        _rec(trading_days_listed=None),
        _rec(avg_amount_20d=None),
        _rec(limit_up_sealed=True, limit_down_sealed=True),
        _rec(trading_days_listed=1, avg_amount_20d=0.0, is_st=True, is_suspended=True),
    ]
    members = set(RejectReason)
    for decision in filter_universe(records):
        assert set(decision.reasons) <= members
        assert decision.passed is (len(decision.reasons) == 0)


def test_enum_members_verbatim():
    """枚举完整性：六个原因码逐字（值 + 声明顺序）+ 均为 str 负载。"""
    assert [member.value for member in RejectReason] == [
        "ST_FLAG",
        "SUSPENDED",
        "IPO_RECENT",
        "LIMIT_LOCKED",
        "ILLIQUID",
        "DATA_MISSING",
    ]
    assert all(isinstance(member, str) for member in RejectReason)
    assert set(RejectReason) == {
        "ST_FLAG",
        "SUSPENDED",
        "IPO_RECENT",
        "LIMIT_LOCKED",
        "ILLIQUID",
        "DATA_MISSING",
    }


# ── Task 1: trading_days_listed（F1） ────────────────────────────────────────


def test_trading_days_listed_single_day():
    """list_date == as_of（交易日）→ 1（含首日）。"""
    calendar = ["2026-09-01", "2026-09-02", "2026-09-03"]
    assert trading_days_listed("2026-09-02", "2026-09-02", calendar) == 1


def test_trading_days_listed_inclusive_bounds():
    """区间含两端：list_date=日历首日、as_of=日历末日 → 全日历长度。"""
    calendar = _calendar(5)
    assert trading_days_listed(calendar[0], calendar[-1], calendar) == 5


def test_trading_days_listed_counts_up_to_as_of():
    """统计 list_date <= d <= as_of：as_of 晚于日历末日时仍按日历内天数计。"""
    calendar = ["2026-09-01", "2026-09-02", "2026-09-03"]
    assert trading_days_listed("2026-09-01", "2026-09-30", calendar) == 3


def test_trading_days_listed_requires_list_date_in_calendar():
    """list_date 不在日历（日历未覆盖上市日）→ None（fail-closed，不猜天数）。"""
    calendar = ["2026-09-01", "2026-09-02", "2026-09-03"]
    assert trading_days_listed("2026-08-31", "2026-09-03", calendar) is None


def test_trading_days_listed_as_of_before_list_date():
    """as_of 早于 list_date → None。"""
    calendar = ["2026-09-01", "2026-09-02", "2026-09-03"]
    assert trading_days_listed("2026-09-03", "2026-09-01", calendar) is None


def test_trading_days_listed_bad_inputs():
    """缺失/不可解析/非规范日期与空日历 → None（一律 fail-closed）。"""
    calendar = ["2026-09-01", "2026-09-02"]
    assert trading_days_listed(None, "2026-09-02", calendar) is None
    assert trading_days_listed("", "2026-09-02", calendar) is None
    assert trading_days_listed("2026-9-1", "2026-09-02", calendar) is None
    assert trading_days_listed("不是日期", "2026-09-02", calendar) is None
    assert trading_days_listed("2026-09-01", "", calendar) is None
    assert trading_days_listed("2026-09-01", "20260902", calendar) is None  # 非规范形态
    assert trading_days_listed("2026-09-01", "2026-09-02", []) is None


def test_trading_days_listed_skips_malformed_calendar_entries():
    """日历中的不可解析项跳过（与 snapshot 日历解析同纪律），有效项仍计数。"""
    calendar = ["bad-entry", "2026-09-01", "2026-09-02"]
    assert trading_days_listed("2026-09-01", "2026-09-02", calendar) == 2


# ── Task 1: avg_amount_20d（F2） ─────────────────────────────────────────────


def test_avg_amount_20d_takes_last_20_of_25():
    """25 天 → 取最后 20 天均值（1..25 的最后 20 个 = 6..25，均值 15.5）。"""
    amounts = [float(value) for value in range(1, 26)]
    assert avg_amount_20d(amounts) == 15.5


def test_avg_amount_20d_requires_20_history():
    """19 天 → None（历史不足 MIN_LIQUIDITY_HISTORY_DAYS，快照积累期）。"""
    assert avg_amount_20d([1.0] * 19) is None


def test_avg_amount_20d_exactly_20():
    """恰 20 天 → 全窗口均值。"""
    assert avg_amount_20d([2.0] * 20) == 2.0


def test_avg_amount_20d_non_finite_in_window():
    """窗口内 NaN / +inf / −inf → None（不编造均值）。"""
    nan_mid = [1.0] * 20
    nan_mid[5] = float("nan")
    assert avg_amount_20d(nan_mid) is None
    inf_tail = [1.0] * 19 + [float("inf")]
    assert avg_amount_20d(inf_tail) is None
    ninf_head = [float("-inf")] + [1.0] * 19
    assert avg_amount_20d(ninf_head) is None


def test_avg_amount_20d_non_finite_before_window_ignored():
    """窗口外（更早日期）的非有限值不影响「近 20 日」均值（口径 = 只看判定窗口）。"""
    amounts = [float("nan")] + [1_000_000_000.0] * 4 + [_THRESHOLD] * 20
    assert avg_amount_20d(amounts) == _THRESHOLD


def test_avg_amount_20d_empty():
    """空序列 → None。"""
    assert avg_amount_20d([]) is None


# ── Task 1: build_candidate_record（快照行 → 过滤输入） ──────────────────────


def test_build_candidate_record_maps_snapshot_row():
    """快照行 dict + amounts + calendar → 完整 record；过滤结论与直接构造一致。"""
    calendar = _calendar(60)
    row = {
        "code": "600519",
        "name": "贵州茅台",
        "is_st": 1,
        "is_suspended": 0,
        "limit_up_sealed": 1,
        "limit_down_sealed": 0,
        "list_date": calendar[0],
    }
    record = build_candidate_record(
        row=row, amounts=[_THRESHOLD] * 20, calendar=calendar, as_of=calendar[-1]
    )
    assert record == CandidateRecord(
        code="600519",
        name="贵州茅台",
        is_st=True,
        is_suspended=False,
        limit_up_sealed=True,
        limit_down_sealed=False,
        trading_days_listed=60,
        avg_amount_20d=_THRESHOLD,
    )
    decision = filter_universe([record])[0]
    assert decision.reasons == (RejectReason.ST_FLAG, RejectReason.LIMIT_LOCKED)


def test_build_candidate_record_null_and_missing_bits():
    """状态位 NULL/缺失按 False；list_date=None → 天数 None → DATA_MISSING。"""
    row = {"code": "", "is_st": None, "is_suspended": None, "list_date": None}
    record = build_candidate_record(
        row=row, amounts=[_THRESHOLD] * 20, calendar=_calendar(60), as_of="2026-09-30"
    )
    assert record.is_st is False
    assert record.is_suspended is False
    assert record.limit_up_sealed is False
    assert record.limit_down_sealed is False
    assert record.trading_days_listed is None
    assert record.avg_amount_20d == _THRESHOLD
    assert filter_universe([record])[0].reasons == (RejectReason.DATA_MISSING,)


def test_build_candidate_record_short_amounts():
    """成交额历史不足 20 天 → 均额 None → DATA_MISSING（天数已知仍照常判定）。"""
    calendar = _calendar(60)
    record = build_candidate_record(
        row={"code": "000001", "list_date": calendar[0]},
        amounts=[_THRESHOLD] * 19,
        calendar=calendar,
        as_of=calendar[-1],
    )
    assert record.trading_days_listed == 60
    assert record.avg_amount_20d is None
    assert filter_universe([record])[0].reasons == (RejectReason.DATA_MISSING,)
