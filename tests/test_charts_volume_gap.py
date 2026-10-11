"""K 线成交量子图缺失值断开（chart-data-integrity「图表缺数据禁止填 0 渲染」）。

issue #238 图表层残余审计：`_chart_stock_price` 的成交量序列曾把缺失日
`or 0` 画成 0 高量柱——spec 三级语义要求序列级部分缺失断开该点（NaN 断开，
不画 0 值柱冒充真实成交）。
"""

from __future__ import annotations

import math

import matplotlib

matplotlib.use("Agg")

import pytest

from finance_agent import charts


@pytest.fixture
def capture_figs(monkeypatch, tmp_path):
    saved: dict[str, object] = {}
    orig_close = charts.plt.Figure  # noqa: F841 — 占位说明：真实钩子是 _save_fig

    def fake_save(fig, out, name):
        saved[name] = fig
        return f"{out}/{name}.png"

    monkeypatch.setattr(charts, "_save_fig", fake_save)
    yield saved


def _daily_with_volume_gap() -> list[dict]:
    days = []
    for i in range(12):
        days.append(
            {
                "date": f"2026-01-{i + 1:02d}",
                "open": 10.0 + i,
                "close": 10.5 + i,
                "high": 11.0 + i,
                "low": 9.5 + i,
                # 第 3 天（index 2）成交量缺失
                "volume": None if i == 2 else 1000.0 + i,
            }
        )
    return days


def test_volume_gap_breaks_not_zero(capture_figs):
    data = {"price": {"daily": _daily_with_volume_gap(), "ma": {}}}
    charts._chart_stock_price(data, "out")
    fig = capture_figs["chart_stock_price"]
    vol_ax = fig.axes[-1]  # 成交量子图是最后坐标系
    heights = [p.get_height() for p in vol_ax.patches]
    assert len(heights) == 12
    assert math.isnan(heights[2]), "缺失日量柱应断开（NaN），不得画 0 高柱"
    assert all(not math.isnan(h) for i, h in enumerate(heights) if i != 2)
    assert all(h > 0 for i, h in enumerate(heights) if i != 2)
