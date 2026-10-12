"""execution/matching.py 撮合引擎单测（add-matching-engine Task 1–2）。

计划 docs/superpowers/plans/2026-10-13-add-matching-engine.md 关键设计决策：
- M1 成交价基准：买单 open×(1+滑点)；卖单 open×(1−滑点)，round(…, 2)；
- M2 一字板判定：开=高=低=收 且涨跌幅达 ±阈值（阈值覆盖 ST 5%，与
  data/snapshot.py D3 的 4.8 规则同源）；
- M3 校验顺序：① ODD_LOT（卖零股全卖例外）→ ② 涨跌停 → ③ T+1；
  qty<=0 是调用方编程错误（ValueError），不是业务原因码；
- M4 滑点分档：按成交额在全市场的分位取档（market_amounts 缺失 → 最保守档）；
- M5 费用：佣金 = max(最低佣金, 成交额×佣金率)（买卖双向）+ 印花税（仅卖出）。

本文件数值断言全部独立手算（不复用被测函数）。
"""

from __future__ import annotations

import pytest

from finance_agent.execution.matching import (
    FillResult,
    MarketBar,
    MatchingConfig,
    Order,
    PositionContext,
    SlippageTier,
    match_order,
    sealed_flags_from_ohlc,
)

# 与 config/matching_engine.json 逐字一致的三档滑点（低流动性 → 高流动性）
_TIERS = (
    SlippageTier(quantile_max=0.2, slippage_rate=0.002),
    SlippageTier(quantile_max=0.6, slippage_rate=0.001),
    SlippageTier(quantile_max=1.0, slippage_rate=0.0005),
)
_CONFIG = MatchingConfig(
    commission_rate=0.00025,
    min_commission=5.0,
    stamp_tax_rate=0.0005,
    slippage_tiers=_TIERS,
)

# 10 个全市场成交额样本：amount 的分位 = count(样本 <= amount) / 10
_MARKET_AMOUNTS = (100.0, 200.0, 300.0, 400.0, 500.0, 600.0, 700.0, 800.0, 900.0, 1000.0)


def _make_bar(
    *,
    open_: float = 100.0,
    amount: float | None = 500.0,
    limit_up_sealed: bool = False,
    limit_down_sealed: bool = False,
) -> MarketBar:
    """构造当日 bar 载体：引擎只用 open/amount/sealed 位，其余字段填同值不参与判定。"""
    return MarketBar(
        trade_date="2026-09-01",
        open=open_,
        high=open_,
        low=open_,
        close=open_,
        prev_close=open_,
        amount=amount,
        limit_up_sealed=limit_up_sealed,
        limit_down_sealed=limit_down_sealed,
    )


# ── 涨跌停约束（M3-②）：正反各例 ─────────────────────────────────────────────


def test_limit_up_sealed_buy_unfillable():
    """一字涨停买单不成交：量 0、价 None、费 0、原因码逐字 LIMIT_UP_UNFILLABLE。"""
    result = match_order(
        Order(code="600519", side="buy", qty=100),
        _make_bar(open_=11.0, limit_up_sealed=True),
        config=_CONFIG,
        market_amounts=_MARKET_AMOUNTS,
    )
    assert result == FillResult(
        filled_qty=0,
        fill_price=None,
        fee=0.0,
        reason="LIMIT_UP_UNFILLABLE",
        slippage_rate=0.0,
    )


def test_limit_up_sealed_sell_still_fills():
    """一字涨停卖单不因涨停被阻断（有买盘可成交）：成交价 = round(100×(1−0.001), 2)。"""
    result = match_order(
        Order(code="600519", side="sell", qty=100),
        _make_bar(open_=100.0, limit_up_sealed=True),
        config=_CONFIG,
        position=PositionContext(sellable_qty=100, total_qty=100),
        market_amounts=_MARKET_AMOUNTS,
    )
    assert result.filled_qty == 100
    assert result.fill_price == 99.9
    assert result.reason is None
    assert result.slippage_rate == 0.001


def test_limit_down_sealed_sell_unfillable():
    """一字跌停卖单不成交，原因码逐字 LIMIT_DOWN_UNFILLABLE。"""
    result = match_order(
        Order(code="600519", side="sell", qty=100),
        _make_bar(open_=9.0, limit_down_sealed=True),
        config=_CONFIG,
        position=PositionContext(sellable_qty=100, total_qty=100),
        market_amounts=_MARKET_AMOUNTS,
    )
    assert result == FillResult(
        filled_qty=0,
        fill_price=None,
        fee=0.0,
        reason="LIMIT_DOWN_UNFILLABLE",
        slippage_rate=0.0,
    )


def test_limit_down_sealed_buy_still_fills():
    """一字跌停买单可成交（卖盘充足）：成交价 = round(9×(1+0.001), 2) = 9.01。"""
    result = match_order(
        Order(code="600519", side="buy", qty=100),
        _make_bar(open_=9.0, limit_down_sealed=True),
        config=_CONFIG,
        market_amounts=_MARKET_AMOUNTS,
    )
    assert result.filled_qty == 100
    assert result.fill_price == 9.01
    assert result.reason is None


# ── 成交价与滑点分档（M1/M4）────────────────────────────────────────────────


def test_fill_price_uses_market_amount_quantile_tier():
    """成交价按成交额全市场分位取档：0.2→0.002、0.5→0.001、0.9→0.0005。"""
    low = match_order(
        Order(code="600519", side="buy", qty=100),
        _make_bar(open_=100.0, amount=200.0),
        config=_CONFIG,
        market_amounts=_MARKET_AMOUNTS,
    )
    # round(100.0 × (1 + 0.002), 2) = 100.2
    assert low.fill_price == 100.2
    assert low.slippage_rate == 0.002

    mid = match_order(
        Order(code="600519", side="buy", qty=100),
        _make_bar(open_=100.0, amount=500.0),
        config=_CONFIG,
        market_amounts=_MARKET_AMOUNTS,
    )
    # round(100.0 × (1 + 0.001), 2) = 100.1
    assert mid.fill_price == 100.1
    assert mid.slippage_rate == 0.001

    high = match_order(
        Order(code="600519", side="buy", qty=100),
        _make_bar(open_=100.0, amount=900.0),
        config=_CONFIG,
        market_amounts=_MARKET_AMOUNTS,
    )
    # round(100.0 × (1 + 0.0005), 2) = 100.05
    assert high.fill_price == 100.05
    assert high.slippage_rate == 0.0005


def test_fill_price_falls_back_to_most_conservative_tier_without_market_amounts():
    """market_amounts 缺失（回放不传样本）→ 最保守档 0.002（宁可高估成本）。"""
    result = match_order(
        Order(code="600519", side="buy", qty=100),
        _make_bar(open_=100.0, amount=None),
        config=_CONFIG,
        market_amounts=None,
    )
    # 成交价 = round(100.0 × (1 + 0.002), 2) = 100.2
    assert result.fill_price == 100.2
    assert result.slippage_rate == 0.002


# ── 费用（M5）────────────────────────────────────────────────────────────────


def test_min_commission_applies_to_small_ticket():
    """小额成交佣金取最低 5 元；卖单额外计印花税（数值钉死）。"""
    buy = match_order(
        Order(code="600519", side="buy", qty=100),
        _make_bar(open_=10.0),
        config=_CONFIG,
        market_amounts=_MARKET_AMOUNTS,
    )
    # 成交价 round(10.0×1.001,2)=10.01；成交额 1001；佣金 1001×0.00025=0.25 → 取最低 5.0
    assert buy == FillResult(
        filled_qty=100, fill_price=10.01, fee=5.0, reason=None, slippage_rate=0.001
    )

    sell = match_order(
        Order(code="600519", side="sell", qty=100),
        _make_bar(open_=10.0),
        config=_CONFIG,
        position=PositionContext(sellable_qty=100, total_qty=100),
        market_amounts=_MARKET_AMOUNTS,
    )
    # 成交价 round(10.0×0.999,2)=9.99；成交额 999；佣金取最低 5.0；印花税 round(0.4995,2)=0.5
    assert sell == FillResult(
        filled_qty=100, fill_price=9.99, fee=5.5, reason=None, slippage_rate=0.001
    )


def test_sell_fee_includes_stamp_tax_buy_does_not():
    """同数量买卖对比：卖单费用 = 佣金 + 印花税；买单仅佣金（数值钉死）。"""
    sell = match_order(
        Order(code="600519", side="sell", qty=800),
        _make_bar(open_=100.0),
        config=_CONFIG,
        position=PositionContext(sellable_qty=800, total_qty=800),
        market_amounts=_MARKET_AMOUNTS,
    )
    # 成交价 round(100.0×0.999,2)=99.9；成交额 79920
    # 佣金 = max(5.0, 79920×0.00025=19.98) = 19.98；印花税 = 79920×0.0005 = 39.96
    assert sell.fill_price == 99.9
    assert sell.fee == 59.94  # 19.98 + 39.96

    buy = match_order(
        Order(code="600519", side="buy", qty=800),
        _make_bar(open_=100.0),
        config=_CONFIG,
        market_amounts=_MARKET_AMOUNTS,
    )
    # 成交价 round(100.0×1.001,2)=100.1；成交额 80080；佣金 = 80080×0.00025 = 20.02；无印花税
    assert buy.fill_price == 100.1
    assert buy.fee == 20.02


# ── T+1 约束（M3-③）─────────────────────────────────────────────────────────


def test_t1_sell_blocked_when_not_sellable():
    """T 日买入当日不可卖：卖出数量 > 可卖数量 → T1_SELL_BLOCKED。"""
    result = match_order(
        Order(code="600519", side="sell", qty=500),
        _make_bar(open_=10.0),
        config=_CONFIG,
        position=PositionContext(sellable_qty=0, total_qty=500),
        market_amounts=_MARKET_AMOUNTS,
    )
    assert result == FillResult(
        filled_qty=0, fill_price=None, fee=0.0, reason="T1_SELL_BLOCKED", slippage_rate=0.0
    )


def test_t1_sell_allowed_when_sellable_covers_qty():
    """隔日仓位足额可卖：整手卖单正常成交（成交价/费用数值钉死）。"""
    result = match_order(
        Order(code="600519", side="sell", qty=500),
        _make_bar(open_=10.0),
        config=_CONFIG,
        position=PositionContext(sellable_qty=500, total_qty=500),
        market_amounts=_MARKET_AMOUNTS,
    )
    # 成交价 round(10.0×0.999,2)=9.99；成交额 4995；佣金取最低 5.0；印花税 round(2.4975,2)=2.5
    assert result == FillResult(
        filled_qty=500, fill_price=9.99, fee=7.5, reason=None, slippage_rate=0.001
    )


def test_sell_without_position_context_is_blocked():
    """position 缺省 = 无可卖持仓（sellable_qty 视作 0）→ T1_SELL_BLOCKED（保守口径）。"""
    result = match_order(
        Order(code="600519", side="sell", qty=100),
        _make_bar(open_=10.0),
        config=_CONFIG,
        position=None,
        market_amounts=_MARKET_AMOUNTS,
    )
    assert result.reason == "T1_SELL_BLOCKED"
    assert result.filled_qty == 0


# ── 整手约束（M3-①）──────────────────────────────────────────────────────────


def test_odd_lot_buy_rejected():
    """买单非 100 整数倍 → ODD_LOT。"""
    result = match_order(
        Order(code="600519", side="buy", qty=150),
        _make_bar(open_=10.0),
        config=_CONFIG,
        market_amounts=_MARKET_AMOUNTS,
    )
    assert result == FillResult(
        filled_qty=0, fill_price=None, fee=0.0, reason="ODD_LOT", slippage_rate=0.0
    )


def test_odd_lot_sell_rejected_when_not_full_position():
    """卖单非整手且 ≠ 持仓总量（非零股全卖）→ ODD_LOT。"""
    result = match_order(
        Order(code="600519", side="sell", qty=350),
        _make_bar(open_=10.0),
        config=_CONFIG,
        position=PositionContext(sellable_qty=350, total_qty=1000),
        market_amounts=_MARKET_AMOUNTS,
    )
    assert result.reason == "ODD_LOT"
    assert result.filled_qty == 0


def test_odd_lot_full_position_sell_allowed():
    """卖零股例外：qty == 持仓总量 且总量非整手 → 允许全卖（数值钉死）。"""
    result = match_order(
        Order(code="600519", side="sell", qty=350),
        _make_bar(open_=10.0),
        config=_CONFIG,
        position=PositionContext(sellable_qty=350, total_qty=350),
        market_amounts=_MARKET_AMOUNTS,
    )
    # 成交价 round(10.0×0.999,2)=9.99；成交额 3496.5
    # 佣金取最低 5.0；印花税 round(3496.5×0.0005=1.74825,2)=1.75 → 6.75
    assert result == FillResult(
        filled_qty=350, fill_price=9.99, fee=6.75, reason=None, slippage_rate=0.001
    )


# ── 校验顺序（M3：同单多因的裁决）───────────────────────────────────────────


def test_validation_order_odd_lot_before_limit_seal():
    """① 整手先于 ② 涨跌停：整手不合规的跌停卖单裁 ODD_LOT。"""
    result = match_order(
        Order(code="600519", side="sell", qty=150),
        _make_bar(open_=9.0, limit_down_sealed=True),
        config=_CONFIG,
        position=PositionContext(sellable_qty=0, total_qty=1000),
        market_amounts=_MARKET_AMOUNTS,
    )
    assert result.reason == "ODD_LOT"


def test_validation_order_limit_seal_before_t1():
    """② 涨跌停先于 ③ T+1：不可卖的跌停整手卖单裁 LIMIT_DOWN_UNFILLABLE。"""
    result = match_order(
        Order(code="600519", side="sell", qty=500),
        _make_bar(open_=9.0, limit_down_sealed=True),
        config=_CONFIG,
        position=PositionContext(sellable_qty=0, total_qty=500),
        market_amounts=_MARKET_AMOUNTS,
    )
    assert result.reason == "LIMIT_DOWN_UNFILLABLE"


# ── 调用方编程错误（ValueError，非业务原因码）───────────────────────────────


@pytest.mark.parametrize("qty", [0, -100])
def test_non_positive_qty_raises_value_error(qty):
    """qty <= 0 → ValueError（调用方编程错误，不产生 FillResult）。"""
    with pytest.raises(ValueError):
        match_order(
            Order(code="600519", side="buy", qty=qty),
            _make_bar(),
            config=_CONFIG,
            market_amounts=_MARKET_AMOUNTS,
        )


def test_invalid_side_raises_value_error():
    """side 非 buy/sell → ValueError（同上，调用方编程错误）。"""
    with pytest.raises(ValueError):
        match_order(
            Order(code="600519", side="hold", qty=100),  # type: ignore[arg-type]
            _make_bar(),
            config=_CONFIG,
            market_amounts=_MARKET_AMOUNTS,
        )


# ── 一字板判定纯函数（M2）────────────────────────────────────────────────────


def test_sealed_flags_one_price_limit_up():
    """一字涨停：开=高=低=收 且涨幅 +10% → (True, False)。"""
    assert sealed_flags_from_ohlc(11.0, 11.0, 11.0, 11.0, 10.0) == (True, False)


def test_sealed_flags_one_price_limit_down():
    """一字跌停：开=高=低=收 且跌幅 −10% → (False, True)。"""
    assert sealed_flags_from_ohlc(9.0, 9.0, 9.0, 9.0, 10.0) == (False, True)


def test_sealed_flags_st_five_percent_limit():
    """ST ±5% 档：+5% 判涨停、−5% 判跌停（阈值 4.8 覆盖）。"""
    assert sealed_flags_from_ohlc(10.5, 10.5, 10.5, 10.5, 10.0) == (True, False)
    assert sealed_flags_from_ohlc(9.5, 9.5, 9.5, 9.5, 10.0) == (False, True)


def test_sealed_flags_normal_day_is_not_sealed():
    """普通日（非一字）→ (False, False)。"""
    assert sealed_flags_from_ohlc(10.5, 10.8, 10.2, 10.6, 10.4) == (False, False)


def test_sealed_flags_flat_but_not_limit_is_not_sealed():
    """一字但涨跌幅未达阈值（如无成交平盘）→ (False, False)，不误判为板。"""
    assert sealed_flags_from_ohlc(10.2, 10.2, 10.2, 10.2, 10.0) == (False, False)


def test_sealed_flags_missing_prev_close_is_not_sealed():
    """prev_close 缺失 → (False, False)（无法计算涨跌幅，不编造板状态）。"""
    assert sealed_flags_from_ohlc(10.0, 10.0, 10.0, 10.0, None) == (False, False)


def test_sealed_flags_non_positive_prev_close_is_not_sealed():
    """prev_close <= 0 → (False, False)（守卫除零，不编造板状态）。"""
    assert sealed_flags_from_ohlc(10.0, 10.0, 10.0, 10.0, 0.0) == (False, False)


def test_sealed_flags_custom_threshold():
    """threshold 可覆盖：+3% 在默认 0.048 下不是板、在 0.02 阈值下是板。"""
    assert sealed_flags_from_ohlc(10.3, 10.3, 10.3, 10.3, 10.0) == (False, False)
    assert sealed_flags_from_ohlc(10.3, 10.3, 10.3, 10.3, 10.0, threshold=0.02) == (True, False)
