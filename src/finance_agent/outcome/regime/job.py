"""拥挤度指标日批（delta add-crowding-regime-positioning T2）。

编排顺序（钉死，同计划 Task 4 Interfaces）：

1. ``latest_snapshot_date``——快照库为空 → 不产行 + ``no_snapshot_data``
   （不拉行情；MUST NOT 以 0 值冒充）；
2. spot 拉取（``fetch_market_spot``，与快照任务同源）→ caps（``流通市值`` 列）；
   拉取失败/缺列/覆盖不全 → 不产行 + ``cap_data_missing``（MUST NOT 以成交额等
   替代口径冒充市值分组）；
3. ``read_universe(as_of)``（当日无行 → 同上 ``no_snapshot_data``）→
   ``compute_indicators``（``quantile`` 取自配置，默认 0.3）；
4. 历史分位：窗口 = 历史行（严格早于当日，最近 ``history_window - 1`` 条）+ 当日值
   = 最多 ``history_window`` 个交易日（含当日）；样本 < ``min_history`` → null；
5. ``upsert_regime_daily``（``trade_date`` 唯一，同日重跑覆盖）。

旁路铁律：本模块只吞「快照不可用」类跳过（返回摘要），其余故障（网络异常、库不可写）
上抛交 ops ``run_job`` 记 failed 并按 scheduler 重试，不静默产伪行。
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

from finance_agent.data.akshare_client import AKShareClient
from finance_agent.data.snapshot import (
    SnapshotError,
    _norm_code,
    latest_snapshot_date,
    read_universe,
)
from finance_agent.outcome.regime.config import load_regime_config
from finance_agent.outcome.regime.indicators import compute_indicators, percentile_rank
from finance_agent.outcome.regime.model import list_regime_history, upsert_regime_daily

# 流通市值列名（EM spot 主源；新浪回退列形无此列 → cap_data_missing）
_CAP_COLUMN = "流通市值"
# 快照代码列名（与 market_snapshot.code 同口径的 6 位裸码）
_CODE_COLUMN = "代码"


def _finite(value: Any) -> float | None:
    """数值强转；None/NaN/inf/不可解析 → None（不编造数值）。"""
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _caps_from_spot(spot: Any) -> dict[str, float] | None:
    """spot 表 → ``{6 位裸码: 流通市值}``；缺列/无可用值 → None（不产伪 caps）。"""
    columns = getattr(spot, "columns", None)
    if columns is None or _CAP_COLUMN not in list(columns):
        return None
    caps: dict[str, float] = {}
    for _, row in spot.iterrows():
        code = _norm_code(row.get(_CODE_COLUMN))
        cap = _finite(row.get(_CAP_COLUMN))
        if code and cap is not None and cap > 0:
            caps[code] = cap
    return caps or None


def _window_values(
    history: Sequence[Mapping[str, Any]],
    trade_date: str,
    key: str,
    *,
    window: int,
    today: float,
) -> list[float]:
    """分位窗口样本 = 早于当日的最近 ``window - 1`` 条历史值 + 当日值（含当日口径）。"""
    prior: list[float] = []
    for row in history:
        if str(row.get("trade_date")) >= trade_date:
            continue
        value = _finite(row.get(key))
        if value is not None:
            prior.append(value)
    tail = prior[-(window - 1) :] if window > 1 else []
    return [*tail, today]


def run_regime_daily(
    as_of: str | None = None,
    *,
    db_path: str | Path | None = None,
    snapshot_db_path: str | Path | None = None,
    client: AKShareClient | None = None,
) -> dict[str, Any]:
    """跑一轮拥挤度日批，返回摘要（编排顺序见模块 docstring）。

    摘要：``{"produced": True, "trade_date": ...}``；跳过态 ``{"produced": False,
    "skipped_reason": "no_snapshot_data" | "cap_data_missing"}``（cap 跳过含
    ``trade_date`` 便于运维定位）。
    """
    latest = latest_snapshot_date(snapshot_db_path)
    trade_date = (str(as_of).strip() if as_of is not None else "") or latest
    if not trade_date or latest is None:
        return {"produced": False, "skipped_reason": "no_snapshot_data"}

    client = client or AKShareClient()
    spot = client.fetch_market_spot()
    caps = _caps_from_spot(spot)
    if caps is None:
        return {
            "produced": False,
            "skipped_reason": "cap_data_missing",
            "trade_date": trade_date,
        }

    try:
        universe = read_universe(trade_date, db_path=snapshot_db_path)
    except SnapshotError:
        return {"produced": False, "skipped_reason": "no_snapshot_data"}

    config = load_regime_config()
    indicators = compute_indicators(universe, caps, quantile=config.small_cap_quantile)
    if indicators is None:
        return {
            "produced": False,
            "skipped_reason": "cap_data_missing",
            "trade_date": trade_date,
        }

    history = list_regime_history(limit=config.history_window, db_path=db_path)
    small_values = _window_values(
        history,
        trade_date,
        "small_cap_share",
        window=config.history_window,
        today=indicators["small_cap_share"],
    )
    total_values = _window_values(
        history,
        trade_date,
        "total_amount",
        window=config.history_window,
        today=indicators["total_amount"],
    )
    pctiles = {
        "small_cap_share_pctile": percentile_rank(
            small_values, indicators["small_cap_share"], min_history=config.min_history
        ),
        "total_amount_pctile": percentile_rank(
            total_values, indicators["total_amount"], min_history=config.min_history
        ),
    }
    upsert_regime_daily(
        trade_date,
        indicators,
        pctiles,
        computed_at=datetime.now().isoformat(),
        db_path=db_path,
    )
    return {"produced": True, "trade_date": trade_date}
