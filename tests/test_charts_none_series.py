"""复现缺陷：chart_growth_vs_price 收到含 None 的序列时崩溃缺图。

线上事故（2026-08-25 601700 深研管线）：chart_growth_vs_price 抛
"unsupported operand type(s) for +: 'int' and 'NoneType'"，被
generate_all_charts 吞掉，报告缺该图。None 在数据契约中合法——
growth 序列在基期为负/缺失时增长率为 None（_chart_growth 同源
数据已按 None 分支处理），股价涨幅在上市首年/缺前年数据时为 None。
bar 类图表必须把 None 归一为 nan（matplotlib 自动断开），不得崩溃。
"""

from __future__ import annotations

from finance_agent.charts import generate_all_charts


def _chart_data_with_none_series() -> dict:
    """构造含 None 的合法数据：3 年年报、2024 起才有行情、增速含 None。"""
    daily = [{"date": f"2024-06-{d:02d}", "close": 5.0 + d * 0.1} for d in range(1, 11)]
    daily += [{"date": f"2025-06-{d:02d}", "close": 6.0 + d * 0.1} for d in range(1, 11)]
    return {
        "annual": [
            {"year": "2023", "revenue": 100.0, "net_profit": -2.0},
            {"year": "2024", "revenue": 92.0, "net_profit": 0.9},
            {"year": "2025", "revenue": 85.0, "net_profit": -3.9},
        ],
        "growth": {
            "years": ["2024", "2025"],
            # 2024 基期亏损/数据缺失 → 增速 None
            "revenue_growth": [None, -6.7],
            "profit_growth": [None, -120.5],
        },
        "price": {"daily": daily},
    }


def test_growth_vs_price_tolerates_none_series(tmp_path):
    """含 None 的增速/股价涨幅序列不得导致 chart_growth_vs_price 缺图。"""
    charts = generate_all_charts(_chart_data_with_none_series(), str(tmp_path))
    assert "chart_growth_vs_price" in charts, (
        "chart_growth_vs_price 未生成：含 None 的序列（增速基期缺失/股价缺前年）"
        "是合法数据，bar 图须以 nan 断开而非崩溃"
    )
    assert charts["chart_growth_vs_price"].endswith(".png")


# ── 银行业利润率图占位（add-banking-industry-calibration，issue #241）──


class TestBankingMarginPlaceholder:
    """占位语义区分「数据缺失」与「行业不适用」：银行毛利率 None 是口径不适用。"""

    def test_banking_industry_placeholder_text(self):
        from finance_agent.charts import margin_placeholder_text

        text = margin_placeholder_text("银行")
        assert "银行业不适用毛利率口径" in text
        assert "数据缺失" not in text

    def test_non_banking_placeholder_unchanged(self):
        from finance_agent.charts import margin_placeholder_text

        assert margin_placeholder_text("白酒") == "利润率数据缺失"
        assert margin_placeholder_text(None) == "利润率数据缺失"
