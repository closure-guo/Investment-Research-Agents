"""toolize-price-levels Task 1.1/3.3：价位预算工具 + 派生值表 测试（TDD 先行）。"""

import math

import pandas as pd
import pytest

from finance_agent.metrics.levels import calc_price_levels
from finance_agent.metrics.technical import calc_derived_series


def _kline(n=80, base=100.0, trend=0.0):
    """构造确定性 K 线：日期递增、收盘线性、最高=收盘+1、最低=收盘-1。"""
    rows = []
    for i in range(n):
        close = base + trend * i
        rows.append(
            {
                "日期": f"2026-{(i // 28) + 1:02d}-{(i % 28) + 1:02d}",
                "开盘": close - 0.5,
                "收盘": close,
                "最高": close + 1.0,
                "最低": close - 1.0,
                "成交量": 1000,
            }
        )
    return pd.DataFrame(rows)


class TestCalcPriceLevels:
    def test_available_with_full_fields(self):
        levels = calc_price_levels(_kline(80, base=100.0))
        assert levels["available"] is True
        assert levels["entry_ref"] == pytest.approx(100.0 + 79 * 0.0)  # trend=0 → 100
        assert levels["recent_high"] == pytest.approx(101.0)
        assert levels["recent_low"] == pytest.approx(99.0)
        assert levels["atr"] > 0
        # 止损带（long）：[close-2ATR, close-1ATR]，目标带：[close+2ATR, close+4ATR]
        assert levels["stop_band_long"]["low"] < levels["stop_band_long"]["high"] < 100.0
        assert 100.0 < levels["target_band_long"]["low"] < levels["target_band_long"]["high"]
        # 放宽带（sanity 用）：[recent_low-2ATR, recent_high+2ATR]
        assert levels["full_band"][0] < levels["recent_low"]
        assert levels["full_band"][1] > levels["recent_high"]

    def test_atr_deterministic(self):
        # 等宽 K 线（最高-最低=2，无跳空）：TR 恒为 2（首行除外取 high-low）
        levels = calc_price_levels(_kline(80))
        assert levels["atr"] == pytest.approx(2.0, abs=1e-6)

    def test_insufficient_data(self):
        levels = calc_price_levels(_kline(5))
        assert levels["available"] is False
        assert "insufficient" in levels["reason"]

    def test_none_kline(self):
        assert calc_price_levels(None)["available"] is False


class TestCalcDerivedSeries:
    def test_window_changes(self):
        k = _kline(80, base=100.0, trend=1.0)  # 每日 +1，收盘 100→179
        d = calc_derived_series(k)
        # 5 日涨跌幅：(179-174)/174
        assert d["chg_5d"] == pytest.approx((179.0 - 174.0) / 174.0, abs=1e-4)
        assert d["chg_20d"] > d["chg_5d"]
        assert d["chg_60d"] > d["chg_20d"]

    def test_drawdown_and_rebound(self):
        # 单调上涨：距高点回撤=0（最新即最高），距低点反弹>0
        k = _kline(80, base=100.0, trend=1.0)
        d = calc_derived_series(k)
        assert d["drawdown_from_high_250d"] == pytest.approx(0.0, abs=1e-6)
        assert d["rebound_from_low_250d"] > 0

    def test_insufficient_windows_none(self):
        d = calc_derived_series(_kline(10))
        assert d["chg_20d"] is None
        assert d["chg_60d"] is None
        assert d["chg_5d"] is not None

    def test_ma_spread_fields(self):
        # trend=1.0：收盘 100→179；MA5 末值=177.0，MA20=169.5，MA60=149.5
        # 精确断言（非 approx）：钉死 round 2 位契约，删除 round 或换舍入模式即红
        d = calc_derived_series(_kline(80, base=100.0, trend=1.0))
        assert d["ma_spread_5_20_pct"] == 4.42
        assert d["ma_spread_20_60_pct"] == 13.38
        assert d["close_vs_ma20_pct"] == 5.60
        assert d["close_vs_ma60_pct"] == 19.73

    def test_ma_spreads_match_technical_ma(self):
        # 与 calc_technical 的均线口径必须同源（同一份 _calc_ma）
        from finance_agent.metrics.technical import calc_technical

        k = _kline(80, base=100.0, trend=1.0)
        d = calc_derived_series(k)
        ma = calc_technical(k)["MA"]
        ma5, ma20 = ma["5"][-1], ma["20"][-1]
        assert d["ma_spread_5_20_pct"] == pytest.approx((ma5 - ma20) / ma20 * 100, abs=0.01)

    def test_ma60_spreads_none_when_short(self):
        d = calc_derived_series(_kline(30))
        assert d["ma_spread_20_60_pct"] is None
        assert d["close_vs_ma60_pct"] is None
        assert d["ma_spread_5_20_pct"] is not None

    def test_ma_spreads_none_kline(self):
        d = calc_derived_series(None)
        assert d["ma_spread_5_20_pct"] is None
        assert d["close_vs_ma60_pct"] is None

    def test_ma_spreads_none_when_denominator_zero(self):
        # 全零收盘：均线恒为 0，分母为 0 → 四项 None（不得抛除零异常 / 不得伪造数值）
        d = calc_derived_series(_kline(80, base=0.0, trend=0.0))
        assert d["ma_spread_5_20_pct"] is None
        assert d["ma_spread_20_60_pct"] is None
        assert d["close_vs_ma20_pct"] is None
        assert d["close_vs_ma60_pct"] is None

    def test_ma60_computable_at_exact_window(self):
        # n == 60 恰在下界内：MA60 可算（区别于 n=30 的缺失分支）
        d = calc_derived_series(_kline(60))
        assert d["ma_spread_20_60_pct"] is not None

    def test_ma_spreads_empty_dataframe(self):
        # 空表（非 None）：走同一缺失分支，四键齐全且为 None
        d = calc_derived_series(pd.DataFrame())
        assert d["ma_spread_5_20_pct"] is None
        assert d["ma_spread_20_60_pct"] is None
        assert d["close_vs_ma20_pct"] is None
        assert d["close_vs_ma60_pct"] is None

    def test_ma_spread_negative_zero_normalized(self):
        # 差幅为极小负值（round 2 后归零）：不得返回 -0.0（context JSON 会渲染 "-0.0"）
        k = _kline(25)
        k.loc[k.index[-1], "收盘"] = 99.99
        d = calc_derived_series(k)
        assert d["ma_spread_5_20_pct"] == 0.0
        assert math.copysign(1.0, d["ma_spread_5_20_pct"]) > 0


class TestSearchStockSnapshotContract:
    """quick search_stock 结果快照契约由 agent_factory 测试覆盖（test_toolize_quick.py），
    此处仅占位说明归属，避免 metrics 测试越界。"""
