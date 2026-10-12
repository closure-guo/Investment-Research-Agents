"""因子衰减监控日批（add-factor-library-and-monitor Task 6；关键设计决策 E9）。

``run_factor_monitor(panel, as_of, *, db_path=None)`` 两步（E9 逐字）：

1. **记录当日可算 RankIC**：为每个 ``active`` 因子在面板上求值 → 与远期收益
   ``rank_ic_series`` → 取 ``≤ as_of`` 的最新**可算**交易日记录进 ``factor_ic``
   （``record_ic`` 幂等：同 ``(factor, date)`` 覆盖）。IC 需要次日收盘价，故
   ``as_of`` 当日（面板末日）通常不可算，记录的是**上一交易日**的 IC——每个交易日
   的 IC 在次日批中恰好记录一次，无前视。
2. **滚动 IC 降级判定**：取该因子落库 IC 序列的最近
   ``DECAY_WINDOW + DECAY_NEGATIVE_DAYS - 1`` 个交易日；滚动窗口 = ``DECAY_WINDOW``
   （逐日滚动 mean/std，``min_periods=窗口``），对**最后 ``DECAY_NEGATIVE_DAYS``
   个滚动点**判定：全部 ``mean < 0`` 且每点 ``|mean/std| < DECAY_ICIR_FLOOR``
   （std=0 → ICIR 记 0）→ 置 ``degraded`` + ``status_change`` 事件落库（detail 含
   滚动 IC/ICIR 快照）。历史不足（< ``DECAY_WINDOW`` 个 IC 点，或不足
   ``DECAY_NEGATIVE_DAYS`` 个滚动点）→ 计入 ``skipped``，**不判降级**。

降级后该因子不再出现在 ``list_active_factors``（消费方据此过滤，E9 末条）。

``run_factor_decay_monitor`` 是 ops 日批入口（``factor_decay_monitor``，15:45
mon-fri）：快照库 → 最近 ``_PANEL_LOOKBACK_DAYS`` 个交易日面板 → 上述监控。
"""

from __future__ import annotations

import logging
import math
from pathlib import Path
from typing import Any

import pandas as pd

from finance_agent.data.snapshot import (
    _connect,  # noqa: PLC2701 - 私有跨模块导入与 snapshot_job 同先例（连接纪律单一来源）
    _parse_date_param,  # noqa: PLC2701
    latest_snapshot_date,
    read_universe,
)
from finance_agent.factors.constraints import (
    DECAY_ICIR_FLOOR,
    DECAY_NEGATIVE_DAYS,
    DECAY_WINDOW,
)
from finance_agent.factors.dsl import parse
from finance_agent.factors.eval_engine import (
    PANEL_FIELDS,
    MarketPanel,
    build_panel_from_rows,
    evaluate,
    forward_returns,
    rank_ic_series,
)
from finance_agent.factors.library import (
    list_active_factors,
    list_ic_series,
    record_ic,
    set_status,
)

logger = logging.getLogger(__name__)

# 面板取数窗口（交易日）：滚动判定用落库序列，面板只需覆盖 DSL 窗口 + 冗余
_PANEL_LOOKBACK_DAYS = DECAY_WINDOW + 30
# 滚动判定所需 IC 点数：DECAY_WINDOW 个点起才有一个滚动点，20 个滚动点需 60+19
_REQUIRED_IC_POINTS = DECAY_WINDOW + DECAY_NEGATIVE_DAYS - 1


def _finite_or_none(value: float) -> float | None:
    """非有限 → ``None``（事件 detail 经 json.dumps 落库，不写 NaN/Inf）。"""
    return float(value) if math.isfinite(float(value)) else None


def _record_latest_ic(
    row: dict[str, Any], panel: MarketPanel, fwd: pd.DataFrame, as_of: str, *, db_path: Any
) -> tuple[str, float] | None:
    """记录 ``≤ as_of`` 的最新可算 IC；无可算日 → ``None``（不写坏值）。"""
    factor_id = int(row["id"])
    values = evaluate(parse(str(row["expression"])), panel)
    ic_series = rank_ic_series(values, fwd)
    eligible = ic_series.loc[:as_of].dropna()
    if eligible.empty:
        return None
    trade_date = str(eligible.index[-1])
    rank_ic = float(eligible.iloc[-1])
    record_ic(factor_id, trade_date, rank_ic, db_path=db_path)
    return trade_date, rank_ic


def _decay_snapshot(factor_id: int, as_of: str, *, db_path: Any) -> dict[str, Any] | None:
    """滚动 IC 判定快照；历史不足 → ``None``（不判降级）。

    返回 ``{"degraded": bool, "rolling_ic_mean": float, "rolling_icir": float|None,
    "tail_means": [...], "tail_icirs": [...], "tail_dates": [...]}``。
    """
    history = list_ic_series(factor_id, limit=_REQUIRED_IC_POINTS, db_path=db_path)
    if len(history) < DECAY_WINDOW:
        return None
    series = pd.Series(
        {str(trade_date): float(value) for trade_date, value in history}, dtype="float64"
    ).sort_index()
    series = series.loc[:as_of]
    rolling_mean = series.rolling(DECAY_WINDOW, min_periods=DECAY_WINDOW).mean()
    rolling_std = series.rolling(DECAY_WINDOW, min_periods=DECAY_WINDOW).std(ddof=1)
    rolling_icir = (rolling_mean / rolling_std).where(rolling_std != 0.0, 0.0)
    tail_means = rolling_mean.dropna().iloc[-DECAY_NEGATIVE_DAYS:]
    if int(tail_means.size) < DECAY_NEGATIVE_DAYS:
        return None
    tail_icirs = rolling_icir.reindex(tail_means.index)
    degraded = bool((tail_means < 0.0).all() and (tail_icirs.abs() < DECAY_ICIR_FLOOR).all())
    return {
        "degraded": degraded,
        "rolling_ic_mean": float(tail_means.iloc[-1]),
        "rolling_icir": _finite_or_none(tail_icirs.iloc[-1]),
        "tail_means": [float(value) for value in tail_means],
        "tail_icirs": [_finite_or_none(value) for value in tail_icirs],
        "tail_dates": [str(value) for value in tail_means.index],
    }


def run_factor_monitor(
    panel: MarketPanel, as_of: str, *, db_path: str | Path | None = None
) -> dict[str, Any]:
    """衰减监控日批（E9）：记录可算 IC → 滚动判定 → 降级落库。

    ``as_of`` 为数据截止交易日，**必须在面板日期内**（否则 ``ValueError``，
    fail loud）；面板须含 ``close`` 字段（``forward_returns`` 输入）。返回
    ``{"recorded", "checked", "skipped", "degraded": [id...], "judged": [...]}``
    （``judged`` 为逐因子判定快照，供报告/运维排查；``degraded`` 为本次降级 id）。
    """
    if "close" not in panel.fields:
        raise ValueError("面板缺少 close 字段（forward_returns 输入）")
    if as_of not in panel.fields["close"].index:
        raise ValueError(f"as_of {as_of!r} 不在面板日期内（禁止跨区间取值）")
    fwd = forward_returns(panel.fields["close"])
    recorded = 0
    checked = 0
    skipped = 0
    degraded: list[int] = []
    judged: list[dict[str, Any]] = []
    for row in list_active_factors(db_path=db_path):
        factor_id = int(row["id"])
        if _record_latest_ic(row, panel, fwd, as_of, db_path=db_path) is None:
            skipped += 1
            logger.info("因子 %s 在 %s 前无可用 IC（窗口不足/全 NaN），跳过", factor_id, as_of)
            continue
        recorded += 1
        snapshot = _decay_snapshot(factor_id, as_of, db_path=db_path)
        if snapshot is None:
            skipped += 1
            continue
        checked += 1
        judged.append(
            {
                "factor_id": factor_id,
                "degraded": snapshot["degraded"],
                "rolling_ic_mean": snapshot["rolling_ic_mean"],
                "rolling_icir": snapshot["rolling_icir"],
            }
        )
        if not snapshot["degraded"]:
            continue
        set_status(
            factor_id,
            "degraded",
            detail={
                "reason": "DECAY",
                "as_of": as_of,
                "window": DECAY_WINDOW,
                "negative_days": DECAY_NEGATIVE_DAYS,
                "icir_floor": DECAY_ICIR_FLOOR,
                "rolling_ic_mean": snapshot["tail_means"],
                "rolling_icir": snapshot["tail_icirs"],
                "trade_dates": snapshot["tail_dates"],
            },
            db_path=db_path,
        )
        degraded.append(factor_id)
        logger.warning(
            "因子 %s 衰减降级（滚动 IC 连续 %d 日为负且 |ICIR| < %.2f）",
            factor_id,
            DECAY_NEGATIVE_DAYS,
            DECAY_ICIR_FLOOR,
        )
    return {
        "recorded": recorded,
        "checked": checked,
        "skipped": skipped,
        "degraded": degraded,
        "judged": judged,
    }


def _recent_trade_dates(
    snapshot_db_path: str | Path | None, *, as_of: str, limit: int
) -> list[str]:
    """快照库中 ``≤ as_of`` 的最近 ``limit`` 个交易日（升序；无数据 → ``[]``）。"""
    normalized = _parse_date_param(as_of, param="as_of").isoformat()
    conn = _connect(snapshot_db_path)
    try:
        rows = conn.execute(
            "SELECT DISTINCT trade_date FROM market_snapshot WHERE trade_date <= ? "
            "ORDER BY trade_date DESC LIMIT ?",
            (normalized, limit),
        ).fetchall()
    finally:
        conn.close()
    return sorted(str(row[0]) for row in rows)


def _load_panel(snapshot_db_path: str | Path | None, *, dates: list[str]) -> MarketPanel:
    """逐日读快照行 → 逐日构面板 → 按字段纵向拼接（内存有界；口径单一来源）。"""
    chunks = [
        build_panel_from_rows(pd.DataFrame(read_universe(trade_date, db_path=snapshot_db_path)))
        for trade_date in dates
    ]
    return MarketPanel(
        fields={
            name: pd.concat([chunk.fields[name] for chunk in chunks]).sort_index()
            for name in PANEL_FIELDS
        }
    )


def run_factor_decay_monitor(
    *,
    as_of: str | None = None,
    db_path: str | Path | None = None,
    snapshot_db_path: str | Path | None = None,
) -> dict[str, Any]:
    """ops 日批入口（``factor_decay_monitor``）：快照库 → 面板 → ``run_factor_monitor``。

    ``as_of`` 缺省 = 快照库最新数据日期；快照库为空 / ``as_of`` 前无数据 →
    ``ValueError``（fail loud，交 ``run_job`` 记 failed 并退避重试）。
    """
    if as_of is None:
        as_of = latest_snapshot_date(snapshot_db_path)
        if as_of is None:
            raise ValueError("快照库为空：无法运行因子衰减监控（先跑 market_snapshot_daily）")
    else:
        as_of = _parse_date_param(as_of, param="as_of").isoformat()
    dates = _recent_trade_dates(snapshot_db_path, as_of=as_of, limit=_PANEL_LOOKBACK_DAYS)
    if not dates:
        raise ValueError(f"快照库在 {as_of} 前无数据：无法构造因子监控面板")
    panel = _load_panel(snapshot_db_path, dates=dates)
    return run_factor_monitor(panel, as_of, db_path=db_path)
