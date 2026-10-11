"""Task 12：报告渲染层确定性呈现最新报告期快照/估值快照/健康度行业口径。

spec industry-threshold-coverage「健康度评分行业口径披露」：报告渲染层 SHALL 使
读者可见评分所用口径——不能只依赖 LLM markdown 引用（三轮实跑证明其方差）。
"""

from finance_agent.nodes.report import _format_freshness_section


def test_full_section_with_industry_override():
    state = {
        "latest_period_snapshot": {
            "报告日": "2026-06-30",
            "期类型": "中报",
            "毛利率(%)": 41.0,
            "资产负债率(%)": 47.85,
            "存货": 88.33,
            "合同负债": 51.31,
            "营收同比(%)": 49.08,
            "归母净利同比(%)": 1328.72,
            "missing": [],
        },
        "valuation_snapshot": {
            "market_cap": 1918.64,
            "PE": None,
            "PE_ttm": 88.17,
            "PE_caliber": "derived_ttm",
            "PB": 15.09,
            "missing_reasons": [],
        },
        "health_score": {
            "total": 48.8,
            "rating": "warning",
            "industry_override": {
                "industry": "半导体设备",
                "metrics": ["存货周转率", "速动比率", "应付账款周转率"],
            },
        },
    }
    section = _format_freshness_section(state)
    assert section is not None
    assert (
        "###" not in section and "财务数据口径披露" not in section
    )  # 标题由 generate_report 编号注入
    assert "最新报告期快照" in section and "2026-06-30" in section and "中报" in section
    assert "41.0" in section and "47.85" in section and "88.33" in section and "51.31" in section
    assert "累计口径" in section
    assert "1918.64" in section and "88.17" in section and "TTM 推导口径" in section
    assert "48.8" in section
    assert "半导体设备" in section and "存货周转率" in section and "行业口径" in section
    assert "暂缺" not in section  # 全字段在位时无暂缺


def test_generic_caliber_when_no_override():
    state = {
        "health_score": {
            "total": 55.0,
            "rating": "caution",
            "industry_override": {"industry": None, "metrics": []},
        }
    }
    section = _format_freshness_section(state)
    assert section is not None
    assert "通用口径" in section and "55.0" in section


def test_valuation_missing_declared():
    state = {
        "valuation_snapshot": {
            "market_cap": None,
            "PE": None,
            "PE_ttm": None,
            "PE_caliber": None,
            "PB": None,
            "missing_reasons": ["market_cap 缺失", "快照缺失"],
        },
    }
    section = _format_freshness_section(state)
    assert section is not None
    assert "估值数据缺失" in section and "market_cap 缺失" in section


def test_missing_fields_render_without_unit_suffix():
    state = {
        "latest_period_snapshot": {
            "报告日": "2026-06-30",
            "期类型": "中报",
            "毛利率(%)": None,
            "存货": None,
        },
    }
    section = _format_freshness_section(state)
    assert section is not None
    assert "毛利率 暂缺" in section and "存货 暂缺" in section
    assert "暂缺%" not in section and "暂缺 亿" not in section
    assert "None" not in section


def test_empty_state_returns_none():
    assert _format_freshness_section({}) is None


class TestRiskMetricsAndPriceLevelsDisclosure:
    """add-risk-metrics-disclosure（issue #242 断点 1）：风控链路确定性计算
    （risk_metrics/price_levels）SHALL 在口径披露节可溯源——光大 601818 报告中
    波动率/回撤/VaR/beta/触发价位首现于决策与风控节、报告内无出处的 P0-3 缺口。
    """

    def test_full_risk_metrics_and_price_levels_rendered(self):
        state = {
            "risk_metrics": {
                "volatility": 0.1568,
                "max_drawdown": 0.1868,
                "var_95": 0.0173,
                "beta": 0.004,
            },
            "price_levels": {
                "available": True,
                "entry_ref": 3.21,
                "stop_band_long": {"low": 2.92, "high": 3.05},
                "target_band_long": {"low": 3.55, "high": 3.86},
            },
        }
        section = _format_freshness_section(state)
        assert section is not None
        # 百分数两位小数（光大审计原值复现口径）
        assert "15.68%" in section and "18.68%" in section and "1.73%" in section
        assert "0.004" in section  # beta
        assert "波动率" in section and "最大回撤" in section and "VaR" in section
        # 价位带
        assert "3.21" in section and "2.92" in section and "3.05" in section
        assert "3.55" in section and "3.86" in section
        assert "止损" in section and "目标" in section
        # 节内列表项：不产生新章节标题（导出切章/段数不变）
        assert "###" not in section and "##" not in section

    def test_beta_omitted_when_benchmark_unavailable(self):
        state = {
            "risk_metrics": {
                "volatility": 0.1568,
                "max_drawdown": 0.1868,
                "var_95": 0.0173,
            }
        }
        section = _format_freshness_section(state)
        assert section is not None
        assert "15.68%" in section and "VaR" in section
        # beta 缺席是数据语义（基准不可得）而非字段缺失——省略该段而非「暂缺」
        assert "beta" not in section.lower()
        assert "暂缺" not in section

    def test_price_levels_unavailable_honest_note(self):
        state = {"price_levels": {"available": False, "reason": "insufficient_kline"}}
        section = _format_freshness_section(state)
        assert section is not None
        assert "价位参考不可用" in section and "insufficient_kline" in section

    def test_both_keys_absent_zero_regression(self):
        # 两键全缺：不增行；三旧数据源也全缺时节整体 None（与现状逐字节一致）
        assert _format_freshness_section({}) is None
        # 仅有旧数据源时，输出与无两键完全一致（新增行不出现）
        old_only = {
            "health_score": {
                "total": 55.0,
                "rating": "caution",
                "industry_override": {"industry": None, "metrics": []},
            }
        }
        section = _format_freshness_section(old_only)
        assert section is not None
        assert "波动率" not in section and "止损" not in section


class TestBankingCalibrationRendering:
    """add-banking-industry-calibration：健康度满分渲染 + 快照信用减值损失行。"""

    def test_health_line_shows_cap_when_below_100(self):
        state = {
            "health_score": {
                "total": 16.67,
                "rating": "caution",
                "score_cap": 50,
                "industry_override": {"industry": "银行", "metrics": ["资产负债率", "ROA"]},
            }
        }
        section = _format_freshness_section(state)
        assert section is not None
        assert "满分 50" in section

    def test_health_line_unchanged_when_cap_100(self):
        state = {
            "health_score": {
                "total": 55.0,
                "rating": "caution",
                "score_cap": 100,
                "industry_override": {"industry": None, "metrics": []},
            }
        }
        section = _format_freshness_section(state)
        assert section is not None
        assert "满分" not in section

    def test_snapshot_line_appends_credit_impairment(self):
        state = {
            "latest_period_snapshot": {
                "报告日": "2026-06-30",
                "期类型": "中报",
                "毛利率(%)": None,
                "资产负债率(%)": 90.9,
                "存货": None,
                "合同负债": None,
                "营收同比(%)": 5.2,
                "归母净利同比(%)": -24.01,
                "信用减值损失": 208.79,
                "信用减值损失同比(%)": 31.3,
            },
        }
        section = _format_freshness_section(state)
        assert section is not None
        assert "信用减值损失 208.79 亿（同比 31.3%）" in section

    def test_snapshot_line_unchanged_without_credit_impairment(self):
        state = {
            "latest_period_snapshot": {
                "报告日": "2026-06-30",
                "期类型": "中报",
                "毛利率(%)": 41.0,
                "资产负债率(%)": 47.9,
                "存货": 88.33,
                "合同负债": 51.31,
                "营收同比(%)": 49.06,
                "归母净利同比(%)": 1324.1,
            },
        }
        section = _format_freshness_section(state)
        assert section is not None
        assert "信用减值损失" not in section
