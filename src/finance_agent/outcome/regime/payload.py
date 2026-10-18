"""``GET /api/regime`` 只读载荷（delta add-crowding-regime-positioning T4）。

载荷形状（端点契约）：

- 有记录：``{available: True, as_of, state, multiplier, reasons, indicators,
  history, age_days, stale, cap_source}``——``indicators`` 逐指标 ``{value, pctile}``
  （触发指标取落库分位——判定所依据的读数；Breadth 计数分位由历史窗口派生）；
  ``history`` 为最近 ``history_window`` 行（升序）；``cap_source`` = 市值取数源
  留痕（``spot|tencent``）；
- 无记录 → ``{available: False, reason: "missing"}``；配置/记录/读取不可解析 →
  ``{available: False, reason: "corrupt"}``（调用方翻 200，MUST NOT 500 或伪数据）。

只读保证：不写/不改任何行；缺库时读取入口会幂等建**空表**（仓库既有读取约定，
空 schema 非状态），不产行。
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

from finance_agent.outcome.regime.config import RegimeConfig, load_regime_config
from finance_agent.outcome.regime.indicators import percentile_rank
from finance_agent.outcome.regime.judgment import judge, staleness
from finance_agent.outcome.regime.model import latest_regime, list_regime_history

# 载荷指标键顺序（= 指标卡展示顺序：小市值占比 / 涨跌家数（含平）/ 涨停跌停 / 全市场成交额）
_PAYLOAD_METRIC_KEYS: tuple[str, ...] = (
    "small_cap_share",
    "up_count",
    "down_count",
    "flat_count",
    "limit_up_count",
    "limit_down_count",
    "total_amount",
)
# 落库分位列（触发指标：直接读判定的输入读数）
_STORED_PCTILE_KEYS: frozenset[str] = frozenset({"small_cap_share", "total_amount"})


def _finite(value: Any) -> float | None:
    """数值强转；None/NaN/inf/不可解析 → None（分位派生用，缺失即剔除样本）。"""
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _indicator_block(
    row: dict[str, Any], history: list[dict[str, Any]], config: RegimeConfig
) -> dict[str, Any]:
    """四指标当日值 + 分位（触发指标取落库分位；Breadth 计数分位由窗口派生）。"""
    blocks: dict[str, Any] = {}
    for key in _PAYLOAD_METRIC_KEYS:
        value = _finite(row.get(key))
        if key in _STORED_PCTILE_KEYS:
            pctile = _finite(row.get(f"{key}_pctile"))
        elif value is None:
            pctile = None
        else:
            values = [item for entry in history if (item := _finite(entry.get(key))) is not None]
            pctile = percentile_rank(values, value, min_history=config.min_history)
        blocks[key] = {"value": value, "pctile": pctile}
    return blocks


def build_regime_payload(
    db_path: str | Path | None = None, config: RegimeConfig | None = None
) -> dict[str, Any]:
    """构造只读载荷（口径见模块 docstring）；任何不可解析 → 原因码，不抛给调用方。"""
    try:
        resolved_config = config if config is not None else load_regime_config()
    except ValueError:
        return {"available": False, "reason": "corrupt"}
    try:
        row = latest_regime(db_path)
    except Exception:  # noqa: BLE001 - 库损坏/不可读按 corrupt 处置（端点翻 200）
        return {"available": False, "reason": "corrupt"}
    if row is None:
        return {"available": False, "reason": "missing"}
    try:
        verdict = judge(row, resolved_config)
        age_days, stale = staleness(
            str(row.get("trade_date")), stale_days=resolved_config.stale_days
        )
        history = list_regime_history(limit=resolved_config.history_window, db_path=db_path)
        indicators = _indicator_block(row, history, resolved_config)
    except Exception:  # noqa: BLE001 - 记录不可解析（脏日期/脏分位）按 corrupt
        return {"available": False, "reason": "corrupt"}
    return {
        "available": True,
        "as_of": str(row.get("trade_date")),
        "state": verdict["state"],
        "multiplier": verdict["multiplier"],
        "reasons": verdict["reasons"],
        "indicators": indicators,
        "history": history,
        "age_days": age_days,
        "stale": stale,
        # 市值取数源留痕（spot|tencent；老行/未传 → null，不编造来源）
        "cap_source": row.get("cap_source"),
    }
