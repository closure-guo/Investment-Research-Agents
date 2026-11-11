"""execution 只读端点 book 参数（add-microcap-lane-analysis T8）。

- 非法 book → 422（``BrokerBook = Literal["main", "microcap"]``，FastAPI 校验，
  与 api.py ``ScreeningPoolTier`` 既有风格一致；``_broker_db`` 内另保留
  HTTPException(422) 防御性兜底）；
- book=microcap 读第二库（``EXECUTION_MICROCAP_DB_PATH``，调用期读取）；缺省
  main 显式走 ``EXECUTION_DB_PATH`` / ``data/execution.db``——与既有隐式缺省
  （store ``_default_db_path``）同 env 同默认值，主档行为零变化。

零网络：TestClient + tmp 库（三 env 逐例注入；不落开发库 data/execution.db）。
"""

from __future__ import annotations

import sqlite3

import pytest
from fastapi.testclient import TestClient

from finance_agent.api import app
from finance_agent.execution.store import EXECUTION_DDL, execution_conn, write_position


@pytest.fixture
def client(tmp_path, monkeypatch):
    """零网络 TestClient；三库路径指 tmp（任何用例不得污染开发库，照抄 test_api_broker 纪律）。"""
    monkeypatch.setenv("EXECUTION_DB_PATH", str(tmp_path / "execution.db"))
    monkeypatch.setenv("SNAPSHOT_DB_PATH", str(tmp_path / "market_snapshot.db"))
    monkeypatch.setenv("EXECUTION_MICROCAP_DB_PATH", str(tmp_path / "execution_microcap.db"))
    return TestClient(app)


def test_book_param_rejects_invalid(client):
    """非法 book → 422（Literal 校验口径，同 ScreeningPoolTier）。"""
    resp = client.get("/api/broker/positions", params={"book": "foo"})
    assert resp.status_code == 422


def test_book_microcap_reads_second_db(client, tmp_path, monkeypatch):
    """book=microcap 读第二库（EXECUTION_MICROCAP_DB_PATH）；空账本返回空持仓列表，不报错。

    注：既有契约 ``/api/broker/positions`` 返回 ``{"snapshot_date": …, "positions": […]}``
    （test_api_broker 逐字断言 ``.json()["positions"]``）——brief 原稿
    ``resp.json() == []`` 与该契约冲突，按既有契约修正断言形态，语义不变。
    """
    exec_db = tmp_path / "exec-mc.db"
    conn = sqlite3.connect(exec_db)
    conn.executescript(EXECUTION_DDL)
    conn.commit()
    conn.close()
    monkeypatch.setenv("EXECUTION_MICROCAP_DB_PATH", str(exec_db))
    resp = client.get("/api/broker/positions", params={"book": "microcap"})
    assert resp.status_code == 200
    assert resp.json()["positions"] == []  # 空账本返回空持仓列表，不报错


def test_book_microcap_routes_to_second_db_not_main(client, tmp_path, monkeypatch):
    """路由判别（RED 依据）：microcap 库内的持仓只在 book=microcap 可见，缺省 main 看不到。"""
    mc_db = tmp_path / "exec-mc.db"
    with execution_conn(mc_db) as conn:
        write_position(conn, "300750.SZ", 100, 25.0, "2026-11-10")
    monkeypatch.setenv("EXECUTION_MICROCAP_DB_PATH", str(mc_db))

    default_resp = client.get("/api/broker/positions")  # 缺省 main：小票库持仓不可见
    assert default_resp.status_code == 200
    assert default_resp.json()["positions"] == []

    mc_resp = client.get("/api/broker/positions", params={"book": "microcap"})
    assert mc_resp.status_code == 200
    assert [row["symbol"] for row in mc_resp.json()["positions"]] == ["300750.SZ"]


def test_book_microcap_reads_microcap_orders(client, tmp_path, monkeypatch):
    """orders 同一 book 口径：microcap 订单从第二库回查（gate-log 走同 payload 函数）。"""
    from finance_agent.execution.brokers.base import OrderRecord
    from finance_agent.execution.store import insert_order

    mc_db = tmp_path / "exec-mc.db"
    with execution_conn(mc_db) as conn:
        insert_order(
            conn,
            OrderRecord(
                order_id="sig-mc:300750.SZ:buy",
                symbol="300750.SZ",
                side="buy",
                qty=100,
                status="filled",
                reason=None,
                fill_price=25.0,
                fee=0.0,
                decision_id="decision-sig-mc",
                signal_id="sig-mc",
                created_at="2026-11-10T09:31:00",
                attempts=0,
            ),
        )
    monkeypatch.setenv("EXECUTION_MICROCAP_DB_PATH", str(mc_db))

    default_resp = client.get("/api/broker/orders")  # 缺省 main：小票库订单不可见
    assert default_resp.json()["orders"] == []

    mc_resp = client.get("/api/broker/orders", params={"book": "microcap"})
    assert mc_resp.status_code == 200
    assert [row["signal_id"] for row in mc_resp.json()["orders"]] == ["sig-mc"]
