"""execution/brokers/{base,paper}.py paper 全链路单测（add-paper-broker Task 1 / B1）。

覆盖 delta spec broker-execution「Broker 抽象接口」「仿真撮合走统一引擎」：

- ``Broker`` 抽象接口存在（place_order/query_positions/query_cash/cancel）且
  ``PaperBroker`` 实现之（isinstance 检查）；
- spec 场景：初始资金 100 万元，买入 600519×100 且当日快照可成交 → 持仓 +100、
  资金按成交价+fee 扣减、订单含 decision_id/signal_id；
- spec 场景：一字涨停买入 → ``pending`` + ``LIMIT_UP_UNFILLABLE`` + 零持仓变动；
- 资金不足 → ``rejected`` + ``INSUFFICIENT_CASH``（结构化，非抛错）；
- 卖出路径（隔日仓位可卖）→ 资金加回、持仓清零；未过 T+1（可卖 0）→ 挂单；
- ``query_positions``/``query_cash``/``cancel``（pending 可撤、filled 不可撤）；
- ``retry_pending``：次日可成交 → filled；连续 3 次不成交 → expired；无 bar 跳过；
- 落库幂等：同订单重跑不翻倍（资金/持仓只动一次）。

数值断言独立手算（不复用被测实现的计算；逐式见各用例注释）：

- 买入成交价 = round(10.00 × (1+0.001), 2) = 10.01（amount=500 在
  {100..1000} 样本中分位 5/10=0.5 → 落 0.6 档滑点 0.001）；
- 买入费用 = max(5, 10.01×100×0.00025=0.25025) = 5.00（最低佣金）；
- 买入成本 = 1001.00 + 5.00 = 1006.00 → 资金 1,000,000 − 1006 = 998,994.00；
  加权成本 = 1006.00 / 100 = 10.06；
- 卖出成交价 = round(11.00 × (1−0.0005), 2) = 10.99（amount=1000 → 末档滑点）；
- 卖出费用 = max(5, 1099×0.00025=0.27475)=5.00 + 印花税 round(1099×0.0005=0.5495, 2)=0.55
  → 5.55；净回款 = 1099 − 5.55 = 1093.45 → 资金 998,994 + 1093.45 = 1,000,087.45。
"""

from __future__ import annotations

import pytest

from finance_agent.execution.brokers.base import (
    REASON_CANCELED,
    REASON_INSUFFICIENT_CASH,
    Broker,
    OrderRecord,
)
from finance_agent.execution.brokers.paper import PaperBroker
from finance_agent.execution.matching import (
    REASON_LIMIT_UP_UNFILLABLE,
    REASON_T1_SELL_BLOCKED,
    MarketBar,
    MatchingConfig,
    PositionContext,
    SlippageTier,
)
from finance_agent.execution.risk_gate import OrderIntent
from finance_agent.execution.store import list_orders, list_positions

# 与 config/matching_engine.json 逐字一致的三档滑点
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


def _broker(tmp_path, **overrides) -> PaperBroker:
    return PaperBroker(tmp_path / "execution.db", config=_CONFIG, **overrides)


def _bar(
    *,
    open_: float = 10.0,
    amount: float | None = 500.0,
    limit_up_sealed: bool = False,
    trade_date: str = "2026-10-13",
) -> MarketBar:
    return MarketBar(
        trade_date=trade_date,
        open=open_,
        high=open_,
        low=open_,
        close=open_,
        prev_close=open_,
        amount=amount,
        limit_up_sealed=limit_up_sealed,
    )


def _intent(
    *,
    side: str = "buy",
    qty: int = 100,
    symbol: str = "600519.SH",
    decision_id: str = "dec-1",
    signal_id: str = "600519.SH:2026-10-13",
) -> OrderIntent:
    return OrderIntent(
        decision_id=decision_id,
        signal_id=signal_id,
        symbol=symbol,
        side=side,  # type: ignore[arg-type]  # 测试参数由用例自证
        qty=qty,
        target_weight=0.2,
    )


def _buy(broker: PaperBroker, **overrides) -> OrderRecord:
    """基座买入（快照可成交）：10.01 成交、5.00 费用、成本 1006.00。"""
    return broker.place_order(_intent(**overrides), bar=_bar(), market_amounts=_MARKET_AMOUNTS)


# ── 抽象接口 ────────────────────────────────────────────────────────────────


def test_paper_broker_implements_broker_interface(tmp_path):
    """spec「Broker 抽象接口」：抽象存在且 paper 实现之；四方法齐备。"""
    broker = _broker(tmp_path)
    assert isinstance(broker, Broker)
    for method in ("place_order", "query_positions", "query_cash", "cancel"):
        assert callable(getattr(broker, method))


def test_broker_abstract_cannot_be_instantiated():
    """抽象性证明：接口本身不可实例化（实盘实现只能经子类接入）。"""
    with pytest.raises(TypeError):
        Broker()  # type: ignore[abstract]


# ── spec 场景：买入全链路 ───────────────────────────────────────────────────


def test_place_order_fill_chain_spec_scenario(tmp_path):
    """初始 100 万，买入 600519×100 快照可成交 → 持仓/资金/证据链逐字。"""
    broker = _broker(tmp_path)
    assert broker.query_cash() == 1_000_000.0  # 初始资金 100 万元

    rec = _buy(broker)

    assert rec.status == "filled"
    assert rec.fill_price == 10.01  # round(10.00 × 1.001, 2)
    assert rec.fee == 5.00
    assert rec.side == "buy" and rec.qty == 100
    assert rec.decision_id == "dec-1"  # 证据链
    assert rec.signal_id == "600519.SH:2026-10-13"

    positions = broker.query_positions()
    assert len(positions) == 1
    assert positions[0]["symbol"] == "600519.SH"
    assert positions[0]["qty"] == 100
    assert positions[0]["avg_cost"] == 10.06  # (1001.00 + 5.00) / 100
    assert positions[0]["updated_at"] == "2026-10-13"

    assert broker.query_cash() == 998_994.00  # 1,000,000 − 1006.00


def test_limit_up_buy_stays_pending_spec_scenario(tmp_path):
    """spec 场景：一字涨停买入 → pending + LIMIT_UP_UNFILLABLE + 零持仓变动。"""
    broker = _broker(tmp_path)
    rec = broker.place_order(
        _intent(),
        bar=_bar(limit_up_sealed=True),
        market_amounts=_MARKET_AMOUNTS,
    )
    assert rec.status == "pending"
    assert rec.reason == REASON_LIMIT_UP_UNFILLABLE == "LIMIT_UP_UNFILLABLE"
    assert rec.fill_price is None and rec.fee == 0.0
    assert broker.query_positions() == []
    assert broker.query_cash() == 1_000_000.0  # 零资金变动


def test_insufficient_cash_rejected_structured(tmp_path):
    """资金不足（成交额+fee > 余额）→ rejected + INSUFFICIENT_CASH，不抛错。"""
    broker = _broker(tmp_path, initial_cash=1_000.0)
    rec = _buy(broker)  # 需 1006.00 > 1000.00
    assert rec.status == "rejected"
    assert rec.reason == REASON_INSUFFICIENT_CASH == "INSUFFICIENT_CASH"
    assert broker.query_positions() == []
    assert broker.query_cash() == 1_000.0


def test_sell_adds_cash_back_and_clears_position(tmp_path):
    """卖出路径：隔日仓位可卖 → 资金加回（成交额−fee）、持仓清零。"""
    broker = _broker(tmp_path)
    _buy(broker)
    rec = broker.place_order(
        _intent(side="sell"),
        bar=_bar(open_=11.0, amount=1000.0, trade_date="2026-10-14"),
        market_amounts=_MARKET_AMOUNTS,
    )
    assert rec.status == "filled"
    assert rec.fill_price == 10.99  # round(11.00 × 0.9995, 2)
    assert rec.fee == 5.55  # 佣金 5.00 + 印花税 0.55
    assert broker.query_positions() == []
    assert broker.query_cash() == 1_000_087.45  # 998,994 + (1099 − 5.55)


def test_sell_blocked_without_sellable_position_stays_pending(tmp_path):
    """T+1 未过（可卖 0）→ 挂单 + T1_SELL_BLOCKED，零变动（调用方显式传持仓上下文）。"""
    broker = _broker(tmp_path)
    _buy(broker)
    rec = broker.place_order(
        _intent(side="sell"),
        bar=_bar(open_=11.0, amount=1000.0, trade_date="2026-10-14"),
        position=PositionContext(sellable_qty=0, total_qty=100),
    )
    assert rec.status == "pending"
    assert rec.reason == REASON_T1_SELL_BLOCKED == "T1_SELL_BLOCKED"
    assert broker.query_positions()[0]["qty"] == 100
    assert broker.query_cash() == 998_994.00


# ── 查询与撤销 ──────────────────────────────────────────────────────────────


def test_query_without_any_order_returns_initial_state(tmp_path):
    broker = _broker(tmp_path)
    assert broker.query_positions() == []
    assert broker.query_cash() == 1_000_000.0


def test_cancel_pending_order(tmp_path):
    """pending 可撤；撤销落库可查（逐字四态内以 expired + CANCELED 表达撤销）。"""
    broker = _broker(tmp_path)
    rec = broker.place_order(_intent(), bar=_bar(limit_up_sealed=True))
    assert rec.status == "pending"

    canceled = broker.cancel(rec.order_id)
    assert canceled.status == "expired"
    assert canceled.reason == REASON_CANCELED == "CANCELED"
    assert broker.query_positions() == []
    assert broker.query_cash() == 1_000_000.0
    rows = list_orders(db_path=tmp_path / "execution.db")
    assert [r["status"] for r in rows] == ["expired"]


def test_cancel_filled_or_unknown_order_rejected(tmp_path):
    """filled 不可撤；未知订单同样报错（编程错误，不得静默）。"""
    broker = _broker(tmp_path)
    rec = _buy(broker)
    try:
        broker.cancel(rec.order_id)
    except ValueError as exc:
        assert "pending" in str(exc)
    else:  # pragma: no cover - 断言失败路径
        raise AssertionError("filled 订单不得撤销")
    try:
        broker.cancel("no-such-order")
    except ValueError as exc:
        assert "no-such-order" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("未知订单不得静默")


# ── retry_pending（挂单次日重试 / 按配置过期）──────────────────────────────


def test_retry_pending_fills_next_day(tmp_path):
    """涨停挂单 → 次日 bar 可成交 → filled（持仓/资金按撮合结果变动）。

    重试无全市场成交额样本（计划签名无 market_amounts）→ matching 取最保守档
    0.002：成交价 = round(10.00 × 1.002, 2) = 10.02；费用 max(5, 0.2505)=5.00；
    成本 1007.00 → 资金 1,000,000 − 1007 = 998,993.00；成本价 10.07。
    """
    broker = _broker(tmp_path)
    rec = broker.place_order(_intent(), bar=_bar(limit_up_sealed=True))
    assert rec.status == "pending"

    summary = broker.retry_pending(
        "2026-10-14",
        bars={"600519.SH": _bar(trade_date="2026-10-14")},
    )
    assert summary == {
        "retried": 1,
        "filled": 1,
        "rejected": 0,
        "pending": 0,
        "expired": 0,
        "skipped_no_bar": 0,
    }
    rows = list_orders(db_path=tmp_path / "execution.db")
    assert rows[0]["status"] == "filled" and rows[0]["attempts"] == 1
    assert rows[0]["fill_price"] == 10.02
    position = broker.query_positions()[0]
    assert position["qty"] == 100 and position["avg_cost"] == 10.07
    assert broker.query_cash() == 998_993.00


def test_retry_pending_expires_after_three_unfilled_attempts(tmp_path):
    """默认 pending_expire_days=3：连续 3 次重试仍不成交 → expired（零变动）。"""
    broker = _broker(tmp_path)
    broker.place_order(_intent(), bar=_bar(limit_up_sealed=True))

    attempts_status = []
    for day in ("2026-10-14", "2026-10-15", "2026-10-16"):
        summary = broker.retry_pending(
            day, bars={"600519.SH": _bar(limit_up_sealed=True, trade_date=day)}
        )
        attempts_status.append(summary)
        row = list_orders(db_path=tmp_path / "execution.db")[0]
        assert row["reason"] == "LIMIT_UP_UNFILLABLE"
        if day != "2026-10-16":
            assert row["status"] == "pending"
    assert [s["pending"] for s in attempts_status] == [1, 1, 0]
    assert attempts_status[-1]["expired"] == 1
    final = list_orders(db_path=tmp_path / "execution.db")[0]
    assert final["status"] == "expired" and final["attempts"] == 3
    assert broker.query_positions() == []
    assert broker.query_cash() == 1_000_000.0


def test_retry_pending_skips_symbol_without_bar(tmp_path):
    """无 bar（数据缺失）→ 跳过，不记尝试（attempts 不变，仍 pending）。"""
    broker = _broker(tmp_path)
    broker.place_order(_intent(), bar=_bar(limit_up_sealed=True))
    summary = broker.retry_pending("2026-10-14", bars={})
    assert summary == {
        "retried": 0,
        "filled": 0,
        "rejected": 0,
        "pending": 0,
        "expired": 0,
        "skipped_no_bar": 1,
    }
    row = list_orders(db_path=tmp_path / "execution.db")[0]
    assert row["status"] == "pending" and row["attempts"] == 0


# ── 落库幂等 ────────────────────────────────────────────────────────────────


def test_place_order_rerun_idempotent(tmp_path):
    """同订单（同 signal_id/symbol/side）重跑 → 不翻倍：单行、资金/持仓只动一次。"""
    broker = _broker(tmp_path)
    first = _buy(broker)
    second = _buy(broker)
    assert first == second
    rows = list_orders(db_path=tmp_path / "execution.db")
    assert len(rows) == 1
    assert broker.query_cash() == 998_994.00
    assert [p["qty"] for p in broker.query_positions()] == [100]
    # 库里也只有一行持仓（非两行/翻倍数量）
    assert len(list_positions(db_path=tmp_path / "execution.db")) == 1


def test_place_order_missing_evidence_chain_rejected(tmp_path):
    """证据链必填：decision_id/signal_id 任一为空 → ValueError（不落库）。"""
    broker = _broker(tmp_path)
    for bad in (_intent(decision_id=""), _intent(signal_id="  ")):
        try:
            broker.place_order(bad, bar=_bar())
        except ValueError as exc:
            assert "decision_id" in str(exc) or "signal_id" in str(exc)
        else:  # pragma: no cover
            raise AssertionError("证据链缺失不得下单")
    assert list_orders(db_path=tmp_path / "execution.db") == []
