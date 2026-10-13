"""Broker 抽象接口与共享订单记录（add-paper-broker Task 1；delta spec broker-execution）。

全部执行调用 SHALL 面向本接口编程；实盘实现（QMT/PTrade 等）SHALL 仅通过新增
接口实现接入，禁止绕过抽象直连（spec 逐字，本 change 不实现任何实盘接口）。

契约（逐字，消费方比较时不得改写）：

- 订单状态：``pending``/``filled``/``rejected``/``expired``（``ORDER_STATUSES``）；
- 账户级原因码：``INSUFFICIENT_CASH``（买入资金不足拒单）、``CANCELED``（用户
  撤单终态，落在四态内的 ``expired`` 上）；撮合未成交原因码复用
  ``execution/matching.py`` 常量；
- ``OrderIntent`` 复用风控门禁的纯数据类型（``risk_gate.OrderIntent``）：
  ``decision_id``/``signal_id`` 为证据链必填字段，broker 落库必须留存。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from finance_agent.execution.matching import MarketBar, PositionContext
from finance_agent.execution.risk_gate import OrderIntent

# 订单状态枚举（spec 逐字四态；本模块为唯一常量源）
STATUS_PENDING = "pending"
STATUS_FILLED = "filled"
STATUS_REJECTED = "rejected"
STATUS_EXPIRED = "expired"
ORDER_STATUSES: tuple[str, ...] = (STATUS_PENDING, STATUS_FILLED, STATUS_REJECTED, STATUS_EXPIRED)

# 账户级原因码（非撮合原因；撮合码见 matching.py）
REASON_INSUFFICIENT_CASH = "INSUFFICIENT_CASH"
REASON_CANCELED = "CANCELED"


@dataclass(frozen=True)
class OrderRecord:
    """订单落库形态（orders 表逐列）。

    ``status`` 取 ``ORDER_STATUSES``；``reason`` 为未成交/拒单/撤销原因码
    （成交为 None）；``fill_price``/``fee`` 仅在成交时有意义；``attempts`` 为
    挂单重试次数（每次重撮合 +1，达到配置上限转 ``expired``）。
    """

    order_id: str
    symbol: str
    side: str
    qty: int
    status: str
    reason: str | None
    fill_price: float | None
    fee: float
    decision_id: str
    signal_id: str
    created_at: str
    attempts: int


class Broker(ABC):
    """执行末端抽象（paper 与未来实盘实现的共同接口）。"""

    @abstractmethod
    def place_order(
        self,
        order: OrderIntent,
        *,
        bar: MarketBar,
        position: PositionContext | None = None,
        market_amounts: Sequence[float] | None = None,
    ) -> OrderRecord:
        """提交订单并返回落库结果（成交/挂单/拒单；状态机语义由实现定义）。"""

    @abstractmethod
    def query_positions(self) -> list[dict[str, Any]]:
        """当前持仓（字段：symbol/qty/avg_cost/updated_at，symbol 升序）。"""

    @abstractmethod
    def query_cash(self) -> float:
        """当前可用资金余额。"""

    @abstractmethod
    def cancel(self, order_id: str) -> OrderRecord:
        """撤销挂单（仅 ``pending`` 可撤；返回撤销后的订单记录）。"""
