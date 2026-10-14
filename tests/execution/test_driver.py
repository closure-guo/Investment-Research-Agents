"""执行链日批驱动器单测（add-execution-chain-driver Task 1：五段编排）。

覆盖计划 §1.1（合成决策 + 合成快照，手算期望，不复用被测实现的计算）：

1. 闭环：1 条 open 买入决策（含 confidence）→ 信号落库（含 ``decision_id``）→
   买入订单成交 → 持仓/资金/订单三处断言；
2. 幂等重跑：同决策同快照再跑 → ``signals_created == 0``、订单零新增、资金持仓不变；
3. flat 平仓：持仓 500 股 + 卖出决策 → 清仓（卖出成交、资金入账）；
4. 快照缺失：``skipped_reason == "no_snapshot_data"``、零订单零持仓变动、信号不生成；
5. 门禁拒绝留痕：ST 与一字涨停标的目标订单 → ``orders`` 行 ``status='rejected'`` +
   原因码（``ST_FLAG`` / ``LIMIT_UP_UNFILLABLE``），其余订单不受阻（照常成交）；
6. 同日决策顺延（决策日次一交易日开盘口径：``timestamp[:10] >= as_of`` 不处理）；
7. 空库零摘要：无决策无持仓（快照可用）→ 安全返回零摘要，不抛错。

造数口径：decision_log 走 ``insert_decision``（与生产写入方同表同列）；快照走
``write_snapshot_rows``（列名与 market_snapshot 逐字一致）。滑点档位由快照行的
``amount`` 在全市场样本中的分位决定（撮合 M4 口径）——各用例把目标标的的 amount
设为全市场最小值（分位 1/5 = 0.2 → 最保守档 0.002），手算式见各用例注释。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from finance_agent.data.snapshot import write_snapshot_rows
from finance_agent.execution.brokers.paper import PaperBroker
from finance_agent.execution.driver import run_execution_daily
from finance_agent.execution.store import (
    ensure_account,
    execution_conn,
    list_orders,
    list_positions,
    list_signals,
    write_position,
)
from finance_agent.outcome.store import init_decision_log, insert_decision

_AS_OF = "2026-10-14"
_DECISION_DATE = "2026-10-13"


# ── 造数 ────────────────────────────────────────────────────────────────────


def _spot_row(
    code: str,
    *,
    open_: float,
    amount: float,
    close_raw: float | None = None,
    prev_close: float | None = None,
    is_st: int = 0,
    is_suspended: int = 0,
    limit_up_sealed: int = 0,
    limit_down_sealed: int = 0,
) -> dict:
    """完整快照行（键与 market_snapshot 列名一致；trade_date 由写入参数覆盖）。"""
    close = open_ if close_raw is None else close_raw
    return {
        "trade_date": _AS_OF,
        "code": code,
        "name": f"标的{code}",
        "open": open_,
        "high": max(open_, close),
        "low": min(open_, close),
        "close_raw": close,
        "close_qfq": close,
        "volume": 1000.0,
        "amount": amount,
        "pct_chg": 0.5,
        "prev_close": open_ if prev_close is None else prev_close,
        "is_st": is_st,
        "is_suspended": is_suspended,
        "limit_up_sealed": limit_up_sealed,
        "limit_down_sealed": limit_down_sealed,
        "listed_days": 100,
        "list_date": "2020-01-01",
    }


def _decision(**overrides: object) -> dict:
    """decision_log 造数基座（api._persist_decision_log 生产写入列的同形记录）。"""
    base: dict = {
        "session_id": "sess-1",
        "timestamp": f"{_DECISION_DATE}T15:00:00",
        "ticker": "600519",
        "name": "贵州茅台",
        "action": "buy",
        "entry_price": 10.0,
        "stop_loss": 9.0,
        "target_price": 12.0,
        "confidence": 0.8,
        "position_size": 0.2,
    }
    base.update(overrides)
    return base


def _dbs(tmp_path: Path) -> tuple[Path, Path, Path]:
    """(决策库, 执行库, 快照库) 三库路径。"""
    return tmp_path / "sessions.db", tmp_path / "execution.db", tmp_path / "snapshot.db"


def _seed_position(db: Path, symbol: str, qty: int, avg_cost: float, when: str) -> None:
    """直接落一笔隔日持仓（造数：当日买入不参与 T+1 语义，此处为既有持仓）。"""
    with execution_conn(db) as conn:
        ensure_account(conn, 1_000_000.0, when)
        write_position(conn, symbol, qty, avg_cost, when)


def _fillers() -> list[dict]:
    """陪跑快照行（把目标标的的 amount 衬成全市场最小值——滑点档可手算）。"""
    return [
        _spot_row("600000", open_=8.0, amount=200.0),
        _spot_row("000001", open_=12.0, amount=300.0),
        _spot_row("600036", open_=30.0, amount=400.0),
        _spot_row("601318", open_=50.0, amount=500.0),
    ]


# ── 1. 决策 → 持仓闭环 ──────────────────────────────────────────────────────


def test_decision_to_position_closed_loop(tmp_path):
    """闭环：1 条买入决策（confidence 0.8）→ 信号 + 20,000 股买入成交。

    手算：equity = 现金 1,000,000（无持仓）；1 条 long 等权 → 1/1 → 单票 clip
    20% → 目标市值 200,000；目标股数 = floor(200,000 / 10.00 / 100) × 100 = 20,000；
    滑点：目标 amount=100 为 5 条样本最小值 → 分位 1/5 = 0.2 → 最保守档 0.002；
    成交价 = round(10.00 × 1.002, 2) = 10.02；成交额 = 10.02 × 20,000 = 200,400.00；
    佣金 = max(5.00, 200,400 × 0.00025 = 50.10) = 50.10（买入无印花税）；
    扣款 = 200,400.00 + 50.10 = 200,450.10 → 余额 = 1,000,000 − 200,450.10 = 799,549.90；
    成本价 = 200,450.10 / 20,000 = 10.022505。
    """
    dec_db, exe_db, snap_db = _dbs(tmp_path)
    init_decision_log(dec_db)
    decision_id = insert_decision(_decision(), db_path=dec_db)
    write_snapshot_rows(
        _AS_OF,
        [_spot_row("600519", open_=10.0, close_raw=10.2, amount=100.0), *_fillers()],
        db_path=snap_db,
    )

    summary = run_execution_daily(
        db_path=dec_db, execution_db_path=exe_db, snapshot_db_path=snap_db
    )

    assert summary == {
        "trade_date": _AS_OF,
        "signals_created": 1,
        "orders_placed": 1,
        "rejected": 0,
        "pending": 0,
        "skipped_reason": None,
        "skipped_symbols": [],
    }
    signals = list_signals(db_path=exe_db)
    assert len(signals) == 1
    signal = signals[0]
    assert signal.decision_id == decision_id
    assert signal.symbol == "600519.SH" and signal.direction == "long"
    assert signal.strength == 0.8 and signal.source == "decision_log"
    assert signal.created_at == _DECISION_DATE  # 决策日（执行日次一交易日口径）

    orders = list_orders(db_path=exe_db)
    assert len(orders) == 1
    order = orders[0]
    assert (order["side"], order["qty"], order["status"]) == ("buy", 20000, "filled")
    assert order["fill_price"] == 10.02 and order["fee"] == 50.10
    assert order["decision_id"] == decision_id and order["signal_id"] == signal.signal_id

    positions = list_positions(db_path=exe_db)
    assert len(positions) == 1
    assert positions[0]["symbol"] == "600519.SH" and positions[0]["qty"] == 20000
    assert positions[0]["avg_cost"] == pytest.approx(10.022505)
    assert PaperBroker(exe_db).query_cash() == 799_549.90


# ── 2. 幂等重跑 ─────────────────────────────────────────────────────────────


def test_rerun_is_idempotent(tmp_path):
    """幂等重跑：同决策/同快照再跑 → 信号零新增、订单零新增、持仓与资金不变。"""
    dec_db, exe_db, snap_db = _dbs(tmp_path)
    init_decision_log(dec_db)
    insert_decision(_decision(), db_path=dec_db)
    write_snapshot_rows(
        _AS_OF,
        [_spot_row("600519", open_=10.0, close_raw=10.2, amount=100.0), *_fillers()],
        db_path=snap_db,
    )
    first = run_execution_daily(db_path=dec_db, execution_db_path=exe_db, snapshot_db_path=snap_db)
    assert first["orders_placed"] == 1
    orders_after_first = list_orders(db_path=exe_db)
    positions_after_first = list_positions(db_path=exe_db)
    cash_after_first = PaperBroker(exe_db).query_cash()

    second = run_execution_daily(db_path=dec_db, execution_db_path=exe_db, snapshot_db_path=snap_db)

    assert second["signals_created"] == 0
    assert second["orders_placed"] == 0 and second["rejected"] == 0
    assert second["pending"] == 0 and second["skipped_reason"] is None
    assert len(list_signals(db_path=exe_db)) == 1  # 同 decision_id 不重复生成
    assert list_orders(db_path=exe_db) == orders_after_first  # 订单零新增
    assert list_positions(db_path=exe_db) == positions_after_first
    assert PaperBroker(exe_db).query_cash() == cash_after_first


# ── 3. flat 平仓 ────────────────────────────────────────────────────────────


def test_flat_signal_liquidates_position(tmp_path):
    """flat 平仓：持仓 500 股 + 卖出决策 → 全部卖出、清仓、资金入账。

    手算：equity = 1,000,000 + 500 × 20.50 = 1,010,250（仅用于权重口径，不参与本单）；
    卖出 500 股；滑点 0.002（amount 最小档）→ 成交价 = round(20.00 × 0.998, 2) = 19.96；
    成交额 = 19.96 × 500 = 9,980.00；佣金 = max(5.00, 9,980 × 0.00025 = 2.495) = 5.00；
    印花税 = 9,980 × 0.0005 = 4.99 → 费 = 9.99；入账 = 9,980.00 − 9.99 = 9,970.01
    → 余额 = 1,000,000 + 9,970.01 = 1,009,970.01；持仓清空。
    """
    dec_db, exe_db, snap_db = _dbs(tmp_path)
    init_decision_log(dec_db)
    insert_decision(_decision(action="sell", confidence=0.6), db_path=dec_db)
    _seed_position(exe_db, "600519.SH", 500, 10.0, "2026-10-10")
    write_snapshot_rows(
        _AS_OF,
        [_spot_row("600519", open_=20.0, close_raw=20.5, amount=100.0), *_fillers()],
        db_path=snap_db,
    )

    summary = run_execution_daily(
        db_path=dec_db, execution_db_path=exe_db, snapshot_db_path=snap_db
    )

    assert summary["signals_created"] == 1
    assert summary["orders_placed"] == 1
    assert summary["rejected"] == 0 and summary["pending"] == 0
    assert summary["skipped_symbols"] == []
    signals = list_signals(db_path=exe_db)
    assert len(signals) == 1 and signals[0].direction == "flat"

    orders = list_orders(db_path=exe_db)
    assert len(orders) == 1
    order = orders[0]
    assert (order["side"], order["qty"], order["status"]) == ("sell", 500, "filled")
    assert order["fill_price"] == 19.96 and order["fee"] == 9.99
    assert list_positions(db_path=exe_db) == []  # 清仓（零股尾数照卖同 matching 语义）
    assert PaperBroker(exe_db).query_cash() == 1_009_970.01


# ── 4. 快照缺失跳过 ─────────────────────────────────────────────────────────


def test_missing_snapshot_skips_without_side_effects(tmp_path):
    """快照库为空 → 跳过摘要（no_snapshot_data）：不生成信号、零订单、持仓不变。"""
    dec_db, exe_db, snap_db = _dbs(tmp_path)
    init_decision_log(dec_db)
    insert_decision(_decision(), db_path=dec_db)
    _seed_position(exe_db, "600519.SH", 500, 10.0, "2026-10-10")
    positions_before = list_positions(db_path=exe_db)
    cash_before = PaperBroker(exe_db).query_cash()

    summary = run_execution_daily(
        db_path=dec_db, execution_db_path=exe_db, snapshot_db_path=snap_db
    )

    assert summary == {
        "trade_date": None,
        "skipped_reason": "no_snapshot_data",
        "signals_created": 0,
        "orders_placed": 0,
        "rejected": 0,
        "pending": 0,
    }
    assert list_signals(db_path=exe_db) == []  # 信号顺延至下一交易日，不提前生成
    assert list_orders(db_path=exe_db) == []
    assert list_positions(db_path=exe_db) == positions_before
    assert PaperBroker(exe_db).query_cash() == cash_before


# ── 5. 门禁拒绝留痕且不阻断其余订单 ─────────────────────────────────────────


def test_gate_rejections_recorded_and_others_fill(tmp_path):
    """ST 与一字涨停标的被门禁拒绝 → rejected 留痕；正常标的照常成交。

    手算：3 条 long 等权 1/3 → 单票 clip 20%（总 60% ≤ 80% 不缩减）→ 各目标
    市值 200,000；600000 开 10.00 → floor(200,000/10.00/100)×100 = 20,000 股；
    滑点 0.002（amount=100 为样本唯一最小值 → 分位 1/5）→ 成交价 10.02、
    佣金 max(5, 200,400×0.00025=50.10) = 50.10 → 扣款 200,450.10
    → 余额 799,549.90；被拒两单资金/持仓零变动（只留 orders 行）。
    """
    dec_db, exe_db, snap_db = _dbs(tmp_path)
    init_decision_log(dec_db)
    st_id = insert_decision(_decision(ticker="600519"), db_path=dec_db)
    limit_up_id = insert_decision(_decision(ticker="000001"), db_path=dec_db)
    ok_id = insert_decision(_decision(ticker="600000"), db_path=dec_db)
    write_snapshot_rows(
        _AS_OF,
        [
            _spot_row("600519", open_=10.0, close_raw=10.2, amount=150.0, is_st=1),
            _spot_row(
                "000001",
                open_=10.0,
                close_raw=10.0,
                amount=200.0,
                prev_close=9.5,
                limit_up_sealed=1,
            ),
            _spot_row("600000", open_=10.0, close_raw=10.2, amount=100.0),
            _spot_row("600036", open_=30.0, amount=400.0),
            _spot_row("601318", open_=50.0, amount=500.0),
        ],
        db_path=snap_db,
    )

    summary = run_execution_daily(
        db_path=dec_db, execution_db_path=exe_db, snapshot_db_path=snap_db
    )

    assert summary["signals_created"] == 3
    assert summary["rejected"] == 2 and summary["orders_placed"] == 1
    assert summary["pending"] == 0 and summary["skipped_symbols"] == []

    by_symbol = {order["symbol"]: order for order in list_orders(db_path=exe_db)}
    assert set(by_symbol) == {"600519.SH", "000001.SZ", "600000.SH"}
    rejected_st = by_symbol["600519.SH"]
    assert (rejected_st["status"], rejected_st["reason"]) == ("rejected", "ST_FLAG")
    assert rejected_st["decision_id"] == st_id and rejected_st["signal_id"]
    assert rejected_st["fill_price"] is None and rejected_st["fee"] == 0.0
    rejected_limit_up = by_symbol["000001.SZ"]
    assert (rejected_limit_up["status"], rejected_limit_up["reason"]) == (
        "rejected",
        "LIMIT_UP_UNFILLABLE",
    )
    assert rejected_limit_up["decision_id"] == limit_up_id
    filled = by_symbol["600000.SH"]
    assert (filled["status"], filled["qty"], filled["fill_price"]) == ("filled", 20000, 10.02)
    assert filled["decision_id"] == ok_id

    positions = list_positions(db_path=exe_db)
    assert len(positions) == 1
    assert positions[0]["symbol"] == "600000.SH" and positions[0]["qty"] == 20000
    assert PaperBroker(exe_db).query_cash() == 799_549.90  # 仅成交单动账

    # 重跑：拒绝行不重复留痕（同 signal_id 已有订单 → 不再进入待执行集）
    orders_after_first = list_orders(db_path=exe_db)
    cash_after_first = PaperBroker(exe_db).query_cash()
    rerun = run_execution_daily(db_path=dec_db, execution_db_path=exe_db, snapshot_db_path=snap_db)
    assert rerun["signals_created"] == 0 and rerun["rejected"] == 0
    assert rerun["orders_placed"] == 0 and rerun["pending"] == 0
    assert list_orders(db_path=exe_db) == orders_after_first
    assert PaperBroker(exe_db).query_cash() == cash_after_first


# ── 6. 决策日次一交易日口径 ─────────────────────────────────────────────────


def test_same_day_decision_defers_to_next_day(tmp_path):
    """同日决策不执行（决策日次一交易日开盘口径）：timestamp[:10] >= as_of → 跳过。"""
    dec_db, exe_db, snap_db = _dbs(tmp_path)
    init_decision_log(dec_db)
    insert_decision(_decision(timestamp=f"{_AS_OF}T09:30:00"), db_path=dec_db)
    write_snapshot_rows(
        _AS_OF,
        [_spot_row("600519", open_=10.0, close_raw=10.2, amount=100.0), *_fillers()],
        db_path=snap_db,
    )

    summary = run_execution_daily(
        db_path=dec_db, execution_db_path=exe_db, snapshot_db_path=snap_db
    )

    assert summary["signals_created"] == 0
    assert summary["orders_placed"] == 0
    assert list_signals(db_path=exe_db) == [] and list_orders(db_path=exe_db) == []


# ── 7. 空库零摘要 ───────────────────────────────────────────────────────────


def test_empty_library_returns_zero_summary(tmp_path):
    """空库（无决策、无持仓，快照可用）→ 零摘要安全返回，不抛错。"""
    dec_db, exe_db, snap_db = _dbs(tmp_path)
    init_decision_log(dec_db)
    write_snapshot_rows(_AS_OF, _fillers(), db_path=snap_db)

    summary = run_execution_daily(
        db_path=dec_db, execution_db_path=exe_db, snapshot_db_path=snap_db
    )

    assert summary == {
        "trade_date": _AS_OF,
        "signals_created": 0,
        "orders_placed": 0,
        "rejected": 0,
        "pending": 0,
        "skipped_reason": None,
        "skipped_symbols": [],
    }
