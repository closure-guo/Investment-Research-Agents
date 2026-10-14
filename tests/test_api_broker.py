"""执行台账只读 API 单测（delta add-broker-console T1.1/T1.2；spec broker-execution「台账只读查询」）。

- overview：可用资金 / 持仓市值 / 总资产 / 浮动盈亏——持仓市值按最新快照
  ``close_raw`` mark（口径与 ``paper.run_paper_settle_daily`` 同源）；快照缺失或
  标的缺价 → 市值/浮盈为 null + 可判读标注（MUST NOT 以 0 冒充）；空账户如实
  （资金 = 初始 100 万、持仓/订单为空列表）；
- positions：标的/数量/成本/现价（close_raw）/ 市值 / 浮盈——现价不可得逐行 null；
- orders：四态（pending/filled/rejected/expired）+ 原因码 + decision_id/signal_id；
  ``?status=`` 过滤（非法状态 → 400）；
- gate-log：= status=rejected 订单视图（仅拒绝行，含原因码与关联 decision_id）；
- seed 扩展（TESTING=1）：``broker`` 造数（cash/positions/orders 经既有落库）+
  ``broker_reset`` 清账户三表（orders/positions/cash）；库路径于调用期读
  EXECUTION_DB_PATH / SNAPSHOT_DB_PATH（导入期冻结会让 E2E 注入失效）。

零网络：TestClient + tmp 库（两 env 逐例注入；不落开发库 data/execution.db）。
"""

from __future__ import annotations

import importlib

import pytest
from fastapi.testclient import TestClient

from finance_agent.api import app
from finance_agent.data.snapshot import write_snapshot_rows
from finance_agent.execution.brokers.base import OrderRecord
from finance_agent.execution.store import (
    execution_conn,
    insert_order,
    write_cash,
    write_position,
)

_AS_OF = "2026-10-13"
# 造数资金：与初始 100 万不同，断言读的是库内余额而非默认值
_CASH = 876_543.21
# spec 场景「2 条持仓」：成本合计 1500×100 + 12×2000 = 174 000
_POSITIONS = [
    {"symbol": "600519.SH", "qty": 100, "avg_cost": 1500.0},
    {"symbol": "000001.SZ", "qty": 2000, "avg_cost": 12.0},
]
# 快照 close_raw（市值 152 000 + 26 000 = 178 000；浮盈 4 000）
_CLOSE_RAW = {"600519": 1520.0, "000001": 13.0}


@pytest.fixture(autouse=True)
def _broker_dbs(tmp_path, monkeypatch):
    """执行库 + 快照库路径均指向 tmp（env 调用期读取；任何用例不得污染开发库）。"""
    monkeypatch.setenv("EXECUTION_DB_PATH", str(tmp_path / "execution.db"))
    monkeypatch.setenv("SNAPSHOT_DB_PATH", str(tmp_path / "market_snapshot.db"))


@pytest.fixture
def testing_client(monkeypatch):
    """TESTING=1 下 reload 出带 /api/test/seed 的客户端（仓库既有模式）。"""
    monkeypatch.setenv("TESTING", "1")
    import finance_agent.api as api_module

    importlib.reload(api_module)
    try:
        yield TestClient(api_module.app)
    finally:
        monkeypatch.delenv("TESTING", raising=False)
        importlib.reload(api_module)


def _write_snapshot(closes: dict[str, float], trade_date: str = _AS_OF) -> None:
    """写快照行：``close_qfq`` 刻意错开（2999.0）——断言市值走 close_raw 口径。"""
    write_snapshot_rows(
        trade_date,
        [
            {
                "code": code,
                "name": code,
                "open": close,
                "high": close,
                "low": close,
                "close_raw": close,
                "close_qfq": 2999.0,
                "volume": 1.0,
                "amount": 1.0,
                "pct_chg": 0.0,
                "prev_close": close,
                "is_st": 0,
                "is_suspended": 0,
                "limit_up_sealed": 0,
                "limit_down_sealed": 0,
                "listed_days": 100,
                "list_date": "2020-01-01",
            }
            for code, close in closes.items()
        ],
    )


def _order(
    signal_id: str,
    status: str,
    *,
    symbol: str = "600519.SH",
    side: str = "buy",
    qty: int = 100,
    reason: str | None = None,
    fill_price: float | None = None,
    created_at: str = f"{_AS_OF}T09:31:00",
    attempts: int = 0,
) -> OrderRecord:
    """订单行（order_id 按 PaperBroker.order_id_for 同源派生：signal_id:symbol:side）。"""
    return OrderRecord(
        order_id=f"{signal_id}:{symbol}:{side}",
        symbol=symbol,
        side=side,
        qty=qty,
        status=status,
        reason=reason,
        fill_price=fill_price,
        fee=0.0,
        decision_id=f"decision-{signal_id}",
        signal_id=signal_id,
        created_at=created_at,
        attempts=attempts,
    )


def _seed_account(
    *,
    cash: float = _CASH,
    positions: list[dict] | None = None,
    orders: list[OrderRecord] | None = None,
) -> None:
    """经既有 store 落库入口造账户（不绕写路径口径）。"""
    with execution_conn() as conn:
        write_cash(conn, cash, _AS_OF)
        for row in positions if positions is not None else _POSITIONS:
            write_position(
                conn,
                str(row["symbol"]),
                int(row["qty"]),
                float(row["avg_cost"]),
                _AS_OF,
            )
        for order in orders or []:
            insert_order(conn, order)


# ── overview：空账户如实（null vs 0 语义） ─────────────────────────────────


def test_overview_empty_account_truthful() -> None:
    """spec 场景「空账户如实」：初始资金 100 万；无持仓 → 市值/浮盈真实 0（无需价格）；
    持仓与订单为空列表；MUST NOT 伪数据。"""
    resp = TestClient(app).get("/api/broker/overview")

    assert resp.status_code == 200
    body = resp.json()
    assert body["cash"] == 1_000_000.0  # 惰性开户初始资金（PaperBroker 口径）
    assert body["positions_value"] == 0.0  # 空集求和是真实 0，不是「填 0 冒充」
    assert body["equity"] == 1_000_000.0
    assert body["unrealized_pnl"] == 0.0
    assert body["positions_count"] == 0
    assert body["market_value_note"] is None
    assert TestClient(app).get("/api/broker/positions").json()["positions"] == []
    assert TestClient(app).get("/api/broker/orders").json()["orders"] == []
    assert TestClient(app).get("/api/broker/gate-log").json()["orders"] == []


# ── overview / positions：快照 close_raw mark ────────────────────────────


def test_overview_marks_positions_by_snapshot_close_raw() -> None:
    """有快照：市值 = Σ qty×close_raw（手算核对）；总资产 = 资金 + 市值；浮盈 = 市值 − 成本。"""
    _seed_account()
    _write_snapshot(_CLOSE_RAW)

    body = TestClient(app).get("/api/broker/overview").json()

    assert body["cash"] == _CASH
    assert body["positions_value"] == 178_000.0  # 100×1520 + 2000×13（close_qfq=2999 不参与）
    assert body["equity"] == round(_CASH + 178_000.0, 2)
    assert body["unrealized_pnl"] == 4_000.0  # 178 000 − 174 000
    assert body["position_cost"] == 174_000.0
    assert body["positions_count"] == 2
    assert body["snapshot_date"] == _AS_OF
    assert body["market_value_note"] is None
    assert body["missing_symbols"] == []


def test_positions_rows_marked_by_snapshot_close_raw() -> None:
    """持仓列表：现价（close_raw）/ 市值 / 浮盈逐行给出（design Tab 表列口径）。"""
    _seed_account()
    _write_snapshot(_CLOSE_RAW)

    body = TestClient(app).get("/api/broker/positions").json()

    assert body["snapshot_date"] == _AS_OF
    rows = {row["symbol"]: row for row in body["positions"]}
    assert set(rows) == {"600519.SH", "000001.SZ"}
    assert rows["600519.SH"]["qty"] == 100
    assert rows["600519.SH"]["avg_cost"] == 1500.0
    assert rows["600519.SH"]["close"] == 1520.0
    assert rows["600519.SH"]["market_value"] == 152_000.0
    assert rows["600519.SH"]["unrealized_pnl"] == 2_000.0
    assert rows["000001.SZ"]["close"] == 13.0
    assert rows["000001.SZ"]["market_value"] == 26_000.0
    assert rows["000001.SZ"]["unrealized_pnl"] == 2_000.0


# ── 快照缺失：市值 null + 标注（MUST NOT 填 0 冒充） ──────────────────────


def test_overview_snapshot_missing_market_value_null_with_note() -> None:
    """快照库无数据：持仓市值/总资产/浮盈为 null + 标注缺价标的；MUST NOT 以 0 冒充。"""
    _seed_account()

    body = TestClient(app).get("/api/broker/overview").json()

    assert body["cash"] == _CASH  # 资金可得（账本口径）
    assert body["positions_value"] is None
    assert body["equity"] is None  # 市值不可得 → 总资产不可得（不拿资金冒充）
    assert body["unrealized_pnl"] is None
    assert body["position_cost"] == 174_000.0  # 成本来自账本，不依赖快照
    assert body["snapshot_date"] is None
    assert body["missing_symbols"] == ["000001.SZ", "600519.SH"]
    assert "快照" in str(body["market_value_note"])


def test_positions_missing_close_rows_null() -> None:
    """快照缺失：持仓行 close/market_value/unrealized_pnl 逐行 null（不填 0）。"""
    _seed_account()

    body = TestClient(app).get("/api/broker/positions").json()

    assert body["snapshot_date"] is None
    assert len(body["positions"]) == 2
    for row in body["positions"]:
        assert row["close"] is None
        assert row["market_value"] is None
        assert row["unrealized_pnl"] is None


def test_overview_snapshot_missing_one_symbol_names_it() -> None:
    """快照有行但缺某标的 close_raw：整体市值 null（不拿部分覆盖冒充全量）+ 标注缺谁。"""
    _seed_account()
    _write_snapshot({"600519": _CLOSE_RAW["600519"]})

    body = TestClient(app).get("/api/broker/overview").json()

    assert body["positions_value"] is None
    assert body["equity"] is None
    assert body["unrealized_pnl"] is None
    assert body["snapshot_date"] == _AS_OF
    assert body["missing_symbols"] == ["000001.SZ"]
    assert "000001.SZ" in str(body["market_value_note"])


# ── orders：字段齐备 + 状态过滤 ──────────────────────────────────────────


def _four_state_orders() -> list[OrderRecord]:
    """四态订单 + 一条 ST_FLAG 门禁拒绝（add-execution-chain-driver 落库形态）。"""
    return [
        _order(
            "sig-pending", "pending", reason="LIMIT_UP_UNFILLABLE", created_at=f"{_AS_OF}T09:31:00"
        ),
        _order("sig-filled", "filled", fill_price=1518.0, created_at=f"{_AS_OF}T09:32:00"),
        _order(
            "sig-rejected", "rejected", reason="INSUFFICIENT_CASH", created_at=f"{_AS_OF}T09:33:00"
        ),
        _order(
            "sig-expired", "expired", reason="CANCELED", attempts=3, created_at=f"{_AS_OF}T09:34:00"
        ),
        _order(
            "sig-gate",
            "rejected",
            symbol="000001.SZ",
            side="sell",
            qty=500,
            reason="ST_FLAG",
            created_at=f"{_AS_OF}T09:35:00",
        ),
    ]


def test_orders_fields_complete_and_four_states() -> None:
    """订单列表字段齐备：状态/原因码/decision_id/signal_id/created_at（spec 逐字契约）。"""
    _seed_account(orders=_four_state_orders())

    body = TestClient(app).get("/api/broker/orders").json()

    assert [row["status"] for row in body["orders"]] == [
        "pending",
        "filled",
        "rejected",
        "expired",
        "rejected",
    ]
    by_signal = {row["signal_id"]: row for row in body["orders"]}
    gate = by_signal["sig-gate"]
    assert gate["reason"] == "ST_FLAG"
    assert gate["decision_id"] == "decision-sig-gate"
    assert gate["symbol"] == "000001.SZ"
    assert gate["created_at"] == f"{_AS_OF}T09:35:00"
    assert by_signal["sig-filled"]["fill_price"] == 1518.0
    assert by_signal["sig-expired"]["attempts"] == 3
    assert by_signal["sig-pending"]["reason"] == "LIMIT_UP_UNFILLABLE"


def test_orders_status_filter_and_invalid_status() -> None:
    """``?status=`` 过滤（rejected 两行）；非法状态 → 400（fail loud，不静默空列表）。"""
    _seed_account(orders=_four_state_orders())

    rejected = TestClient(app).get("/api/broker/orders", params={"status": "rejected"}).json()
    assert [row["signal_id"] for row in rejected["orders"]] == ["sig-rejected", "sig-gate"]

    pending = TestClient(app).get("/api/broker/orders", params={"status": "pending"}).json()
    assert [row["signal_id"] for row in pending["orders"]] == ["sig-pending"]

    resp = TestClient(app).get("/api/broker/orders", params={"status": "bogus"})
    assert resp.status_code == 400


# ── gate-log：仅 rejected ────────────────────────────────────────────────


def test_gate_log_only_rejected_orders_with_reason_codes() -> None:
    """门禁拒绝视图 = status=rejected 订单（spec 场景：ST_FLAG 行含原因码与关联决策）。"""
    _seed_account(orders=_four_state_orders())

    body = TestClient(app).get("/api/broker/gate-log").json()

    assert {row["status"] for row in body["orders"]} == {"rejected"}
    assert [row["signal_id"] for row in body["orders"]] == ["sig-rejected", "sig-gate"]
    assert sorted(row["reason"] for row in body["orders"]) == ["INSUFFICIENT_CASH", "ST_FLAG"]
    st_flag = next(row for row in body["orders"] if row["reason"] == "ST_FLAG")
    assert st_flag["symbol"] == "000001.SZ"
    assert st_flag["decision_id"] == "decision-sig-gate"


# ── seed（TESTING=1）：broker 造数 + reset ───────────────────────────────


def test_seed_broker_roundtrip_and_reset(testing_client) -> None:
    """seed broker（cash/positions/orders）→ 台账端点可见；broker_reset 清三表回空账户。"""
    resp = testing_client.post(
        "/api/test/seed",
        json={
            "broker": {
                "cash": 500_000.0,
                "positions": [
                    {"symbol": "600519.SH", "qty": 100, "avg_cost": 1500.0, "updated_at": _AS_OF}
                ],
                "orders": [
                    {
                        "symbol": "600519.SH",
                        "side": "buy",
                        "qty": 100,
                        "status": "rejected",
                        "reason": "ST_FLAG",
                        "decision_id": "decision-seed",
                        "signal_id": "sig-seed",
                        "created_at": f"{_AS_OF}T09:31:00",
                    }
                ],
            }
        },
    )
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok", "mode": "testing"}  # 纯造数不落会话

    overview = TestClient(app).get("/api/broker/overview").json()
    assert overview["cash"] == 500_000.0
    assert overview["positions_count"] == 1
    # order_id 缺省按 signal_id:symbol:side 同源派生（与生产口径一致）
    gate = TestClient(app).get("/api/broker/gate-log").json()["orders"]
    assert [row["order_id"] for row in gate] == ["sig-seed:600519.SH:buy"]
    assert gate[0]["reason"] == "ST_FLAG"

    reset = testing_client.post("/api/test/seed", json={"broker_reset": True})
    assert reset.status_code == 200
    assert TestClient(app).get("/api/broker/orders").json()["orders"] == []
    assert TestClient(app).get("/api/broker/positions").json()["positions"] == []
    # reset 后资金回初始 100 万（账户行随 cash 表清空，下次读取惰性开户）
    assert TestClient(app).get("/api/broker/overview").json()["cash"] == 1_000_000.0


def test_seed_broker_positions_default_and_cash_absent(testing_client) -> None:
    """seed 缺 cash → 初始资金开户；仅 positions 造数也可单独使用（不创建会话）。"""
    resp = testing_client.post(
        "/api/test/seed",
        json={"broker": {"positions": [{"symbol": "600519.SH", "qty": 10, "avg_cost": 1500.0}]}},
    )
    assert resp.json() == {"status": "ok", "mode": "testing"}

    overview = TestClient(app).get("/api/broker/overview").json()
    assert overview["cash"] == 1_000_000.0
    assert overview["positions_count"] == 1
    assert overview["positions_value"] is None  # 无快照 → 如实 null


def test_seed_placeholder_untouched_for_symbol_payload(testing_client) -> None:
    """既有 {symbol} 早退路径不受 broker 扩展影响（占位响应逐字不变）。"""
    resp = testing_client.post("/api/test/seed", json={"symbol": "300308"})

    assert resp.status_code == 200
    assert resp.json() == {"status": "ok", "mode": "testing"}
