"""因子库只读工具（add-analyst-factor-tools Task 1；计划关键设计决策 A1）。

契约（实施者不得自行改口径）：

- ``query_factor_library(symbol, *, panel, as_of, factors=None, ic_stats=None,
  db_path=None) -> str`` 返回 ``json.dumps(..., ensure_ascii=False)`` 的**结构化 JSON
  字符串**（严格 JSON：非有限值一律 ``null``，不产 ``NaN``/``Infinity`` 字面量），
  键逐字：``{"symbol", "as_of", "factors": [{"factor_id", "expression", "category",
  "value", "zscore", "ic_mean_60d", "icir_60d"}]}``。
- ``factors`` 缺省 = ``list_active_factors(db_path=...)``（只取在册 active；candidate/
  degraded/retired 不入输出）；库无 active 因子 → ``{"symbol", "as_of", "factors": []}``
  **不抛错**，另附 ``note`` 说明空因。
- 逐因子 ``evaluate(ast, panel)`` 取 ``as_of`` 行的 ``symbol`` 列值（``ast`` 由库内
  ``expression`` 解析，``enforce_complexity=False``——工具/验证旁路口径，库内表达式
  已在提案路径过复杂度硬约束）；z-score = **该日全市场截面**的 z
  （``cross_section_zscore``，样本标准差 ddof=1，与 L2 ranker 同口径）。
- 值不可得一律 ``null``（不编造）：面板无该 symbol 列 / 无 ``as_of`` 行 / 求值窗口不足
  （NaN）/ 截面全相等（std=0）→ 该因子 ``value``/``zscore`` 为 ``null``；``as_of``
  整行缺席时另在 ``note`` 显式写明缺失日期与面板可用范围（A1「不编造」+ 不静默）。
- ``ic_stats`` 缺省 = ``recent_ic_stats(factors, window=60, db_path=...)``（近 60 个
  交易日 RankIC 均值与 ICIR；库内无 IC 记录 → ``(0.0, 0.0)``，与该函数口径一致）。
- **只读保证**：本模块无任何因子库写路径——不调用 ``add_factor``/``set_status``/
  ``set_pool_increment``/``record_ic``/``apply_bucket_quota``/``init_library``，也不
  出现 ``write_*``/``update_*``/``record_*``/``delete_*``/``insert_*`` 类调用与
  ``sqlite3`` import（``tests/factors/test_factor_tools.py`` 以 AST 静态扫描钉死）；
  工具**不接受表达式入参**——不向调用方提供表达式求值能力，只对库内在册因子求值。
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from finance_agent.factors.dsl import parse
from finance_agent.factors.eval_engine import MarketPanel, cross_section_zscore, evaluate
from finance_agent.factors.library import list_active_factors
from finance_agent.screening.ranker import recent_ic_stats

# IC 统计窗口（逐字：近 60 日）
IC_WINDOW: int = 60
# 面板日期轴参照字段（六字段契约；缺该帧时回落字典序首字段）
_DATE_AXIS_FIELD = "close"


def query_factor_library(
    symbol: str,
    *,
    panel: MarketPanel,
    as_of: str,
    factors: list[dict] | None = None,
    ic_stats: dict[int, tuple[float, float]] | None = None,
    db_path: str | Path | None = None,
) -> str:
    """按标的查询 active 因子的取值、截面 z-score 与近 60 日 IC（A1 契约）。

    返回 JSON 字符串（键逐字见模块 docstring）。``factors`` 缺省从库内取 active
    因子；``ic_stats`` 缺省取库内近 60 日 IC 统计。值不可得 → ``null``（不编造），
    并在 ``note`` 显式记录空因/缺失原因（非有限值、缺 symbol 列、缺 as_of 行、
    求值失败）。空 symbol → ``ValueError``（调用契约错，fail loud）。
    """
    normalized_symbol = str(symbol).strip()
    if not normalized_symbol:
        raise ValueError("symbol 不能为空")
    normalized_as_of = str(as_of)
    rows = list_active_factors(db_path=db_path) if factors is None else list(factors)
    stats = (
        recent_ic_stats(rows, window=IC_WINDOW, db_path=db_path) if ic_stats is None else ic_stats
    )

    notes: list[str] = []
    dates = _panel_dates(panel)
    if dates is None:
        notes.append("面板不含任何字段：无法取因子值，value/zscore 记 null")
    elif normalized_as_of not in dates:
        available = "（空面板）" if len(dates) == 0 else f"{dates[0]}..{dates[-1]}"
        notes.append(
            f"as_of {normalized_as_of!r} 不在面板日期内（可用范围 {available}）："
            "value/zscore 记 null"
        )

    entries: list[dict[str, Any]] = []
    if not rows:
        notes.append("因子库无 active 因子：未产出任何因子值")
    else:
        for row in rows:
            entry, defect = _factor_entry(
                row,
                symbol=normalized_symbol,
                panel=panel,
                as_of=normalized_as_of,
                ic_stats=stats,
            )
            entries.append(entry)
            if defect:
                notes.append(defect)

    payload: dict[str, Any] = {
        "symbol": normalized_symbol,
        "as_of": normalized_as_of,
        "factors": entries,
    }
    if notes:
        payload["note"] = "；".join(notes)
    # allow_nan=False：非有限值已在下方统一转 None，此处再兜一层（坏值不许漏成 NaN 字面量）
    return json.dumps(payload, ensure_ascii=False, allow_nan=False)


def _panel_dates(panel: MarketPanel) -> pd.Index | None:
    """面板日期轴（以 ``close`` 帧为准，缺帧回落字典序首字段）；无字段 → None。"""
    if not panel.fields:
        return None
    frame = panel.fields.get(_DATE_AXIS_FIELD)
    if frame is None:
        frame = panel.fields[sorted(panel.fields)[0]]
    return frame.index


def _factor_entry(
    row: dict,
    *,
    symbol: str,
    panel: MarketPanel,
    as_of: str,
    ic_stats: dict[int, tuple[float, float]],
) -> tuple[dict[str, Any], str | None]:
    """单因子输出条目 + 缺陷说明（求值失败不抛错：null 值 + 可见 note）。"""
    factor_id = int(row["id"])
    expression = str(row["expression"])
    category = str(row.get("category") or "")
    value: float | None = None
    zscore: float | None = None
    defect: str | None = None
    try:
        frame = evaluate(parse(expression, enforce_complexity=False), panel)
    except Exception as exc:  # noqa: BLE001 - 读路径缺陷不阻断（expression 坏/面板缺字段）
        defect = f"因子 {factor_id} 求值失败（{type(exc).__name__}）：value/zscore 记 null"
    else:
        value, zscore = _value_and_zscore(frame, symbol=symbol, as_of=as_of)
    ic_mean, icir = ic_stats.get(factor_id, (0.0, 0.0))
    entry: dict[str, Any] = {
        "factor_id": factor_id,
        "expression": expression,
        "category": category,
        "value": value,
        "zscore": zscore,
        "ic_mean_60d": _finite_or_none(ic_mean),
        "icir_60d": _finite_or_none(icir),
    }
    return entry, defect


def _value_and_zscore(
    frame: pd.DataFrame, *, symbol: str, as_of: str
) -> tuple[float | None, float | None]:
    """该日截面取值 + 全市场截面 z（不可得 → ``(None, None)``，不编造）。

    ``as_of`` 不在帧索引 / 面板日期非唯一（不变量破坏）/ 无该 symbol 列 / 取值非有限
    → ``value=None``；截面 z 非有限（std=0、有效样本 < 2、该格 NaN）→ ``zscore=None``。
    """
    if as_of not in frame.index:
        return None, None
    cross_section = frame.loc[as_of]
    if not isinstance(cross_section, pd.Series):
        # 面板日期重复（同一 as_of 多行）→ 面板不变量破坏：不取任意一行、不编造
        return None, None
    numeric = pd.to_numeric(cross_section, errors="coerce").astype("float64")
    finite = numeric.where(np.isfinite(numeric))
    raw = finite.get(symbol)
    if raw is None or not math.isfinite(float(raw)):
        return None, None
    reference = cross_section_zscore(finite.to_frame().T)
    z_raw = reference.iloc[0].get(symbol)
    zscore = float(z_raw) if z_raw is not None and math.isfinite(float(z_raw)) else None
    return float(raw), zscore


def _finite_or_none(value: Any) -> float | None:
    """任意入参 → float；不可解析/非有限（NaN/±inf）→ None（严格 JSON 保证）。"""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None
