"""paper 每日结算单测（add-paper-broker Task 2 / B2）。

覆盖计划 B2：

- ``settle_daily``：净值 = cash + Σ(qty×close)（持仓按收盘价 mark），幂等 upsert
  （同 trade_date 重跑覆盖、单行不翻倍）；
- 持仓标的缺收盘价 → ``ValueError``（不编造价格、不落行）；无持仓 → 纯现金净值；
- ops 日批入口 ``run_paper_settle_daily``：从快照库取最新交易日与 ``close_raw``
  结算（订单 symbol 带交易所后缀，快照 code 为 6 位裸码，映射去后缀），
  快照库为空 → 跳过摘要（不抛错，日批旁路铁律）。

数值断言独立手算（逐式见各用例注释），不复用被测实现的计算。
"""

from __future__ import annotations

import pytest

from finance_agent.data.snapshot import write_snapshot_rows
from finance_agent.execution.brokers.paper import (
    PaperBroker,
    run_paper_settle_daily,
    settle_daily,
)
from finance_agent.execution.matching import MarketBar, MatchingConfig, SlippageTier
from finance_agent.execution.risk_gate import OrderIntent
from finance_agent.execution.store import list_paper_equity

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
_MARKET_AMOUNTS = tuple(float(x) for x in range(100, 1001, 100))


def _exec_db(tmp_path):
    return tmp_path / "execution.db"


def _buy(broker: PaperBroker) -> None:
    """基座买入：现金 998,994.00、持仓 600519.SH 100 股（同 test_paper_broker 手算）。"""
    broker.place_order(
        OrderIntent(
            decision_id="dec-1",
            signal_id="600519.SH:2026-10-13",
            symbol="600519.SH",
            side="buy",
            qty=100,
            target_weight=0.2,
        ),
        bar=MarketBar(
            trade_date="2026-10-13",
            open=10.0,
            high=10.0,
            low=10.0,
            close=10.0,
            prev_close=10.0,
            amount=500.0,
        ),
        market_amounts=_MARKET_AMOUNTS,
    )


# ── settle_daily：净值 = cash + Σ(qty×close) ────────────────────────────────


def test_settle_daily_marks_positions_at_close(tmp_path):
    """998,994 + 100×10.50 = 1,000,044.00；落 paper_equity 单行。"""
    db = _exec_db(tmp_path)
    _buy(PaperBroker(db, config=_CONFIG))

    summary = settle_daily("2026-10-13", close_prices={"600519.SH": 10.50}, db_path=db)

    assert summary == {
        "trade_date": "2026-10-13",
        "cash": 998_994.00,
        "positions_value": 1_050.00,
        "equity": 1_000_044.00,
    }
    rows = list_paper_equity(db_path=db)
    assert len(rows) == 1
    assert rows[0] == {
        "trade_date": "2026-10-13",
        "cash": 998_994.00,
        "positions_value": 1_050.00,
        "equity": 1_000_044.00,
    }


def test_settle_daily_idempotent_rerun(tmp_path):
    """同 trade_date 重跑 → 覆盖单行（998,994 + 100×10.60 = 1,000,054.00）。"""
    db = _exec_db(tmp_path)
    _buy(PaperBroker(db, config=_CONFIG))
    settle_daily("2026-10-13", close_prices={"600519.SH": 10.50}, db_path=db)
    settle_daily("2026-10-13", close_prices={"600519.SH": 10.60}, db_path=db)
    rows = list_paper_equity(db_path=db)
    assert len(rows) == 1
    assert rows[0]["equity"] == 1_000_054.00


def test_settle_daily_missing_close_price_fails_loud(tmp_path):
    """持仓标的缺收盘价 → ValueError（点名缺价标的；不落任何净值行）。"""
    db = _exec_db(tmp_path)
    _buy(PaperBroker(db, config=_CONFIG))
    with pytest.raises(ValueError, match="600519.SH"):
        settle_daily("2026-10-13", close_prices={}, db_path=db)
    assert list_paper_equity(db_path=db) == []


def test_settle_daily_no_positions_cash_only(tmp_path):
    """无持仓 → 净值 = 现金（初始 100 万）；持仓项为 0 不是编造。"""
    db = _exec_db(tmp_path)
    summary = settle_daily("2026-10-13", close_prices={}, db_path=db)
    assert summary["cash"] == 1_000_000.0
    assert summary["positions_value"] == 0.0
    assert summary["equity"] == 1_000_000.0


def test_settle_daily_rejects_blank_trade_date(tmp_path):
    with pytest.raises(ValueError):
        settle_daily("  ", close_prices={}, db_path=_exec_db(tmp_path))


# ── ops 日批入口：从快照库取收盘价 ──────────────────────────────────────────


def _spot_row(code: str = "600519", *, close_raw: float = 10.5) -> dict:
    """完整快照行（键与 market_snapshot 列名一致）。"""
    return {
        "trade_date": "2026-10-13",
        "code": code,
        "name": "贵州茅台",
        "open": 10.0,
        "high": 10.8,
        "low": 9.9,
        "close_raw": close_raw,
        "close_qfq": close_raw,
        "volume": 1000.0,
        "amount": 10500.0,
        "pct_chg": 0.5,
        "prev_close": 10.4,
        "is_st": 0,
        "is_suspended": 0,
        "limit_up_sealed": 0,
        "limit_down_sealed": 0,
        "listed_days": 100,
        "list_date": "2020-01-01",
    }


def test_run_paper_settle_daily_uses_snapshot_close(tmp_path, monkeypatch):
    """快照 close_raw=10.50 → 998,994 + 1,050 = 1,000,044.00（symbol 后缀映射到裸码）。"""
    db = _exec_db(tmp_path)
    snapshot_db = tmp_path / "snapshot.db"
    monkeypatch.setenv("EXECUTION_DB_PATH", str(db))
    monkeypatch.setenv("SNAPSHOT_DB_PATH", str(snapshot_db))
    _buy(PaperBroker(db, config=_CONFIG))
    write_snapshot_rows("2026-10-13", [_spot_row()], db_path=snapshot_db)

    summary = run_paper_settle_daily()

    assert summary["trade_date"] == "2026-10-13"
    assert summary["equity"] == 1_000_044.00
    assert summary["reason"] is None
    rows = list_paper_equity(db_path=db)
    assert rows[0]["equity"] == 1_000_044.00


def test_run_paper_settle_daily_skips_without_snapshot(tmp_path, monkeypatch):
    """快照库为空 → 跳过摘要（不抛错）；不落净值行。"""
    db = _exec_db(tmp_path)
    monkeypatch.setenv("EXECUTION_DB_PATH", str(db))
    monkeypatch.setenv("SNAPSHOT_DB_PATH", str(tmp_path / "snapshot.db"))
    summary = run_paper_settle_daily()
    assert summary["trade_date"] is None
    assert summary["reason"] == "no_snapshot_data"
    assert summary["equity"] is None
    assert list_paper_equity(db_path=db) == []
