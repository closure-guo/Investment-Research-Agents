"""候选池前向收益跟踪单测（add-screening-evaluation Task 1；合成面板，零网络）。

口径钉死（spec evaluation「候选池前向命中率」+ 计划 V1）：

- 入场 = 池日次一交易日开盘经 ``execution.matching.match_order``（买 1 手，滑点
  取最保守档 0.002）：一字涨停不成交 → 记原因码并剔除（fail-closed）；
- 出场 = 入场日后第 horizon 个交易日收盘；收益 = exit/entry − 1；
- 时点截断：``as_of`` 给定 → 只用 ≤ as_of 的面板行（未来行不得影响读数）；
- 分组 = ``selection_pool`` 入选 vs ``l2_candidates`` 中落选；
- 配对 bootstrap 以**池日**为配对单元，B=10000 / seed=42，两次执行逐位一致。
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from evals.screening import forward as fwd
from finance_agent.execution.config import default_matching_config
from finance_agent.execution.matching import REASON_LIMIT_UP_UNFILLABLE
from finance_agent.factors.eval_engine import MarketPanel
from finance_agent.screening.ranker import Candidate
from finance_agent.screening.store import record_l2_candidates, record_screening_batch

DATES = tuple(f"2026-01-{day:02d}" for day in range(1, 13))  # d0..d11（面板交易日轴）
D0, D1, D2, D3, D5, D6, D7, D8, D9, D10, D11 = (
    DATES[i] for i in (0, 1, 2, 3, 5, 6, 7, 8, 9, 10, 11)
)

# default_matching_config 的最保守档（回放无全市场成交额样本 → M4 取最大滑点）
SLIPPAGE_RATE = 0.002

# 入选/落选分组（DB 造数）：D 于 d1 一字涨停 → 买单不成交
L2: dict[str, list[str]] = {
    D0: ["A", "B", "C", "D"],
    D1: ["B", "A", "C"],
    D2: ["A", "B", "C"],
}
POOL: dict[str, list[str]] = {D0: ["A", "B"], D1: ["B"], D2: ["C"]}


def _fill_price(open_price: float) -> float:
    """手算入场价：``round(open × (1 + 滑点), 2)``（撮合引擎 M1 同口径）。"""
    return round(open_price * (1 + SLIPPAGE_RATE), 2)


def _ret(open_price: float, close_price: float) -> float:
    """手算单笔收益：出场收盘 / 入场价 − 1。"""
    return close_price / _fill_price(open_price) - 1.0


def _panel(overrides: dict[str, dict[str, float]] | None = None) -> MarketPanel:
    """合成面板（12 日 × A/B/C/D）：open=high=low=close（滑点外无噪声）。

    D 于 d1 一字涨停（open=high=low=close=11.0，昨收 10.0 → +10% ≥ 4.8%）。
    ``overrides`` 由 symbol → {日期: 收盘价} 覆写（可注入 NaN 造缺失）。
    """
    closes: dict[str, list[float]] = {
        "A": [10.0] * 12,
        "B": [10.0, 10.0, 10.0, 10.0, 11.0, 12.0, 13.0, 14.0, 15.0, 16.0, 17.0, 18.0],
        "C": [10.0, 10.0, 10.0, 10.0, 9.0, 8.0, 7.0, 6.0, 5.0, 4.0, 3.0, 2.0],
        "D": [10.0] * 12,
    }
    closes["D"][1] = 11.0  # 一字涨停
    for symbol, changes in (overrides or {}).items():
        for trade_date, value in changes.items():
            closes[symbol][DATES.index(trade_date)] = value
    frame = pd.DataFrame(closes, index=list(DATES)).sort_index().sort_index(axis=1)
    fields = {name: frame.copy() for name in ("open", "high", "low", "close")}
    fields["amount"] = pd.DataFrame(
        {symbol: [1000.0] * len(DATES) for symbol in closes}, index=list(DATES)
    ).sort_index(axis=1)
    return MarketPanel(fields=fields)


def _seed(tmp_path: Path, *, l2: dict[str, list[str]], pool: dict[str, list[str]]) -> Path:
    """造 screening 库：l2 名单 + 精选池（入选组来源）。"""
    db_path = tmp_path / "screening.db"
    for trade_date, symbols in l2.items():
        record_l2_candidates(
            trade_date,
            [
                Candidate(
                    symbol=symbol,
                    trade_date=trade_date,
                    composite_score=float(len(symbols) - index),
                    factor_contribs=(),
                    rank=index + 1,
                )
                for index, symbol in enumerate(symbols)
            ],
            db_path=db_path,
        )
    for trade_date, symbols in pool.items():
        record_screening_batch(
            trade_date,
            status="ok",
            started_at="2026-01-01T00:00:00",
            finished_at="2026-01-01T00:01:00",
            l1_passed=len(l2.get(trade_date, [])),
            l2_top=len(l2.get(trade_date, [])),
            l3_keep=len(symbols),
            pool_rows=[
                {
                    "symbol": symbol,
                    "composite_score": 1.0,
                    "rank": index + 1,
                    "consecutive_days": 3,
                    "l3_verdict": "keep",
                    "l3_reason": "合成数据",
                    "factor_contribs": [],
                }
                for index, symbol in enumerate(symbols)
            ],
            db_path=db_path,
        )
    return db_path


def _day(pool_date: str, rets: dict[str, float]) -> fwd.DayForwardResult:
    """构造单池日的合成结果（配对 bootstrap 单测用）。"""
    samples = tuple(
        fwd.ForwardSample(
            pool_date=pool_date,
            symbol=symbol,
            horizon=20,
            entry_date=pool_date,
            entry_price=1.0,
            exit_date=pool_date,
            exit_price=1.0 + ret,
            ret=ret,
        )
        for symbol, ret in rets.items()
    )
    return fwd.DayForwardResult(
        pool_date=pool_date, samples=samples, excluded_counts={}, unfilled_counts={}
    )


def test_hand_computed_three_pool_dates_two_horizons(tmp_path):
    """3 池日 × 2 horizon 逐数对照（入场/出场/分组/未成交剔除均为手算值）。"""
    db_path = _seed(tmp_path, l2=L2, pool=POOL)
    payload = fwd.forward_pool_returns(
        pool_dates=(D0, D1, D2),
        panel=_panel(),
        db_path=db_path,
        horizons=(5, 10),
        config=default_matching_config(),
    )
    assert payload["pool_dates"] == [D0, D1, D2]

    # horizon 5：d0 入场 d1、出场 d6；d1 入场 d2、出场 d7；d2 入场 d3、出场 d8
    h5 = payload["horizons"][5]
    selected = [
        _ret(10.0, 10.0),  # A@d0
        _ret(10.0, 13.0),  # B@d0
        _ret(10.0, 14.0),  # B@d1
        _ret(10.0, 5.0),  # C@d2
    ]
    assert h5["selected"]["n"] == 4
    assert h5["selected"]["days"] == 3
    assert h5["selected"]["mean"] == pytest.approx(sum(selected) / len(selected))
    assert h5["selected"]["win_rate"] == pytest.approx(0.5)  # 只有 B 两笔为正

    rejected = [
        _ret(10.0, 7.0),  # C@d0
        _ret(10.0, 10.0),  # A@d1
        _ret(10.0, 6.0),  # C@d1
        _ret(10.0, 10.0),  # A@d2
        _ret(10.0, 15.0),  # B@d2
    ]
    assert h5["rejected"]["n"] == 5
    assert h5["rejected"]["mean"] == pytest.approx(sum(rejected) / len(rejected))
    assert h5["rejected"]["win_rate"] == pytest.approx(0.2)

    # 配对单元 = 池日：逐日两组均值之差 → 均值差 + 95% CI（B=10000 / seed=42）
    day_diffs = [
        (_ret(10.0, 10.0) + _ret(10.0, 13.0)) / 2 - _ret(10.0, 7.0),
        _ret(10.0, 14.0) - (_ret(10.0, 10.0) + _ret(10.0, 6.0)) / 2,
        _ret(10.0, 5.0) - (_ret(10.0, 10.0) + _ret(10.0, 15.0)) / 2,
    ]
    diff = h5["diff"]
    assert diff["paired_days"] == 3
    assert diff["diff_mean"] == pytest.approx(sum(day_diffs) / len(day_diffs))
    assert diff["bootstrap_B"] == 10000
    assert diff["seed"] == 42
    lo, hi = diff["ci95"]
    assert lo <= diff["diff_mean"] <= hi

    # 一字涨停（D@d0）不成交 → 剔除并计原因码（来自撮合引擎，不按涨停价假设成交）
    assert h5["unfilled_counts"] == {REASON_LIMIT_UP_UNFILLABLE: 1}
    assert h5["rejected"]["excluded_counts"] == {REASON_LIMIT_UP_UNFILLABLE: 1}
    assert h5["selected"]["excluded_counts"] == {}
    assert REASON_LIMIT_UP_UNFILLABLE == fwd.REASON_LIMIT_UP_UNFILLABLE

    # horizon 10：仅 d0 三笔可结算（入场 d1 + 10 → d11）；d1/d2 全部右删失（面板只到 d11）
    h10 = payload["horizons"][10]
    selected10 = [_ret(10.0, 10.0), _ret(10.0, 18.0)]
    assert h10["selected"]["n"] == 2
    assert h10["selected"]["mean"] == pytest.approx(sum(selected10) / 2)
    assert h10["selected"]["excluded_counts"] == {fwd.REASON_INSUFFICIENT_FORWARD_DATA: 2}
    assert h10["rejected"]["n"] == 1
    assert h10["rejected"]["mean"] == pytest.approx(_ret(10.0, 2.0))
    assert h10["rejected"]["excluded_counts"] == {
        fwd.REASON_INSUFFICIENT_FORWARD_DATA: 4,
        REASON_LIMIT_UP_UNFILLABLE: 1,
    }
    # 配对日 < 2 → 不产伪 CI（单日 bootstrap 是伪精度）
    assert h10["diff"]["paired_days"] == 1
    assert h10["diff"]["ci95"] is None

    # artifact 落盘路径依赖 JSON 可序列化
    json.dumps(payload, ensure_ascii=False)


def test_ci_deterministic_across_runs(tmp_path):
    """配对 bootstrap 固定种子：两次执行 CI 逐位一致。"""
    db_path = _seed(tmp_path, l2=L2, pool=POOL)
    kwargs = {
        "pool_dates": (D0, D1, D2),
        "panel": _panel(),
        "db_path": db_path,
        "horizons": (5,),
        "config": default_matching_config(),
    }
    first = fwd.forward_pool_returns(**kwargs)  # type: ignore[arg-type]
    second = fwd.forward_pool_returns(**kwargs)  # type: ignore[arg-type]
    assert first["horizons"][5]["diff"] == second["horizons"][5]["diff"]
    assert first["horizons"][5]["diff"]["ci95"] is not None


def test_as_of_truncation_only_uses_rows_up_to_as_of(tmp_path):
    """时点截断：as_of 之后的面板行不得影响任何读数（无前视）。"""
    db_path = _seed(tmp_path, l2=L2, pool=POOL)
    kwargs = {
        "pool_dates": (D0, D1, D2),
        "db_path": db_path,
        "horizons": (5, 10),
        "as_of": D9,
        "config": default_matching_config(),
    }
    base = fwd.forward_pool_returns(panel=_panel(), **kwargs)  # type: ignore[arg-type]
    mutated = _panel(overrides={"A": {D10: 999.0, D11: 999.0}, "B": {D10: 999.0, D11: 999.0}})
    future_changed = fwd.forward_pool_returns(panel=mutated, **kwargs)  # type: ignore[arg-type]
    assert future_changed["horizons"] == base["horizons"]  # 未来行改动零影响

    # as_of=d9：horizon 5 三池日全部可结算；horizon 10 出场（d11）被截断 → 全体右删失
    assert base["horizons"][5]["selected"]["n"] == 4
    assert base["horizons"][10]["selected"]["n"] == 0
    assert base["horizons"][10]["selected"]["excluded_counts"] == {
        fwd.REASON_INSUFFICIENT_FORWARD_DATA: 4
    }
    # 不截断（as_of=None）同 panel 则含 d11 出场样本 → 截断真实生效（非恒空）
    untruncated = fwd.forward_pool_returns(
        pool_dates=(D0, D1, D2),
        panel=_panel(),
        db_path=db_path,
        horizons=(10,),
        config=default_matching_config(),
    )
    assert untruncated["horizons"][10]["selected"]["n"] == 2


def test_pool_date_after_as_of_and_missing_next_day_reasons(tmp_path):
    """池日 > as_of 与「出场超出截断」两类原因码；as_of 边界逐日钉死。"""
    db_path = _seed(
        tmp_path,
        l2={**L2, D9: ["A", "B", "C"]},
        pool={**POOL, D9: ["C"]},
    )
    kwargs = {
        "pool_dates": (D0, D9),
        "panel": _panel(),
        "db_path": db_path,
        "horizons": (5,),
        "config": default_matching_config(),
    }
    # as_of=d6 = d0 入场 d1 + 5 的出场日：恰可结算；d9 > as_of → 池日越界原因码
    payload = fwd.forward_pool_returns(as_of=D6, **kwargs)  # type: ignore[arg-type]
    h5 = payload["horizons"][5]
    assert h5["selected"]["n"] == 2  # d0 的 A、B（出场 d6）
    assert h5["selected"]["excluded_counts"] == {fwd.REASON_POOL_DATE_AFTER_AS_OF: 1}
    assert h5["rejected"]["excluded_counts"] == {
        fwd.REASON_POOL_DATE_AFTER_AS_OF: 2,
        REASON_LIMIT_UP_UNFILLABLE: 1,
    }
    # as_of=d5（少一天）：出场 d6 > as_of → 右删失（同一个池日、两种截断原因码）
    earlier = fwd.forward_pool_returns(as_of=D5, **kwargs)  # type: ignore[arg-type]
    assert earlier["horizons"][5]["selected"]["n"] == 0
    assert earlier["horizons"][5]["selected"]["excluded_counts"] == {
        fwd.REASON_INSUFFICIENT_FORWARD_DATA: 2,
        fwd.REASON_POOL_DATE_AFTER_AS_OF: 1,
    }

    days = fwd.forward_returns_by_day(
        symbols_by_date={D1: ["B"]},
        panel=_panel(),
        horizon=5,
        as_of=D1,
        config=default_matching_config(),
    )
    assert days[0].excluded_counts == {fwd.REASON_NO_NEXT_TRADING_DAY: 1}


def test_entry_caliber_and_exit_day_are_pinned():
    """单笔逐字段：入场价 = 引擎口径；出场日 = 入场日后第 horizon 个交易日。"""
    outcome = fwd.forward_return(_panel(), "B", D0, 5, config=default_matching_config())
    assert outcome.reason is None
    sample = outcome.sample
    assert sample is not None
    assert sample.entry_date == D1
    assert sample.exit_date == D6
    assert sample.entry_price == round(10.0 * (1 + SLIPPAGE_RATE), 2)
    assert sample.exit_price == 13.0
    assert sample.ret == pytest.approx(13.0 / sample.entry_price - 1.0)

    sealed = fwd.forward_return(_panel(), "D", D0, 5, config=default_matching_config())
    assert sealed.sample is None
    assert sealed.reason == REASON_LIMIT_UP_UNFILLABLE


def test_missing_bar_and_unknown_symbol_reasons():
    """入场 bar 缺失 / 出场 bar 缺失 / 标的不在面板 → 各自原因码（fail-closed）。"""
    nan = float("nan")
    panel = _panel(overrides={"C": {D8: nan}})
    days = fwd.forward_returns_by_day(
        symbols_by_date={D2: ["C"]},
        panel=panel,
        horizon=5,
        config=default_matching_config(),
    )
    assert days[0].samples == ()
    assert days[0].excluded_counts == {fwd.REASON_MISSING_EXIT_BAR: 1}

    days = fwd.forward_returns_by_day(
        symbols_by_date={D0: ["E"]},
        panel=panel,
        horizon=5,
        config=default_matching_config(),
    )
    assert days[0].excluded_counts == {fwd.REASON_MISSING_ENTRY_BAR: 1}


def test_empty_day_reports_zero_n_and_no_fabricated_stats(tmp_path):
    """空池日：n=0、mean/win_rate 记 None（不编造 0 值）。"""
    db_path = _seed(tmp_path, l2={D0: ["A"], D1: ["B"]}, pool={D0: ["A"]})
    payload = fwd.forward_pool_returns(
        pool_dates=(D1,),
        panel=_panel(),
        db_path=db_path,
        horizons=(5,),
        config=default_matching_config(),
    )
    h5 = payload["horizons"][5]
    assert h5["selected"] == {
        "n": 0,
        "mean": None,
        "win_rate": None,
        "days": 0,
        "excluded_counts": {},
        "unfilled_counts": {},
    }
    assert h5["rejected"]["n"] == 1  # 落选组 = l2(D1) − pool(D1) = {B}
    assert h5["diff"]["ci95"] is None


def test_paired_diff_ci_degenerate_exact_and_min_days():
    """配对 bootstrap 已知分布手算对照：逐日差恒 0.04 → CI 退化到 [0.04, 0.04]。"""
    left = [_day(D0, {"X": 0.05}), _day(D1, {"X": 0.05})]
    right = [_day(D0, {"Y": 0.01}), _day(D1, {"Y": 0.01})]
    diff = fwd.paired_diff_ci(left, right, B=10000, seed=42)
    assert diff["paired_days"] == 2
    assert diff["diff_mean"] == pytest.approx(0.04)
    lo, hi = diff["ci95"]
    assert (lo, hi) == pytest.approx((0.04, 0.04))
    assert diff["bootstrap_B"] == 10000
    assert diff["seed"] == 42

    single = fwd.paired_diff_ci(left[:1], right[:1])
    assert single["paired_days"] == 1
    assert single["diff_mean"] == pytest.approx(0.04)
    assert single["ci95"] is None  # 配对日 < 2 → 不产 CI

    empty = fwd.paired_diff_ci([], [])
    assert empty["paired_days"] == 0
    assert empty["diff_mean"] is None
    assert empty["ci95"] is None


def test_multi_symbol_day_uses_day_mean_for_pairing():
    """配对单元是「池日日均值」：同日多标的不膨胀配对日数（手算对照）。"""
    left = [_day(D0, {"X": 0.10, "Y": 0.02}), _day(D1, {"X": 0.06})]
    right = [_day(D0, {"Z": 0.01}), _day(D1, {"Z": 0.03})]
    diff = fwd.paired_diff_ci(left, right, B=10000, seed=42)
    assert diff["paired_days"] == 2
    assert diff["diff_mean"] == pytest.approx((0.06 - 0.01 + 0.06 - 0.03) / 2)


def test_fail_loud_on_bad_inputs(tmp_path):
    """非法输入 fail loud（horizon<=0 / 非规范日期 / 重复池日 / 面板缺字段）。"""
    panel = _panel()
    with pytest.raises(ValueError):
        fwd.forward_return(panel, "A", D0, 0, config=default_matching_config())
    with pytest.raises(ValueError):
        fwd.forward_return(panel, "A", "2026-1-1", 5, config=default_matching_config())
    db_path = _seed(tmp_path, l2=L2, pool=POOL)
    with pytest.raises(ValueError):
        fwd.forward_pool_returns(
            pool_dates=(D0, D0),
            panel=panel,
            db_path=db_path,
            horizons=(5,),
            config=default_matching_config(),
        )
    with pytest.raises(ValueError):
        fwd.forward_pool_returns(
            pool_dates=(D0,),
            panel=panel,
            db_path=db_path,
            horizons=(5, 5),
            config=default_matching_config(),
        )
    broken = MarketPanel(fields={"close": panel.fields["close"]})
    with pytest.raises(ValueError):
        fwd.forward_return(broken, "A", D0, 5, config=default_matching_config())
