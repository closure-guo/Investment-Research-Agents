"""决策文本价位交叉校验器（update-decision-integrity-gates Task 1）。

spec price-level-tooling：决策层产出的自由文本价位（``reeval_triggers`` 条目与
``inaction_reason``/``reasoning`` 中出现的数值价位）由确定性代码与 state 中已验证的
技术指标值（最新收盘、MA5/10/20/60、布林轨道、近期高低点、价位参考带）交叉核对，
MUST NOT 仅因数值出现在决策文本即视为可信。两种 anomaly 形态（update-decision-price-gate 门禁化：调用方据 anomaly 打回重试/阻断）：

- **偏差形态**（deviation）：文本价位与归属指标已验证值偏差 > 2%
  （``_DEVIATION_THRESHOLD``），且不落在 ``price_levels`` 参考带内；
- **空洞形态**（empty_trigger）：上破类触发（站上/突破/收复/站稳 X）中 X 不高于
  最新收盘价，或下破类触发（跌破/回落至/失守 X）中 X 不低于最新收盘价——触发
  条件在当前时点已满足，不构成有效再评估门槛。空洞形态同样覆盖结构化触发位
  （add-watch-trigger-tracking：``trigger_high``/``trigger_low`` 字段，与文本
  来源同构、同门禁）。

归属规则：文本点名指标名（MA60/布林上轨/止损参考带上沿等）→ 直接取对应已验证值；
未点名 → 在全部已验证指标中取相对偏差最小者。非价格量纲数值不进入价位校验——
量纲归类（结构记号/量纲语境/自引用/量纲外/子单元比值 vs 价位候选）由
``metrics.price_text`` 唯一承载（C1 归类化重写：本模块是交叉核对消费方，
「什么数字是什么量纲」的知识不在本文件）。

「已站上/已跌破」为事实陈述而非再评估门槛，不构成空洞形态。处置语义由调用方（risk_judge 门禁回路）保证：anomaly 非空 → 打回重试一次 → 仍异常阻断；校验器自身异常由调用方 fail-open。本模块保持纯函数、不改写决策。
"""

from __future__ import annotations

import math

from finance_agent.metrics.price_text import alias_occurrences, extract_commitments
from finance_agent.models import TradeDecision

# spec：偏差阈值默认 2%
_DEVIATION_THRESHOLD = 0.02

# 点名指标与数值的最大字符距离（任一方向）
_NAMED_WINDOW = 15


def _as_positive_finite(value: object) -> float | None:
    """正有限数值则窄化为 float；None/非数/NaN/≤0 一律不可用（不伪造）。"""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    converted = float(value)
    return converted if math.isfinite(converted) and converted > 0 else None


def _seq_last(seq: object) -> float | None:
    """calc_technical 序列末值（最新值）；非列表/空/预热段 None 不可用。"""
    if isinstance(seq, (list, tuple)) and seq:
        return _as_positive_finite(seq[-1])
    return None


def _verified_table(
    technical_indicators: dict,
    price_levels: dict,
    latest_close: float | None,
) -> dict[str, float]:
    """已验证指标值表（canonical 名 → 数值）：仅价格量纲，RSI/MACD/KDJ 不入表。"""
    table: dict[str, float] = {}

    def _put(name: str, value: object) -> None:
        converted = _as_positive_finite(value)
        if converted is not None:
            table[name] = converted

    ti = technical_indicators or {}
    ma = ti.get("MA") or {}
    _put("MA5", _seq_last(ma.get("5")))
    _put("MA10", _seq_last(ma.get("10")))
    _put("MA20", _seq_last(ma.get("20")))
    _put("MA60", _seq_last(ma.get("60")))
    boll = ti.get("BOLL") or {}
    _put("布林上轨", _seq_last(boll.get("upper")))
    _put("布林中轨", _seq_last(boll.get("middle")))
    _put("布林下轨", _seq_last(boll.get("lower")))

    levels = price_levels or {}
    if levels.get("available"):
        _put("近期高点", levels.get("recent_high"))
        _put("近期低点", levels.get("recent_low"))
        stop_band = levels.get("stop_band_long")
        if isinstance(stop_band, dict):
            _put("止损参考带上沿", stop_band.get("high"))
            _put("止损参考带下沿", stop_band.get("low"))
        target_band = levels.get("target_band_long")
        if isinstance(target_band, dict):
            _put("目标参考带上沿", target_band.get("high"))
            _put("目标参考带下沿", target_band.get("low"))
    # 最新收盘：调用方 kline 尾行优先；缺位时 entry_ref（工具参考入场基准=最新收盘）兜底
    close = _as_positive_finite(latest_close)
    if close is not None:
        table["最新收盘价"] = close
    else:
        entry_ref = _as_positive_finite(levels.get("entry_ref"))
        if entry_ref is not None:
            table["最新收盘价"] = entry_ref
    return table


def _reference_bands(price_levels: dict) -> list[tuple[float, float]]:
    """price_levels 参考带（支撑/压力带 + sanity 放宽带），不可用时无豁免区间。"""
    levels = price_levels or {}
    if not levels.get("available"):
        return []
    bands: list[tuple[float, float]] = []
    for key in ("stop_band_long", "target_band_long"):
        band = levels.get(key)
        if isinstance(band, dict):
            low, high = _as_positive_finite(band.get("low")), _as_positive_finite(band.get("high"))
            if low is not None and high is not None:
                bands.append((low, high))
    full_band = levels.get("full_band")
    if isinstance(full_band, (list, tuple)) and len(full_band) == 2:
        low, high = _as_positive_finite(full_band[0]), _as_positive_finite(full_band[1])
        if low is not None and high is not None:
            bands.append((low, high))
    return bands


# 带边吸附容差 = 最小报价量级 0.01：模型引用 context 参考带边界值时的两位小数
# 舍入（差 ≤ 0.005）必落容差内——301220 round-1 生产实证（trace e6210fcf-843）：
# full_band 下沿 20.6714 舍入成 20.67，精确区间判定 `lo <= value` 差 0.0014 判
# 带外 → 归属近期低点 22.93 偏差 9.86% 阻断，打回后模型删 20.67 改写又触发
# 编号列表缺口 → 二次阻断（incident 022 重试空转形状，模型无错可改）。上下沿
# 对称；容差外（>0.01，即与带边差超过一个报价档位）的真实带外价位仍阻断
_BAND_EDGE_SNAP = 0.01


def _in_reference_bands(value: float, bands: list[tuple[float, float]]) -> bool:
    return any(lo - _BAND_EDGE_SNAP <= value <= hi + _BAND_EDGE_SNAP for lo, hi in bands)


def _median(values: list[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2


def _named_indicator(
    text: str,
    span: tuple[int, int],
    aliases: list[tuple[int, int, str, str]],
    table: dict[str, float],
) -> tuple[str, float] | None:
    """数值就近归属的点名指标（值缺失时返回 None → 回退最接近匹配）。"""
    num_start, num_end = span
    best: tuple[int, str, str] | None = None  # (gap, canonical, key)
    for start, end, canonical, key in aliases:
        if end <= num_start:
            gap = num_start - end
        elif num_end <= start:
            gap = start - num_end
        else:
            gap = 0
        if gap > _NAMED_WINDOW:
            continue
        if best is None or gap < best[0]:
            best = (gap, canonical, key)
    if best is not None and best[2] in table:
        return best[1], table[best[2]]
    return None


def _closest_indicator(table: dict[str, float], value: float) -> tuple[str, float] | None:
    """全部已验证指标中与该数值相对偏差最小者。"""
    best: tuple[str, float] | None = None
    best_dev = math.inf
    for name, verified in table.items():
        dev = abs(value - verified) / verified
        if dev < best_dev:
            best_dev = dev
            best = (name, verified)
    return best


def _excerpt(text: str, span: tuple[int, int], full: bool) -> str:
    """anomaly 原文片段：触发条目整条保留（Task 3 同源文本匹配）；长字段局部摘录。"""
    if full:
        return text
    start = max(0, span[0] - 24)
    end = min(len(text), span[1] + 24)
    prefix = "…" if start > 0 else ""
    suffix = "…" if end < len(text) else ""
    return f"{prefix}{text[start:end]}{suffix}"


def _fmt(value: float) -> str:
    return f"{value:g}"


def _check_snippet(
    text: str,
    source_full: bool,
    table: dict[str, float],
    bands: list[tuple[float, float]],
    scale: float,
    latest_close: float | None,
) -> list[dict]:
    """单段文本的价位交叉核对：消费价格承诺账本，每处承诺至多登记
    deviation + empty_trigger 各一条。

    复合回踩豁免（fix-decision-price-check-false-positives）：同一文本内同一数值
    同时出现下破语境与上破语境（「先跌破/回撤至 X，再站稳/站上 X」）时，该数值的
    空洞判定跳过——两个子条件串联，整体是前瞻有效门槛，非当前时点已满足。
    """
    anomalies: list[dict] = []
    aliases = alias_occurrences(text)
    for c in extract_commitments(text, scale=scale, aliases=aliases):
        named = _named_indicator(text, c.span, aliases, table)
        indicator: str | None = None
        verified: float | None = None
        if named is not None:
            indicator, verified = named
        else:
            closest = _closest_indicator(table, c.value)
            if closest is not None:
                indicator, verified = closest

        source_text = _excerpt(text, c.span, source_full)
        if indicator is not None and verified is not None:
            deviation = abs(c.value - verified) / verified
            if deviation > _DEVIATION_THRESHOLD and not _in_reference_bands(c.value, bands):
                pct = round(deviation * 100, 2)
                anomalies.append(
                    {
                        "kind": "deviation",
                        "source_text": source_text,
                        "indicator": indicator,
                        "verified_value": verified,
                        "deviation_pct": pct,
                        "message": (
                            f"文本价位 {_fmt(c.value)} 与 {indicator} 已验证值 "
                            f"{_fmt(verified)} 偏差 {_fmt(pct)}%，超过 2% 阈值"
                            "且不在 price_levels 参考带内，价位待核实"
                        ),
                    }
                )

        if latest_close is not None:
            direction = c.direction
            is_empty = (direction == "up" and c.value <= latest_close) or (
                direction == "down" and c.value >= latest_close
            )
            if is_empty and "up" in c.dirs and "down" in c.dirs:
                is_empty = False  # 复合回踩结构：down+up 共现，见函数头注释
            if is_empty:
                word = "上破" if direction == "up" else "下破"
                relation = "不高于" if direction == "up" else "不低于"
                anomalies.append(
                    {
                        "kind": "empty_trigger",
                        "source_text": source_text,
                        "indicator": "最新收盘价",
                        "verified_value": latest_close,
                        "deviation_pct": None,
                        "message": (
                            f"{word}触发价位 {_fmt(c.value)} {relation}最新收盘价 "
                            f"{_fmt(latest_close)}，触发条件在当前时点已满足，"
                            "不构成有效再评估门槛"
                        ),
                    }
                )
    return anomalies


def _check_structured_trigger_levels(
    decision: TradeDecision, latest_close: float | None
) -> list[dict]:
    """结构化触发位方向校验（add-watch-trigger-tracking）。

    ``trigger_high``/``trigger_low`` 与文本触发条目同语义：上破门槛须严格高于
    最新收盘、下破门槛须严格低于最新收盘；违反即空洞形态 anomaly——dict 键与
    ``_check_snippet`` 的 empty_trigger 构造完全同构（含 indicator/verified_value/
    deviation_pct），risk.py 门禁零改动共用打回预算与 source_text 集合运算。
    不依赖文本解析；无可用收盘价直通。
    """
    close = _as_positive_finite(latest_close)
    if close is None:
        return []
    anomalies: list[dict] = []
    for field, direction in (("trigger_high", "上破"), ("trigger_low", "下破")):
        value = _as_positive_finite(getattr(decision, field, None))
        if value is None:
            continue  # 未申报（Task 1 宽松清洗已归 None）不构成空洞
        violated = value <= close if field == "trigger_high" else value >= close
        if violated:
            relation = "高于" if field == "trigger_high" else "低于"
            anomalies.append(
                {
                    "kind": "empty_trigger",
                    "source_text": f"结构化触发位 {field}={_fmt(value)}",
                    "indicator": "最新收盘价",
                    "verified_value": close,
                    "deviation_pct": None,
                    "message": (
                        f"结构化{direction}触发位（{field}）{_fmt(value)} 未{relation}"
                        f"最新收盘价 {_fmt(close)}，触发条件在当前时点已满足，"
                        "不构成有效再评估门槛"
                    ),
                }
            )
    return anomalies


def check_decision_prices(
    decision: TradeDecision,
    technical_indicators: dict,
    price_levels: dict,
    latest_close: float | None,
) -> list[dict]:
    """决策文本价位 vs 已验证技术指标交叉校验（纯函数，不改写决策）。

    Parameters
    ----------
    decision : TradeDecision
        待校验决策（读取 reeval_triggers / inaction_reason / reasoning 文本，及
        trigger_high / trigger_low 结构化触发位）。
    technical_indicators : dict
        calc_technical() 输出形态：{"MA": {"5": [...], ...}, "BOLL": {...}}，
        序列与 K 线等长、末值为最新值（预热段 None 视为不可用）。
    price_levels : dict
        calc_price_levels() 输出形态；不可用（available=False）时无参考带豁免。
    latest_close : float | None
        最新收盘价；None 时跳过空洞检测、仅做偏差校验。

    Returns
    -------
    list[dict]
        每条 anomaly：``{"kind": "deviation"|"empty_trigger", "source_text": str,
        "indicator": str|None, "verified_value": float|None,
        "deviation_pct": float|None, "message": str}``。门禁语义：调用方据非空 anomaly 打回重试，
        仍异常阻断交付（见 update-decision-price-gate spec）。
    """
    table = _verified_table(technical_indicators, price_levels, latest_close)
    if not table:
        return []  # 无任何已验证指标可核对：不凭空报 anomaly
    close = _as_positive_finite(latest_close)
    scale = close if close is not None else _median(list(table.values()))
    if scale is None:
        return []

    snippets: list[tuple[bool, str]] = [(True, entry) for entry in decision.reeval_triggers]
    if decision.inaction_reason:
        snippets.append((False, decision.inaction_reason))
    if decision.reasoning:
        snippets.append((False, decision.reasoning))

    bands = _reference_bands(price_levels)
    anomalies: list[dict] = []
    for source_full, text in snippets:
        if text:
            anomalies.extend(
                _check_snippet(text, source_full, table, bands, float(scale), latest_close)
            )
    # 结构化触发位并入同一 anomaly 列表（add-watch-trigger-tracking）：latest_close
    # 可用时本函数不可能在上面提前返回（最新收盘价必在已验证值表中），合并点安全
    anomalies.extend(_check_structured_trigger_levels(decision, latest_close))
    return anomalies
