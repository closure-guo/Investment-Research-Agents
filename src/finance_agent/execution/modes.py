"""回测/仿真/实盘三模式数据源抽象（add-matching-engine Task 3）。

三模式共享同一 ``match_order``（spec「单一撮合实现」）；本模块只负责
「运行模式 → bar 数据源」的解耦接线：

- backtest：hfq 全量 K 线（与结算同源）按 code 持有；bar 由行构造，
  ``prev_close`` 取区间上一交易日收盘，sealed 位按 M2（``sealed_flags_from_ohlc``）；
- paper：调用方预建的 ``MarketBar`` 表（真实盘面价；由快照/日批侧构造）；
- live：仅留抽象，全方法 ``NotImplementedError``（本 change 不实现券商接口）。

口径护栏（Global Constraints）：backtest 源输入一律为已过护栏的 hfq 全量 K 线；
paper 侧取 ``market_snapshot`` 真实盘面价。两者只共享引擎，不共享价格序列。

``match_for_mode`` 签名相对计划的增补：计划签名（``mode/order/source/config/
position/market_amounts``）没有执行日参数，而 backtest/paper 分派必须经
``source.bar(code, trade_date)`` 才能取到 bar——故增补 keyword-only 可选
``trade_date``（原参数逐个保留，属超集不破坏既有调用）；backtest/paper 缺执行日
或无 bar（数据缺失）时 fail loud（``ValueError``，数据缺失不是业务原因码）。
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Any, Protocol

import pandas as pd

from finance_agent.execution.matching import (
    FillResult,
    MarketBar,
    MatchingConfig,
    Order,
    PositionContext,
    match_order,
    sealed_flags_from_ohlc,
)

# 实盘占位消息（逐字；计划 Task 3 Interfaces）
LIVE_NOT_IMPLEMENTED_MESSAGE = "实盘接口未实现（本 change 仅留抽象）"

# BacktestDataSource 必需 K 线列（hfq 全量 K 线：日期/开盘/最高/最低/收盘；成交额可选）
_REQUIRED_KLINE_COLUMNS = ("日期", "开盘", "最高", "最低", "收盘")


class MarketDataSource(Protocol):
    """三模式数据源协议：按 code + 交易日取 bar、按交易日取下一交易日。"""

    mode: str

    def bar(self, code: str, trade_date: str) -> MarketBar | None: ...

    def next_trading_day(self, trade_date: str) -> str | None: ...


def _norm_date(value: Any) -> str:
    """日期归一 ``YYYY-MM-DD``（str/date/datetime/Timestamp 通用；取前 10 位）。"""
    return str(value)[:10]


def _finite(value: Any) -> float | None:
    """数值强转；None/NaN/inf/不可解析 → None（不编造价格，与 ``snapshot._num`` 同哲学）。"""
    if value is None:
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(out):
        return None
    return out


class BacktestDataSource:
    """回测数据源：hfq 全量 K 线按 code 持有（与结算同源口径）。

    构造时按日期升序归一（不依赖输入行序），并建日期→行号索引供 O(1) 取行：
    ``prev_close`` = 升序后上一行收盘（区间首行为 None，不编造昨收）。
    """

    mode = "backtest"

    def __init__(self, kline_by_code: dict[str, pd.DataFrame]) -> None:
        self._frames: dict[str, pd.DataFrame] = {}
        self._date_index: dict[str, dict[str, int]] = {}
        for code, frame in (kline_by_code or {}).items():
            if frame is None or frame.empty:
                continue
            missing = [col for col in _REQUIRED_KLINE_COLUMNS if col not in frame.columns]
            if missing:
                raise ValueError(f"K 线缺必要列 {missing}: {code}")
            ordered = frame.sort_values("日期").reset_index(drop=True)
            self._frames[str(code)] = ordered
            self._date_index[str(code)] = {
                _norm_date(value): idx for idx, value in enumerate(ordered["日期"])
            }

    def bar(self, code: str, trade_date: str) -> MarketBar | None:
        """按 code + 日期取 bar；未登记代码/该日无行/关键价缺失 → None（不编造）。"""
        frame = self._frames.get(code)
        date_index = self._date_index.get(code)
        if frame is None or date_index is None:
            return None
        idx = date_index.get(trade_date)
        if idx is None:
            return None
        row = frame.iloc[idx]
        open_ = _finite(row["开盘"])
        high = _finite(row["最高"])
        low = _finite(row["最低"])
        close = _finite(row["收盘"])
        if open_ is None or high is None or low is None or close is None:
            return None  # 关键价缺失：按缺数据拒绝，不编造
        prev_close = _finite(frame.iloc[idx - 1]["收盘"]) if idx > 0 else None
        amount = _finite(row["成交额"]) if "成交额" in frame.columns else None
        limit_up_sealed, limit_down_sealed = sealed_flags_from_ohlc(
            open_, high, low, close, prev_close
        )
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

    def next_trading_day(self, trade_date: str) -> str | None:
        """下一交易日：全部标的日期并集（交易日历）中 > 该日的首日；无 → None。"""
        dates = sorted({day for index in self._date_index.values() for day in index})
        for day in dates:
            if day > trade_date:
                return day
        return None


class PaperDataSource:
    """仿真数据源：调用方预建的 ``{code: {trade_date: MarketBar}}``（真实盘面价）。"""

    mode = "paper"

    def __init__(self, bars_by_code: dict[str, dict[str, MarketBar]]) -> None:
        self._bars: dict[str, dict[str, MarketBar]] = {
            str(code): dict(per_date) for code, per_date in (bars_by_code or {}).items()
        }

    def bar(self, code: str, trade_date: str) -> MarketBar | None:
        """按 code + 日期取预建 bar；未登记 → None。"""
        per_date = self._bars.get(code)
        if per_date is None:
            return None
        return per_date.get(trade_date)

    def next_trading_day(self, trade_date: str) -> str | None:
        """下一交易日：全部已登记日期的并集中 > 该日的首日；无 → None。"""
        dates = sorted({day for per_date in self._bars.values() for day in per_date})
        for day in dates:
            if day > trade_date:
                return day
        return None


class LiveDataSource:
    """实盘数据源占位（本 change 不实现券商接口；全方法 NotImplementedError）。"""

    mode = "live"

    def bar(self, code: str, trade_date: str) -> MarketBar | None:
        raise NotImplementedError(LIVE_NOT_IMPLEMENTED_MESSAGE)

    def next_trading_day(self, trade_date: str) -> str | None:
        raise NotImplementedError(LIVE_NOT_IMPLEMENTED_MESSAGE)


def match_for_mode(
    mode: str,
    order: Order,
    *,
    source: MarketDataSource,
    config: MatchingConfig,
    position: PositionContext | None = None,
    market_amounts: Sequence[float] | None = None,
    trade_date: str | None = None,
) -> FillResult:
    """按运行模式把订单交给同一 ``match_order``（spec「单一撮合实现」）。

    - ``mode == "live"`` → ``NotImplementedError``（实盘仅留抽象）；
    - 未知 mode → ``ValueError``；
    - ``mode ∈ {"backtest", "paper"}``：``trade_date`` 必给（执行日；回放语义 =
      决策日次一交易日），取 ``source.bar(order.code, trade_date)`` 后调
      ``match_order``；缺执行日或无 bar（数据缺失）→ ``ValueError``。
    """
    if mode == "live":
        raise NotImplementedError(LIVE_NOT_IMPLEMENTED_MESSAGE)
    if mode not in ("backtest", "paper"):
        raise ValueError(f"未知运行模式: {mode!r}（支持 backtest/paper/live）")
    if trade_date is None:
        raise ValueError(f"match_for_mode({mode}) 需要 trade_date 才能定位数据源 bar")
    bar = source.bar(order.code, trade_date)
    if bar is None:
        raise ValueError(
            f"数据源无 bar: mode={mode!r} code={order.code!r} trade_date={trade_date!r}"
        )
    return match_order(
        order,
        bar,
        config=config,
        position=position,
        market_amounts=market_amounts,
    )
