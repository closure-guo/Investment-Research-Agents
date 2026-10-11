"""图表数据收集 + matplotlib PNG 生成。

collect_chart_data(state) → dict: 从 state 提取结构化财务/股价序列
generate_all_charts(chart_data, output_dir) → dict[name, path]: 生成全部 PNG
"""

from __future__ import annotations

import logging
import os

import matplotlib
import pandas as pd

matplotlib.use("Agg")  # 非交互后端，适配服务器环境

import matplotlib.pyplot as plt
import numpy as np

from finance_agent.metrics.traffic_light import is_banking_industry

logger = logging.getLogger(__name__)

# ── 中文字体 ──
# 回退链覆盖 Windows（微软雅黑/黑体）、macOS（Arial Unicode MS）与 Linux 容器
# （Noto Sans CJK SC，由 Dockerfile 安装 fonts-noto-cjk 提供）
plt.rcParams["font.sans-serif"] = [
    "Microsoft YaHei",
    "SimHei",
    "Noto Sans CJK SC",
    "Arial Unicode MS",
    "Arial",
]
plt.rcParams["axes.unicode_minus"] = False

# ── 配色 ──
_C_BLUE = "#2563eb"
_C_RED = "#dc2626"
_C_GREEN = "#059669"
_C_PURPLE = "#7c3aed"
_C_ORANGE = "#ea580c"
_C_CYAN = "#0891b2"
_C_LIME = "#65a30d"
_C_PINK = "#db2777"
_C_BG = "#fafafa"
_C_GRID = "#e5e7eb"


# ════════════════════════════════════════════════════════
#  数据收集
# ════════════════════════════════════════════════════════


def _safe_float(val, default=None):
    """安全转 float，处理 None/NaN/空字符串。"""
    if val is None:
        return default
    if isinstance(val, float) and pd.isna(val):
        return default
    try:
        v = float(val)
        if np.isinf(v) or np.isnan(v):
            return default
        return v
    except (ValueError, TypeError):
        return default


def _nan_series(values: list) -> list[float]:
    """序列中 None 归一为 nan，供 bar/fill_between 消费。

    数据契约中 None 合法（增速在基期缺失/为 0 时、股价涨幅在缺前年
    数据时为 None）；plot 原生容忍 None，但 bar/fill_between 会因
    object 数组抛 TypeError（线上 chart_growth_vs_price 缺图根因）。
    nan 在两类图表中均按缺口的语义断开。
    """
    return [float("nan") if v is None else v for v in values]


# 元 → 亿元（图表层呈现单位换算；数据层/quote 层「统一元」契约不变）
_YI = 1e8

_MISSING_MARK = "缺"


def _ascending(values: list) -> list:
    """年度序列升序呈现辅助。

    数据契约「index 0 = 最新」（降序）供指标计算消费，MUST NOT 改变；
    本函数仅用于图表呈现层的时间轴反转（add-chart-data-integrity R3）。
    """
    return list(reversed(values))


def _yi_series(values: list) -> list[float]:
    """元金额序列 → 亿元；缺失（None/NaN）→ nan（断柱/断线，禁填 0）。"""
    out: list[float] = []
    for v in values:
        f = _safe_float(v)
        out.append(f / _YI if f is not None else float("nan"))
    return out


def _all_missing(values: list) -> bool:
    return all(v is None for v in values)


def _placeholder_text(ax, text: str) -> None:
    """整序列缺失时在坐标系内渲染占位说明（对齐市场份额占位图语义）。"""
    ax.text(
        0.5,
        0.5,
        text,
        ha="center",
        va="center",
        fontsize=10,
        color="#999",
        transform=ax.transAxes,
    )


# add-watch-trigger-tracking：watch 双向触发位随决策价位同路采集（缺省不携带）
_DECISION_LEVEL_KEYS = ("entry_price", "stop_loss", "target_price", "trigger_high", "trigger_low")


def _moving_average(values: list[float | None], window: int) -> list[float | None]:
    """简单移动平均：窗口不足或窗口内含 None 的位置为 None。"""
    out: list[float | None] = [None] * len(values)
    for i in range(window - 1, len(values)):
        seg = [v for v in values[i - window + 1 : i + 1] if v is not None]
        if len(seg) < window:
            continue
        out[i] = sum(seg) / window
    return out


def _extract_decision_levels(state: dict) -> dict[str, float]:
    """提取交易决策价位（dict/属性双形态，与 report._format_trade_decision 同源逻辑）。

    缺失/非法价位的字段不携带；决策整体无价位时返回空 dict（调用侧不挂键）。
    """
    decision = state.get("final_trade_decision") or state.get("trader_plan")
    if decision is None:
        return {}
    levels: dict[str, float] = {}
    for key in _DECISION_LEVEL_KEYS:
        raw = decision.get(key) if isinstance(decision, dict) else getattr(decision, key, None)
        val = _safe_float(raw)
        if val is not None:
            levels[key] = val
    return levels


def _year_from_date(date_str) -> str:
    return str(date_str)[:4]


def margin_placeholder_text(industry: str | None) -> str:
    """利润率子图占位文本（add-banking-industry-calibration D7）。

    占位语义区分「数据缺失」与「行业不适用」：银行毛利率 None 是口径不适用
    （银行报表无营业成本概念），误述为数据缺失会误导读者以为数据管道缺数。
    """
    if is_banking_industry(industry):
        return "银行业不适用毛利率口径（无营业成本概念）"
    return "利润率数据缺失"


def collect_chart_data(state: dict) -> dict:
    """从 state 提取结构化图表数据，返回 JSON-serializable dict。"""
    chart_data: dict = {
        "stock_code": state.get("stock_code", ""),
        "stock_name": state.get("stock_name", ""),
        "industry": (state.get("industry_info") or {}).get("industry"),
        "annual": [],
        "growth": {"years": [], "revenue_growth": [], "profit_growth": []},
        "price": {"daily": [], "earnings_dates": [], "ma": {"ma5": [], "ma20": [], "ma60": []}},
        "kpi": {},
        "market_share": None,
    }

    income = state.get("income_statement")
    balance = state.get("balance_sheet")
    cashflow = state.get("cash_flow_statement")
    profitability = state.get("profitability_metrics") or {}
    solvency = state.get("solvency_metrics") or {}

    # ── 年度财务数据 ──
    if income is not None and not income.empty:
        years = [_year_from_date(d) for d in income["报告日"]]
        for i, year in enumerate(years):
            row_is = income.iloc[i]
            row_bs = balance.iloc[i] if balance is not None and i < len(balance) else None
            row_cf = cashflow.iloc[i] if cashflow is not None and i < len(cashflow) else None

            annual_entry = {
                "year": year,
                "revenue": _safe_float(row_is.get("营业收入")),
                "net_profit": _safe_float(row_is.get("归母净利润"))
                or _safe_float(row_is.get("净利润")),
                "gross_margin": _safe_float(profitability.get("毛利率", {}).get(year)),
                "net_margin": _safe_float(profitability.get("净利率", {}).get(year)),
                "roe": _safe_float(profitability.get("ROE", {}).get(year)),
                "roa": _safe_float(profitability.get("ROA", {}).get(year)),
                "ocf": _safe_float(row_cf.get("经营活动产生的现金流量净额"))
                if row_cf is not None
                else None,
                "total_assets": _safe_float(row_bs.get("资产总计")) if row_bs is not None else None,
                "equity": _safe_float(row_bs.get("归母所有者权益")) if row_bs is not None else None,
                "contract_liab": _safe_float(row_bs.get("合同负债"))
                if row_bs is not None
                else None,
                "cip": _safe_float(row_bs.get("在建工程")) if row_bs is not None else None,
                "debt_ratio": _safe_float(solvency.get("资产负债率", {}).get(year)),
            }
            chart_data["annual"].append(annual_entry)

        # ── 同比增速 ──
        annual = chart_data["annual"]
        for i in range(len(annual) - 1):
            curr = annual[i]
            prev = annual[i + 1]
            chart_data["growth"]["years"].append(curr["year"])
            rev_g = None
            profit_g = None
            if prev["revenue"] and curr["revenue"] and prev["revenue"] != 0:
                rev_g = round((curr["revenue"] - prev["revenue"]) / abs(prev["revenue"]) * 100, 2)
            if prev["net_profit"] and curr["net_profit"] and prev["net_profit"] != 0:
                profit_g = round(
                    (curr["net_profit"] - prev["net_profit"]) / abs(prev["net_profit"]) * 100, 2
                )
            chart_data["growth"]["revenue_growth"].append(rev_g)
            chart_data["growth"]["profit_growth"].append(profit_g)

    # ── KPI ──
    quote = state.get("stock_quote") or {}
    chart_data["kpi"] = {
        "current_price": _safe_float(quote.get("price")),
        "market_cap": _safe_float(quote.get("market_cap")),
        "pe": _safe_float(quote.get("PE")) or _safe_float(quote.get("PE_ttm")),
        "pb": _safe_float(quote.get("PB")),
    }

    # ── 股价日线（update-price-chart-kline：补齐 OHLCV + MA 均线）──
    kline = state.get("kline")
    if kline is not None and not kline.empty:
        # 取最近 250 个交易日（约一年）
        recent = kline.tail(250)
        for _, row in recent.iterrows():
            date_str = str(row.get("日期", row.get("date", "")))[:10]
            close = _safe_float(row.get("收盘", row.get("close")))
            if date_str and close is not None:
                chart_data["price"]["daily"].append(
                    {
                        "date": date_str,
                        "close": close,
                        "open": _safe_float(row.get("开盘", row.get("open"))),
                        "high": _safe_float(row.get("最高", row.get("high"))),
                        "low": _safe_float(row.get("最低", row.get("low"))),
                        "volume": _safe_float(row.get("成交量", row.get("volume"))),
                    }
                )

        # ── MA 均线：全量 kline 计算后切尾对齐（窗口前段 None），口径同技术指标 ──
        if chart_data["price"]["daily"]:
            closes_all: list[float | None] = [_safe_float(v) for v in kline["收盘"]]
            offset = len(closes_all) - len(chart_data["price"]["daily"])
            for w in (5, 20, 60):
                chart_data["price"]["ma"][f"ma{w}"] = _moving_average(closes_all, w)[offset:]

            closes = [d["close"] for d in chart_data["price"]["daily"]]
            chart_data["kpi"]["52w_high"] = max(closes)
            chart_data["kpi"]["52w_low"] = min(closes)

    # ── 交易决策价位（独立于 kline，缺失不携带）──
    decision_levels = _extract_decision_levels(state)
    if decision_levels:
        chart_data["price"]["decision_levels"] = decision_levels

    # ── 报告期截止日（利润表报告日，非披露日；标注措辞不得称「发布日」）──
    if income is not None and not income.empty:
        for d in income["报告日"]:
            date_str = str(d)
            if len(date_str) == 8:
                formatted = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:8]}"
            else:
                formatted = date_str[:10]
            chart_data["price"]["earnings_dates"].append(formatted)

    return chart_data


# ════════════════════════════════════════════════════════
#  PNG 生成
# ════════════════════════════════════════════════════════

_DPI = 150
_FIGSIZE_WIDE = (10, 5)
_FIGSIZE_SQUARE = (8, 6)


def _style_ax(ax, title: str = ""):
    """统一坐标系样式。"""
    ax.set_facecolor(_C_BG)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.spines["left"].set_color(_C_GRID)
    ax.spines["bottom"].set_color(_C_GRID)
    ax.tick_params(colors="#666", labelsize=9)
    ax.yaxis.grid(True, color=_C_GRID, linewidth=0.5, alpha=0.7)
    ax.set_axisbelow(True)
    if title:
        ax.set_title(title, fontsize=13, fontweight="bold", color="#333", pad=12)


def _save_fig(fig, output_dir: str, name: str) -> str:
    """保存图表，返回绝对路径。"""
    path = os.path.join(output_dir, f"{name}.png")
    fig.savefig(path, dpi=_DPI, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    return path


def _chart_revenue_profit(data: dict, out: str) -> str | None:
    """P0: 营业收入与归母净利润（双轴柱状图，亿元）。"""
    annual = data.get("annual", [])
    if len(annual) < 2:
        return None
    annual = _ascending(annual)
    years = [a["year"] for a in annual]
    revenue = _yi_series([a.get("revenue") for a in annual])
    profit = _yi_series([a.get("net_profit") for a in annual])

    fig, ax1 = plt.subplots(figsize=_FIGSIZE_WIDE)
    x = np.arange(len(years))
    w = 0.35
    ax1.bar(x - w / 2, revenue, w, label="营业收入", color=_C_BLUE, alpha=0.85)
    ax1.set_ylabel("营业收入（亿元）", color=_C_BLUE, fontsize=10)
    ax1.tick_params(axis="y", labelcolor=_C_BLUE)
    ax1.set_xticks(x)
    ax1.set_xticklabels(years)

    ax2 = ax1.twinx()
    ax2.bar(x + w / 2, profit, w, label="归母净利润", color=_C_RED, alpha=0.85)
    ax2.set_ylabel("归母净利润（亿元）", color=_C_RED, fontsize=10)
    ax2.tick_params(axis="y", labelcolor=_C_RED)
    ax2.spines["top"].set_visible(False)

    _style_ax(ax1, "营业收入与归母净利润")
    lines1, labels1 = ax1.get_legend_handles_labels()
    lines2, labels2 = ax2.get_legend_handles_labels()
    ax1.legend(lines1 + lines2, labels1 + labels2, loc="upper left", fontsize=9)
    fig.tight_layout()
    return _save_fig(fig, out, "chart_revenue_profit")


def _chart_growth(data: dict, out: str) -> str | None:
    """P0: 同比增速（折线图，时间升序）。"""
    growth = data.get("growth", {})
    years = _ascending(growth.get("years", []))
    if len(years) < 2:
        return None
    rev_g = _ascending(growth.get("revenue_growth", []))
    profit_g = _ascending(growth.get("profit_growth", []))

    fig, ax = plt.subplots(figsize=_FIGSIZE_WIDE)
    ax.plot(years, rev_g, marker="o", color=_C_BLUE, linewidth=2, label="营收增速")
    ax.plot(years, profit_g, marker="s", color=_C_RED, linewidth=2, label="净利润增速")
    ax.axhline(y=0, color="#999", linewidth=0.8, linestyle="--")
    ax.set_ylabel("同比增长率（%）", fontsize=10)
    ax.set_xlabel("年份", fontsize=10)
    _style_ax(ax, "同比增速")
    ax.legend(fontsize=9, loc="best")
    # 标注数值
    for i, (r, p) in enumerate(zip(rev_g, profit_g, strict=False)):
        if r is not None:
            ax.annotate(
                f"{r:.1f}%",
                (i, r),
                textcoords="offset points",
                xytext=(0, 8),
                ha="center",
                fontsize=7,
                color=_C_BLUE,
            )
        if p is not None:
            ax.annotate(
                f"{p:.1f}%",
                (i, p),
                textcoords="offset points",
                xytext=(0, -12),
                ha="center",
                fontsize=7,
                color=_C_RED,
            )
    fig.tight_layout()
    return _save_fig(fig, out, "chart_growth")


def _chart_margin(data: dict, out: str) -> str | None:
    """P0: 毛利率与净利率（平滑折线图，时间升序）。"""
    annual = data.get("annual", [])
    if len(annual) < 2:
        return None
    annual = _ascending(annual)
    years = [a["year"] for a in annual]
    gm = [a.get("gross_margin") for a in annual]
    nm = [a.get("net_margin") for a in annual]
    # 两条序列全缺 → 跳过（对齐 contract_liab/debt_ratio 全缺语义）
    if _all_missing(gm) and _all_missing(nm):
        return None

    fig, ax = plt.subplots(figsize=_FIGSIZE_WIDE)
    ax.plot(years, gm, marker="o", color=_C_GREEN, linewidth=2.5, label="毛利率")
    ax.plot(years, nm, marker="s", color=_C_PURPLE, linewidth=2.5, label="净利率")
    ax.set_ylabel("百分比（%）", fontsize=10)
    ax.set_xlabel("年份", fontsize=10)
    _style_ax(ax, "毛利率与净利率")
    ax.legend(fontsize=9, loc="best")
    for i, (g, n) in enumerate(zip(gm, nm, strict=False)):
        if g is not None:
            ax.annotate(
                f"{g:.1f}%",
                (i, g),
                textcoords="offset points",
                xytext=(0, 8),
                ha="center",
                fontsize=7,
                color=_C_GREEN,
            )
        if n is not None:
            ax.annotate(
                f"{n:.1f}%",
                (i, n),
                textcoords="offset points",
                xytext=(0, -12),
                ha="center",
                fontsize=7,
                color=_C_PURPLE,
            )
    fig.tight_layout()
    return _save_fig(fig, out, "chart_margin")


def _chart_roe(data: dict, out: str) -> str | None:
    """P0: ROE 变化（面积折线图 + 15% 优秀线，时间升序）。"""
    annual = data.get("annual", [])
    if len(annual) < 2:
        return None
    annual = _ascending(annual)
    years = [a["year"] for a in annual]
    roe = [a.get("roe") for a in annual]

    fig, ax = plt.subplots(figsize=_FIGSIZE_WIDE)
    ax.fill_between(range(len(years)), _nan_series(roe), alpha=0.15, color=_C_ORANGE)
    ax.plot(years, roe, marker="o", color=_C_ORANGE, linewidth=2.5, label="ROE")
    ax.axhline(y=15, color="#999", linewidth=1, linestyle="--", label="优秀线 15%")
    ax.set_ylabel("ROE（%）", fontsize=10)
    ax.set_xlabel("年份", fontsize=10)
    _style_ax(ax, "ROE 变化趋势")
    ax.legend(fontsize=9, loc="best")
    for i, r in enumerate(roe):
        if r is not None:
            ax.annotate(
                f"{r:.1f}%",
                (i, r),
                textcoords="offset points",
                xytext=(0, 8),
                ha="center",
                fontsize=7,
                color=_C_ORANGE,
            )
    fig.tight_layout()
    return _save_fig(fig, out, "chart_roe")


def _chart_cashflow(data: dict, out: str) -> str | None:
    """P0: 经营现金流净额（柱状图，亿元、时间升序）。"""
    annual = data.get("annual", [])
    if len(annual) < 2:
        return None
    annual = _ascending(annual)
    years = [a["year"] for a in annual]
    ocf = _yi_series([a.get("ocf") for a in annual])
    if all(np.isnan(v) for v in ocf):
        return None

    fig, ax = plt.subplots(figsize=_FIGSIZE_WIDE)
    colors = [_C_BLUE if (np.isfinite(v) and v >= 0) else _C_RED for v in ocf]
    ax.bar(years, ocf, color=colors, alpha=0.85, edgecolor="white", linewidth=0.5)
    ax.set_ylabel("经营现金流净额（亿元）", fontsize=10)
    ax.set_xlabel("年份", fontsize=10)
    _style_ax(ax, "经营现金流净额")
    for i, v in enumerate(ocf):
        if not np.isfinite(v):
            continue
        ax.annotate(
            f"{v:.1f}",
            (i, v),
            textcoords="offset points",
            xytext=(0, 5),
            ha="center",
            fontsize=8,
            color="#333",
        )
    fig.tight_layout()
    return _save_fig(fig, out, "chart_cashflow")


_STOCK_PRICE_MA_SPECS = (("ma5", _C_ORANGE, 1.0), ("ma20", _C_PURPLE, 1.0), ("ma60", _C_CYAN, 1.2))
_DECISION_LEVEL_SPECS = (
    ("entry_price", "入场", _C_BLUE),
    ("stop_loss", "止损", _C_GREEN),
    ("target_price", "目标", _C_RED),
)
# add-watch-trigger-tracking：触发位参考线（点线 + 独立配色，与入场/止损/目标虚线可区分）
_TRIGGER_LEVEL_SPECS = (
    ("trigger_high", "上破触发", "#E67E22"),
    ("trigger_low", "下破触发", "#8E44AD"),
)


def _mark_earnings(ax, daily: list[dict], closes: list, earnings_dates: list[str]) -> None:
    """报告期截止日竖线标注（K 线/折线两形态共用）。日期为报告期截止日而非披露日。"""
    for ed in earnings_dates:
        for i, d in enumerate(daily):
            if d["date"] == ed:
                ax.axvline(x=i, color=_C_RED, linewidth=0.8, linestyle="--", alpha=0.5)
                ax.annotate(
                    "报告期止",
                    (i, closes[i]),
                    textcoords="offset points",
                    xytext=(5, 5),
                    fontsize=7,
                    color=_C_RED,
                )
                break


def _set_date_ticks(ax, dates: list[str]) -> None:
    n = len(dates)
    step = max(1, n // 8)
    ax.set_xticks(range(0, n, step))
    ax.set_xticklabels([dates[i] for i in range(0, n, step)], rotation=30, ha="right")


def _chart_stock_price_line(
    daily: list[dict], dates: list[str], earnings_dates: list[str], out: str
) -> str:
    """历史形态（仅 close）的收盘折线渲染（升级前行为，原样保留）。"""
    closes = [d["close"] for d in daily]
    fig, ax = plt.subplots(figsize=_FIGSIZE_WIDE)
    ax.plot(dates, closes, color=_C_BLUE, linewidth=1.5)
    ax.fill_between(range(len(dates)), closes, alpha=0.1, color=_C_BLUE)
    ax.set_ylabel("股价（元）", fontsize=10)
    ax.set_xlabel("日期", fontsize=10)
    _style_ax(ax, "股价趋势")
    _mark_earnings(ax, daily, closes, earnings_dates)
    _set_date_ticks(ax, dates)
    fig.tight_layout()
    return _save_fig(fig, out, "chart_stock_price")


def _chart_stock_price(data: dict, out: str) -> str | None:
    """股价 K 线图（update-price-chart-kline）：蜡烛主图 + MA 均线 + 决策价位线 + 成交量副图。

    历史形态（缺 OHLC 字段）降级为收盘折线，保持升级前渲染。
    """
    daily = data.get("price", {}).get("daily", [])
    if len(daily) < 10:
        return None
    dates = [d["date"] for d in daily]
    earnings_dates = data.get("price", {}).get("earnings_dates", [])
    has_ohlc = all(
        d.get("open") is not None and d.get("high") is not None and d.get("low") is not None
        for d in daily
    )
    if not has_ohlc:
        return _chart_stock_price_line(daily, dates, earnings_dates, out)

    ma = data.get("price", {}).get("ma", {})
    levels = data.get("price", {}).get("decision_levels", {})

    x = np.arange(len(dates))
    opens = [d["open"] for d in daily]
    highs = [d["high"] for d in daily]
    lows = [d["low"] for d in daily]
    closes = [d["close"] for d in daily]
    colors = [_C_RED if c >= o else _C_GREEN for o, c in zip(opens, closes, strict=False)]
    # 蜡烛实体宽度随样本数自适应（250 根时约 0.12）；十字星给最小可见高度
    candle_w = max(0.12, min(0.8, 30.0 / len(dates)))
    price_range = max(highs) - min(lows) or 1.0
    body = [
        max(abs(c - o), candle_w * 1e-3 * price_range) for o, c in zip(opens, closes, strict=False)
    ]

    fig = plt.figure(figsize=(_FIGSIZE_WIDE[0], _FIGSIZE_WIDE[1] + 1.6))
    # 不显式传 hspace：matplotlib 3.11 下 gridspec 固定 hspace 与 tight_layout
    # 不兼容（触发 UserWarning 且 tight_layout 失效回退默认边距）；
    # 3:1 高度比由 tight_layout 自行给出紧凑间距，效果等同。
    gs = fig.add_gridspec(2, 1, height_ratios=[3, 1])
    ax = fig.add_subplot(gs[0])
    ax_vol = fig.add_subplot(gs[1])

    ax.vlines(x, lows, highs, colors=colors, linewidth=0.7)
    ax.bar(
        x,
        body,
        bottom=[min(o, c) for o, c in zip(opens, closes, strict=False)],
        width=candle_w,
        color=colors,
    )

    for key, color, lw in _STOCK_PRICE_MA_SPECS:
        series = ma.get(key) or []
        if len(series) == len(dates):
            ax.plot(x, _nan_series(series), color=color, linewidth=lw, label=key.upper())
    if any(len(ma.get(key) or []) == len(dates) for key, _, _ in _STOCK_PRICE_MA_SPECS):
        ax.legend(fontsize=8, loc="upper left")

    for key, label, color in _DECISION_LEVEL_SPECS:
        if key in levels:
            ax.axhline(y=levels[key], color=color, linewidth=1.0, linestyle="--", alpha=0.85)
            ax.annotate(
                label,
                xy=(1.0, levels[key]),
                xycoords=("axes fraction", "data"),
                xytext=(3, 0),
                textcoords="offset points",
                fontsize=8,
                color=color,
                va="center",
            )

    for key, label, color in _TRIGGER_LEVEL_SPECS:
        if key in levels:
            ax.axhline(y=levels[key], color=color, linewidth=1.0, linestyle=":", alpha=0.9)
            ax.annotate(
                label,
                xy=(1.0, levels[key]),
                xycoords=("axes fraction", "data"),
                xytext=(3, 0),
                textcoords="offset points",
                fontsize=8,
                color=color,
                va="center",
            )

    _mark_earnings(ax, daily, closes, earnings_dates)
    ax.set_ylabel("股价（元）", fontsize=10)
    _style_ax(ax, "股价 K 线（MA5/20/60，虚线为决策价位）")
    ax.tick_params(labelbottom=False)

    # 缺失日断开（NaN），不画 0 高量柱冒充真实成交（chart-data-integrity 三级语义）
    volumes = [np.nan if d.get("volume") is None else d["volume"] for d in daily]
    ax_vol.bar(x, volumes, width=candle_w, color=colors, alpha=0.7)
    ax_vol.set_ylabel("成交量", fontsize=9)
    ax_vol.set_xlabel("日期", fontsize=10)
    _style_ax(ax_vol)
    _set_date_ticks(ax_vol, dates)

    fig.tight_layout()
    return _save_fig(fig, out, "chart_stock_price")


def _chart_growth_vs_price(data: dict, out: str) -> str | None:
    """P1: 财务增速 vs 股价涨幅对比（时间升序）。"""
    annual = data.get("annual", [])
    daily = data.get("price", {}).get("daily", [])
    if len(annual) < 3 or len(daily) < 10:
        return None

    # 计算各年股价涨跌幅（时间升序）
    annual = _ascending(annual)
    years = [a["year"] for a in annual]
    rev_g = _ascending(data.get("growth", {}).get("revenue_growth", []))
    profit_g = _ascending(data.get("growth", {}).get("profit_growth", []))

    # 计算年度股价涨跌幅
    price_changes: list[float | None] = []
    for _i, year in enumerate(years):
        year_prices = [d["close"] for d in daily if d["date"].startswith(year)]
        prev_year = str(int(year) - 1)
        prev_prices = [d["close"] for d in daily if d["date"].startswith(prev_year)]
        if year_prices and prev_prices:
            change = (year_prices[-1] - prev_prices[-1]) / prev_prices[-1] * 100
            price_changes.append(round(change, 2))
        else:
            price_changes.append(None)

    # 对齐：growth 的 years 比 annual 少一年
    growth_years = _ascending(data.get("growth", {}).get("years", []))

    fig, ax = plt.subplots(figsize=_FIGSIZE_WIDE)
    x = np.arange(len(growth_years))
    w = 0.25

    rev_vals = rev_g[: len(growth_years)]
    profit_vals = profit_g[: len(growth_years)]
    # 股价涨跌幅需要对齐到 growth_years
    price_vals = []
    for gy in growth_years:
        idx = years.index(gy) if gy in years else -1
        if idx >= 0 and idx < len(price_changes):
            price_vals.append(price_changes[idx])
        else:
            price_vals.append(None)

    ax.bar(x - w, _nan_series(rev_vals), w, label="营收增速", color=_C_BLUE, alpha=0.85)
    ax.bar(x, _nan_series(profit_vals), w, label="净利润增速", color=_C_RED, alpha=0.85)
    ax.bar(x + w, _nan_series(price_vals), w, label="股价涨幅", color=_C_GREEN, alpha=0.85)
    ax.set_ylabel("变化率（%）", fontsize=10)
    ax.set_xticks(x)
    ax.set_xticklabels(growth_years)
    ax.axhline(y=0, color="#999", linewidth=0.8, linestyle="--")
    _style_ax(ax, "财务增速 vs 股价涨幅")
    ax.legend(fontsize=9)
    if all(v is None for v in price_vals):
        # 整序列缺失 → 占位说明，不得静默只画一半冒充完整对比
        _placeholder_text(ax, "股价涨幅数据不足（K线窗口未覆盖完整年度）")
    fig.tight_layout()
    return _save_fig(fig, out, "chart_growth_vs_price")


def _chart_assets(data: dict, out: str) -> str | None:
    """P1: 总资产与归母权益（柱状图，亿元、时间升序）。"""
    annual = data.get("annual", [])
    if len(annual) < 2:
        return None
    annual = _ascending(annual)
    years = [a["year"] for a in annual]
    assets = _yi_series([a.get("total_assets") for a in annual])
    equity = _yi_series([a.get("equity") for a in annual])
    # 两条序列全缺（如券商资产负债表未取到）→ 跳过，MUST NOT 画 0 高柱空白图
    if all(np.isnan(v) for v in assets) and all(np.isnan(v) for v in equity):
        return None

    fig, ax = plt.subplots(figsize=_FIGSIZE_WIDE)
    x = np.arange(len(years))
    w = 0.35
    ax.bar(x - w / 2, assets, w, label="总资产", color=_C_CYAN, alpha=0.85)
    ax.bar(x + w / 2, equity, w, label="归母权益", color=_C_LIME, alpha=0.85)
    ax.set_ylabel("金额（亿元）", fontsize=10)
    ax.set_xticks(x)
    ax.set_xticklabels(years)
    _style_ax(ax, "总资产与归母权益")
    ax.legend(fontsize=9)
    fig.tight_layout()
    return _save_fig(fig, out, "chart_assets")


def _chart_contract_liab(data: dict, out: str) -> str | None:
    """P1: 合同负债（柱状图，亿元、时间升序）。"""
    annual = data.get("annual", [])
    if len(annual) < 2:
        return None
    annual = _ascending(annual)
    years = [a["year"] for a in annual]
    cl = [a.get("contract_liab") for a in annual]
    if _all_missing(cl):
        return None
    cl = _yi_series(cl)

    fig, ax = plt.subplots(figsize=_FIGSIZE_WIDE)
    ax.bar(years, cl, color=_C_PINK, alpha=0.85, edgecolor="white", linewidth=0.5)
    ax.set_ylabel("合同负债（亿元）", fontsize=10)
    ax.set_xlabel("年份", fontsize=10)
    _style_ax(ax, "合同负债")
    for i, v in enumerate(cl):
        if not np.isfinite(v):
            continue
        ax.annotate(
            f"{v:.1f}",
            (i, v),
            textcoords="offset points",
            xytext=(0, 5),
            ha="center",
            fontsize=8,
            color="#333",
        )
    fig.tight_layout()
    return _save_fig(fig, out, "chart_contract_liab")


def _chart_debt_ratio(data: dict, out: str) -> str | None:
    """P1: 资产负债率趋势（时间升序）。"""
    annual = data.get("annual", [])
    if len(annual) < 2:
        return None
    annual = _ascending(annual)
    years = [a["year"] for a in annual]
    dr = [a.get("debt_ratio") for a in annual]
    if _all_missing(dr):
        return None
    dr_nan = _nan_series(dr)

    fig, ax = plt.subplots(figsize=_FIGSIZE_WIDE)
    ax.plot(years, dr_nan, marker="o", color=_C_RED, linewidth=2.5)
    ax.fill_between(range(len(years)), dr_nan, alpha=0.1, color=_C_RED)
    ax.set_ylabel("资产负债率（%）", fontsize=10)
    ax.set_xlabel("年份", fontsize=10)
    _style_ax(ax, "资产负债率趋势")
    for i, v in enumerate(dr):
        if v is not None:
            ax.annotate(
                f"{v:.1f}%",
                (i, v),
                textcoords="offset points",
                xytext=(0, 8),
                ha="center",
                fontsize=7,
                color=_C_RED,
            )
    fig.tight_layout()
    return _save_fig(fig, out, "chart_debt_ratio")


def _chart_heatmap(data: dict, out: str) -> str | None:
    """P2: 年报报告期窗口股价变化热力图（行序时间升序，缺失格标注「缺」）。锚点为报告期截止日。"""
    daily = data.get("price", {}).get("daily", [])
    earnings_dates = data.get("price", {}).get("earnings_dates", [])
    if len(earnings_dates) < 2 or len(daily) < 30:
        return None

    # 计算每个报告期截止日前后 N 天的收益率
    windows = [-5, -1, 0, 1, 5, 10, 30]
    years_labels = []
    heatmap_data: list[list[float | None]] = []

    for ed in earnings_dates:
        ed_dt = pd.to_datetime(ed)
        year_label = ed_dt.strftime("%Y年报")
        years_labels.append(year_label)
        row: list[float | None] = []
        for offset in windows:
            target = ed_dt + pd.Timedelta(days=offset)
            # 找最近交易日
            target_str = target.strftime("%Y-%m-%d")
            base_idx = None
            for i, d in enumerate(daily):
                if d["date"] == target_str:
                    base_idx = i
                    break
            if base_idx is None:
                # 找最近的
                for i, d in enumerate(daily):
                    if d["date"] <= target_str:
                        base_idx = i
                    else:
                        break
            if base_idx is not None and base_idx > 0:
                prev_close = daily[max(0, base_idx - 1)]["close"]
                curr_close = daily[base_idx]["close"]
                if prev_close != 0:
                    ret = (curr_close - prev_close) / prev_close * 100
                    row.append(round(ret, 2))
                else:
                    row.append(None)
            else:
                # 窗口不在 K 线覆盖范围内 → 缺失，MUST NOT 填 0 冒充无波动
                row.append(None)
        heatmap_data.append(row)

    if not heatmap_data:
        return None

    # 行序时间升序（最旧在顶部）
    years_labels = _ascending(years_labels)
    heatmap_data = _ascending(heatmap_data)

    arr = np.array([[np.nan if v is None else v for v in row] for row in heatmap_data], dtype=float)
    masked = np.ma.masked_invalid(arr)
    cmap = plt.cm.RdYlGn.with_extremes(bad="#e5e7eb")  # 缺失格灰底
    fig, ax = plt.subplots(figsize=_FIGSIZE_WIDE)
    im = ax.imshow(masked, cmap=cmap, aspect="auto", vmin=-8, vmax=8)
    ax.set_xticks(range(len(windows)))
    ax.set_xticklabels([f"T{w:+d}" if w != 0 else "T0" for w in windows])
    ax.set_yticks(range(len(years_labels)))
    ax.set_yticklabels(years_labels)
    ax.set_title(
        "年报报告期窗口股价变化（%）", fontsize=13, fontweight="bold", color="#333", pad=12
    )
    # 标注数值；缺失格标注「缺」，MUST NOT 渲染为 0.0
    for i in range(len(years_labels)):
        for j in range(len(windows)):
            val = masked[i, j]
            if np.ma.is_masked(masked[i, j]) or (isinstance(val, float) and np.isnan(val)):
                ax.text(j, i, _MISSING_MARK, ha="center", va="center", fontsize=8, color="#999")
                continue
            color = "white" if abs(float(val)) > 5 else "#333"
            ax.text(j, i, f"{float(val):.1f}", ha="center", va="center", fontsize=8, color=color)
    fig.colorbar(im, ax=ax, shrink=0.8, label="收益率（%）")
    fig.tight_layout()
    return _save_fig(fig, out, "chart_heatmap")


def _chart_dashboard(data: dict, out: str) -> str | None:
    """P2: 综合仪表盘（多子图拼图，时间升序、金额亿元、全缺子图占位）。"""
    annual = data.get("annual", [])
    if len(annual) < 2:
        return None

    annual = _ascending(annual)
    years = [a["year"] for a in annual]
    fig, axes = plt.subplots(2, 3, figsize=(15, 8))
    fig.suptitle("财务指标综合仪表盘", fontsize=16, fontweight="bold", color="#333", y=0.98)

    # 1. 营收 & 净利润（亿元）
    ax = axes[0, 0]
    ax.bar(
        years,
        _yi_series([a.get("revenue") for a in annual]),
        color=_C_BLUE,
        alpha=0.7,
        label="营收",
    )
    ax.bar(
        years,
        _yi_series([a.get("net_profit") for a in annual]),
        color=_C_RED,
        alpha=0.7,
        label="净利润",
    )
    ax.set_ylabel("亿元", fontsize=8)
    _style_ax(ax, "营收 & 净利润")
    ax.legend(fontsize=7)

    # 2. 毛利率 & 净利率
    ax = axes[0, 1]
    gm = [a.get("gross_margin") for a in annual]
    nm = [a.get("net_margin") for a in annual]
    if _all_missing(gm) and _all_missing(nm):
        _placeholder_text(ax, margin_placeholder_text(data.get("industry")))
        ax.set_xticks(range(len(years)))
        ax.set_xticklabels(years, fontsize=7)
    else:
        ax.plot(years, _nan_series(gm), marker="o", color=_C_GREEN, label="毛利率")
        ax.plot(years, _nan_series(nm), marker="s", color=_C_PURPLE, label="净利率")
        ax.legend(fontsize=7)
    _style_ax(ax, "利润率")

    # 3. ROE
    ax = axes[0, 2]
    roe = [a.get("roe") for a in annual]
    if _all_missing(roe):
        _placeholder_text(ax, "ROE 数据缺失")
        ax.set_xticks(range(len(years)))
        ax.set_xticklabels(years, fontsize=7)
    else:
        ax.plot(years, _nan_series(roe), marker="o", color=_C_ORANGE)
        ax.axhline(y=15, color="#999", linewidth=0.8, linestyle="--")
    _style_ax(ax, "ROE")

    # 4. 现金流（亿元）
    ax = axes[1, 0]
    ocf = _yi_series([a.get("ocf") for a in annual])
    if all(np.isnan(v) for v in ocf):
        _placeholder_text(ax, "经营现金流数据缺失")
        ax.set_xticks(range(len(years)))
        ax.set_xticklabels(years, fontsize=7)
    else:
        ax.bar(years, ocf, color=_C_CYAN, alpha=0.85)
        ax.set_ylabel("亿元", fontsize=8)
    _style_ax(ax, "经营现金流")

    # 5. 资产负债率
    ax = axes[1, 1]
    dr = [a.get("debt_ratio") for a in annual]
    if _all_missing(dr):
        # 券商等报表缺失时 MUST NOT 渲染 0.00% 与正文矛盾（#238）
        _placeholder_text(ax, "资产负债率数据缺失")
        ax.set_xticks(range(len(years)))
        ax.set_xticklabels(years, fontsize=7)
    else:
        dr_nan = _nan_series(dr)
        ax.plot(years, dr_nan, marker="o", color=_C_RED)
        ax.fill_between(range(len(years)), dr_nan, alpha=0.1, color=_C_RED)
    _style_ax(ax, "资产负债率")

    # 6. 增速
    ax = axes[1, 2]
    growth = data.get("growth", {})
    gy = _ascending(growth.get("years", []))
    if gy:
        ax.bar(
            np.arange(len(gy)) - 0.15,
            _nan_series(_ascending(growth.get("revenue_growth", []))),
            0.3,
            color=_C_BLUE,
            alpha=0.7,
            label="营收增速",
        )
        ax.bar(
            np.arange(len(gy)) + 0.15,
            _nan_series(_ascending(growth.get("profit_growth", []))),
            0.3,
            color=_C_RED,
            alpha=0.7,
            label="利润增速",
        )
        ax.set_xticks(range(len(gy)))
        ax.set_xticklabels(gy, fontsize=7)
        ax.axhline(y=0, color="#999", linewidth=0.5, linestyle="--")
        ax.legend(fontsize=7)
    _style_ax(ax, "同比增速")

    fig.tight_layout(rect=[0, 0, 1, 0.96])
    return _save_fig(fig, out, "chart_dashboard")


def _chart_market_share(data: dict, out: str) -> str | None:
    """P2: 市场份额（占位图，数据不可得时生成说明图）。"""
    market_share = data.get("market_share")
    fig, ax = plt.subplots(figsize=_FIGSIZE_SQUARE)
    ax.set_facecolor(_C_BG)
    ax.axis("off")

    if market_share and isinstance(market_share, dict) and market_share.get("shares"):
        shares = market_share["shares"]
        labels = [s["name"] for s in shares]
        sizes = [s["value"] for s in shares]
        colors = plt.cm.Set2(np.linspace(0, 1, len(labels)))
        wedges, texts, autotexts = ax.pie(
            sizes, labels=labels, autopct="%1.1f%%", colors=colors, startangle=90
        )
        ax.set_title("全球市场份额", fontsize=13, fontweight="bold", color="#333", pad=12)
    else:
        ax.text(
            0.5,
            0.5,
            "市场份额数据暂不可得\n（需额外数据源）",
            ha="center",
            va="center",
            fontsize=14,
            color="#999",
            transform=ax.transAxes,
        )
        ax.set_title("全球市场份额", fontsize=13, fontweight="bold", color="#333", pad=12)

    fig.tight_layout()
    return _save_fig(fig, out, "chart_market_share")


# ════════════════════════════════════════════════════════
#  统一入口
# ════════════════════════════════════════════════════════


def generate_all_charts(chart_data: dict, output_dir: str) -> dict[str, str]:
    """生成全部图表 PNG，返回 {chart_name: file_path}。"""
    os.makedirs(output_dir, exist_ok=True)
    charts: dict[str, str] = {}

    generators = [
        ("chart_revenue_profit", _chart_revenue_profit),
        ("chart_growth", _chart_growth),
        ("chart_margin", _chart_margin),
        ("chart_roe", _chart_roe),
        ("chart_cashflow", _chart_cashflow),
        ("chart_stock_price", _chart_stock_price),
        ("chart_growth_vs_price", _chart_growth_vs_price),
        ("chart_assets", _chart_assets),
        ("chart_contract_liab", _chart_contract_liab),
        ("chart_debt_ratio", _chart_debt_ratio),
        ("chart_heatmap", _chart_heatmap),
        ("chart_dashboard", _chart_dashboard),
        ("chart_market_share", _chart_market_share),
    ]

    for name, gen_func in generators:
        try:
            path = gen_func(chart_data, output_dir)
            if path:
                charts[name] = path
        except Exception as e:
            logger.warning("图表 %s 生成失败: %s", name, e)

    return charts
