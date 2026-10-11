"""compute_metrics: 编排全部 metrics/ 模块，写入 State Layer 3 字段。"""

from __future__ import annotations

from typing import Any

import pandas as pd

from finance_agent.metrics.cashflow import calc_cashflow
from finance_agent.metrics.dupont import calc_dupont
from finance_agent.metrics.efficiency import calc_efficiency
from finance_agent.metrics.garp import calc_garp
from finance_agent.metrics.levels import calc_price_levels
from finance_agent.metrics.profitability import calc_profitability
from finance_agent.metrics.relative import calc_relative_valuation
from finance_agent.metrics.risk import calc_risk
from finance_agent.metrics.solvency import calc_solvency
from finance_agent.metrics.technical import calc_derived_series, calc_technical
from finance_agent.metrics.traffic_light import assess_traffic_lights, compute_health_score
from finance_agent.state import AnalysisState


def compute_metrics(state: AnalysisState) -> dict[str, Any]:
    bs = state["balance_sheet"]
    inc = state["income_statement"]
    cf = state["cash_flow_statement"]
    ind = state.get("financial_indicators")

    result: dict = {}

    # ── 四维度指标 ──
    solvency = calc_solvency(bs, inc, ind)
    profitability = calc_profitability(bs, inc, ind)
    efficiency = calc_efficiency(bs, inc, ind)
    cashflow = calc_cashflow(bs, inc, cf)

    result["solvency_metrics"] = solvency
    result["profitability_metrics"] = profitability
    result["efficiency_metrics"] = efficiency
    result["cashflow_metrics"] = cashflow

    # ── 杜邦 ──
    result["dupont_tree"] = calc_dupont(bs, inc)

    # ── 红黄绿灯 + 评分 ──
    # 保留 source 标注，但 all_metrics 只含数值指标
    efficiency_numeric = {k: v for k, v in efficiency.items() if not k.endswith("_source")}

    all_metrics = {
        "solvency": solvency,
        "profitability": profitability,
        "efficiency": efficiency_numeric,
        "cashflow": cashflow,
    }
    industry = (state.get("industry_info") or {}).get("industry")
    traffic_lights = assess_traffic_lights(all_metrics, industry=industry)
    result["traffic_lights"] = traffic_lights

    # ── 价位参考 + 派生值（toolize-price-levels）：工具预生成，LLM 引用不心算 ──
    result["price_levels"] = calc_price_levels(state.get("kline"))
    result["derived_series"] = calc_derived_series(state.get("kline"))

    years = sorted(
        {y for dim in all_metrics.values() for v in dim.values() for y in v},
        reverse=True,
    )
    latest_year = years[0] if years else None
    if latest_year:
        result["health_score"] = compute_health_score(
            traffic_lights, latest_year, industry=industry
        )

    # ── 增长率 ──
    growth = _calc_growth_rates(all_metrics, years)

    # 补算营收和净利润绝对值增长率，避免 LLM 自行计算
    if len(years) >= 2:
        _append_absolute_growth(growth, inc, years, "营业收入", "profitability")
        _append_absolute_growth(growth, inc, years, "归母净利润", "profitability")
        _append_absolute_growth(
            growth, inc, years, "净利润", "profitability", fallback_col="归母净利润"
        )

    result["growth_rates"] = growth

    # ── 异常检测 ──
    result["anomalies"] = _detect_anomalies(traffic_lights, result["growth_rates"], latest_year)

    # ── 相对估值（需要同业数据）──
    peer_financials = state.get("peer_financials")

    # ── 净利润增长率（用于 GARP）──
    net_profit_growth = _calc_net_profit_growth(inc, latest_year, years)

    # ── 估值快照（PE/PB/市值 + PE_ttm 推导口径标注）──
    result["valuation_snapshot"] = _build_valuation_snapshot(state)

    # ── 相对估值（valuation_snapshot 装配后：quote PE 缺失时用推导 PE_ttm，估值维度不再整体跳过）──
    quote = state.get("stock_quote") or {}
    vs = result["valuation_snapshot"] or {}
    effective_pe = vs.get("PE") or vs.get("PE_ttm")
    if peer_financials is not None and quote:
        pb = quote.get("PB") or quote.get("pb")
        if effective_pe is not None or pb is not None:
            target = {"PE": effective_pe, "PB": pb}
            peers_list = _build_peers_list(peer_financials)
            if peers_list:
                result["relative_valuation"] = calc_relative_valuation(target, peers_list)
                # 终审 I1：静态 PE 缺失回落 PE_ttm 时，行业均值（cninfo 静态市盈率）
                # 是跨口径比较——spec「与同业口径一致的那个并注明」
                if vs.get("PE") is None and vs.get("PE_ttm") is not None:
                    result["relative_valuation"]["PE"]["caliber_note"] = (
                        "目标 PE 为 TTM 推导口径，行业均值为静态口径，跨口径比较仅供参考"
                    )

    # ── GARP（估值快照装配后调用：PE 取快照已选好口径的值，行业 PE 取 state.industry_pe）──
    result["garp_result"] = _try_garp(
        result["valuation_snapshot"],
        profitability,
        solvency,
        ind,
        net_profit_growth,
        latest_year,
        industry_pe_avg=(state.get("industry_pe") or {}).get("avg_pe"),
        latest_period_snapshot=state.get("latest_period_snapshot"),
        industry=industry,
    )

    # ── 季度趋势 ──
    q_income = state.get("quarterly_income")
    if q_income is not None and not q_income.empty:
        result["quarterly_trend"] = _calc_quarterly_trend(q_income)

    # ── 同业对比（add-peer-comparison：格式化材料注入，关闭 Issue #4 标志位占位）──
    peer_text = format_peer_comparison(state, result.get("valuation_snapshot"))
    if peer_text is not None:
        result["peer_comparison"] = peer_text

    # ── 技术指标 + 风控指标（需要 K 线数据）──
    kline = state.get("kline")
    if kline is not None and not kline.empty:
        result["technical_indicators"] = calc_technical(kline)
        benchmark = state.get("benchmark_kline")
        result["risk_metrics"] = calc_risk(kline, benchmark)

    return result


def _calc_growth_rates(
    all_metrics: dict[str, dict],
    years: list[str],
) -> dict[str, dict[str, float | None]]:
    if len(years) < 2:
        return {}
    latest, prev = years[0], years[1]
    growth: dict[str, dict[str, float | None]] = {}
    for dim_name, dim_metrics in all_metrics.items():
        for metric_name, year_values in dim_metrics.items():
            v_new = year_values.get(latest)
            v_old = year_values.get(prev)
            if v_new is not None and v_old is not None and v_old != 0:
                rate = (v_new - v_old) / abs(v_old)
            else:
                rate = None
            growth.setdefault(dim_name, {})[metric_name] = rate
    return growth


def _append_absolute_growth(
    growth: dict[str, dict[str, float | None]],
    income_statement: pd.DataFrame,
    years: list[str],
    col_name: str,
    dim_name: str,
    fallback_col: str | None = None,
) -> None:
    """从利润表取绝对值计算同比增长率，追加到 growth dict。"""
    latest, prev = years[0], years[1]

    def _get_val(year: str) -> float | None:
        mask = income_statement["报告日"].astype(str).str.startswith(year)
        rows = income_statement[mask]
        if rows.empty:
            return None
        v = rows.iloc[0].get(col_name)
        if (v is None or (isinstance(v, float) and pd.isna(v))) and fallback_col:
            v = rows.iloc[0].get(fallback_col)
        if v is None or (isinstance(v, float) and pd.isna(v)):
            return None
        return float(v)

    v_new = _get_val(latest)
    v_old = _get_val(prev)
    if v_new is not None and v_old is not None and v_old != 0:
        growth.setdefault(dim_name, {})[col_name] = (v_new - v_old) / abs(v_old)


def _detect_anomalies(
    traffic_lights: dict,
    growth_rates: dict,
    latest_year: str | None,
) -> list[str]:
    anomalies: list[str] = []
    if not latest_year:
        return anomalies
    for dim_name, dim_metrics in traffic_lights.items():
        for metric_name, year_data in dim_metrics.items():
            entry = year_data.get(latest_year, {})
            if entry.get("final") == "red":
                anomalies.append(f"{dim_name}.{metric_name}: 红灯")
            growth = growth_rates.get(dim_name, {}).get(metric_name)
            if growth is not None and abs(growth) > 0.50:
                anomalies.append(f"{dim_name}.{metric_name}: 变化率{growth:.0%}")
    return anomalies


def _build_peers_list(peer_financials) -> list[dict]:
    import pandas as pd

    if not isinstance(peer_financials, pd.DataFrame) or peer_financials.empty:
        return []
    peers = []
    for _, row in peer_financials.iterrows():
        p = {"name": row.get("name", row.get("股票名称", ""))}
        for k in ("PE", "PB", "pe", "pb"):
            v = row.get(k)
            if v is not None:
                p[k.upper()] = v
        peers.append(p)
    return peers


def _calc_net_profit_growth(
    income_statement: pd.DataFrame,
    latest_year: str | None,
    years: list[str],
) -> float | None:
    """计算归母净利润的同比增长率（用于 GARP）。"""
    if not latest_year or len(years) < 2:
        return None
    prev_year = years[1] if years[0] == latest_year else None
    if not prev_year:
        return None

    def _get_np(df: pd.DataFrame, year: str) -> float | None:
        mask = df["报告日"].astype(str).str.startswith(year)
        rows = df[mask]
        if rows.empty:
            return None
        # 优先使用归母净利润，回退到合并净利润
        val = rows.iloc[0].get("归母净利润") or rows.iloc[0].get("净利润")
        if val is None or (isinstance(val, float) and pd.isna(val)):
            return None
        return float(val)

    latest_np = _get_np(income_statement, latest_year)
    prev_np = _get_np(income_statement, prev_year)
    if latest_np is not None and prev_np is not None and prev_np != 0:
        return (latest_np - prev_np) / abs(prev_np)
    return None


def _derive_pe_ttm(
    market_cap_yi: float | None,
    annual_np_yi: float | None,
    snapshot: dict | None,
) -> tuple[float | None, str | None]:
    """TTM PE 推导（纯规则）。市值与净利润均亿元口径。

    TTM = 年报归母净利 − 上年同期累计 + 最新累计；最新期即年报时直取年报值。
    返回 (pe_ttm, 失败原因)；输入缺失/TTM 非正 → (None, reason)。
    """
    if market_cap_yi is None or (isinstance(market_cap_yi, float) and pd.isna(market_cap_yi)):
        # NaN 视同缺失（东财 spot 停牌股 总市值=NaN 直达 quote）；NaN<=0 恒 False，
        # 不守卫会产出 NaN PE（审查 F1）
        return None, "market_cap 缺失或非正"
    if market_cap_yi <= 0:
        return None, "market_cap 缺失或非正"
    if annual_np_yi is None or (isinstance(annual_np_yi, float) and pd.isna(annual_np_yi)):
        return None, "年报归母净利润缺失或非正"
    if annual_np_yi <= 0:
        return None, "年报归母净利润缺失或非正"
    snap = snapshot or {}
    if snap.get("期类型") == "年报":
        ttm = annual_np_yi
    else:
        cur = snap.get("归母净利润(累计)")
        prev = snap.get("上年同期归母净利润")
        if cur is None or prev is None:
            return None, "最新报告期快照缺失或同期数据缺失，无法拼合 TTM"
        ttm = annual_np_yi - prev + cur
    if ttm is None or (isinstance(ttm, float) and pd.isna(ttm)):
        # NaN 同样不得外泄（审查 F1）
        return None, "TTM 归母净利润非正或缺失，PE 无意义"
    if ttm <= 0:
        # 内插数值格式化（add-output-lint R2）：原始 float 尾巴（如
        # -13.060000000000002，2026-10-05 南航实例）MUST NOT 进交付文案
        return None, f"TTM 归母净利润({ttm:.2f})非正，PE 无意义"
    return round(market_cap_yi / ttm, 2), None


def _build_valuation_snapshot(state: AnalysisState) -> dict:
    """估值快照：PE/PB/市值 + PE_ttm 推导与口径标注。NaN 一律视同缺失。"""
    quote = state.get("stock_quote") or {}
    inc = state.get("income_statement")
    annual_np_yi: float | None = None
    if inc is not None and not inc.empty and "归母净利润" in inc.columns:
        v = inc.iloc[0].get("归母净利润")
        if v is not None and not (isinstance(v, float) and pd.isna(v)):
            annual_np_yi = round(float(v) / 1e8, 2)

    market_cap_raw = quote.get("market_cap")
    if isinstance(market_cap_raw, float) and pd.isna(market_cap_raw):
        # 东财 spot 停牌股 总市值=NaN 原样直达 quote——视同缺失（审查 F1）
        market_cap_raw = None
    # state 契约：quote.market_cap 统一为元（东财主源原样透传；百度回退已在
    # fetch 层 ×1e8 归一——终审 C1）。估值链路亿元口径，此处 元→亿。
    market_cap = round(market_cap_raw / 1e8, 2) if market_cap_raw is not None else None

    snap = state.get("latest_period_snapshot")
    pe_ttm, reason = _derive_pe_ttm(
        market_cap,
        annual_np_yi,
        snap if isinstance(snap, dict) else None,
    )
    static_pe = quote.get("PE") or quote.get("pe")
    if isinstance(static_pe, float) and pd.isna(static_pe):
        # NaN PE 不得标 static 口径外泄（审查 F1）
        static_pe = None
    if static_pe is not None:
        caliber = "static"
    elif pe_ttm is not None:
        caliber = "derived_ttm"
    else:
        caliber = None

    missing: list[str] = []
    if market_cap is None:
        missing.append("market_cap 缺失")
    if static_pe is None and pe_ttm is None and reason:
        missing.append(reason)
    if quote.get("PB") is None:
        missing.append("PB 缺失")
    return {
        "market_cap": market_cap,
        "PE": static_pe,
        "PE_ttm": pe_ttm,
        "PE_caliber": caliber,
        "PB": quote.get("PB"),
        "missing_reasons": missing,
    }


def _try_garp(
    valuation_snapshot: dict | None,
    profitability,
    solvency,
    indicators,
    net_profit_growth: float | None,
    latest_year: str | None,
    industry_pe_avg: float | None = None,
    latest_period_snapshot: dict | None = None,
    industry: str | None = None,
) -> dict | None:
    vs = valuation_snapshot or {}
    # PE 取 valuation_snapshot 已选好口径的值（static 优先，回落 PE_ttm；NaN 已守卫为 None）
    pe = vs.get("PE") or vs.get("PE_ttm")
    if not latest_year:
        return None
    roe = profitability.get("ROE", {}).get(latest_year)
    # 负债率期次对齐（update-garp-input-period-alignment）：时点指标优先最新披露期
    # 快照（中报/季报披露后年报口径即过时）；快照缺字段回落年报并标注，回落可见
    snap = latest_period_snapshot if isinstance(latest_period_snapshot, dict) else {}
    snap_debt = snap.get("资产负债率(%)")
    debt: float | None
    debt_period: str | None
    if isinstance(snap_debt, (int, float)) and snap_debt == snap_debt:
        debt = snap_debt / 100
        debt_period = f"{snap.get('报告日', '')}{snap.get('期类型', '报告期')}（快照）"
    else:
        # 负债率从百分比转为小数（如 16.42 → 0.1642）
        debt_pct = solvency.get("资产负债率", {}).get(latest_year)
        debt = debt_pct / 100 if debt_pct is not None else None
        debt_period = f"{latest_year}年报（快照缺失回落）" if debt is not None else None
    data = {
        "PE": pe,
        "industry_avg_PE": industry_pe_avg,
        "net_profit_growth": net_profit_growth,
        "ROE": roe,
        "debt_ratio": debt,
        "PE_caliber": vs.get("PE_caliber"),
        "debt_ratio_period": debt_period,
        "roe_period": f"{latest_year}年报",
        # 银行业负债率豁免判定（add-banking-industry-calibration D5）
        "industry": industry,
    }
    return calc_garp(data)


def _calc_quarterly_trend(q_income: pd.DataFrame) -> dict:
    """计算季度趋势：同比/环比序列 + 拐点检测。"""
    if q_income.empty or "归母净利润(单季)" not in q_income.columns:
        return {}

    trend: dict = {
        "quarters": [],
        "net_profit": [],
        "revenue": [],
        "revenue_yoy": [],
        "gross_margin": [],
        "qoq": [],
        "yoy": [],
        "warnings": [],
    }

    for _, row in q_income.iterrows():
        trend["quarters"].append(row.get("季度", ""))
        np_val = row.get("归母净利润(单季)")
        trend["net_profit"].append(round(np_val / 1e8, 2) if pd.notna(np_val) else None)
        rev = row.get("营业收入(单季)")
        cost = row.get("营业成本(单季)")
        trend["revenue"].append(round(rev / 1e8, 2) if pd.notna(rev) else None)
        if pd.notna(rev) and pd.notna(cost) and rev:
            trend["gross_margin"].append(round((1 - cost / rev) * 100, 2))
        else:
            trend["gross_margin"].append(None)
        qoq = row.get("环比")
        trend["qoq"].append(qoq)
        yoy = row.get("同比")
        trend["yoy"].append(yoy)
        # 营收同比由 fetch 层在宽窗口（截断前）算好随行携带，这里仅透传——
        # 本层只见最近 quarters 季，结构性找不到去年同期
        trend["revenue_yoy"].append(row.get("营收同比"))

    # 拐点检测
    yoy_vals = [v for v in trend["yoy"] if v is not None]
    q_vals = trend["quarters"]
    if yoy_vals:
        # 1. 最近季度同比为负或大幅下降
        if yoy_vals[0] < -20:
            trend["warnings"].append(
                f"最近季度 ({q_vals[0]}) 归母净利润同比大幅下降 {yoy_vals[0]:.1f}%"
            )
        elif yoy_vals[0] < 0:
            trend["warnings"].append(
                f"最近季度 ({q_vals[0]}) 归母净利润同比下降 {yoy_vals[0]:.1f}%"
            )

        # 2. 最近 4 个季度中存在同比大幅下降（即使不是最近季度）
        for i, yoy in enumerate(yoy_vals):
            if yoy is not None and yoy < -20 and i > 0:
                trend["warnings"].append(f"{q_vals[i]} 归母净利润同比大幅下降 {yoy:.1f}%")
                break  # 只报告第一个历史大幅下降

        # 3. 连续两个季度同比下降
        if len(yoy_vals) >= 2 and yoy_vals[0] < 0 and yoy_vals[1] < 0:
            trend["warnings"].append(
                f"连续两个季度同比下降 ({q_vals[0]}: {yoy_vals[0]:.1f}%, "
                f"{q_vals[1]}: {yoy_vals[1]:.1f}%)"
            )

    return trend


_PEER_TABLE_COLUMNS = [
    ("name", "名称"),
    ("code", "代码"),
    ("PE", "PE"),
    ("PB", "PB"),
    ("total_mv", "总市值(亿)"),
    ("revenue_yoy", "营收同比(%)"),
    ("netprofit_yoy", "归母净利同比(%)"),
    ("gross_margin", "毛利率(%)"),
    ("report_period", "报告期"),
]


def _peer_fmt(v) -> str:
    try:
        if v is None or pd.isna(v):
            return "—"
    except (TypeError, ValueError):
        # pd.isna 对 list 等长度歧义对象 raise ValueError——按非缺失处理
        pass
    if isinstance(v, float):
        return f"{v:.2f}"
    return str(v)


def format_peer_comparison(state: AnalysisState, valuation_snapshot: dict | None) -> str | None:
    """peer_financials + 主标的估值快照/最新期快照 → markdown 对照表（主标的首行）。

    财务组主标的与对标股同源自 latest_period_snapshot（同口径）；主标的 PE 取
    估值快照已选口径（static 优先，回落 PE_ttm 时附跨口径提示）。peer 数据缺失时：
    对比请求成立（state.peer_codes 非空）返回中性缺失声明（spec R4）；未指定
    peer_codes 返回 None（调用方不写入 state.peer_comparison，R3 optional 降级）。
    """
    peer_df = state.get("peer_financials")
    # isinstance 守卫：契约外形态（如 citation 覆盖测试用 list 充当 peer_financials）
    # 不得炸 compute——按不可用处理，走与缺失相同的降级路径。
    if not isinstance(peer_df, pd.DataFrame) or peer_df.empty:
        # spec R4「peer 缺失如实声明」：对比请求成立（携带 peer_codes）但同业
        # 抓取全部降级/未执行时，注入中性缺失声明，分析师不再对对比被请求过
        # 一无所知；未指定 peer_codes 时维持 None（R3「无对标股不注入」）。
        # 声明不断言具体原因——抓取失败与未执行（如行业信息缺失跳过）同路。
        if state.get("peer_codes"):
            return "同业数据不可用（抓取失败或未执行），无法提供同业对比；不作对比结论。"
        return None
    vs = valuation_snapshot or {}
    snap = state.get("latest_period_snapshot") or {}
    pe = vs.get("PE")
    pe_ttm_fallback = False
    if pe is None and vs.get("PE_ttm") is not None:
        pe = vs.get("PE_ttm")
        pe_ttm_fallback = True
    target = {
        "name": state.get("stock_name") or state.get("stock_code"),
        "code": state.get("stock_code"),
        "PE": pe,
        "PB": vs.get("PB"),
        "total_mv": vs.get("market_cap"),
        "revenue_yoy": snap.get("营收同比(%)"),
        "netprofit_yoy": snap.get("归母净利同比(%)"),
        "gross_margin": snap.get("毛利率(%)"),
        "report_period": snap.get("报告日"),
    }
    header = "| " + " | ".join(label for _, label in _PEER_TABLE_COLUMNS) + " |"
    sep = "|" + "---|" * len(_PEER_TABLE_COLUMNS)

    def _row(d: dict) -> str:
        return "| " + " | ".join(_peer_fmt(d.get(k)) for k, _ in _PEER_TABLE_COLUMNS) + " |"

    lines = [
        "同业对比（主标的首行；估值为行情快照口径，财务组为最新报告期累计同比口径）：",
        header,
        sep,
        _row(target),
    ]
    for _, r in peer_df.iterrows():
        lines.append(_row(r.to_dict()))
    if pe_ttm_fallback:
        lines.append(
            "注：主标的 PE 为 TTM 推导口径，对标股 PE 为行情快照口径，跨口径比较仅供参考。"
        )
    return "\n".join(lines)
