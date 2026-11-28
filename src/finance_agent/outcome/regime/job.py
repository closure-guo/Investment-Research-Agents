"""拥挤度指标日批（delta add-crowding-regime-positioning T2）。

编排顺序（钉死）：

1. ``latest_snapshot_date``——快照库为空 → 不产行 + ``no_snapshot_data``
   （不拉行情；MUST NOT 以 0 值冒充）；
2. ``read_universe(as_of)`` 先校验快照新鲜度（as_of 超范围 → ``as_of_not_fresh``；
   当日无行 → ``no_snapshot_data``；校验不过不拉行情）；
3. 市值取数链：spot（``fetch_market_spot``，与快照任务同源）``流通市值`` 列优先
   → 缺列/无可用值 → 腾讯批量兜底（``fetch_market_ref_bulk`` 优先，旧客户端回退
   ``fetch_market_caps_bulk``；``cap_source=tencent`` 留痕）→ 两源皆无 → 不产行 +
   ``cap_data_missing``（MUST NOT 以成交额等替代口径冒充市值分组）；
   取数成功的市值/总市值/换手率顺手落 ``market_ref_daily`` 侧表（best-effort，
   写失败只 WARN，不影响拥挤度行；见 delta extend-factor-library-coverage §8）；
4. ``compute_indicators``（``quantile`` 取自配置，默认 0.3；参与集任一标的
   amount/pct_chg 非有限值亦不可计算 → 同 ``cap_data_missing`` 槽，不产行）；
5. 历史分位：窗口 = 历史行（严格早于当日，最近 ``history_window - 1`` 条）+ 当日值
   = 最多 ``history_window`` 个交易日（含当日）；样本 < ``min_history`` → null；
   同日重跑时当日已有行不重复计数（按 ``trade_date < as_of`` 过滤后回加当日值）；
6. ``upsert_regime_daily``（``trade_date`` 唯一，同日重跑覆盖；含 ``cap_source``）。

旁路铁律：本模块只吞「快照不可用」类跳过（返回摘要），其余故障（网络异常、库不可写）
上抛交 ops ``run_job`` 记 failed 并按 scheduler 重试，不静默产伪行。
"""

from __future__ import annotations

import logging
import math
from collections.abc import Mapping, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

from finance_agent.data.akshare_client import AKShareClient
from finance_agent.data.snapshot import (
    SnapshotError,
    SnapshotNotFreshError,
    latest_snapshot_date,
    norm_code,
    read_universe,
    upsert_market_ref_rows,
)
from finance_agent.outcome.regime.config import load_regime_config
from finance_agent.outcome.regime.indicators import compute_indicators, percentile_rank
from finance_agent.outcome.regime.model import list_regime_history, upsert_regime_daily

logger = logging.getLogger(__name__)

# 流通市值列名（EM spot 主源；新浪回退列形无此列 → 腾讯批量兜底）
_CAP_COLUMN = "流通市值"
# 参考数据附加列（EM spot 具备；新浪回退列形缺 → 子 dict 只含市值）
_TOTAL_CAP_COLUMN = "总市值"
_TURNOVER_COLUMN = "换手率"
# 快照代码列名（spot 表列形，中文列名「代码」）
_CODE_COLUMN = "代码"
# 快照行键名（``read_universe`` 返回 dict 行的 code 键，与 market_snapshot.code 同口径）
_ROW_CODE_KEY = "code"


def _finite(value: Any) -> float | None:
    """数值强转；None/NaN/inf/不可解析 → None（不编造数值）。"""
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _ref_from_spot(spot: Any) -> dict[str, dict[str, float]] | None:
    """spot 表 → ``{6 位裸码: {float_cap[, total_cap, turnover]}}``。

    缺 ``流通市值`` 列 → None（走腾讯批量兜底）。``总市值``/``换手率`` 列存在才
    附加：换手率上游为百分数 → ÷100 归一为小数（与侧表口径一致）；非正市值与
    负换手率视为缺失，不写子 dict（不以 0 冒充）。无任何可用码 → None。
    """
    columns = getattr(spot, "columns", None)
    if columns is None:
        return None
    present = {str(column) for column in columns}
    if _CAP_COLUMN not in present:
        return None
    has_total = _TOTAL_CAP_COLUMN in present
    has_turnover = _TURNOVER_COLUMN in present
    ref: dict[str, dict[str, float]] = {}
    for _, row in spot.iterrows():
        code = norm_code(row.get(_CODE_COLUMN))
        if not code:
            continue
        values: dict[str, float] = {}
        cap = _finite(row.get(_CAP_COLUMN))
        if cap is not None and cap > 0:
            values["float_cap"] = cap
        if has_total:
            total = _finite(row.get(_TOTAL_CAP_COLUMN))
            if total is not None and total > 0:
                values["total_cap"] = total
        if has_turnover:
            turnover = _finite(row.get(_TURNOVER_COLUMN))
            if turnover is not None and turnover >= 0:
                values["turnover"] = turnover / 100.0
        if values:
            ref[code] = values
    return ref or None


def _caps_from_ref(ref: Mapping[str, Mapping[str, float]]) -> dict[str, float] | None:
    """参考数据 → ``{裸码: 流通市值}``；无任何有效市值 → None。"""
    caps = {
        code: float(values["float_cap"])
        for code, values in ref.items()
        if values.get("float_cap") is not None and float(values["float_cap"]) > 0
    }
    return caps or None


def _tencent_ref(
    client: Any, codes: Sequence[str]
) -> tuple[dict[str, dict[str, float]] | None, dict[str, float] | None]:
    """腾讯批量兜底：优先三字段参考数据；旧客户端无该方法 → 仅市值接口（不写侧表）。"""
    fetch_ref = getattr(client, "fetch_market_ref_bulk", None)
    if callable(fetch_ref):
        ref = fetch_ref(list(codes))
        if not ref:
            return None, None
        return ref, _caps_from_ref(ref)
    return None, client.fetch_market_caps_bulk(list(codes))


def _persist_ref_rows(
    trade_date: str,
    ref: Mapping[str, Mapping[str, float]],
    source: str,
    snapshot_db_path: str | Path | None,
) -> None:
    """参考数据侧表落库（best-effort：失败只 WARN，绝不拖垮拥挤度行）。"""
    rows = [
        {"trade_date": trade_date, "code": code, **values, "source": source}
        for code, values in ref.items()
    ]
    if not rows:
        return
    try:
        upsert_market_ref_rows(rows, db_path=snapshot_db_path)
    except Exception as e:  # noqa: BLE001 - 旁路数据落库失败不改变拥挤度日批结果
        logger.warning("参考数据侧表落库失败(trade_date=%s)：本次跳过: %s", trade_date, e)


def _universe_codes(universe: Sequence[Mapping[str, Any]]) -> list[str]:
    """在册代码（6 位裸码，保持升序；兜底源按此批量取市值）。"""
    return [code for row in universe if (code := norm_code(row.get(_ROW_CODE_KEY)))]


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
    "skipped_reason": "no_snapshot_data" | "as_of_not_fresh" | "cap_data_missing"}``
    （后两者含 ``trade_date`` 便于运维定位）。
    """
    latest = latest_snapshot_date(snapshot_db_path)
    trade_date = (str(as_of).strip() if as_of is not None else "") or latest
    if not trade_date or latest is None:
        return {"produced": False, "skipped_reason": "no_snapshot_data"}

    # 先校验快照新鲜度（as_of 超范围 → 独立原因；失败即返回，不拉行情）
    try:
        universe = read_universe(trade_date, db_path=snapshot_db_path)
    except SnapshotNotFreshError:
        return {
            "produced": False,
            "skipped_reason": "as_of_not_fresh",
            "trade_date": trade_date,
        }
    except SnapshotError:
        return {"produced": False, "skipped_reason": "no_snapshot_data"}

    client = client or AKShareClient()
    ref = _ref_from_spot(client.fetch_market_spot())
    caps = _caps_from_ref(ref) if ref is not None else None
    ref_source = "spot"
    if caps is None:
        # 快照同源 spot 无流通市值（新浪回退列形）→ 腾讯批量兜底（真实市值字段）
        ref, caps = _tencent_ref(client, _universe_codes(universe))
        ref_source = "tencent"
    if caps is None:
        return {
            "produced": False,
            "skipped_reason": "cap_data_missing",
            "trade_date": trade_date,
        }
    cap_source = "spot" if ref_source == "spot" else "tencent"
    if ref:
        # §8：顺手落市场参考数据侧表（市值/换手率日频唯一免费增量的落库点）
        _persist_ref_rows(trade_date, ref, ref_source, snapshot_db_path)

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
        cap_source=cap_source,
        db_path=db_path,
    )
    return {"produced": True, "trade_date": trade_date}
