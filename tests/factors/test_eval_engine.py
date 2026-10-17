"""factors/eval_engine.py 单测（add-factor-library-and-monitor Task 1）。

覆盖计划关键设计决策 E1（时序算子沿日期轴、横截面算子沿 code 轴、
``min_periods=window``、``div`` 除零 / ``log`` 非正 → NaN、全向量化）与
E2（``forward_returns`` 末日 NaN、``rank_ic_series`` 逐日 Spearman +
``MIN_IC_SAMPLES`` 门槛、IC 序列仅取两帧共同日期）。

期望值逐条独立手写（不复用被测模块生成期望）；面板全部为合成数据，零网络。
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from finance_agent.factors.dsl import parse
from finance_agent.factors.eval_engine import (
    MarketPanel,
    build_panel_from_rows,
    evaluate,
    forward_returns,
    rank_ic_series,
)

_NAN = float("nan")


def _dates(count: int) -> list[str]:
    """规范 ``YYYY-MM-DD`` 日期序列（天数足够，不依赖真实交易日历）。"""
    return [f"2026-01-{day:02d}" for day in range(1, count + 1)]


def _frame(values: dict[str, list[float]], dates: list[str] | None = None) -> pd.DataFrame:
    """code → 逐日值列表 → date×code 面板帧（列顺序 = dict 顺序）。"""
    length = len(next(iter(values.values())))
    return pd.DataFrame(
        values, index=dates if dates is not None else _dates(length), dtype="float64"
    )


def _assert_frame(result: pd.DataFrame, expected: pd.DataFrame) -> None:
    """帧对拍（容差 1e-5；列顺序必须一致——列序错位本身就是口径 bug）。"""
    pd.testing.assert_frame_equal(result, expected, check_dtype=False, check_names=False)


def _panel(**fields: pd.DataFrame) -> MarketPanel:
    return MarketPanel(fields=dict(fields))


def _eval(expr: str, panel: MarketPanel) -> pd.DataFrame:
    return evaluate(parse(expr), panel)


# ── 叶子节点 ────────────────────────────────────────────────────────────────


def test_evaluate_field_returns_panel_frame():
    """Field 叶子直接取面板同名字段（不复制、不改口径）。"""
    close = _frame({"A": [1.0, 2.0], "B": [3.0, 4.0]})
    panel = _panel(close=close)
    _assert_frame(_eval("close", panel), close)


def test_evaluate_number_leaf_broadcasts_constant():
    """Number 叶子广播为与面板同形状的常量帧。"""
    close = _frame({"A": [1.0, 2.0], "B": [3.0, 4.0]})
    panel = _panel(close=close)
    expected = _frame({"A": [2.5, 2.5], "B": [2.5, 2.5]})
    _assert_frame(_eval("2.5", panel), expected)


def test_evaluate_missing_field_raises_value_error():
    """面板缺字段 → ValueError（fail loud，不静默补 NaN）。"""
    panel = _panel(close=_frame({"A": [1.0]}))
    with pytest.raises(ValueError, match="volume"):
        _eval("volume", panel)


def test_evaluate_empty_panel_raises_value_error():
    """空面板：Number 叶子无可广播的参照帧 → ValueError。"""
    with pytest.raises(ValueError, match="空面板"):
        _eval("1", MarketPanel(fields={}))


# ── 时序算子（沿日期轴，按 code 计算） ──────────────────────────────────────


def test_ts_mean_window_and_min_periods():
    """ts_mean：窗口不足 → NaN；满窗 → 窗口均值（E1 min_periods=window）。"""
    close = _frame({"A": [1.0, 2.0, 3.0, 4.0]})
    expected = _frame({"A": [_NAN, _NAN, 2.0, 3.0]})
    _assert_frame(_eval("ts_mean(close,3)", _panel(close=close)), expected)


def test_ts_mean_insufficient_data_all_nan():
    """序列长度 < window → 全 NaN（不抛错、不做部分窗口）。"""
    close = _frame({"A": [1.0, 2.0]})
    expected = _frame({"A": [_NAN, _NAN]})
    _assert_frame(_eval("ts_mean(close,3)", _panel(close=close)), expected)


def test_ts_std_is_sample_std_ddof1():
    """ts_std = 窗口内样本标准差（ddof=1，pandas 默认）。"""
    close = _frame({"A": [1.0, 2.0, 5.0]})
    expected = _frame({"A": [_NAN, _NAN, 2.0816659994661326]})
    _assert_frame(_eval("ts_std(close,3)", _panel(close=close)), expected)


def test_ts_corr_is_rolling_pearson():
    """ts_corr = 滚动皮尔逊相关（两帧按位置对齐，min_periods=window）。"""
    close = _frame({"A": [1.0, 2.0, 3.0, 4.0]})
    volume = _frame({"A": [1.0, 2.0, 3.0, 10.0]})
    expected = _frame({"A": [_NAN, _NAN, 1.0, 0.9176629354822471]})
    _assert_frame(_eval("ts_corr(close,volume,3)", _panel(close=close, volume=volume)), expected)


def test_ts_rank_pct_rank_of_last_value_with_ties():
    """ts_rank：窗口内「当前值」的平均秩百分比（含并列取平均秩）。"""
    close = _frame({"A": [1.0, 3.0, 2.0, 2.0, 5.0]})
    expected = _frame({"A": [_NAN, _NAN, 2.0 / 3.0, 0.5, 1.0]})
    _assert_frame(_eval("ts_rank(close,3)", _panel(close=close)), expected)


def test_delta_is_value_minus_shift():
    """delta(x,w) = x − x.shift(w)；前 w 行 NaN。"""
    close = _frame({"A": [1.0, 2.0, 4.0, 7.0, 11.0]})
    one = _frame({"A": [_NAN, 1.0, 2.0, 3.0, 4.0]})
    two = _frame({"A": [_NAN, _NAN, 3.0, 5.0, 7.0]})
    panel = _panel(close=close)
    _assert_frame(_eval("delta(close,1)", panel), one)
    _assert_frame(_eval("delta(close,2)", panel), two)


def test_ref_shifts_within_instrument_only():
    """spec 场景（extend-factor-dsl-ref）：ref(close, 2) 逐标的后移 2 期（前 2 行 NaN、不跨标的）。"""
    close = _frame({"A": [10.0, 11.0, 12.0, 13.0], "B": [20.0, 21.0, 22.0, 23.0]})
    expected = _frame(
        {
            "A": [_NAN, _NAN, 10.0, 11.0],
            "B": [_NAN, _NAN, 20.0, 21.0],
        }
    )
    _assert_frame(_eval("ref(close,2)", _panel(close=close)), expected)


def test_ref_one_period_shift_and_composite():
    """ref(x,1) = 前一日值；复合式 `div(close,ref(close,1))` 逐元素相除（前 1 行 NaN）。"""
    close = _frame({"A": [10.0, 12.0, 9.0]})
    one = _frame({"A": [_NAN, 10.0, 12.0]})
    ratio = _frame({"A": [_NAN, 12.0 / 10.0, 9.0 / 12.0]})
    panel = _panel(close=close)
    _assert_frame(_eval("ref(close,1)", panel), one)
    _assert_frame(_eval("div(close,ref(close,1))", panel), ratio)


def test_time_series_operators_are_per_code():
    """时序算子按 code 各自独立滚动（列间不串味）。"""
    close = _frame({"A": [1.0, 2.0, 3.0], "B": [10.0, 20.0, 40.0]})
    expected = _frame({"A": [_NAN, _NAN, 2.0], "B": [_NAN, _NAN, 70.0 / 3.0]})
    _assert_frame(_eval("ts_mean(close,3)", _panel(close=close)), expected)


# ── 横截面算子（沿 code 轴，按日期计算） ────────────────────────────────────


def test_rank_cross_section_pct_with_nan_and_ties():
    """rank：逐日对全市场 pct 排名（平均秩；NaN 不参与且结果保持 NaN）。"""
    close = _frame(
        {
            "A": [10.0, 5.0],
            "B": [20.0, 5.0],
            "C": [30.0, 1.0],
            "D": [_NAN, 9.0],
        }
    )
    # 第 1 日 3 个有效值 → pct 秩 = 1/3、2/3、3/3；第 2 日 A/B 并列 2.5 秩 →
    # 2.5/4 = 0.625，C = 1/4，D = 4/4
    expected = _frame(
        {
            "A": [1.0 / 3.0, 0.625],
            "B": [2.0 / 3.0, 0.625],
            "C": [1.0, 0.25],
            "D": [_NAN, 1.0],
        }
    )
    _assert_frame(_eval("rank(close)", _panel(close=close)), expected)


def test_zscore_cross_section_ddof1():
    """zscore：逐日横截面 (x − mean) / std（样本标准差 ddof=1）。"""
    close = _frame(
        {
            "A": [1.0],
            "B": [2.0],
            "C": [3.0],
            "D": [4.0],
        }
    )
    expected = _frame(
        {
            "A": [-1.1618950038622251],
            "B": [-0.3872983346207417],
            "C": [0.3872983346207417],
            "D": [1.1618950038622251],
        }
    )
    _assert_frame(_eval("zscore(close)", _panel(close=close)), expected)


def test_zscore_flat_cross_section_is_nan():
    """横截面全相等 → std=0 → 0/0 为 NaN（不产生 inf）。"""
    close = _frame({"A": [2.0], "B": [2.0]})
    expected = _frame({"A": [_NAN], "B": [_NAN]})
    _assert_frame(_eval("zscore(close)", _panel(close=close)), expected)


def test_rank_and_ts_rank_use_different_axes():
    """rank 沿 code 轴、ts_rank 沿日期轴——同一数据两种口径互不混淆。"""
    close = _frame(
        {
            "A": [1.0, 3.0, 2.0],
            "B": [3.0, 1.0, 3.0],
            "C": [2.0, 2.0, 1.0],
        }
    )
    # 逐日横截面 pct 排名（3 个 code）
    expected_rank = _frame(
        {
            "A": [1.0 / 3.0, 1.0, 2.0 / 3.0],
            "B": [1.0, 1.0 / 3.0, 1.0],
            "C": [2.0 / 3.0, 2.0 / 3.0, 1.0 / 3.0],
        }
    )
    # 逐 code 沿日期：仅第 3 日满窗；窗口 [1,3,2] 末值 2 → 秩 2/3；
    # 窗口 [3,1,3] 末值 3 与该窗另一 3 并列 → 平均秩 2.5 → 2.5/3；窗口 [2,2,1] 末值 1 → 1/3
    expected_ts_rank = _frame(
        {
            "A": [_NAN, _NAN, 2.0 / 3.0],
            "B": [_NAN, _NAN, 2.5 / 3.0],
            "C": [_NAN, _NAN, 1.0 / 3.0],
        }
    )
    panel = _panel(close=close)
    _assert_frame(_eval("rank(close)", panel), expected_rank)
    _assert_frame(_eval("ts_rank(close,3)", panel), expected_ts_rank)


# ── 逐元素算子 ──────────────────────────────────────────────────────────────


def test_arithmetic_operators_elementwise():
    """add/sub/mul 逐元素（NaN 传播）。"""
    close = _frame({"A": [1.0, 2.0, _NAN]})
    volume = _frame({"A": [10.0, 20.0, 30.0]})
    panel = _panel(close=close, volume=volume)
    _assert_frame(_eval("add(close,volume)", panel), _frame({"A": [11.0, 22.0, _NAN]}))
    _assert_frame(_eval("sub(volume,close)", panel), _frame({"A": [9.0, 18.0, _NAN]}))
    _assert_frame(_eval("mul(close,volume)", panel), _frame({"A": [10.0, 40.0, _NAN]}))


def test_div_zero_denominator_is_nan():
    """div：分母 0 → NaN（含 0/0 与 x/0，不产生 ±inf、不抛错）。"""
    close = _frame({"A": [4.0, 0.0, 6.0]})
    volume = _frame({"A": [2.0, 0.0, 0.0]})
    expected = _frame({"A": [2.0, _NAN, _NAN]})
    _assert_frame(_eval("div(close,volume)", _panel(close=close, volume=volume)), expected)


def test_max_min_elementwise_with_nan_propagation():
    """max/min 逐元素；任一侧 NaN → NaN。"""
    close = _frame({"A": [1.0, _NAN, 3.0]})
    volume = _frame({"A": [2.0, 5.0, _NAN]})
    panel = _panel(close=close, volume=volume)
    _assert_frame(_eval("max(close,volume)", panel), _frame({"A": [2.0, _NAN, _NAN]}))
    _assert_frame(_eval("min(close,volume)", panel), _frame({"A": [1.0, _NAN, _NAN]}))


def test_abs_and_log_semantics():
    """abs 取绝对值；log 非正 → NaN（自然对数）。"""
    close = _frame({"A": [-1.5, 2.0, _NAN]})
    _assert_frame(_eval("abs(close)", _panel(close=close)), _frame({"A": [1.5, 2.0, _NAN]}))
    volume = _frame({"A": [1.0, 2.718281828459045, 0.0, -1.0, _NAN]})
    expected = _frame({"A": [0.0, 1.0, _NAN, _NAN, _NAN]})
    _assert_frame(_eval("log(volume)", _panel(volume=volume)), expected)


def test_nested_expression_matches_manual_calculation():
    """嵌套表达式：sub(div(close,ts_mean(close,3)),1) 手算对拍。"""
    close = _frame({"A": [10.0, 11.0, 12.0, 12.0]})
    expected = _frame(
        {
            "A": [_NAN, _NAN, 12.0 / 11.0 - 1.0, 12.0 / (35.0 / 3.0) - 1.0],
        }
    )
    _assert_frame(_eval("sub(div(close,ts_mean(close,3)),1)", _panel(close=close)), expected)


def test_div_with_number_leaf():
    """Number 叶子与字段混合运算（常量帧参与对齐）。"""
    close = _frame({"A": [3.0, 5.0], "B": [7.0, 9.0]})
    expected = _frame({"A": [1.5, 2.5], "B": [3.5, 4.5]})
    _assert_frame(_eval("div(close,2)", _panel(close=close)), expected)


# ── build_panel_from_rows（market_snapshot 行 → 面板） ──────────────────────


def test_build_panel_from_rows_qfq_conversion():
    """价格字段前复权换算：open/high/low × (close_qfq/close_raw)，close 取 close_qfq。"""
    rows = pd.DataFrame(
        [
            {
                "trade_date": "2026-01-06",
                "code": "600000",
                "open": 10.5,
                "high": 11.0,
                "low": 9.5,
                "close_raw": 10.0,
                "close_qfq": 8.0,
                "volume": 100.0,
                "amount": 1000.0,
            },
            {
                "trade_date": "2026-01-05",
                "code": "600000",
                "open": 9.0,
                "high": 9.6,
                "low": 8.8,
                "close_raw": 9.6,
                "close_qfq": 9.6,
                "volume": 90.0,
                "amount": 900.0,
            },
            {
                "trade_date": "2026-01-05",
                "code": "000001",
                "open": 20.0,
                "high": 21.0,
                "low": 19.0,
                "close_raw": 20.0,
                "close_qfq": 15.0,
                "volume": 200.0,
                "amount": 3000.0,
            },
        ]
    )
    panel = build_panel_from_rows(rows)

    assert sorted(panel.fields) == ["amount", "close", "high", "low", "open", "volume"]
    dates = ["2026-01-05", "2026-01-06"]
    # 2026-01-06 的 000001 无行 → 全字段 NaN（面板缺格不参与）
    expected_close = _frame({"000001": [15.0, _NAN], "600000": [9.6, 8.0]}, dates)
    _assert_frame(panel.fields["close"], expected_close)
    expected_open = _frame(
        {"000001": [20.0 * 0.75, _NAN], "600000": [9.0 * 1.0, 10.5 * 0.8]}, dates
    )
    _assert_frame(panel.fields["open"], expected_open)
    expected_high = _frame({"000001": [21.0 * 0.75, _NAN], "600000": [9.6, 11.0 * 0.8]}, dates)
    _assert_frame(panel.fields["high"], expected_high)
    expected_low = _frame({"000001": [19.0 * 0.75, _NAN], "600000": [8.8, 9.5 * 0.8]}, dates)
    _assert_frame(panel.fields["low"], expected_low)
    expected_volume = _frame({"000001": [200.0, _NAN], "600000": [90.0, 100.0]}, dates)
    _assert_frame(panel.fields["volume"], expected_volume)
    expected_amount = _frame({"000001": [3000.0, _NAN], "600000": [900.0, 1000.0]}, dates)
    _assert_frame(panel.fields["amount"], expected_amount)


def test_build_panel_from_rows_missing_column_raises():
    """缺必需列 → ValueError（逐字列出缺列）。"""
    rows = pd.DataFrame([{"trade_date": "2026-01-05", "code": "600000", "close_qfq": 8.0}])
    with pytest.raises(ValueError, match="close_raw"):
        build_panel_from_rows(rows)


def test_build_panel_from_rows_zero_raw_price_gives_nan_prices():
    """close_raw=0（缺价）→ 复权因子 NaN → 价格字段 NaN；volume/amount 原样保留。"""
    rows = pd.DataFrame(
        [
            {
                "trade_date": "2026-01-05",
                "code": "600000",
                "open": 0.0,
                "high": 0.0,
                "low": 0.0,
                "close_raw": 0.0,
                "close_qfq": 0.0,
                "volume": 0.0,
                "amount": 0.0,
            }
        ]
    )
    panel = build_panel_from_rows(rows)
    dates = ["2026-01-05"]
    _assert_frame(panel.fields["open"], _frame({"600000": [_NAN]}, dates))
    _assert_frame(panel.fields["close"], _frame({"600000": [0.0]}, dates))
    _assert_frame(panel.fields["volume"], _frame({"600000": [0.0]}, dates))
    _assert_frame(panel.fields["amount"], _frame({"600000": [0.0]}, dates))


# ── forward_returns / rank_ic_series（E2） ──────────────────────────────────


def test_forward_returns_horizon_one_and_two():
    """forward_returns = close.shift(-h)/close − 1；末 h 日为 NaN。"""
    close = _frame({"A": [100.0, 110.0, 99.0, 108.9]})
    expected_one = _frame({"A": [0.1, -0.1, 0.1, _NAN]})
    expected_two = _frame({"A": [-0.01, -0.01, _NAN, _NAN]})
    _assert_frame(forward_returns(close), expected_one)
    _assert_frame(forward_returns(close, horizon=2), expected_two)


def test_forward_returns_rejects_non_positive_horizon():
    """horizon 必须为正整数（0 / 负数 → ValueError）。"""
    close = _frame({"A": [1.0, 2.0]})
    with pytest.raises(ValueError, match="horizon"):
        forward_returns(close, horizon=0)


def test_rank_ic_series_hand_computed_spearman():
    """逐日 Spearman = 因子值秩与远期收益秩的 Pearson（手算对拍）。"""
    factor = _frame({"A": [1.0], "B": [2.0], "C": [3.0], "D": [10.0]})
    fwd = _frame({"A": [0.02], "B": [0.01], "C": [0.03], "D": [0.1]})
    ic = rank_ic_series(factor, fwd, min_samples=4)
    assert isinstance(ic, pd.Series)
    assert list(ic.index) == ["2026-01-01"]
    assert ic.iloc[0] == pytest.approx(0.8, rel=1e-12)


def test_rank_ic_series_average_ranks_for_ties():
    """并列值取平均秩（[1,1,3] vs [1,2,3] → 1.5/√3）。"""
    factor = _frame({"A": [1.0], "B": [1.0], "C": [3.0]})
    fwd = _frame({"A": [1.0], "B": [2.0], "C": [3.0]})
    ic = rank_ic_series(factor, fwd, min_samples=3)
    assert ic.iloc[0] == pytest.approx(1.5 / np.sqrt(3.0), rel=1e-12)


def test_rank_ic_series_min_samples_gate_and_nan_pairs():
    """有效对不足 min_samples → 该日 NaN；NaN 格不参与样本计数。"""
    factor = _frame({"A": [1.0, 5.0], "B": [2.0, 5.0], "C": [_NAN, 1.0], "D": [4.0, 9.0]})
    fwd = _frame({"A": [0.1, 0.4], "B": [0.2, 0.3], "C": [0.3, 0.2], "D": [0.4, 0.1]})
    # 第 1 日因子 NaN 格（C）从秩计算中剔除后两帧秩皆为 [1,2,3] → 1.0；
    # 第 2 日：因子秩 [2.5,2.5,1,4]、收益秩 [4,3,2,1] →
    # ρ = -1.5 / √(4.5 × 5)（并列取平均秩）
    day_two = -1.5 / np.sqrt(22.5)
    strict = rank_ic_series(factor, fwd, min_samples=4)
    assert strict.iloc[0] != strict.iloc[0]  # 第 1 日仅 3 个有效对 → NaN
    assert strict.iloc[1] == pytest.approx(day_two, rel=1e-12)
    loose = rank_ic_series(factor, fwd, min_samples=3)
    assert loose.iloc[0] == pytest.approx(1.0, rel=1e-12)
    assert loose.iloc[1] == pytest.approx(day_two, rel=1e-12)


def test_rank_ic_series_default_min_samples_is_30():
    """默认门槛 MIN_IC_SAMPLES=30：30 个 code 可算，29 个 → NaN。"""
    codes30 = [f"c{i:02d}" for i in range(30)]
    values = np.arange(30, dtype="float64")
    factor = pd.DataFrame([values], index=["2026-01-01"], columns=codes30)
    fwd = pd.DataFrame([values[::-1]], index=["2026-01-01"], columns=codes30)
    ic = rank_ic_series(factor, fwd)
    assert ic.iloc[0] == pytest.approx(-1.0, rel=1e-12)

    codes29 = codes30[:29]
    factor29 = factor[codes29]
    fwd29 = fwd[codes29]
    ic29 = rank_ic_series(factor29, fwd29)
    assert ic29.iloc[0] != ic29.iloc[0]  # 29 < 30 → NaN


def test_rank_ic_series_only_common_dates():
    """IC 序列仅取两帧共同日期（索引交集，升序）。"""
    factor = pd.DataFrame({"A": [1.0, 2.0], "B": [2.0, 1.0]}, index=["2026-01-01", "2026-01-02"])
    # 同日之外的日期一侧缺行（01-01 只有因子、01-03 只有收益）→ 不产出 IC
    fwd = pd.DataFrame({"A": [2.0, _NAN], "B": [1.0, _NAN]}, index=["2026-01-02", "2026-01-03"])
    ic = rank_ic_series(factor, fwd, min_samples=2)
    assert list(ic.index) == ["2026-01-02"]
    # 01-02：因子 [2,1] vs 收益 [2,1] → Spearman = 1.0
    assert ic.iloc[0] == pytest.approx(1.0, rel=1e-12)


def test_rank_ic_series_constant_factor_is_nan():
    """因子值横截面恒定 → 秩方差 0 → 相关为 NaN。"""
    factor = _frame({"A": [3.0], "B": [3.0], "C": [3.0]})
    fwd = _frame({"A": [1.0], "B": [2.0], "C": [3.0]})
    ic = rank_ic_series(factor, fwd, min_samples=3)
    assert ic.iloc[0] != ic.iloc[0]


def test_rank_ic_series_empty_intersection_returns_empty_series():
    """两帧无共同日期 → 空浮点序列（不抛错）。"""
    factor = _frame({"A": [1.0]})
    fwd = _frame({"A": [1.0]})
    fwd.index = pd.Index(["2027-01-01"])
    ic = rank_ic_series(factor, fwd, min_samples=1)
    assert ic.empty
    assert str(ic.dtype) == "float64"
