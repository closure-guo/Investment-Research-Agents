"""运营效率 4 指标计算。

指标：
1. 存货周转率（次）— AKShare 预计算（年报）
2. 应收账款周转率（次）— 营业收入 / 应收账款平均余额（自算）
3. 总资产周转率（次）— 营业收入 / 资产总计
4. 应付账款周转率（次）— 营业成本 / 应付账款
"""

from __future__ import annotations

import pandas as pd


def _year(date_str: str) -> str:
    return str(date_str)[:4]


def _safe(val: object, default: float = 0.0) -> float:
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return default
    return float(val)  # type: ignore[arg-type]


def _find_indicator(indicators: pd.DataFrame | None, year: str) -> pd.Series | None:
    """从 indicators 按年份匹配，返回对应行。"""
    if indicators is None or indicators.empty or "日期" not in indicators.columns:
        return None
    match = indicators[indicators["日期"].astype(str).str[:4] == year]
    return match.iloc[0] if len(match) > 0 else None


def calc_efficiency(
    balance_sheet: pd.DataFrame,
    income_statement: pd.DataFrame,
    indicators: pd.DataFrame | None,
) -> dict[str, dict[str, float | str | None]]:
    years = [_year(d) for d in income_statement["报告日"]]
    result: dict[str, dict[str, float | str | None]] = {
        "存货周转率": {},
        "存货周转率_source": {},
        "应收账款周转率": {},
        "总资产周转率": {},
        "应付账款周转率": {},
    }

    for i, year in enumerate(years):
        row_is = income_statement.iloc[i]
        row_bs = balance_sheet.iloc[i]

        revenue = _safe(row_is.get("营业收入"))
        cost = _safe(row_is.get("营业成本"))
        total_assets = _safe(row_bs.get("资产总计"))
        accounts_payable = _safe(row_bs.get("应付账款"))

        # 从 indicators 按日期匹配取存货周转率
        ind_val = _find_indicator(indicators, year)
        if ind_val is not None:
            val = ind_val.get("存货周转率(次)")
            if val is not None and not (isinstance(val, float) and pd.isna(val)):
                result["存货周转率"][year] = float(val)
                result["存货周转率_source"][year] = "official"
            else:
                result["存货周转率"][year] = None
        else:
            # indicators 无该年数据，自算：营业成本 / 平均存货
            inventory = _safe(row_bs.get("存货"))
            if i < len(years) - 1:
                prev_inventory = _safe(balance_sheet.iloc[i + 1].get("存货"))
                avg_inventory = (inventory + prev_inventory) / 2
            else:
                avg_inventory = inventory
            if avg_inventory != 0:
                result["存货周转率"][year] = cost / avg_inventory
                result["存货周转率_source"][year] = "calc"
            else:
                result["存货周转率"][year] = None

        # 应收账款周转率 — 自算: 营业收入 / 应收账款
        # 数据最新在前：i+1 是上一年，用年初+年末均值
        accounts_receivable = _safe(row_bs.get("应收账款"))
        if i < len(years) - 1:
            prev_ar = _safe(balance_sheet.iloc[i + 1].get("应收账款"))
            avg_ar = (accounts_receivable + prev_ar) / 2
        else:
            avg_ar = accounts_receivable
        if avg_ar != 0:
            result["应收账款周转率"][year] = revenue / avg_ar
        else:
            result["应收账款周转率"][year] = None

        # 总资产周转率 — 自算
        if total_assets != 0:
            result["总资产周转率"][year] = revenue / total_assets
        else:
            result["总资产周转率"][year] = None

        # 应付账款周转率 — 自算
        if accounts_payable != 0:
            result["应付账款周转率"][year] = cost / accounts_payable
        else:
            result["应付账款周转率"][year] = None

    return result
