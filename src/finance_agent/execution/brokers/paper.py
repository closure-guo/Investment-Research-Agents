"""PaperBroker：仿真执行末端（add-paper-broker Task 1/2；计划 B1/B2）。

设计决策（B1/B2，不得自行改口径）：

- 成交判定一律经 ``matching.match_order``（spec「仿真撮合走统一引擎」，禁止在
  broker 内重写成交逻辑）；
- 落 execution 库三表 ``orders``/``positions``/``cash``（与 signals 同库不同表，
  见 ``execution/store.py``）；
- 订单状态逐字 ``pending``/``filled``/``rejected``/``expired``；未成交原因复用
  matching 原因码（``LIMIT_UP_UNFILLABLE`` 等）；买入成交额+fee 超余额 →
  ``rejected`` + ``INSUFFICIENT_CASH``（结构化，非抛错）；
- ``order_id = f"{signal_id}:{symbol}:{side}"``（确定性 → 同订单重跑幂等：
  已存在直接返回原记录，资金/持仓不二次变动）；
- 卖出可卖数量：调用方显式传 ``position``；缺省取库内该票持仓全额（视作隔日
  仓位），当日买入的 T+1 锁定须调用方显式传 ``PositionContext(sellable_qty=...)``；
- 加权成本价含买入费用（成本与资金流一致，便于独立手算核对）；
- ``created_at`` 取 ``bar.trade_date``（日期口径；回放/日批确定性，不引入时钟）；
- ``cancel`` 为终态动作：在逐字四态内以 ``expired`` + reason ``CANCELED`` 表达
  （不新增第五个状态）；仅 ``pending`` 可撤；
- ``retry_pending``：对挂单逐单重撮合；无 bar 的标的跳过（数据缺失不记尝试）；
  仍不成交 → attempts+1，达到 ``pending_expire_days``（默认 3）→ ``expired``；
- ``settle_daily``（B2）：持仓按注入收盘价 mark 成 ``paper_equity`` 曲线
  （``cash + Σ(qty×close)``，trade_date 幂等 upsert）；持仓标的缺收盘价 →
  ``ValueError``（不编造价格）；生产入口 ``run_paper_settle_daily`` 从快照库取
  最新交易日与 ``close_raw`` 注入（快照 code 为 6 位裸码，订单 symbol 带去后缀）。

数值口径：成交价/费用一律来自 matching-engine（M1/M4/M5），本模块只做资金与
持仓记账；金额 round(2) 落库（账户台账两位小数）。
"""

from __future__ import annotations

import math
import sqlite3
from collections.abc import Mapping, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any, Literal, cast

from finance_agent.execution.brokers.base import (
    REASON_CANCELED,
    REASON_INSUFFICIENT_CASH,
    STATUS_EXPIRED,
    STATUS_FILLED,
    STATUS_PENDING,
    STATUS_REJECTED,
    Broker,
    OrderRecord,
)
from finance_agent.execution.config import load_matching_config
from finance_agent.execution.matching import (
    FillResult,
    MarketBar,
    MatchingConfig,
    Order,
    PositionContext,
    match_order,
)
from finance_agent.execution.risk_gate import OrderIntent
from finance_agent.execution.store import (
    delete_position,
    ensure_account,
    execution_conn,
    insert_order,
    list_pending_orders,
    list_positions,
    read_cash,
    read_order,
    read_position,
    read_positions,
    update_order,
    write_cash,
    write_paper_equity,
    write_position,
)

# 浮点尘埃容差：成交额+fee 恰等于余额时必须可成交（金额已 round(2)，容差只防
# 二进制表示误差把「刚好买得起」判成超支）
_CASH_TOLERANCE = 1e-9


def _now_iso() -> str:
    """惰性开户时间戳（仅账户行建行用；订单时间口径取 bar.trade_date）。"""
    return datetime.now().isoformat(timespec="seconds")


def _record_from_row(row: Mapping[str, Any]) -> OrderRecord:
    """orders 表行 → OrderRecord（列名与 dataclass 字段同名逐字）。"""
    return OrderRecord(
        order_id=str(row["order_id"]),
        symbol=str(row["symbol"]),
        side=str(row["side"]),
        qty=int(row["qty"]),
        status=str(row["status"]),
        reason=row["reason"] if row["reason"] is None else str(row["reason"]),
        fill_price=row["fill_price"] if row["fill_price"] is None else float(row["fill_price"]),
        fee=float(row["fee"]),
        decision_id=str(row["decision_id"]),
        signal_id=str(row["signal_id"]),
        created_at=str(row["created_at"]),
        attempts=int(row["attempts"]),
    )


class PaperBroker(Broker):
    """paper 仿真 broker（默认初始资金 100 万元；spec 场景逐字）。"""

    def __init__(
        self,
        db_path: str | Path | None = None,
        *,
        config: MatchingConfig | None = None,
        initial_cash: float = 1_000_000.0,
        pending_expire_days: int = 3,
    ) -> None:
        if pending_expire_days < 1:
            raise ValueError(f"pending_expire_days 必须为正整数: {pending_expire_days!r}")
        self._db_path = db_path
        self._config = config if config is not None else load_matching_config()
        self._initial_cash = float(initial_cash)
        self._pending_expire_days = int(pending_expire_days)

    # ── 订单 id 与证据链校验 ────────────────────────────────────────────────

    @staticmethod
    def order_id_for(order: OrderIntent) -> str:
        """确定性订单 id：同一信号 + 同一标的 + 同一方向 = 同一笔订单。"""
        return f"{order.signal_id}:{order.symbol}:{order.side}"

    @staticmethod
    def _validate_evidence(order: OrderIntent) -> None:
        """证据链必填（spec：订单记录含 decision_id 与 signal_id）。"""
        if not str(order.decision_id or "").strip():
            raise ValueError("缺少必填字段: decision_id（证据链）")
        if not str(order.signal_id or "").strip():
            raise ValueError("缺少必填字段: signal_id（证据链）")

    # ── 下单 ────────────────────────────────────────────────────────────────

    def place_order(
        self,
        order: OrderIntent,
        *,
        bar: MarketBar,
        position: PositionContext | None = None,
        market_amounts: Sequence[float] | None = None,
    ) -> OrderRecord:
        """下单 → 撮合 → 资金/持仓记账 → 落库（同 order_id 重跑幂等）。"""
        self._validate_evidence(order)
        order_id = self.order_id_for(order)
        now = str(bar.trade_date)
        with execution_conn(self._db_path) as conn:
            ensure_account(conn, self._initial_cash, now)
            existing = read_order(conn, order_id)
            if existing is not None:
                return _record_from_row(existing)
            position_ctx = position
            if order.side == "sell" and position_ctx is None:
                position_ctx = self._position_context(conn, order.symbol)
            fill = match_order(
                Order(code=order.symbol, side=order.side, qty=order.qty),
                bar,
                config=self._config,
                position=position_ctx,
                market_amounts=market_amounts,
            )
            status, reason, fill_price, fee = self._apply_fill(
                conn,
                symbol=order.symbol,
                side=order.side,
                qty=order.qty,
                fill=fill,
                updated_at=now,
            )
            record = OrderRecord(
                order_id=order_id,
                symbol=order.symbol,
                side=order.side,
                qty=order.qty,
                status=status,
                reason=reason,
                fill_price=fill_price,
                fee=fee,
                decision_id=order.decision_id,
                signal_id=order.signal_id,
                created_at=now,
                attempts=0,
            )
            insert_order(conn, record)
        return record

    def _position_context(self, conn: sqlite3.Connection, symbol: str) -> PositionContext | None:
        """库内持仓 → 可卖上下文（全额视作隔日仓位；无持仓 → None）。"""
        stored = read_position(conn, symbol)
        if stored is None:
            return None
        qty = int(stored["qty"])
        return PositionContext(sellable_qty=qty, total_qty=qty)

    def _apply_fill(
        self,
        conn: sqlite3.Connection,
        *,
        symbol: str,
        side: str,
        qty: int,
        fill: FillResult,
        updated_at: str,
    ) -> tuple[str, str | None, float | None, float]:
        """成交处置（资金/持仓写入同事务）→ ``(status, reason, fill_price, fee)``。

        - 未成交（matching 原因码）→ ``pending``，零变动；
        - 买入成交但成交额+fee 超余额 → ``rejected`` + ``INSUFFICIENT_CASH``，零变动；
        - 买入成交 → 扣资金（成交额+fee）+ 加持仓（加权成本含费用）；
        - 卖出成交 → 加资金（成交额−fee）+ 减持仓（清仓删行）。
        """
        if fill.reason is not None:
            return STATUS_PENDING, fill.reason, None, 0.0
        price = float(fill.fill_price or 0.0)
        if side == "buy":
            cost = round(price * qty + fill.fee, 2)
            cash = read_cash(conn)
            if cost > cash + _CASH_TOLERANCE:
                return STATUS_REJECTED, REASON_INSUFFICIENT_CASH, None, 0.0
            write_cash(conn, round(cash - cost, 2), updated_at)
            stored = read_position(conn, symbol)
            old_qty = int(stored["qty"]) if stored is not None else 0
            old_cost = float(stored["avg_cost"]) if stored is not None else 0.0
            new_qty = old_qty + qty
            avg_cost = (old_qty * old_cost + price * qty + fill.fee) / new_qty
            write_position(conn, symbol, new_qty, avg_cost, updated_at)
        else:
            stored = read_position(conn, symbol)
            if stored is None:
                # 撮合放行卖出（调用方显式传了可卖上下文）但账本无持仓 = 调用方错误
                raise ValueError(f"卖出无持仓记录: {symbol}")
            new_qty = int(stored["qty"]) - qty
            if new_qty < 0:
                raise ValueError(f"卖出数量超过持仓: {symbol} {qty} > {stored['qty']}")
            if new_qty == 0:
                delete_position(conn, symbol)
            else:
                write_position(conn, symbol, new_qty, float(stored["avg_cost"]), updated_at)
            proceeds = round(price * qty - fill.fee, 2)
            write_cash(conn, round(read_cash(conn) + proceeds, 2), updated_at)
        return STATUS_FILLED, None, price, fill.fee

    # ── 查询与撤销 ──────────────────────────────────────────────────────────

    def query_positions(self) -> list[dict[str, Any]]:
        """当前持仓列表（symbol 升序；结构与 ``store.list_positions`` 一致）。"""
        return list_positions(db_path=self._db_path)

    def query_cash(self) -> float:
        """当前资金余额（惰性开户：首次读取即落初始资金行）。"""
        with execution_conn(self._db_path) as conn:
            ensure_account(conn, self._initial_cash, _now_iso())
            return read_cash(conn)

    def cancel(self, order_id: str) -> OrderRecord:
        """撤销挂单（仅 ``pending``；撤销 = ``expired`` + reason ``CANCELED``）。"""
        with execution_conn(self._db_path) as conn:
            row = read_order(conn, order_id)
            if row is None:
                raise ValueError(f"订单不存在: {order_id}")
            if str(row["status"]) != STATUS_PENDING:
                raise ValueError(f"仅 pending 订单可撤销（当前 {row['status']}）: {order_id}")
            update_order(
                conn,
                order_id,
                status=STATUS_EXPIRED,
                reason=REASON_CANCELED,
                fill_price=row["fill_price"],
                fee=float(row["fee"]),
                attempts=int(row["attempts"]),
            )
            # 同一事务内已更新：直接用行数据构造终态记录（不回读）
            return _record_from_row({**row, "status": STATUS_EXPIRED, "reason": REASON_CANCELED})

    # ── 挂单重试 / 过期 ────────────────────────────────────────────────────

    def retry_pending(self, as_of: str, *, bars: Mapping[str, MarketBar]) -> dict[str, int]:
        """对全部挂单按当日 bar 重撮合（返回逐类计数摘要）。

        - 无 bar 的标的 → ``skipped_no_bar``（数据缺失不是一次失败尝试）；
        - 重撮合成交 → ``filled``（走与下单相同的记账路径）；
        - 仍未成交（或资金不足被拒）→ ``attempts+1``；attempts 达到
          ``pending_expire_days`` → ``expired``（保留最后一次未成交原因码）。
        """
        summary = {
            "retried": 0,
            "filled": 0,
            "rejected": 0,
            "pending": 0,
            "expired": 0,
            "skipped_no_bar": 0,
        }
        with execution_conn(self._db_path) as conn:
            ensure_account(conn, self._initial_cash, str(as_of))
            for row in list_pending_orders(conn):
                symbol = str(row["symbol"])
                bar = bars.get(symbol)
                if bar is None:
                    summary["skipped_no_bar"] += 1
                    continue
                summary["retried"] += 1
                # 写入侧（place_order/_validate）只落 buy/sell，此处窄化类型供撮合
                side = cast("Literal['buy', 'sell']", str(row["side"]))
                qty = int(row["qty"])
                fill = match_order(
                    Order(code=symbol, side=side, qty=qty),
                    bar,
                    config=self._config,
                    position=self._position_context(conn, symbol),
                    market_amounts=None,  # 重试无全市场成交额样本 → matching 取最保守档
                )
                status, reason, fill_price, fee = self._apply_fill(
                    conn,
                    symbol=symbol,
                    side=side,
                    qty=qty,
                    fill=fill,
                    updated_at=str(as_of),
                )
                attempts = int(row["attempts"]) + 1
                if status == STATUS_PENDING and attempts >= self._pending_expire_days:
                    status = STATUS_EXPIRED
                update_order(
                    conn,
                    str(row["order_id"]),
                    status=status,
                    reason=reason,
                    fill_price=fill_price,
                    fee=fee,
                    attempts=attempts,
                )
                if status == STATUS_FILLED:
                    summary["filled"] += 1
                elif status == STATUS_REJECTED:
                    summary["rejected"] += 1
                elif status == STATUS_EXPIRED:
                    summary["expired"] += 1
                else:
                    summary["pending"] += 1
        return summary


# ── 每日结算（B2）：持仓 mark → paper_equity 曲线 ───────────────────────────


def _valid_close(value: object) -> float | None:
    """收盘价有效性：None/不可转数值/非有限 → None（不可用于 mark）。"""
    if value is None:
        return None
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _snapshot_code(symbol: str) -> str:
    """'600519.SH' → '600519'（快照 code 为 6 位裸码；同 outcome 侧 _code 口径）。"""
    return str(symbol).split(".")[0]


def settle_daily(
    as_of: str,
    *,
    close_prices: Mapping[str, float],
    db_path: str | Path | None = None,
) -> dict[str, Any]:
    """按收盘价 mark 持仓 → ``paper_equity`` 曲线（trade_date 幂等 upsert）。

    净值口径：``equity = cash + Σ(qty×close)``；收盘价由调用方注入（生产入口
    ``run_paper_settle_daily`` 从快照库取；回放/核对可注入合成面板）。持仓标的
    缺有效收盘价 → ``ValueError``（fail loud，不编造价格）。无持仓时净值 = 现金
    （账户曲线从纯现金起步，不是编造）。账户尚未建行时按 spec 默认初始资金
    100 万建行（``ensure_account`` 不覆盖已有余额）。返回写入的净值字典。
    """
    trade_date = str(as_of or "").strip()
    if not trade_date:
        raise ValueError("缺少必填字段: as_of（结算交易日）")
    close_map = {
        str(symbol): price
        for symbol, value in close_prices.items()
        if (price := _valid_close(value)) is not None
    }
    with execution_conn(db_path) as conn:
        ensure_account(conn, 1_000_000.0, trade_date)
        cash = read_cash(conn)
        positions = read_positions(conn)
        missing = [str(p["symbol"]) for p in positions if str(p["symbol"]) not in close_map]
        if missing:
            raise ValueError(f"缺收盘价（不编造价格）: {', '.join(sorted(missing))}")
        positions_value = round(
            sum(int(p["qty"]) * close_map[str(p["symbol"])] for p in positions), 2
        )
        equity = round(cash + positions_value, 2)
        write_paper_equity(conn, trade_date, cash, positions_value, equity)
    return {
        "trade_date": trade_date,
        "cash": cash,
        "positions_value": positions_value,
        "equity": equity,
    }


def run_paper_settle_daily() -> dict[str, Any]:
    """ops 日批入口（B2）：取快照最新交易日与持仓标的 ``close_raw`` → ``settle_daily``。

    - 快照库为空（无任何数据行）→ 跳过摘要 ``reason="no_snapshot_data"``（部署初期
      常态，不抛错；旁路铁律下 job 记 ok 摘要）；
    - 持仓标的在快照中缺有效收盘价 → 由 ``settle_daily`` 抛错（job 记 failed、
      按重试策略处理，unfail 前不落错日期净值）；
    - 快照行取 ``close_raw``（真实盘面价，计划 B2 口径）；symbol `600519.SH` 与
      快照 code `600519` 的映射经 ``_snapshot_code`` 去后缀。
    """
    from finance_agent.data.snapshot import latest_snapshot_date, read_bars

    as_of = latest_snapshot_date()
    if as_of is None:
        return {
            "trade_date": None,
            "cash": None,
            "positions_value": None,
            "equity": None,
            "reason": "no_snapshot_data",
        }
    positions = list_positions()
    close_prices: dict[str, float] = {}
    if positions:
        codes = sorted({_snapshot_code(str(p["symbol"])) for p in positions})
        frame = read_bars(codes, as_of, as_of, as_of=as_of)
        by_code = {
            str(row["code"]): price
            for row in frame[["code", "close_raw"]].to_dict("records")
            if (price := _valid_close(row["close_raw"])) is not None
        }
        close_prices = {
            str(p["symbol"]): by_code[code]
            for p in positions
            if (code := _snapshot_code(str(p["symbol"]))) in by_code
        }
    return {**settle_daily(as_of, close_prices=close_prices), "reason": None}
