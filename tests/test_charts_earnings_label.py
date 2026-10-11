"""报告期截止日标注措辞（fix-earnings-marker-caliber / issue #243 子项3）。

earnings_dates 数据源为利润表「报告日」= 报告期截止日（12-31 等），非披露日。
spec「K 线图双端渲染」要求标注措辞如实表述为「报告期截止」，禁止「发布日」。
"""

from __future__ import annotations

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt

from finance_agent import charts


class _RecordingAx:
    """记录 annotate / axvline 调用的假轴。"""

    def __init__(self) -> None:
        self.annotations: list[str] = []
        self.lines: int = 0

    def axvline(self, **kwargs) -> None:
        self.lines += 1

    def annotate(self, text, *args, **kwargs) -> None:
        self.annotations.append(text)


def test_mark_earnings_label_is_period_end_not_release():
    daily = [{"date": "2024-12-31", "close": 10.0}, {"date": "2025-01-02", "close": 10.1}]
    ax = _RecordingAx()
    charts._mark_earnings(ax, daily, [10.0, 10.1], ["2024-12-31"])
    assert ax.lines == 1
    assert ax.annotations == ["报告期止"]
    assert all("发布" not in t for t in ax.annotations)


def test_build_chart_data_comment_caliber(tmp_path):
    """端到端口径：collect_chart_data 产出的 earnings_dates 即报告期截止日原样。"""
    import pandas as pd

    state = {
        "stock_code": "601818",
        "stock_name": "光大银行",
        "income_statement": pd.DataFrame({"报告日": ["20241231", "20231231"]}),
        "kline": pd.DataFrame(
            {
                "日期": ["2024-12-31", "2025-01-02"],
                "开盘": [3.0, 3.1],
                "收盘": [3.1, 3.2],
                "最高": [3.15, 3.25],
                "最低": [2.95, 3.05],
                "成交量": [1000, 1100],
            }
        ),
    }
    data = charts.collect_chart_data(state)
    assert data["price"]["earnings_dates"] == ["2024-12-31", "2023-12-31"]


def test_png_renders_without_release_wording(tmp_path):
    """服务端 PNG 生成不报错（措辞断言由 _mark_earnings 单测钉子）。"""
    daily = [{"date": f"2025-01-{i + 1:02d}", "close": 10 + i * 0.1} for i in range(12)]
    data = {
        "stock_code": "601818",
        "stock_name": "光大银行",
        "annual": [],
        "growth": {"years": [], "revenue_growth": [], "profit_growth": []},
        "price": {
            "daily": daily,
            "earnings_dates": ["2025-01-03"],
            "ma": {"ma5": [], "ma20": [], "ma60": []},
        },
        "kpi": {},
        "market_share": None,
    }
    out = charts._chart_stock_price_line(
        daily, [d["date"] for d in daily], data["price"]["earnings_dates"], str(tmp_path)
    )
    assert out.endswith(".png")
    plt.close("all")
