"""execution/modes.py 三模式数据源与 match_for_mode 分派单测（Task 3）。

覆盖：backtest 源从 hfq K 线行构造 bar（prev_close = 上一交易日收盘、sealed 位
按 M2、成交额列可选、关键价缺失 → None）；paper 源从预建 MarketBar 表按
code→日期取；缺数据 → None；live 全方法 NotImplementedError；未知 mode →
ValueError；跨模式一致性（spec「单一撮合实现」场景：同一份数值喂两源、同单同
配置 → FillResult 逐字段相等）。

``match_for_mode`` 的 ``trade_date`` 为 keyword-only 增补参数（计划签名无执行日
参数，无法经 ``source.bar(code, trade_date)`` 定位 bar；原参数逐个保留）。
"""

from __future__ import annotations

import dataclasses

import pandas as pd
import pytest

from finance_agent.execution.matching import (
    MarketBar,
    MatchingConfig,
    Order,
    PositionContext,
    SlippageTier,
)
from finance_agent.execution.modes import (
    BacktestDataSource,
    LiveDataSource,
    MarketDataSource,
    PaperDataSource,
    match_for_mode,
)

_CODE = "600519"
_OTHER_CODE = "000001"

_CONFIG = MatchingConfig(
    commission_rate=0.00025,
    min_commission=5.0,
    stamp_tax_rate=0.0005,
    slippage_tiers=(
        SlippageTier(quantile_max=0.2, slippage_rate=0.002),
        SlippageTier(quantile_max=0.6, slippage_rate=0.001),
        SlippageTier(quantile_max=1.0, slippage_rate=0.0005),
    ),
)

# 10 个全市场成交额样本：amount 的分位 = count(样本 <= amount) / 10
_MARKET_AMOUNTS = (100.0, 200.0, 300.0, 400.0, 500.0, 600.0, 700.0, 800.0, 900.0, 1000.0)


def _kline() -> pd.DataFrame:
    """三行 hfq K 线（升序）：09-01 普通日、09-02 一字涨停（+7.8% ≥ 4.8 阈值）、09-03 普通日。"""
    return pd.DataFrame(
        {
            "日期": ["2026-09-01", "2026-09-02", "2026-09-03"],
            "开盘": [10.0, 11.0, 11.5],
            "最高": [10.4, 11.0, 11.8],
            "最低": [9.9, 11.0, 11.2],
            "收盘": [10.2, 11.0, 11.6],
            "成交额": [1000.0, 500.0, 900.0],
        }
    )


def _other_kline() -> pd.DataFrame:
    """另一标的的单行 K 线（用于 next_trading_day 并集日历）。"""
    return pd.DataFrame(
        {
            "日期": ["2026-09-04"],
            "开盘": [3.0],
            "最高": [3.1],
            "最低": [2.9],
            "收盘": [3.05],
            "成交额": [700.0],
        }
    )


def _paper_bar(
    *,
    trade_date: str = "2026-09-03",
    open_: float = 11.5,
    high: float = 11.8,
    low: float = 11.2,
    close: float = 11.6,
    prev_close: float | None = 11.0,
    amount: float | None = 900.0,
    limit_up_sealed: bool = False,
    limit_down_sealed: bool = False,
) -> MarketBar:
    """按快照/盘面价预建的 paper bar（数值与 backtest 源同源可比）。"""
    return MarketBar(
        trade_date=trade_date,
        open=open_,
        high=high,
        low=low,
        close=close,
        prev_close=prev_close,
        amount=amount,
        limit_up_sealed=limit_up_sealed,
        limit_down_sealed=limit_down_sealed,
    )


# ── BacktestDataSource ───────────────────────────────────────────────────────


def test_backtest_bar_built_from_kline_row():
    """K 线行 → bar：OHLC/成交额直取，prev_close = 区间上一行收盘，普通日非板。"""
    bar = BacktestDataSource({_CODE: _kline()}).bar(_CODE, "2026-09-03")
    assert bar == MarketBar(
        trade_date="2026-09-03",
        open=11.5,
        high=11.8,
        low=11.2,
        close=11.6,
        prev_close=11.0,
        amount=900.0,
        limit_up_sealed=False,
        limit_down_sealed=False,
    )


def test_backtest_bar_sets_sealed_flag_for_one_price_limit_up():
    """一字涨停行（开=高=低=收 且 ≥ 阈值）→ limit_up_sealed=True（M2）。"""
    bar = BacktestDataSource({_CODE: _kline()}).bar(_CODE, "2026-09-02")
    assert bar is not None
    assert bar.limit_up_sealed is True
    assert bar.limit_down_sealed is False
    assert bar.prev_close == 10.2  # 判定所用昨收 = 09-01 收盘


def test_backtest_bar_first_row_has_no_prev_close():
    """区间首行无上一行 → prev_close=None、非板（不编造昨收）。"""
    bar = BacktestDataSource({_CODE: _kline()}).bar(_CODE, "2026-09-01")
    assert bar is not None
    assert bar.prev_close is None
    assert (bar.limit_up_sealed, bar.limit_down_sealed) == (False, False)


def test_backtest_source_normalizes_unsorted_frames():
    """输入乱序 K 线 → 按日期升序归一，prev_close 取升序后上一行（不依赖输入行序）。"""
    shuffled = _kline().iloc[[2, 0, 1]].reset_index(drop=True)
    bar = BacktestDataSource({_CODE: shuffled}).bar(_CODE, "2026-09-03")
    assert bar is not None
    assert bar.prev_close == 11.0  # 升序后上一行 = 09-02 收盘


def test_backtest_bar_missing_code_or_date_returns_none():
    """未登记代码 / 该日无行 → None。"""
    source = BacktestDataSource({_CODE: _kline()})
    assert source.bar(_OTHER_CODE, "2026-09-03") is None
    assert source.bar(_CODE, "2026-09-04") is None


def test_backtest_bar_missing_price_returns_none():
    """行内关键价缺失（NaN）→ None（不编造价格）。"""
    frame = _kline()
    frame.loc[2, "开盘"] = float("nan")
    assert BacktestDataSource({_CODE: frame}).bar(_CODE, "2026-09-03") is None


def test_backtest_next_trading_day_uses_union_calendar():
    """next_trading_day 无 code 参数 → 全部标的日期并集（交易日历）中 > 该日的首日。"""
    source = BacktestDataSource({_CODE: _kline(), _OTHER_CODE: _other_kline()})
    assert source.next_trading_day("2026-08-01") == "2026-09-01"
    assert source.next_trading_day("2026-09-01") == "2026-09-02"
    assert source.next_trading_day("2026-09-03") == "2026-09-04"  # 并集：他标的晚一日
    assert source.next_trading_day("2026-09-04") is None


def test_backtest_source_mode_and_protocol_shape():
    """模式标签固定 backtest；结构上满足 MarketDataSource 协议（mypy 钉形状）。"""
    source: MarketDataSource = BacktestDataSource({_CODE: _kline()})
    assert source.mode == "backtest"


# ── PaperDataSource ──────────────────────────────────────────────────────────


def test_paper_bar_lookup_by_code_and_date():
    """预建 MarketBar 表按 code→日期直取（真实盘面价，原对象返回）。"""
    bar = _paper_bar()
    source = PaperDataSource({_CODE: {"2026-09-03": bar}})
    assert source.bar(_CODE, "2026-09-03") is bar
    assert source.mode == "paper"


def test_paper_bar_missing_code_or_date_returns_none():
    """未登记代码 / 该日无 bar → None。"""
    source = PaperDataSource({_CODE: {"2026-09-03": _paper_bar()}})
    assert source.bar(_OTHER_CODE, "2026-09-03") is None
    assert source.bar(_CODE, "2026-09-04") is None


def test_paper_next_trading_day_uses_union_calendar():
    """next_trading_day = 全部已登记日期的并集中 > 该日的首日；无 → None。"""
    source = PaperDataSource(
        {
            _CODE: {"2026-09-03": _paper_bar()},
            _OTHER_CODE: {"2026-09-04": _paper_bar(trade_date="2026-09-04")},
        }
    )
    assert source.next_trading_day("2026-09-01") == "2026-09-03"
    assert source.next_trading_day("2026-09-03") == "2026-09-04"
    assert source.next_trading_day("2026-09-04") is None


# ── LiveDataSource（仅留抽象）────────────────────────────────────────────────


def test_live_source_all_methods_not_implemented():
    """实盘源全方法 NotImplementedError（逐字消息），mode 标签为 live。"""
    source = LiveDataSource()
    assert source.mode == "live"
    with pytest.raises(NotImplementedError, match="实盘接口未实现"):
        source.bar(_CODE, "2026-09-03")
    with pytest.raises(NotImplementedError, match="实盘接口未实现"):
        source.next_trading_day("2026-09-03")


def test_match_for_mode_live_raises_not_implemented():
    """match_for_mode("live") → NotImplementedError（不落任何撮合）。"""
    with pytest.raises(NotImplementedError, match="实盘接口未实现"):
        match_for_mode(
            "live",
            Order(code=_CODE, side="buy", qty=100),
            source=LiveDataSource(),
            config=_CONFIG,
            trade_date="2026-09-03",
        )


# ── match_for_mode 分派 ──────────────────────────────────────────────────────


def test_match_for_mode_unknown_mode_raises_value_error():
    """未知 mode → ValueError（写错模式不得静默按某数据源撮合）。"""
    with pytest.raises(ValueError):
        match_for_mode(
            "intraday",
            Order(code=_CODE, side="buy", qty=100),
            source=BacktestDataSource({_CODE: _kline()}),
            config=_CONFIG,
            trade_date="2026-09-03",
        )


def test_match_for_mode_requires_trade_date():
    """keyword-only 增补参数 trade_date 缺失 → ValueError（fail loud，不猜执行日）。"""
    with pytest.raises(ValueError):
        match_for_mode(
            "backtest",
            Order(code=_CODE, side="buy", qty=100),
            source=BacktestDataSource({_CODE: _kline()}),
            config=_CONFIG,
        )


def test_match_for_mode_missing_bar_raises_value_error():
    """数据源无该标的该日 bar → ValueError（数据缺失不是业务原因码）。"""
    with pytest.raises(ValueError):
        match_for_mode(
            "paper",
            Order(code=_CODE, side="buy", qty=100),
            source=PaperDataSource({}),
            config=_CONFIG,
            trade_date="2026-09-03",
        )


def test_match_for_mode_delegates_position_and_market_amounts():
    """position / market_amounts 透传同一 match_order：拦截与成交价档位一致。"""
    source = PaperDataSource({_CODE: {"2026-09-03": _paper_bar()}})
    blocked = match_for_mode(
        "paper",
        Order(code=_CODE, side="sell", qty=100),
        source=source,
        config=_CONFIG,
        trade_date="2026-09-03",
        position=PositionContext(sellable_qty=0, total_qty=100),
        market_amounts=_MARKET_AMOUNTS,
    )
    assert blocked.reason == "T1_SELL_BLOCKED"
    assert blocked.filled_qty == 0

    filled = match_for_mode(
        "paper",
        Order(code=_CODE, side="sell", qty=100),
        source=source,
        config=_CONFIG,
        trade_date="2026-09-03",
        position=PositionContext(sellable_qty=100, total_qty=100),
        market_amounts=_MARKET_AMOUNTS,
    )
    # amount=900 → 分位 0.9 → 末档 0.0005；卖价 = round(11.5 × (1 − 0.0005), 2) = 11.49
    assert filled.filled_qty == 100
    assert filled.slippage_rate == 0.0005
    assert filled.fill_price == 11.49


# ── 跨模式一致性（spec「单一撮合实现」场景）──────────────────────────────────


def test_cross_mode_consistency_same_numbers_same_fill_result():
    """同一份数值喂 backtest/paper 两源 → 同 Order 同 config → FillResult 逐字段相等。"""
    trade_date = "2026-09-03"
    backtest = BacktestDataSource({_CODE: _kline()})
    paper = PaperDataSource(
        {
            _CODE: {
                trade_date: _paper_bar(
                    trade_date=trade_date,
                    open_=11.5,
                    high=11.8,
                    low=11.2,
                    close=11.6,
                    prev_close=11.0,
                    amount=900.0,
                )
            }
        }
    )
    order = Order(code=_CODE, side="buy", qty=1000)

    result_backtest = match_for_mode(
        "backtest",
        order,
        source=backtest,
        config=_CONFIG,
        trade_date=trade_date,
        market_amounts=_MARKET_AMOUNTS,
    )
    result_paper = match_for_mode(
        "paper",
        order,
        source=paper,
        config=_CONFIG,
        trade_date=trade_date,
        market_amounts=_MARKET_AMOUNTS,
    )

    # 先钉住确实成交且价格正确（防「同错」式一致）
    assert result_backtest.filled_qty == 1000
    assert result_backtest.fill_price == round(11.5 * (1 + 0.0005), 2) == 11.51
    # 两源 bar 逐字段相等（同源数值）
    assert dataclasses.astuple(backtest.bar(_CODE, trade_date)) == dataclasses.astuple(
        paper.bar(_CODE, trade_date)
    )
    # 成交结果逐字段相等（spec：成交价、成交量、费用完全一致）
    assert dataclasses.astuple(result_backtest) == dataclasses.astuple(result_paper)
