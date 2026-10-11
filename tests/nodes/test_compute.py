"""compute.py 单元测试 — 验证编排层正确写入 State 字段。"""

import pandas as pd
import pytest

from finance_agent.nodes.compute import format_peer_comparison


@pytest.fixture
def sample_state(balance_sheet, income_statement, cash_flow, indicators):
    return {
        "balance_sheet": balance_sheet,
        "income_statement": income_statement,
        "cash_flow_statement": cash_flow,
        "financial_indicators": indicators,
        "stock_quote": {},
        "industry_info": {},
        "peer_financials": None,
    }


@pytest.fixture
def kline_state(sample_state):
    """带 K 线数据的 state，用于技术指标和风控指标计算。"""
    sample_state["kline"] = pd.DataFrame(
        {
            "日期": pd.date_range("2024-01-02", periods=30, freq="B"),
            "开盘": [float(i) for i in range(10, 40)],
            "收盘": [float(i) for i in range(11, 41)],
            "最高": [float(i) for i in range(11, 41)],
            "最低": [float(i) for i in range(10, 40)],
            "成交量": [1000 + i * 100 for i in range(30)],
        }
    )
    return sample_state


class TestComputeMetrics:
    def test_returns_all_metric_keys(self, sample_state):
        from finance_agent.nodes.compute import compute_metrics

        result = compute_metrics(sample_state)
        expected_keys = [
            "solvency_metrics",
            "profitability_metrics",
            "efficiency_metrics",
            "cashflow_metrics",
            "dupont_tree",
            "traffic_lights",
            "growth_rates",
            "anomalies",
            "garp_result",
        ]
        for key in expected_keys:
            assert key in result, f"missing key: {key}"

    def test_solvency_has_5_metrics(self, sample_state):
        from finance_agent.nodes.compute import compute_metrics

        result = compute_metrics(sample_state)
        solv = result["solvency_metrics"]
        assert len(solv) == 5
        assert "资产负债率" in solv

    def test_profitability_has_5_metrics(self, sample_state):
        from finance_agent.nodes.compute import compute_metrics

        result = compute_metrics(sample_state)
        prof = result["profitability_metrics"]
        assert len(prof) == 5
        assert "ROE" in prof

    def test_traffic_lights_structure(self, sample_state):
        from finance_agent.nodes.compute import compute_metrics

        result = compute_metrics(sample_state)
        tl = result["traffic_lights"]
        assert "solvency" in tl
        assert "profitability" in tl

    def test_health_score_exists(self, sample_state):
        from finance_agent.nodes.compute import compute_metrics

        result = compute_metrics(sample_state)
        assert "health_score" in result
        score = result["health_score"]
        assert "total" in score
        assert "rating" in score
        assert score["total"] >= 0

    def test_growth_rates_not_empty(self, sample_state):
        from finance_agent.nodes.compute import compute_metrics

        result = compute_metrics(sample_state)
        gr = result["growth_rates"]
        assert len(gr) > 0

    def test_anomalies_is_list(self, sample_state):
        from finance_agent.nodes.compute import compute_metrics

        result = compute_metrics(sample_state)
        assert isinstance(result["anomalies"], list)

    def test_dupont_tree_has_levels(self, sample_state):
        from finance_agent.nodes.compute import compute_metrics

        result = compute_metrics(sample_state)
        dupont = result["dupont_tree"]
        assert "L1" in dupont

    def test_garp_result_exists(self, sample_state):
        from finance_agent.nodes.compute import compute_metrics

        result = compute_metrics(sample_state)
        assert "garp_result" in result
        assert "pass" in result["garp_result"]

    def test_garp_industry_pe_nan_avg_treated_as_missing(self, sample_state):
        """复审 F2：state.industry_pe.avg_pe=NaN 不得让 PE 项假性通过。

        NaN 参与比较恒 False，不守卫会把「行业 PE 缺失」伪装成 PE 达标（与诚实性反向）。
        """
        from finance_agent.nodes.compute import compute_metrics

        # valuation_snapshot 有 PE_ttm（年报直取：927 / 9.27 = 100.0）
        sample_state["stock_quote"] = {"market_cap": 927e8}  # 元口径（东财形）→ 927 亿
        sample_state["latest_period_snapshot"] = {"期类型": "年报"}
        inc = sample_state["income_statement"].rename(
            columns={"归属于母公司所有者的净利润": "归母净利润"}
        )
        inc["归母净利润"] = [9.27e8, 6.88e8, 6.34e8]
        sample_state["income_statement"] = inc
        sample_state["industry_pe"] = {"avg_pe": float("nan")}

        result = compute_metrics(sample_state)
        garp = result["garp_result"]
        assert "行业平均 PE 数据缺失（未参与比较）" in garp["failures"]
        assert "PE >= 行业平均" not in garp["failures"]
        # PE 本身有值（PE_ttm 路径），只缺行业均值 → 缺数桶而非缺 PE 桶
        assert garp["details"]["PE"] == 100.0
        assert garp["details"]["PE_caliber"] == "derived_ttm"

    def test_technical_indicators_when_kline_present(self, kline_state):
        """有 K 线数据时计算技术指标。"""
        from finance_agent.nodes.compute import compute_metrics

        result = compute_metrics(kline_state)
        assert "technical_indicators" in result
        assert "MA" in result["technical_indicators"]
        assert "MACD" in result["technical_indicators"]

    def test_risk_metrics_when_kline_present(self, kline_state):
        """有 K 线数据时计算风控指标。"""
        from finance_agent.nodes.compute import compute_metrics

        result = compute_metrics(kline_state)
        assert "risk_metrics" in result
        assert "max_drawdown" in result["risk_metrics"]
        assert "volatility" in result["risk_metrics"]

    def test_no_technical_when_kline_absent(self, sample_state):
        """无 K 线数据时不产出技术指标。"""
        from finance_agent.nodes.compute import compute_metrics

        result = compute_metrics(sample_state)
        assert "technical_indicators" not in result
        assert "risk_metrics" not in result


def _peer_df(rows):
    cols = [
        "name",
        "code",
        "PE",
        "PB",
        "total_mv",
        "revenue_yoy",
        "netprofit_yoy",
        "gross_margin",
        "report_period",
    ]
    return pd.DataFrame(rows, columns=cols)


class TestFormatPeerComparison:
    def test_full_table_target_first_row(self):
        state = {
            "stock_code": "600519",
            "stock_name": "贵州茅台",
            "peer_financials": _peer_df(
                [
                    {
                        "name": "五粮液",
                        "code": "000858",
                        "PE": 15.0,
                        "PB": 3.2,
                        "total_mv": 5000.0,
                        "revenue_yoy": 7.1,
                        "netprofit_yoy": 8.2,
                        "gross_margin": 76.5,
                        "report_period": "2026-06-30",
                    }
                ]
            ),
            "latest_period_snapshot": {
                "营收同比(%)": 9.1,
                "归母净利同比(%)": 10.2,
                "毛利率(%)": 91.3,
                "报告日": "2026-06-30",
            },
        }
        vs = {"PE": 22.0, "PE_ttm": None, "PB": 8.0, "market_cap": 18000.0}
        text = format_peer_comparison(state, vs)
        assert text is not None
        lines = text.splitlines()
        assert "总市值(亿)" in lines[1] and "毛利率(%)" in lines[1]
        target_row = next(line for line in lines if "600519" in line)
        peer_row = next(line for line in lines if "000858" in line)
        assert "贵州茅台" in target_row and "22.0" in target_row and "91.3" in target_row
        assert "五粮液" in peer_row and "15.0" in peer_row and "76.5" in peer_row
        # 主标的必须在首行（对标股之前）
        assert lines.index(target_row) < lines.index(peer_row)

    def test_financial_group_missing_rendered_as_dash(self):
        state = {
            "stock_code": "600519",
            "stock_name": "贵州茅台",
            "peer_financials": _peer_df(
                [
                    {
                        "name": "五粮液",
                        "code": "000858",
                        "PE": 15.0,
                        "PB": 3.2,
                        "total_mv": 5000.0,
                        "revenue_yoy": None,
                        "netprofit_yoy": None,
                        "gross_margin": None,
                        "report_period": None,
                    }
                ]
            ),
            "latest_period_snapshot": {},
        }
        vs = {"PE": 22.0, "PE_ttm": None, "PB": 8.0, "market_cap": 18000.0}
        text = format_peer_comparison(state, vs)
        assert text is not None
        peer_row = next(line for line in text.splitlines() if "000858" in line)
        assert "—" in peer_row  # 缺失标记，列不消失
        assert "毛利率" in text  # 表头仍在

    def test_no_peer_data_returns_none(self):
        assert format_peer_comparison({"stock_code": "600519"}, {"PE": 22.0}) is None

    def test_non_dataframe_peer_financials_treated_as_unusable(self):
        """回归（CI test_citation 覆盖测试）：契约外形态（list 充当 peer_financials）
        不得炸 compute——按不可用走既有降级路径。"""
        bad = [{"code": "000001", "PE": 12.0, "PB": 1.1}]
        assert (
            format_peer_comparison({"stock_code": "600519", "peer_financials": bad}, {"PE": 22.0})
            is None
        )
        text = format_peer_comparison(
            {"stock_code": "600519", "peer_financials": bad, "peer_codes": ["000001"]},
            {"PE": 22.0},
        )
        assert text is not None and "不可用" in text

    def test_peer_requested_but_fetch_degraded_returns_missing_declaration(self):
        """对比请求成立但同业抓取全部降级 → 注入缺失声明而非静默省略（spec R4）。"""
        state = {
            "stock_code": "600519",
            "peer_codes": ["000858"],
        }
        text = format_peer_comparison(state, {"PE": 22.0})
        assert text is not None
        assert "同业" in text
        assert "不可用" in text

    def test_no_peer_codes_and_no_data_returns_none(self):
        """未指定对标股且无数据 → 维持省略注入（spec R3「无对标股不注入」）。"""
        state = {"stock_code": "600519"}
        assert format_peer_comparison(state, {"PE": 22.0}) is None

    def test_pe_ttm_fallback_caliber_note(self):
        """主标的静态 PE 缺失回落 PE_ttm 时，附跨口径提示（与 relative_valuation 口径标注同族）。"""
        state = {
            "stock_code": "600519",
            "stock_name": "贵州茅台",
            "peer_financials": _peer_df(
                [
                    {
                        "name": "五粮液",
                        "code": "000858",
                        "PE": 15.0,
                        "PB": 3.2,
                        "total_mv": 5000.0,
                        "revenue_yoy": 7.1,
                        "netprofit_yoy": 8.2,
                        "gross_margin": 76.5,
                        "report_period": "2026-06-30",
                    }
                ]
            ),
            "latest_period_snapshot": {},
        }
        vs = {"PE": None, "PE_ttm": 21.5, "PB": 8.0, "market_cap": 18000.0}
        text = format_peer_comparison(state, vs)
        assert text is not None
        assert "TTM" in text and "跨口径" in text
        target_row = next(line for line in text.splitlines() if "600519" in line)
        assert "21.5" in target_row


class TestPeerPEMissingDisclosure:
    """issue #276：腾讯主源 _TENCENT_FIELD_IDX 有意不含 PE → 同业行 PE 系统性
    None；旧跨口径注在 peer PE 全缺时谎称「对标股 PE 为行情快照口径」——
    违反 peer-comparison spec「如实呈现」。修复=按缺失形态给诚实披露。"""

    def _pe_missing_peers(self):
        return _peer_df(
            [
                {
                    "name": "五粮液",
                    "code": "000858",
                    "PE": None,
                    "PB": 3.2,
                    "total_mv": 5000.0,
                    "revenue_yoy": 7.1,
                    "netprofit_yoy": 8.2,
                    "gross_margin": 76.5,
                    "report_period": "2026-06-30",
                }
            ]
        )

    def test_pe_ttm_fallback_with_peers_missing_honest_note(self):
        """主标的回落 TTM + 同业 PE 全缺：MUST NOT 谎称对标股有行情快照口径 PE。"""
        state = {
            "stock_code": "600519",
            "stock_name": "贵州茅台",
            "peer_financials": self._pe_missing_peers(),
            "latest_period_snapshot": {},
        }
        vs = {"PE": None, "PE_ttm": 21.5, "PB": 8.0, "market_cap": 18000.0}
        text = format_peer_comparison(state, vs)
        assert text is not None
        assert "对标股 PE 为行情快照口径" not in text
        assert "缺失" in text and "行情主源" in text

    def test_peers_missing_without_fallback_still_disclosed(self):
        """主标的 PE 在（他路来源）而同业 PE 全缺：仍须披露缺失原因。"""
        state = {
            "stock_code": "600519",
            "stock_name": "贵州茅台",
            "peer_financials": self._pe_missing_peers(),
            "latest_period_snapshot": {},
        }
        vs = {"PE": 22.0, "PE_ttm": None, "PB": 8.0, "market_cap": 18000.0}
        text = format_peer_comparison(state, vs)
        assert text is not None
        assert "行情主源" in text and "缺失" in text

    def test_peers_present_keeps_original_note(self):
        """同业 PE 在（如他源/未来字段扩展）：原跨口径注语义不变（零回归）。"""
        state = {
            "stock_code": "600519",
            "stock_name": "贵州茅台",
            "peer_financials": _peer_df(
                [
                    {
                        "name": "五粮液",
                        "code": "000858",
                        "PE": 15.0,
                        "PB": 3.2,
                        "total_mv": 5000.0,
                        "revenue_yoy": 7.1,
                        "netprofit_yoy": 8.2,
                        "gross_margin": 76.5,
                        "report_period": "2026-06-30",
                    }
                ]
            ),
            "latest_period_snapshot": {},
        }
        vs = {"PE": None, "PE_ttm": 21.5, "PB": 8.0, "market_cap": 18000.0}
        text = format_peer_comparison(state, vs)
        assert text is not None
        assert "跨口径" in text and "对标股 PE 为行情快照口径" in text
