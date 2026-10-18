"""候选池前向收益跟踪与分层统计（add-screening-evaluation Task 1 / 计划 V1）。

口径（spec evaluation「候选池前向命中率」；与决策回放 ``evals/backtest/replay``
相同的时点截断纪律与撮合口径，实施者不得自行改口径）：

- **入场**：池日 ``D`` 的次一交易日（``D+1``）开盘经系统唯一撮合引擎
  （``execution.matching.match_order``，买 1 手）定价——成交价 =
  ``round(open × (1 + 滑点), 2)``；一字涨停买单**不成交** → 记原因码并剔除
  （fail-closed，绝不假设按涨停价成交）；回放无全市场成交额样本 → 滑点取最保守档
  （``amount=None`` 语义，同 ``replay._entry_bar``）。
- **出场**：入场日后第 ``horizon`` 个交易日**收盘**（同一面板序列），
  收益 = ``exit_close / entry_price − 1``（费用不折算，报告披露）。
- **时点截断**：``as_of`` 给定 → 只用 ≤ ``as_of`` 的面板行；入场/出场任一落在截断
  之外 → 剔除并计原因（``NO_NEXT_TRADING_DAY`` / ``INSUFFICIENT_FORWARD_DATA``）；
  池日 > ``as_of`` → ``POOL_DATE_AFTER_AS_OF``。任何统计只消费 ≤ ``as_of`` 数据。
- **分组**（``forward_pool_returns``）：入选组 = ``selection_pool`` 当日行；落选组 =
  当日 ``l2_candidates`` 中不在池内的标的。
- **统计**：每组每 horizon ``{n, mean, win_rate, days, excluded_counts, unfilled_counts}``；
  配对置信区间以**池日**为配对单元（同日先取组内均值，再对池日重采样
  mean-diff 的 95% 百分位 CI，``evals.stats.paired_bootstrap_ci`` 单一实现，
  B=10000 / seed=42 可复现）。配对日 < ``MIN_PAIRED_DAYS`` → 不产 CI（单日
  bootstrap 是伪精度）。

数据来源：screening 库（``selection_pool`` / ``l2_candidates``，``db_path`` 显式注入）
+ ``factors.eval_engine.MarketPanel``（前复权六字段面板，测试用合成面板）。
"""

from __future__ import annotations

import bisect
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd

from evals.stats import paired_bootstrap_ci
from finance_agent.execution.config import load_matching_config
from finance_agent.execution.matching import (
    REASON_LIMIT_UP_UNFILLABLE,
    MarketBar,
    MatchingConfig,
    Order,
    match_order,
    sealed_flags_from_ohlc,
)
from finance_agent.factors.eval_engine import MarketPanel
from finance_agent.screening.store import POOL_MAIN, list_l2_candidates, list_selection_pool

# 默认 horizon（Global Constraints：5/10/20 交易日）
DEFAULT_HORIZONS: tuple[int, ...] = (5, 10, 20)
# 入场委托数量：单笔 1 手（同回放 ``_REPLAY_ORDER_QTY``；成交可行性与滑点不依赖数量）
ENTRY_ORDER_QTY = 100
# 配对日下限：< 2 个配对池日不产 CI（bootstrap 对单日重采样是伪精度）
MIN_PAIRED_DAYS = 2
# 配对 bootstrap 规格（B=10000 固定；seed 固定保证 CI 可回归）
BOOTSTRAP_B = 10_000
BOOTSTRAP_SEED = 42

# 剔除原因码（跨模块契约；撮合引擎原因码直接复用 matching 常量，不另造口径）
REASON_MISSING_ENTRY_BAR = "MISSING_ENTRY_BAR"
REASON_NO_NEXT_TRADING_DAY = "NO_NEXT_TRADING_DAY"
REASON_POOL_DATE_AFTER_AS_OF = "POOL_DATE_AFTER_AS_OF"
REASON_INSUFFICIENT_FORWARD_DATA = "INSUFFICIENT_FORWARD_DATA"
REASON_MISSING_EXIT_BAR = "MISSING_EXIT_BAR"
# 撮合引擎原因码子集（未成交 → 剔除并计 ``unfilled_counts``）
MATCHING_REASONS: tuple[str, ...] = (REASON_LIMIT_UP_UNFILLABLE,)

_DATE_AXIS_FIELD = "close"
# 入场 bar 所需字段（六字段契约；缺字段 fail loud，缺格回退见 forward_return）
_ENTRY_FIELDS: tuple[str, ...] = ("open", "high", "low", "close")


@dataclass(frozen=True)
class ForwardSample:
    """单笔可结算样本：入场（D+1 撮合成交价）→ 出场（入场日后第 horizon 个交易日收盘）。"""

    pool_date: str
    symbol: str
    horizon: int
    entry_date: str
    entry_price: float
    exit_date: str
    exit_price: float
    ret: float


@dataclass(frozen=True)
class SymbolOutcome:
    """单标的单池日单 horizon 的结算结果：``sample`` 与 ``reason`` 恰有一个非空。"""

    pool_date: str
    symbol: str
    sample: ForwardSample | None
    reason: str | None


@dataclass(frozen=True)
class DayForwardResult:
    """单池日的组内结果：可结算样本 + 剔除/未成交原因计数。"""

    pool_date: str
    samples: tuple[ForwardSample, ...]
    excluded_counts: dict[str, int]
    unfilled_counts: dict[str, int]


# ── 解析与面板小工具 ────────────────────────────────────────────────────────


def _canonical_date(value: str, field_name: str) -> str:
    """严格规范 ``YYYY-MM-DD``（非规范 → ``ValueError``；日期漂移不静默）。"""
    try:
        parsed = date.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} 须为规范 YYYY-MM-DD: {value!r}") from exc
    if parsed.isoformat() != value:
        raise ValueError(f"{field_name} 须为规范 YYYY-MM-DD: {value!r}")
    return value


def _finite_price(value: Any) -> float | None:
    """价格强转：None/NaN/±inf/不可解析 → None（不编造价格，同 replay 口径）。"""
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _frame(panel: MarketPanel, field: str) -> pd.DataFrame:
    """取面板字段帧；缺字段 → ``ValueError``（六字段契约，fail loud）。"""
    frame = panel.fields.get(field)
    if frame is None:
        raise ValueError(f"面板缺少 {field!r} 字段（前向收益口径要求六字段契约）")
    return frame


def _date_axis(panel: MarketPanel, *, as_of: str | None) -> list[str]:
    """面板交易日轴（close 帧 index，升序、截断到 ≤ as_of）。"""
    index = _frame(panel, _DATE_AXIS_FIELD).index
    axis = sorted({str(day) for day in index})
    if as_of is not None:
        axis = [day for day in axis if day <= as_of]
    return axis


def _cell(frame: pd.DataFrame, trade_date: str, symbol: str) -> float | None:
    """取单元格有限价格；缺列/缺行/非有限 → None。"""
    if trade_date not in frame.index or symbol not in frame.columns:
        return None
    return _finite_price(frame.at[trade_date, symbol])


def _is_positive_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 1


# ── 单标的结算（入场撮合 + 出场收盘） ───────────────────────────────────────


def forward_return(
    panel: MarketPanel,
    symbol: str,
    pool_date: str,
    horizon: int,
    *,
    as_of: str | None = None,
    config: MatchingConfig | None = None,
) -> SymbolOutcome:
    """单标的单 horizon 前向结算：入场走撮合引擎、出场取 horizon 日收盘。

    返回 ``SymbolOutcome``：可结算 → ``sample``；否则 ``reason`` ∈ 原因码常量。
    任何价格/面板缺格 → fail-closed 剔除（缺失视同不可交易，不猜）。
    """
    normalized_pool_date = _canonical_date(pool_date, "pool_date")
    if as_of is not None:
        _canonical_date(as_of, "as_of")
    if not _is_positive_int(horizon):
        raise ValueError(f"horizon 必须是正整数: {horizon!r}")
    active_config = config if config is not None else load_matching_config()
    axis = _date_axis(panel, as_of=as_of)
    close_frame = _frame(panel, _DATE_AXIS_FIELD)
    for field in _ENTRY_FIELDS:
        _frame(panel, field)  # 缺字段 fail loud（不缺格）

    if as_of is not None and normalized_pool_date > as_of:
        return SymbolOutcome(
            pool_date=normalized_pool_date,
            symbol=symbol,
            sample=None,
            reason=REASON_POOL_DATE_AFTER_AS_OF,
        )
    position = bisect.bisect_right(axis, normalized_pool_date)
    if position >= len(axis):
        return SymbolOutcome(
            pool_date=normalized_pool_date,
            symbol=symbol,
            sample=None,
            reason=REASON_NO_NEXT_TRADING_DAY,
        )
    entry_date = axis[position]

    close_price = _cell(close_frame, entry_date, symbol)
    if close_price is None or close_price <= 0:
        return SymbolOutcome(normalized_pool_date, symbol, None, REASON_MISSING_ENTRY_BAR)
    open_price = _cell(_frame(panel, "open"), entry_date, symbol)
    if open_price is None or open_price <= 0:
        open_price = close_price  # 同 replay：价基不可用回落该行收盘
    high_price = _cell(_frame(panel, "high"), entry_date, symbol)
    if high_price is None or high_price <= 0:
        high_price = close_price
    low_price = _cell(_frame(panel, "low"), entry_date, symbol)
    if low_price is None or low_price <= 0:
        low_price = close_price
    prev_close = _cell(close_frame, axis[position - 1], symbol) if position > 0 else None
    amount_frame = panel.fields.get("amount")
    amount = _cell(amount_frame, entry_date, symbol) if amount_frame is not None else None
    limit_up_sealed, limit_down_sealed = sealed_flags_from_ohlc(
        open_price, high_price, low_price, close_price, prev_close
    )
    bar = MarketBar(
        trade_date=entry_date,
        open=open_price,
        high=high_price,
        low=low_price,
        close=close_price,
        prev_close=prev_close,
        amount=amount,
        limit_up_sealed=limit_up_sealed,
        limit_down_sealed=limit_down_sealed,
    )
    order = Order(code=symbol, side="buy", qty=ENTRY_ORDER_QTY)
    fill = match_order(order, bar, config=active_config, position=None)
    if fill.reason is not None:
        return SymbolOutcome(normalized_pool_date, symbol, None, fill.reason)
    fill_price = fill.fill_price
    if fill_price is None:  # 引擎契约：reason is None ⇒ 已成交有价（防御，不静默出 None）
        raise ValueError("撮合引擎返回已成交但无成交价")

    exit_position = position + horizon
    if exit_position >= len(axis):
        return SymbolOutcome(normalized_pool_date, symbol, None, REASON_INSUFFICIENT_FORWARD_DATA)
    exit_date = axis[exit_position]
    exit_price = _cell(close_frame, exit_date, symbol)
    if exit_price is None or exit_price <= 0:
        return SymbolOutcome(normalized_pool_date, symbol, None, REASON_MISSING_EXIT_BAR)
    sample = ForwardSample(
        pool_date=normalized_pool_date,
        symbol=symbol,
        horizon=horizon,
        entry_date=entry_date,
        entry_price=fill_price,
        exit_date=exit_date,
        exit_price=exit_price,
        ret=exit_price / fill_price - 1.0,
    )
    return SymbolOutcome(normalized_pool_date, symbol, sample, None)


# ── 逐日批量结算 + 分组统计 + 配对 CI ────────────────────────────────────────


def forward_returns_by_day(
    *,
    symbols_by_date: Mapping[str, Sequence[str]],
    panel: MarketPanel,
    horizon: int,
    as_of: str | None = None,
    config: MatchingConfig | None = None,
) -> list[DayForwardResult]:
    """逐池日逐标的结算（``symbols_by_date`` 注入；缺覆盖/重复标的 → fail loud）。

    返回与 ``sorted(symbols_by_date)`` 对齐的 ``DayForwardResult``；未成交（撮合
    原因码）计入 ``excluded_counts`` 与 ``unfilled_counts``，绝不进收益序列。
    """
    if not _is_positive_int(horizon):
        raise ValueError(f"horizon 必须是正整数: {horizon!r}")
    for pool_date, symbols in symbols_by_date.items():
        _canonical_date(pool_date, "symbols_by_date 键（池日）")
        if len(set(symbols)) != len(list(symbols)):
            raise ValueError(f"池日 {pool_date!r} 存在重复标的（每标的至多一条）")
    results: list[DayForwardResult] = []
    for pool_date in sorted(symbols_by_date):
        samples: list[ForwardSample] = []
        excluded: dict[str, int] = {}
        unfilled: dict[str, int] = {}
        for symbol in symbols_by_date[pool_date]:
            outcome = forward_return(panel, symbol, pool_date, horizon, as_of=as_of, config=config)
            if outcome.sample is not None:
                samples.append(outcome.sample)
                continue
            reason = str(outcome.reason)
            excluded[reason] = excluded.get(reason, 0) + 1
            if reason in MATCHING_REASONS:
                unfilled[reason] = unfilled.get(reason, 0) + 1
        results.append(
            DayForwardResult(
                pool_date=pool_date,
                samples=tuple(samples),
                excluded_counts=dict(sorted(excluded.items())),
                unfilled_counts=dict(sorted(unfilled.items())),
            )
        )
    return results


def group_stats(days: Sequence[DayForwardResult]) -> dict[str, Any]:
    """组合统计：``{n, mean, win_rate, days, excluded_counts, unfilled_counts}``。

    空样本 → ``mean``/``win_rate`` 记 ``None``（不编造 0 值）；``win_rate`` 定义 =
    收益 > 0 的样本占比；``days`` = 有可结算样本的池日数（有效 n 的分辨率披露）。
    """
    rets = [sample.ret for day in days for sample in day.samples]
    excluded: dict[str, int] = {}
    unfilled: dict[str, int] = {}
    for day in days:
        for reason, count in day.excluded_counts.items():
            excluded[reason] = excluded.get(reason, 0) + count
        for reason, count in day.unfilled_counts.items():
            unfilled[reason] = unfilled.get(reason, 0) + count
    sample_days = sum(1 for day in days if day.samples)
    return {
        "n": len(rets),
        "mean": sum(rets) / len(rets) if rets else None,
        "win_rate": (sum(1 for ret in rets if ret > 0) / len(rets)) if rets else None,
        "days": sample_days,
        "excluded_counts": dict(sorted(excluded.items())),
        "unfilled_counts": dict(sorted(unfilled.items())),
    }


def paired_diff_ci(
    left: Sequence[DayForwardResult],
    right: Sequence[DayForwardResult],
    *,
    B: int = BOOTSTRAP_B,  # noqa: N803 - 与 evals.stats.paired_bootstrap_ci 参数名一致
    seed: int = BOOTSTRAP_SEED,
) -> dict[str, Any]:
    """配对（池日）均值差 CI：同日先取组内均值，再对池日重采样（B=10000 / seed=42）。

    仅取双侧都有可结算样本的池日（配对完整才计入）；配对日 0 → 全 ``None``；
    配对日 < ``MIN_PAIRED_DAYS`` → ``ci95=None``（不产伪 CI）；``diff_mean`` =
    配对池日日均值差的均值（与 CI 的重采样口径一致）。
    """
    left_by_day = {day.pool_date: day.samples for day in left if day.samples}
    right_by_day = {day.pool_date: day.samples for day in right if day.samples}
    common = sorted(set(left_by_day) & set(right_by_day))
    seq_left: list[float] = []
    seq_right: list[float] = []
    for pool_date in common:
        left_samples = left_by_day[pool_date]
        right_samples = right_by_day[pool_date]
        seq_left.append(sum(sample.ret for sample in left_samples) / len(left_samples))
        seq_right.append(sum(sample.ret for sample in right_samples) / len(right_samples))
    result: dict[str, Any] = {
        "paired_days": len(common),
        "diff_mean": None,
        "ci95": None,
        "bootstrap_B": int(B),
        "seed": int(seed),
    }
    if not common:
        return result
    result["diff_mean"] = (sum(seq_left) - sum(seq_right)) / len(common)
    if len(common) >= MIN_PAIRED_DAYS:
        lo, hi = paired_bootstrap_ci(seq_left, seq_right, B=B, seed=seed)
        result["ci95"] = [lo, hi]
    return result


# ── 候选池入口（入选 vs 落选） ──────────────────────────────────────────────


def build_pool_groups(
    pool_dates: Sequence[str], *, db_path: str | Path | None = None
) -> dict[str, dict[str, list[str]]]:
    """读 screening 库 → ``{"selected": {池日: [symbols]}, "rejected": {...}}``。

    入选组 = ``selection_pool`` 当日行；落选组 = 当日 ``l2_candidates`` 中不在池内
    的标的（含 L3 drop 与未达在榜门槛者——「未入选」的完整对照总体）。
    """
    selected: dict[str, list[str]] = {}
    rejected: dict[str, list[str]] = {}
    for pool_date in sorted(pool_dates):
        normalized = _canonical_date(pool_date, "pool_date")
        pool_symbols = [
            str(row["symbol"]) for row in list_selection_pool(normalized, db_path=db_path)
        ]
        in_pool = set(pool_symbols)
        l2_symbols = [
            str(row["symbol"])
            for row in list_l2_candidates(normalized, db_path=db_path, pool=POOL_MAIN)
        ]
        selected[normalized] = pool_symbols
        rejected[normalized] = [symbol for symbol in l2_symbols if symbol not in in_pool]
    return {"selected": selected, "rejected": rejected}


def forward_pool_returns(
    *,
    pool_dates: Sequence[str],
    panel: MarketPanel,
    db_path: str | Path | None = None,
    horizons: Sequence[int] = DEFAULT_HORIZONS,
    as_of: str | None = None,
    config: MatchingConfig | None = None,
) -> dict[str, Any]:
    """候选池前向跟踪主入口：入选组 vs 落选组的 5/10/20 日收益分层统计。

    返回 ``{"as_of", "pool_dates", "horizons": {h: {selected, rejected, diff,
    unfilled_counts}}, "methodology"}``；每组含 ``{n, mean, win_rate, days,
    excluded_counts, unfilled_counts}``，``diff`` 为配对（池日）均值差 + 95% CI。
    """
    if not horizons:
        raise ValueError("horizons 不能为空")
    for horizon in horizons:
        if not _is_positive_int(horizon):
            raise ValueError(f"horizon 必须是正整数: {horizon!r}")
    if len(set(horizons)) != len(list(horizons)):
        raise ValueError(f"horizons 存在重复: {list(horizons)}")
    active_horizons = sorted(horizons)
    normalized_dates = [_canonical_date(pool_date, "pool_date") for pool_date in pool_dates]
    if len(set(normalized_dates)) != len(normalized_dates):
        raise ValueError(f"pool_dates 存在重复: {normalized_dates}")
    groups = build_pool_groups(normalized_dates, db_path=db_path)

    horizons_payload: dict[int, dict[str, Any]] = {}
    for horizon in active_horizons:
        selected_days = forward_returns_by_day(
            symbols_by_date=groups["selected"],
            panel=panel,
            horizon=horizon,
            as_of=as_of,
            config=config,
        )
        rejected_days = forward_returns_by_day(
            symbols_by_date=groups["rejected"],
            panel=panel,
            horizon=horizon,
            as_of=as_of,
            config=config,
        )
        diff = paired_diff_ci(selected_days, rejected_days)
        unfilled: dict[str, int] = {}
        for day in [*selected_days, *rejected_days]:
            for reason, count in day.unfilled_counts.items():
                unfilled[reason] = unfilled.get(reason, 0) + count
        horizons_payload[horizon] = {
            "selected": group_stats(selected_days),
            "rejected": group_stats(rejected_days),
            "diff": diff,
            "unfilled_counts": dict(sorted(unfilled.items())),
        }
    return {
        "as_of": as_of,
        "pool_dates": sorted(normalized_dates),
        "horizons": horizons_payload,
        "methodology": {
            "entry": (
                "入场 = 池日次一交易日（D+1）开盘经撮合引擎成交（买 1 手；滑点取最保守档，"
                "无全市场成交额样本）；一字涨停不成交 → 记原因码剔除，不按涨停价假设成交"
            ),
            "exit": "出场 = 入场日后第 horizon 个交易日收盘；收益 = exit/entry − 1（费用不折算）",
            "truncation": (
                "时点截断：as_of 给定则只用 ≤ as_of 的面板行；入场/出场超出截断 → 剔除并计原因码"
            ),
            "bootstrap": f"配对单元 = 池日；B={BOOTSTRAP_B}，seed={BOOTSTRAP_SEED}；配对日 < "
            f"{MIN_PAIRED_DAYS} 不产 CI",
        },
    }
