"""执行链日批驱动器（add-execution-chain-driver）：决策 → 信号 → 目标 → 门禁 → 下单。

设计（design.md，实施者不得自行改口径）：

- **驱动器 = ops 日批 job**（``paper_execute_daily``，16:10，先于 paper 结算 16:20）：
  复用统一通道（调度/历史/手动补跑/单飞锁），当日成交当日入账；
- **入场口径 = 决策日次一交易日开盘**：本驱动器只处理 ``timestamp[:10] < as_of`` 的
  决策；job 在执行日 16:10 用当日快照 bar 撮合（fill = 开盘价 × (1 ± 滑点)，M1）
  ——「昨日及更早的未执行信号，在今日开盘成交」，不引入同日盘中决策的回溯偏误；
- **幂等锚点**：信号以 ``decision_id`` 唯一（重复执行不重复生成）；订单 ``order_id``
  由 ``signal_id`` 派生（``PaperBroker.order_id_for``）——日批重试 / 手动补跑 /
  重复执行三态下均不产生重复订单、不重复动账。

五段编排（全程纯代码、无 LLM）：

① **决策 → 信号**：``decision_log`` 中 ``status='open'`` 且决策日早于执行日的决策
   → ``signal_from_decision(approved=True, source="decision_log")`` → ``record_signal``。
   审批语义：decision_log 的唯一生产写入方（``api._persist_decision_log``）只在
   ``fund_manager_decision == "approve"`` 时落行——库内行即审批通过决策，故
   ``approved=True``（库表无 verdict 列，不存在未审批行需过滤）。
   幂等：已存在信号的 ``decision_id`` 集合（全量 ``list_signals``）去重。
② **信号 → 目标**：尚无订单的 signal（``signal_ids_with_orders`` 判定）为待执行集；
   ``long`` 集合经 ``aggregate_targets(method="equal")`` 得目标权重（单票 20% /
   总仓 80% 上限与缩减记录复用既有实现）；``flat`` 单独处理（目标权重 0）。
③ **目标 → 订单**：目标市值 = ``weight × equity``（equity = 现金 + 持仓按快照
   ``close_raw`` 市值；缺行/缺价的标的跳过并记 ``skipped_symbols``）；目标股数 =
   ``floor(目标市值 / 开盘价 / 100) × 100``；与当前持仓比较——**仅当目标 > 当前时
   生成买入 delta 订单**（目标 ≤ 当前不生成订单；**MUST NOT 卖出已有仓位**——
   再平衡/退出属后续 delta，本 delta 的退出仅由 ``flat`` 信号触发）；``flat`` 信号
   且当前持仓 > 0 → 生成全部卖出订单（零股尾数照卖，同 matching 语义）。
   处理顺序：先 ``flat``（退出释放敞口/资金），再 ``long``（入场）；同一 run 内
   每条信号恰好执行一次。
④ **门禁逐单**：``risk_gate.check_order``（纯代码、无 LLM；ST/停牌/一字板/单票
   上限/总仓上限/T+1/整手）；被拒 → ``record_order_rejection`` 以 ``rejected`` +
   逐字原因码落 orders（不静默丢弃），计入摘要 ``rejected``。
⑤ **下单**：通过者经 ``PaperBroker.place_order``（撮合唯一实现 ``match_order``）
   以执行日开盘口径成交；``filled`` → ``orders_placed``，不可成交（一字板等）
   → ``pending``（沿用挂单与次日重试）；下单侧拒单（``INSUFFICIENT_CASH`` 等）
   落库由 broker 完成，计入 ``rejected``。

**快照守卫**：``as_of`` 缺省取快照库最新交易日；库为空（或显式 as_of 但库内无
任何数据）→ 跳过并返回 ``skipped_reason="no_snapshot_data"`` 摘要（不生成信号、
零订单、不伪造成交；信号自然顺延至下一交易日——同 ``run_paper_settle_daily`` 先例）。

已知边界：决策扫描经 ``outcome.store.list_decisions``（单次上限 1000 行，倒序取
最新）；open 决策超过该行数时更早的行本轮不处理，后续轮次处理（信号生成本身幂等，
不丢不重）。
"""

from __future__ import annotations

import logging
import math
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from finance_agent.data.snapshot import (
    SnapshotEmptyError,
    latest_snapshot_date,
    read_bars,
    read_universe,
)
from finance_agent.execution.brokers.base import STATUS_FILLED, STATUS_PENDING
from finance_agent.execution.brokers.paper import PaperBroker
from finance_agent.execution.matching import MarketBar, PositionContext
from finance_agent.execution.portfolio import aggregate_targets
from finance_agent.execution.risk_gate import OrderIntent, check_order
from finance_agent.execution.signals import Signal, SignalContractError, signal_from_decision
from finance_agent.execution.store import (
    list_signals,
    record_order_rejection,
    record_signal,
    signal_ids_with_orders,
)
from finance_agent.outcome.store import list_decisions
from finance_agent.screening.constraints import LOT_SIZE

logger = logging.getLogger(__name__)

# 信号来源标记（decision_log 为唯一决策真源；spec 信号契约的 source 字段）
_SIGNAL_SOURCE = "decision_log"

# 决策扫描上限（list_decisions 的 limit 上限；见模块 docstring「已知边界」）
_DECISION_SCAN_LIMIT = 1000


@dataclass(frozen=True)
class _BarContext:
    """快照行派生的标的上下文：撮合 bar + 门禁状态位（同源一行，下单循环不二次读库）。"""

    bar: MarketBar
    is_st: bool
    is_suspended: bool


@dataclass(frozen=True)
class _PlannedOrder:
    """意图生成期定型的订单包：意图 + bar + 门禁上下文 + 权重快照。"""

    intent: OrderIntent
    bar: MarketBar
    position: PositionContext | None
    is_st: bool
    is_suspended: bool
    current_weight: float
    projected_total_weight: float


def _finite(value: Any) -> float | None:
    """数值强转；None/NaN/inf/不可解析 → None（不编造价格，同 execution.modes._finite）。"""
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _positive(value: Any) -> float | None:
    """有限正数（价格上下文：0/负/NaN 视为数据缺失，不编造）。"""
    number = _finite(value)
    if number is None or number <= 0:
        return None
    return number


def _flag(value: Any) -> bool:
    """快照状态位（INTEGER 0/1；NULL/NaN → False，bool(NaN) 为 True 故显式判）。"""
    number = _finite(value)
    return number is not None and number != 0.0


def _snapshot_code(symbol: str) -> str:
    """``'600519.SH'`` → ``'600519'``（快照 code 为 6 位裸码；同 paper._snapshot_code 口径）。"""
    return str(symbol).split(".")[0]


def _symbol_for_ticker(ticker: Any) -> str:
    """decision_log ticker（6 位裸码）→ 库内 symbol（同 ingest 口径：6 开头 .SH，否则 .SZ）。"""
    code = str(ticker or "").strip()
    if not code:
        return ""
    if "." in code:
        return code
    return f"{code}.SH" if code.startswith("6") else f"{code}.SZ"


def _weight(value: float, equity: float) -> float:
    """组合权重（分母 equity 非正时按 0——权重仅作门禁输入，不反推金额）。"""
    return value / equity if equity > 0 else 0.0


def _bar_from_row(row: Mapping[Any, Any], trade_date: str) -> MarketBar | None:
    """快照行 → MarketBar（一字板状态位取快照 D3 已算好的列，不重复判 OHLC）。

    OHLC 任一缺失/非正 → None（数据缺失按缺 bar 处理，不编造价格）；成交额缺失
    → None（matching M4 取最保守滑点档）。行可来自 ``read_bars`` 的 DataFrame
    记录（键为列名，类型经 pandas 宽化，故接受任意键型）。
    """
    open_ = _positive(row.get("open"))
    high = _positive(row.get("high"))
    low = _positive(row.get("low"))
    close = _positive(row.get("close_raw"))
    if open_ is None or high is None or low is None or close is None:
        return None
    return MarketBar(
        trade_date=trade_date,
        open=open_,
        high=high,
        low=low,
        close=close,
        prev_close=_positive(row.get("prev_close")),
        amount=_finite(row.get("amount")),
        limit_up_sealed=_flag(row.get("limit_up_sealed")),
        limit_down_sealed=_flag(row.get("limit_down_sealed")),
    )


def _first_signal_by_symbol(signals: list[Signal]) -> dict[str, Signal]:
    """symbol → 最早信号（created_at、signal_id 升序 tie-break；证据链归属确定）。"""
    first: dict[str, Signal] = {}
    for signal in sorted(signals, key=lambda item: (item.created_at, item.signal_id)):
        first.setdefault(signal.symbol, signal)
    return first


def _load_bar_contexts(
    trade_date: str,
    symbols: list[str] | set[str],
    snapshot_db_path: str | Path | None,
) -> dict[str, _BarContext]:
    """按标的集合读快照行 → ``{symbol: _BarContext}``（缺行/缺价标的不在结果里）。

    ``read_bars`` 对该日/该代码集无行 → 空映射（逐标的按缺失跳过，不伪造成交）。
    """
    by_code = {_snapshot_code(symbol): symbol for symbol in symbols}
    codes = sorted(by_code)
    if not codes:
        return {}
    try:
        frame = read_bars(codes, trade_date, trade_date, as_of=trade_date, db_path=snapshot_db_path)
    except SnapshotEmptyError:
        return {}
    contexts: dict[str, _BarContext] = {}
    for row in frame.to_dict("records"):
        symbol = by_code.get(str(row.get("code") or ""))
        if symbol is None or symbol in contexts:
            continue
        bar = _bar_from_row(row, trade_date)
        if bar is None:
            continue
        contexts[symbol] = _BarContext(
            bar=bar,
            is_st=_flag(row.get("is_st")),
            is_suspended=_flag(row.get("is_suspended")),
        )
    return contexts


def _create_signals_from_decisions(
    trade_date: str,
    *,
    db_path: str | Path | None,
    execution_db_path: str | Path | None,
    skipped_symbols: set[str],
) -> int:
    """① 决策 → 信号（幂等）：返回本次新落库的信号数。"""
    known_decision_ids = {signal.decision_id for signal in list_signals(db_path=execution_db_path)}
    decisions = list_decisions(status="open", limit=_DECISION_SCAN_LIMIT, db_path=db_path)
    created = 0
    for decision in sorted(
        decisions,
        key=lambda item: (str(item.get("timestamp") or ""), str(item.get("decision_id") or "")),
    ):
        decision_id = str(decision.get("decision_id") or "").strip()
        if not decision_id or decision_id in known_decision_ids:
            continue
        timestamp = str(decision.get("timestamp") or "").strip()
        if len(timestamp) < 10:
            logger.warning("决策 %s 缺有效 timestamp，跳过", decision_id)
            continue
        if timestamp[:10] >= trade_date:
            continue  # 决策日次一交易日口径：当日及更晚的决策不在本执行日
        symbol = _symbol_for_ticker(decision.get("ticker"))
        if not symbol:
            logger.warning("决策 %s 缺有效 ticker，跳过", decision_id)
            continue
        try:
            signal = signal_from_decision(
                {**decision, "symbol": symbol},
                decision_id=decision_id,
                source=_SIGNAL_SOURCE,
                trade_date=trade_date,
                created_at=timestamp[:10],
                approved=True,  # decision_log 仅存审批通过决策（见模块 docstring ①）
            )
        except SignalContractError as exc:
            # 单条历史脏数据不得阻断整条日批：记 warning + skipped 留痕，不落库不伪造
            logger.warning("决策 %s 转换失败，跳过: %s", decision_id, exc)
            skipped_symbols.add(symbol)
            continue
        record_signal(signal, db_path=execution_db_path)
        known_decision_ids.add(decision_id)
        created += 1
    return created


def _plan_flat_exits(
    signals: list[Signal],
    contexts: Mapping[str, _BarContext],
    qty_now: dict[str, int],
    *,
    equity: float,
    projected_value: float,
    skipped_symbols: set[str],
) -> tuple[list[_PlannedOrder], float]:
    """③-a flat 退出：持仓标的生成全部卖出意图（零股尾数照卖）。"""
    planned: list[_PlannedOrder] = []
    for symbol, signal in sorted(_first_signal_by_symbol(signals).items()):
        qty = int(qty_now.get(symbol, 0))
        if qty <= 0:
            continue
        context = contexts.get(symbol)
        if context is None:
            skipped_symbols.add(symbol)
            continue
        current_weight = _weight(qty * context.bar.close, equity)
        projected_value -= qty * context.bar.close
        planned.append(
            _PlannedOrder(
                intent=OrderIntent(
                    decision_id=signal.decision_id,
                    signal_id=signal.signal_id,
                    symbol=symbol,
                    side="sell",
                    qty=qty,
                    target_weight=0.0,  # flat = 平仓目标 0
                ),
                bar=context.bar,
                # 日初持仓视作隔日可卖（T+1）；当日买入不在本意图集内
                position=PositionContext(sellable_qty=qty, total_qty=qty),
                is_st=context.is_st,
                is_suspended=context.is_suspended,
                current_weight=current_weight,
                projected_total_weight=_weight(projected_value, equity),
            )
        )
        qty_now[symbol] = 0
    return planned, projected_value


def _plan_long_entries(
    signals: list[Signal],
    contexts: Mapping[str, _BarContext],
    qty_now: dict[str, int],
    *,
    equity: float,
    projected_value: float,
    skipped_symbols: set[str],
) -> tuple[list[_PlannedOrder], float]:
    """③-b long 入场：按聚合目标权重与当前持仓差分，只补不缩（delta 整手）。"""
    planned: list[_PlannedOrder] = []
    first_long = _first_signal_by_symbol(signals)
    for target in aggregate_targets(signals, method="equal"):
        symbol = target.symbol
        signal = first_long.get(symbol)
        context = contexts.get(symbol)
        if signal is None or context is None:
            skipped_symbols.add(symbol)
            continue
        target_value = target.weight * equity
        target_shares = int(target_value / context.bar.open / LOT_SIZE) * LOT_SIZE
        current = int(qty_now.get(symbol, 0))
        delta = ((target_shares - current) // LOT_SIZE) * LOT_SIZE
        if delta <= 0:
            continue  # 目标 ≤ 当前：不生成订单（MUST NOT 卖出已有仓位）
        current_weight = _weight(current * context.bar.close, equity)
        projected_value += delta * context.bar.close
        planned.append(
            _PlannedOrder(
                intent=OrderIntent(
                    decision_id=signal.decision_id,
                    signal_id=signal.signal_id,
                    symbol=symbol,
                    side="buy",
                    qty=delta,
                    target_weight=target.weight,
                ),
                bar=context.bar,
                position=None,  # 买入不受 T+1 约束
                is_st=context.is_st,
                is_suspended=context.is_suspended,
                current_weight=current_weight,
                projected_total_weight=_weight(projected_value, equity),
            )
        )
        qty_now[symbol] = current + delta
    return planned, projected_value


def _skip_summary() -> dict[str, Any]:
    """快照不可用跳过摘要（逐字；同 run_paper_settle_daily 先例：不抛错、零订单）。"""
    return {
        "trade_date": None,
        "skipped_reason": "no_snapshot_data",
        "signals_created": 0,
        "orders_placed": 0,
        "rejected": 0,
        "pending": 0,
    }


def run_execution_daily(
    as_of: str | None = None,
    *,
    db_path: str | Path | None = None,
    execution_db_path: str | Path | None = None,
    snapshot_db_path: str | Path | None = None,
) -> dict[str, Any]:
    """执行链日批入口：五段编排（见模块 docstring），返回摘要字典。

    参数：``as_of`` 执行日（缺省取快照库最新交易日；决策日次一交易日口径由
    ``timestamp[:10] < as_of`` 保证）；``db_path`` 决策库（缺省 env
    ``SESSIONS_DB_PATH``）；``execution_db_path`` 执行库（缺省 env
    ``EXECUTION_DB_PATH``）；``snapshot_db_path`` 快照库（缺省 env
    ``SNAPSHOT_DB_PATH``）。返回 ``{trade_date, signals_created, orders_placed,
    rejected, pending, skipped_reason, skipped_symbols}``；快照不可用时返回
    ``skipped_reason="no_snapshot_data"`` 的零摘要（键集略窄，见 ``_skip_summary``）。
    """
    latest = latest_snapshot_date(snapshot_db_path)
    trade_date = (str(as_of).strip() if as_of is not None else "") or latest
    if not trade_date or latest is None:
        # 快照库为空（含显式 as_of 但库内无任何数据）：跳过，不生成信号不下单
        return _skip_summary()
    try:
        # 全市场样本：① 该日行情存在性（无行 → 快照不可用）；② 滑点档位分位样本（M4）
        universe = read_universe(trade_date, db_path=snapshot_db_path)
    except SnapshotEmptyError:
        return _skip_summary()
    market_amounts = [
        value for row in universe if (value := _finite(row.get("amount"))) is not None
    ]

    skipped_symbols: set[str] = set()
    signals_created = _create_signals_from_decisions(
        trade_date,
        db_path=db_path,
        execution_db_path=execution_db_path,
        skipped_symbols=skipped_symbols,
    )

    broker = PaperBroker(db_path=execution_db_path)
    cash = broker.query_cash()
    qty_now: dict[str, int] = {
        str(position["symbol"]): int(position["qty"]) for position in broker.query_positions()
    }
    ordered_signal_ids = signal_ids_with_orders(db_path=execution_db_path)
    pending_signals = [
        signal
        for signal in list_signals(db_path=execution_db_path)
        if signal.signal_id not in ordered_signal_ids
    ]

    # 需要行情的标的：现持仓（估值/平仓）+ 待执行信号标的（建仓/平仓）
    needed_symbols = {*qty_now, *(signal.symbol for signal in pending_signals)}
    contexts = _load_bar_contexts(trade_date, needed_symbols, snapshot_db_path)
    for symbol in qty_now:
        if symbol not in contexts:
            skipped_symbols.add(symbol)  # 持仓缺价：不计入市值（保守，不编造价格）
    marked_value = round(
        sum(
            qty * contexts[symbol].bar.close
            for symbol, qty in qty_now.items()
            if symbol in contexts
        ),
        2,
    )
    equity = round(cash + marked_value, 2)
    projected_value = marked_value

    planned: list[_PlannedOrder] = []
    # ③-a 退出先行（释放敞口与资金），再 ③-b 入场
    flat_signals = [signal for signal in pending_signals if signal.direction == "flat"]
    flat_planned, projected_value = _plan_flat_exits(
        flat_signals,
        contexts,
        qty_now,
        equity=equity,
        projected_value=projected_value,
        skipped_symbols=skipped_symbols,
    )
    planned.extend(flat_planned)
    long_signals = [signal for signal in pending_signals if signal.direction == "long"]
    long_planned, projected_value = _plan_long_entries(
        long_signals,
        contexts,
        qty_now,
        equity=equity,
        projected_value=projected_value,
        skipped_symbols=skipped_symbols,
    )
    planned.extend(long_planned)

    # ④ 门禁逐单 → ⑤ 下单
    orders_placed = 0
    rejected = 0
    pending_count = 0
    for item in planned:
        gate = check_order(
            item.intent,
            current_weight=item.current_weight,
            projected_total_weight=item.projected_total_weight,
            bar=item.bar,
            position=item.position,
            is_st=item.is_st,
            is_suspended=item.is_suspended,
        )
        if not gate.approved:
            reason = str(gate.reason)
            record_order_rejection(
                PaperBroker.order_id_for(item.intent),
                item.intent,
                reason,
                trade_date,
                db_path=execution_db_path,
            )
            rejected += 1
            logger.warning(
                "门禁拒绝 %s %s %s 股: %s",
                item.intent.symbol,
                item.intent.side,
                item.intent.qty,
                reason,
            )
            continue
        record = broker.place_order(
            item.intent,
            bar=item.bar,
            position=item.position,
            market_amounts=market_amounts,
        )
        if record.status == STATUS_FILLED:
            orders_placed += 1
        elif record.status == STATUS_PENDING:
            pending_count += 1
        else:
            # 下单侧拒单（INSUFFICIENT_CASH 等）：broker 已落 rejected 行，照计数
            rejected += 1

    return {
        "trade_date": trade_date,
        "signals_created": signals_created,
        "orders_placed": orders_placed,
        "rejected": rejected,
        "pending": pending_count,
        "skipped_reason": None,
        "skipped_symbols": sorted(skipped_symbols),
    }
