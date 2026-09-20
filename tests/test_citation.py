"""TDD tests for citation.py — 确定性引用校验器。

校验器是纯 Python 实现（不调 LLM），复用 metrics/ 纯函数对 Agent 产出的
Claim 进行重算比对。参考 ADR-0011 和 FinGround 六类分类法。

fixture 数据手算验证（来自 conftest.py）：
- 2024: 资产总计=1000, 负债合计=400 → 资产负债率 = 40%
"""

import pandas as pd
import pytest

from finance_agent.citation import CitationReport, Claim, verify_claims
from finance_agent.metrics.dupont import calc_dupont


class TestVerifyClaims:
    """引用校验器测试。"""

    def test_numerical_claim_pass(self):
        """数值型 claim 值匹配时返回 PASS。"""
        state = {
            "solvency_metrics": {"资产负债率": {"2024": 40.0}},
        }
        claim = Claim(
            claim_type="numerical",
            source_type="data",
            field_ref="solvency_metrics.资产负债率.2024",
            stated_value=40.0,
            interpretation="资产负债率为 40%，杠杆水平适中",
        )
        results = verify_claims([claim], state)
        assert len(results) == 1
        assert results[0].status == "PASS"


class TestCitationReport:
    """CitationReport 批量汇总测试。"""

    def test_report_summarizes_mixed_results(self):
        """多 claim 混合结果：1 PASS + 1 FAIL + 1 UNVERIFIABLE。"""
        state = {
            "solvency_metrics": {"资产负债率": {"2024": 40.0, "2023": 38.0}},
        }
        claims = [
            Claim(
                claim_type="numerical",
                source_type="data",
                field_ref="solvency_metrics.资产负债率.2024",
                stated_value=40.0,
                interpretation="",
            ),
            Claim(
                claim_type="numerical",
                source_type="data",
                field_ref="solvency_metrics.资产负债率.2023",
                stated_value=50.0,
                interpretation="",
            ),
            Claim(
                claim_type="numerical",
                source_type="llm_inference",
                field_ref="solvency_metrics.资产负债率.2024",
                stated_value=40.0,
                interpretation="",
            ),
        ]
        results = verify_claims(claims, state)
        report = CitationReport.from_results(results)
        assert report.total == 3
        assert report.passed == 1
        assert report.failed == 1
        assert report.unverifiable == 1
        assert not report.all_passed

    def test_report_all_passed(self):
        """全部 PASS 时 all_passed=True。"""
        state = {
            "solvency_metrics": {"资产负债率": {"2024": 40.0}},
        }
        claims = [
            Claim(
                claim_type="numerical",
                source_type="data",
                field_ref="solvency_metrics.资产负债率.2024",
                stated_value=40.0,
                interpretation="",
            ),
        ]
        results = verify_claims(claims, state)
        report = CitationReport.from_results(results)
        assert report.passed == 1
        assert report.failed == 0
        assert report.all_passed

    def test_llm_inference_claim_skipped(self):
        """source_type=llm_inference 的 claim 跳过校验，返回 UNVERIFIABLE。"""
        state = {"solvency_metrics": {"资产负债率": {"2024": 40.0}}}
        claim = Claim(
            claim_type="numerical",
            source_type="llm_inference",
            field_ref="solvency_metrics.资产负债率.2024",
            stated_value=40.0,
            interpretation="行业惯例资产负债率约 40%",
        )
        results = verify_claims([claim], state)
        assert len(results) == 1
        assert results[0].status == "UNVERIFIABLE"

    def test_computational_claim_dupont_roe_fail(self, balance_sheet, income_statement):
        """计算型 claim：杜邦 ROE 重算不匹配时返回 FAIL。"""
        dupont_tree = calc_dupont(balance_sheet, income_statement)
        state = {
            "balance_sheet": balance_sheet,
            "income_statement": income_statement,
            "dupont_tree": dupont_tree,
        }
        # 实际 ROE ≈ 0.2833，声称 0.50
        claim = Claim(
            claim_type="computational",
            source_type="data",
            field_ref="dupont_tree.L1.2024.ROE",
            stated_value=0.50,
            interpretation="杜邦分解 ROE 为 50%",
        )
        results = verify_claims([claim], state)
        assert len(results) == 1
        assert results[0].status == "FAIL"
        assert results[0].ground_truth is not None
        assert abs(results[0].ground_truth - 0.2833) < 0.01

    def test_comparative_claim_pass(self):
        """比较型 claim：比较方向正确时返回 PASS。

        stated_value 为比较方向: "greater_than" / "less_than" / "equal_to"
        field_ref_b 指向被比较的第二个值。
        """
        state = {
            "profitability_metrics": {"ROE": {"2024": 28.0, "2023": 25.0}},
        }
        claim = Claim(
            claim_type="comparative",
            source_type="data",
            field_ref="profitability_metrics.ROE.2024",
            stated_value="greater_than",
            interpretation="2024 年 ROE 高于 2023 年",
            field_ref_b="profitability_metrics.ROE.2023",
            stated_value_b=25.0,
        )
        results = verify_claims([claim], state)
        assert len(results) == 1
        assert results[0].status == "PASS"

    def test_event_claim_pass(self):
        """事件型 claim：引用的事件存在于 key_events 时返回 PASS。"""
        state = {
            "key_events": [
                {"title": "茅台提价", "date": "2024-01-15"},
                {"title": "新品发布", "date": "2024-06-01"},
            ],
        }
        claim = Claim(
            claim_type="temporal",
            source_type="event",
            field_ref="茅台提价",
            stated_value="2024-01-15",
            interpretation="茅台于 2024 年 1 月提价",
        )
        results = verify_claims([claim], state)
        assert len(results) == 1
        assert results[0].status == "PASS"

    def test_numerical_claim_with_list_index(self):
        """数值型 claim 的 field_ref 包含 list index 时也能正确解析。

        technical_indicators.MA.5.4 → state["technical_indicators"]["MA"]["5"][4]
        """
        state = {
            "technical_indicators": {
                "MA": {"5": [None, None, None, None, 13.0, 14.0]},
            },
        }
        claim = Claim(
            claim_type="numerical",
            source_type="data",
            field_ref="technical_indicators.MA.5.4",
            stated_value=13.0,
            interpretation="MA5 为 13.0",
        )
        results = verify_claims([claim], state)
        assert len(results) == 1
        assert results[0].status == "PASS"

    def test_numerical_claim_fail_wrong_value(self):
        """数值型 claim 值不匹配时返回 FAIL，附带 ground_truth 和 delta。"""
        state = {
            "solvency_metrics": {"资产负债率": {"2024": 40.0}},
        }
        claim = Claim(
            claim_type="numerical",
            source_type="data",
            field_ref="solvency_metrics.资产负债率.2024",
            stated_value=45.0,
            interpretation="资产负债率为 45%",
        )
        results = verify_claims([claim], state)
        assert len(results) == 1
        assert results[0].status == "FAIL"
        assert results[0].ground_truth == 40.0
        assert results[0].delta == 5.0

    def test_computational_claim_dupont_roe_pass(self, balance_sheet, income_statement):
        """计算型 claim：杜邦 ROE 重算匹配时返回 PASS。

        2024: ROE = (170/1000) × (1000/1000) × (1000/600) ≈ 0.2833
        """
        dupont_tree = calc_dupont(balance_sheet, income_statement)
        state = {
            "balance_sheet": balance_sheet,
            "income_statement": income_statement,
            "dupont_tree": dupont_tree,
        }
        claim = Claim(
            claim_type="computational",
            source_type="data",
            field_ref="dupont_tree.L1.2024.ROE",
            stated_value=0.2833,
            interpretation="杜邦分解 ROE 为 28.33%",
        )
        results = verify_claims([claim], state)
        assert len(results) == 1
        assert results[0].status == "PASS"


class TestNumericalRobustness:
    """数值 claim 解析鲁棒性（baseline-v2 r3 炸行回归）。

    GLM 生成的 field_ref 可能指到 dict/list 等非数值字段，
    float(dict) 抛 TypeError 炸整条管线 → 应按 FAIL（无法核验）处理。
    """

    def test_field_ref_resolves_to_dict_fails_gracefully(self):
        state = {"solvency_metrics": {"资产负债率": {"2024": 40.0}}}
        claim = Claim(
            claim_type="numerical",
            source_type="data",
            field_ref="solvency_metrics.资产负债率",  # 指到 dict 而非叶子数值
            stated_value=40.0,
            interpretation="x",
        )
        results = verify_claims([claim], state)
        assert results[0].status == "FAIL"
        assert results[0].ground_truth is None

    def test_field_ref_resolves_to_list_fails_gracefully(self):
        state = {"kline": [1700.0, 1710.0]}
        claim = Claim(
            claim_type="numerical",
            source_type="data",
            field_ref="kline",
            stated_value=1700.0,
            interpretation="x",
        )
        results = verify_claims([claim], state)
        assert results[0].status == "FAIL"


class TestMacroClaimNewStructure:
    """fetch 守卫新结构下 macro claim 的 field_ref 解析回归（final review Critical #1）。

    旧结构 macro_indicators.cpi 为 list；新守卫结构为
    {"as_of_date", "freshness", "records"}。_resolve_field_ref 须在遇到含
    records 键的 dict 时自动下钻 records，保持后续 .index.column 路径语义，
    否则 cpi.0 取 dict.get("0") = None → 数值校验 FAIL → 全分析师 3 倍重试。
    """

    def _state(self) -> dict:
        return {
            "macro_indicators": {
                "cpi": {
                    "as_of_date": "2026-07-01",
                    "freshness": "fresh",
                    "records": [{"月份": "2026年07月份", "全国-当月-同比增长": 0.4}],
                }
            }
        }

    def test_macro_claim_resolves_in_new_structure(self):
        from finance_agent.citation import _resolve_field_ref

        val = _resolve_field_ref("macro_indicators.cpi.0.全国-当月-同比增长", self._state())
        assert val == 0.4

    def test_macro_numerical_claim_pass(self):
        claim = Claim(
            claim_type="numerical",
            source_type="data",
            field_ref="macro_indicators.cpi.0.全国-当月-同比增长",
            stated_value=0.4,
            interpretation="CPI 环比 0.4%",
        )
        results = verify_claims([claim], self._state())
        assert len(results) == 1
        assert results[0].status == "PASS"


class TestFieldRefRecordsGuard:
    """field_ref 显式引用 records 键仍可解析（守卫结构向后兼容）。"""

    def test_explicit_records_key_still_resolves(self):
        from finance_agent.citation import _resolve_field_ref

        val = _resolve_field_ref(
            "macro_indicators.cpi.records.0.全国-当月-同比增长", self_cpi_state()
        )
        assert val == 0.4

    def test_plain_dict_behavior_unchanged(self):
        from finance_agent.citation import _resolve_field_ref

        # 普通 dict 无 records 键时按原样 get，不破坏既有行为
        state = {"solvency_metrics": {"资产负债率": {"2024": 40.0}}}
        assert _resolve_field_ref("solvency_metrics.资产负债率.2024", state) == 40.0
        assert _resolve_field_ref("solvency_metrics.资产负债率", state) == {"2024": 40.0}


def self_cpi_state() -> dict:
    """TestFieldRefRecordsGuard 用 state fixture（避免重复构建）。"""
    return {
        "macro_indicators": {
            "cpi": {
                "as_of_date": "2026-07-01",
                "freshness": "fresh",
                "records": [{"月份": "2026年07月份", "全国-当月-同比增长": 0.4}],
            }
        }
    }


class TestComputationalRegistryCoverage:
    """注册表全覆盖：metrics/ 全部纯函数指标族可重算（spec 计算型声明重算注册表全覆盖）。"""

    def _state(self, balance_sheet, income_statement, cash_flow, indicators):
        kline = pd.DataFrame(
            {
                "日期": pd.date_range("2025-01-01", periods=80, freq="D").strftime("%Y-%m-%d"),
                "开盘": [10.0] * 80,
                "收盘": [10.0 + i * 0.1 for i in range(80)],
                "最高": [10.5 + i * 0.1 for i in range(80)],
                "最低": [9.5 + i * 0.1 for i in range(80)],
                "成交量": [1000.0] * 80,
            }
        )
        bench = kline.copy()
        return {
            "balance_sheet": balance_sheet,
            "income_statement": income_statement,
            "cash_flow_statement": cash_flow,
            "financial_indicators": indicators,
            "kline": kline,
            "benchmark_kline": bench,
        }

    def _full_state(self, balance_sheet, income_statement, cash_flow, indicators):
        """_state + 触发 compute_metrics 其余分支（行业/行情/同业/季报）。"""
        state = self._state(balance_sheet, income_statement, cash_flow, indicators)
        state.update(
            {
                "industry_info": {"industry": "白酒"},
                "stock_quote": {"PE": 20.0, "PB": 3.0},
                "peer_financials": [{"code": "000001", "PE": 12.0, "PB": 1.1}],
                "quarterly_income": income_statement.copy(),
            }
        )
        return state

    def test_recompute_registry_covers_all_compute_outputs(
        self, balance_sheet, income_statement, cash_flow, indicators
    ):
        """覆盖门禁（派生键）：compute_metrics 全部产出键 ⊆ 注册表 ∪ 显式豁免表。

        回归：r9「未注册 ~2.9 条/轮」的来源是 8 个派生键从未注册（靠人肉补不可持续）；
        新派生键未注册即本测试红。豁免表每条须附理由。
        """
        from finance_agent.citation import _COMPUTATIONAL_RECALC, _UNREGISTERED_EXEMPT
        from finance_agent.nodes.compute import compute_metrics

        assert all(reason.strip() for reason in _UNREGISTERED_EXEMPT.values()), (
            "豁免表每条必须附理由"
        )
        produced = set(
            compute_metrics(
                self._full_state(balance_sheet, income_statement, cash_flow, indicators)
            )
        )
        missing = produced - set(_COMPUTATIONAL_RECALC) - set(_UNREGISTERED_EXEMPT)
        assert not missing, f"未注册且未豁免的派生键：{sorted(missing)}"

    def test_alias_root_recomputes(self, balance_sheet, income_statement, cash_flow, indicators):
        """别名根在计算型路径同样生效（注册表查找走归一后的根，否则 UNVERIFIABLE）。"""
        from finance_agent.citation import Claim, verify_claims
        from finance_agent.nodes.compute import compute_metrics

        state = self._full_state(balance_sheet, income_statement, cash_flow, indicators)
        truth = compute_metrics(state)["derived_series"]["chg_5d"]
        claim = Claim(
            claim_type="computational",
            source_type="data",
            field_ref="derived.chg_5d",
            stated_value=float(truth),
            # 解读须回声申报值（_check_internal_echo：含数值则必须有一个能对上）
            interpretation=f"近 5 日涨跌幅 {float(truth):.4f}",
        )
        (r,) = verify_claims([claim], state)
        assert r.status == "PASS", r

    def test_newly_registered_key_recomputes_pass_and_fail(
        self, balance_sheet, income_statement, cash_flow, indicators
    ):
        """新注册派生键的可重算性：growth_rates 按同一份 compute 代码裁决 PASS/FAIL。

        此前该根键未注册 → 按计算型引用恒 UNVERIFIABLE（既不判对也不判错）。
        """
        from finance_agent.citation import Claim, verify_claims
        from finance_agent.nodes.compute import compute_metrics

        state = self._full_state(balance_sheet, income_statement, cash_flow, indicators)
        growth = compute_metrics(state)["growth_rates"]
        dim, metric, rate = next(
            (d, m, v) for d, mv in growth.items() for m, v in mv.items() if v is not None
        )
        ref = f"growth_rates.{dim}.{metric}"

        ok = Claim(
            claim_type="computational",
            source_type="data",
            field_ref=ref,
            stated_value=float(rate),
            interpretation="同比增速",
        )
        assert verify_claims([ok], state)[0].status == "PASS"

        bad = ok.model_copy(update={"stated_value": float(rate) + 1.0})
        result = verify_claims([bad], state)[0]
        assert result.status == "FAIL"
        assert result.bucket == "value_mismatch"

    def test_registry_covers_all_metric_families(self):
        from finance_agent.citation import _COMPUTATIONAL_RECALC

        expected = {
            "dupont_tree",
            "solvency_metrics",
            "profitability_metrics",
            "efficiency_metrics",
            "cashflow_metrics",
            "technical_indicators",
            "risk_metrics",
        }
        assert expected <= set(_COMPUTATIONAL_RECALC)

    def test_solvency_recalc_pass_and_fail(
        self, balance_sheet, income_statement, cash_flow, indicators
    ):
        from finance_agent.citation import _COMPUTATIONAL_RECALC
        from finance_agent.metrics.solvency import calc_solvency

        state = self._state(balance_sheet, income_statement, cash_flow, indicators)
        truth = _COMPUTATIONAL_RECALC["solvency_metrics"](state)
        assert (
            truth["资产负债率"]["2024"]
            == calc_solvency(balance_sheet, income_statement, indicators)["资产负债率"]["2024"]
        )
        ok = Claim(
            claim_type="computational",
            source_type="data",
            field_ref="solvency_metrics.资产负债率.2024",
            stated_value=float(truth["资产负债率"]["2024"]),
            interpretation="",
        )
        bad = Claim(
            claim_type="computational",
            source_type="data",
            field_ref="solvency_metrics.资产负债率.2024",
            stated_value=float(truth["资产负债率"]["2024"]) * 2,
            interpretation="",
        )
        results = verify_claims([ok, bad], state)
        assert results[0].status == "PASS"
        assert results[1].status == "FAIL"

    def test_profitability_recalc(self, balance_sheet, income_statement, cash_flow, indicators):
        from finance_agent.citation import _COMPUTATIONAL_RECALC

        state = self._state(balance_sheet, income_statement, cash_flow, indicators)
        truth = _COMPUTATIONAL_RECALC["profitability_metrics"](state)
        claim = Claim(
            claim_type="computational",
            source_type="data",
            field_ref="profitability_metrics.净利率.2024",
            stated_value=float(truth["净利率"]["2024"]),
            interpretation="",
        )
        assert verify_claims([claim], state)[0].status == "PASS"

    def test_efficiency_recalc(self, balance_sheet, income_statement, cash_flow, indicators):
        from finance_agent.citation import _COMPUTATIONAL_RECALC

        state = self._state(balance_sheet, income_statement, cash_flow, indicators)
        truth = _COMPUTATIONAL_RECALC["efficiency_metrics"](state)
        claim = Claim(
            claim_type="computational",
            source_type="data",
            field_ref="efficiency_metrics.总资产周转率.2024",
            stated_value=float(truth["总资产周转率"]["2024"]),
            interpretation="",
        )
        assert verify_claims([claim], state)[0].status == "PASS"

    def test_cashflow_recalc(self, balance_sheet, income_statement, cash_flow, indicators):
        from finance_agent.citation import _COMPUTATIONAL_RECALC

        state = self._state(balance_sheet, income_statement, cash_flow, indicators)
        truth = _COMPUTATIONAL_RECALC["cashflow_metrics"](state)
        claim = Claim(
            claim_type="computational",
            source_type="data",
            field_ref="cashflow_metrics.经营现金流/净利润.2024",
            stated_value=float(truth["经营现金流/净利润"]["2024"]),
            interpretation="",
        )
        assert verify_claims([claim], state)[0].status == "PASS"

    def test_technical_recalc_with_list_index(
        self, balance_sheet, income_statement, cash_flow, indicators
    ):
        """technical_indicators 值为等长 list，子路径须支持 list index。"""
        state = self._state(balance_sheet, income_statement, cash_flow, indicators)
        from finance_agent.metrics.technical import calc_technical

        truth = calc_technical(state["kline"])
        ma5_last = truth["MA"]["5"][-1]
        assert ma5_last is not None
        claim = Claim(
            claim_type="computational",
            source_type="data",
            field_ref=f"technical_indicators.MA.5.{len(truth['MA']['5']) - 1}",
            stated_value=float(ma5_last),
            interpretation="",
        )
        result = verify_claims([claim], state)[0]
        assert result.status == "PASS"
        assert result.ground_truth == pytest.approx(ma5_last)

    def test_risk_recalc(self, balance_sheet, income_statement, cash_flow, indicators):
        from finance_agent.citation import _COMPUTATIONAL_RECALC

        state = self._state(balance_sheet, income_statement, cash_flow, indicators)
        truth = _COMPUTATIONAL_RECALC["risk_metrics"](state)
        claim = Claim(
            claim_type="computational",
            source_type="data",
            field_ref="risk_metrics.max_drawdown",
            stated_value=float(truth["max_drawdown"]),
            interpretation="",
        )
        assert verify_claims([claim], state)[0].status == "PASS"

    def test_unregistered_root_counts_coverage_gap(self):
        state = {"balance_sheet": pd.DataFrame(), "income_statement": pd.DataFrame()}
        claim = Claim(
            claim_type="computational",
            source_type="data",
            field_ref="unknown_metrics.某指标.2024",
            stated_value=1.0,
            interpretation="",
        )
        results = verify_claims([claim], state)
        report = CitationReport.from_results(results)
        assert results[0].status == "UNVERIFIABLE"
        assert report.coverage_gaps == 1

    def test_registered_root_no_coverage_gap(
        self, balance_sheet, income_statement, cash_flow, indicators
    ):
        from finance_agent.citation import _COMPUTATIONAL_RECALC

        state = self._state(balance_sheet, income_statement, cash_flow, indicators)
        truth = _COMPUTATIONAL_RECALC["dupont_tree"](state)
        # harden-citation-semantic-coverage D5：未申报 metric_name/period 也计覆盖缺口，
        # ehr-style-claim-direction：数值/计算型缺 direction 同样计缺口——
        # 故本用例全申报（direction=flat，ROE 符号随标的而异不做符号断言），
        # 仅钉「已注册根键 → 不计缺口」语义。
        claim = Claim(
            claim_type="computational",
            source_type="data",
            field_ref="dupont_tree.L1.2024.ROE",
            stated_value=float(truth["L1"]["2024"]["ROE"]),
            interpretation="",
            metric_name="ROE",
            period="2024",
            direction="flat",
        )
        results = verify_claims([claim], state)
        report = CitationReport.from_results(results)
        assert results[0].status == "PASS"
        assert report.coverage_gaps == 0

    def test_registry_includes_derived_series(self):
        from finance_agent.citation import _COMPUTATIONAL_RECALC

        assert "derived_series" in _COMPUTATIONAL_RECALC

    def test_derived_series_recalc_and_claim(self):
        from finance_agent.citation import _COMPUTATIONAL_RECALC

        kline = pd.DataFrame(
            {
                "日期": pd.date_range("2025-01-01", periods=80).strftime("%Y-%m-%d"),
                "开盘": [10.0] * 80,
                "收盘": [10.0 + i * 0.1 for i in range(80)],
                "最高": [10.5 + i * 0.1 for i in range(80)],
                "最低": [9.5 + i * 0.1 for i in range(80)],
                "成交量": [1000.0] * 80,
            }
        )
        state = {"kline": kline}
        truth = _COMPUTATIONAL_RECALC["derived_series"](state)
        spread = float(truth["ma_spread_5_20_pct"])
        assert spread == pytest.approx(4.42, abs=0.01)

        ok = Claim(
            claim_type="computational",
            source_type="data",
            field_ref="derived_series.ma_spread_5_20_pct",
            stated_value=spread,
            interpretation="",
        )
        assert verify_claims([ok], state)[0].status == "PASS"

        bad = Claim(
            claim_type="computational",
            source_type="data",
            field_ref="derived_series.ma_spread_5_20_pct",
            stated_value=spread * 1.1,
            interpretation="",
        )
        (r,) = verify_claims([bad], state)
        assert r.status == "FAIL"
        assert r.bucket == "value_mismatch"

    def test_derived_series_direction_check(self):
        kline = pd.DataFrame(
            {
                "日期": pd.date_range("2025-01-01", periods=80).strftime("%Y-%m-%d"),
                "开盘": [10.0] * 80,
                "收盘": [10.0 + i * 0.1 for i in range(80)],
                "最高": [10.5 + i * 0.1 for i in range(80)],
                "最低": [9.5 + i * 0.1 for i in range(80)],
                "成交量": [1000.0] * 80,
            }
        )
        state = {"kline": kline}
        base = {
            "claim_type": "computational",
            "source_type": "data",
            "field_ref": "derived_series.ma_spread_5_20_pct",
            "stated_value": 4.42,
            "interpretation": "",
        }
        (ok,) = verify_claims([Claim(**base, direction="positive")], state)
        assert ok.status == "PASS"
        (bad,) = verify_claims([Claim(**base, direction="negative")], state)
        assert bad.status == "FAIL"
        assert bad.bucket == "direction_mismatch"

    def test_derived_series_direction_alignment_pass(self):
        """spec「派生值方向申报参与符号比对」：真值为负、direction 修饰后同号 → PASS。"""
        from finance_agent.citation import _COMPUTATIONAL_RECALC

        kline = pd.DataFrame(
            {
                "日期": pd.date_range("2025-01-01", periods=80).strftime("%Y-%m-%d"),
                "开盘": [10.0] * 80,
                "收盘": [10.0 - i * 0.1 for i in range(80)],
                "最高": [10.5 - i * 0.1 for i in range(80)],
                "最低": [9.5 - i * 0.1 for i in range(80)],
                "成交量": [1000.0] * 80,
            }
        )
        state = {"kline": kline}
        spread = float(_COMPUTATIONAL_RECALC["derived_series"](state)["ma_spread_5_20_pct"])
        assert spread < 0
        base = {
            "claim_type": "computational",
            "source_type": "data",
            "field_ref": "derived_series.ma_spread_5_20_pct",
            "stated_value": abs(spread),
            "interpretation": "",
        }
        (ok,) = verify_claims([Claim(**base, direction="negative")], state)
        assert ok.status == "PASS"
        assert ok.ground_truth == spread


class TestComputationalDirectionCoverageGap:
    """computational 方向误申报的覆盖缺口（与数值路径 `:607` 对齐）。

    修复前 `_verify_computational` 丢弃 `_align_signed_effective` 返回的
    direction_misapplied——非 signed 根键上的 negative/positive 申报不记缺口，
    而数值路径记 `coverage_gap=claim.direction is None or direction_misapplied`
    （spec「符号校验限定有符号量」：非 signed 申报 SHALL 记覆盖缺口）。
    """

    @staticmethod
    def _kline() -> pd.DataFrame:
        return pd.DataFrame(
            {
                "日期": pd.date_range("2025-01-01", periods=80).strftime("%Y-%m-%d"),
                "开盘": [10.0] * 80,
                "收盘": [10.0 + i * 0.1 for i in range(80)],
                "最高": [10.5 + i * 0.1 for i in range(80)],
                "最低": [9.5 + i * 0.1 for i in range(80)],
                "成交量": [1000.0] * 80,
            }
        )

    def test_misapplied_direction_on_non_signed_root_counts_gap(self):
        # 恒正水平量（MA5）上的 direction="negative" 是「低于阈值」的字面误读：
        # 值级照常 PASS，但必须与数值路径一致记覆盖缺口（不判 direction_mismatch）。
        from finance_agent.metrics.technical import calc_technical

        kline = self._kline()
        state = {"kline": kline}
        ma5 = calc_technical(kline)["MA"]["5"][-1]
        assert ma5 is not None and ma5 > 0
        claim = Claim(
            claim_type="computational",
            source_type="data",
            field_ref="technical_indicators.MA.5.-1",
            stated_value=float(ma5),
            interpretation="",
            metric_name="MA",
            period=str(kline["日期"].iloc[-1]),
            direction="negative",
        )
        (r,) = verify_claims([claim], state)
        assert r.status == "PASS"
        assert r.coverage_gap is True

    def test_correct_direction_on_signed_root_keeps_no_gap(self):
        # 有符号量（derived_series）申报方向且符号一致 → 不计缺口。
        # 直调 _verify_computational：术语/期次缺口由外层 _verify_data_claim 承担，
        # 本用例只钉方向申报本身不产生缺口。
        from finance_agent.citation import _verify_computational
        from finance_agent.metrics.technical import calc_derived_series

        kline = self._kline()
        state = {"kline": kline}
        spread = float(calc_derived_series(kline)["ma_spread_5_20_pct"])
        assert spread > 0
        claim = Claim(
            claim_type="computational",
            source_type="data",
            field_ref="derived_series.ma_spread_5_20_pct",
            stated_value=spread,
            interpretation="",
            direction="positive",
        )
        r = _verify_computational(claim, state)
        assert r.status == "PASS"
        assert r.coverage_gap is False


class TestComparativeBaseDeclaration:
    """refine-citation-coverage-v3 D3：comparative 基期值双端申报与校验。"""

    _STATE = {"profitability_metrics": {"净利率": {"2025": 19.07, "2024": 21.93}}}

    def _claim(self, **kw):
        params = {
            "claim_type": "comparative",
            "source_type": "data",
            "field_ref": "profitability_metrics.净利率.2025",
            "stated_value": "less_than",
            "interpretation": "2025 净利率较 2024 下滑",
            "field_ref_b": "profitability_metrics.净利率.2024",
        }
        params.update(kw)
        return Claim(**params)  # type: ignore[arg-type]

    def test_base_value_correct_passes(self):
        (r,) = verify_claims([self._claim(stated_value_b=21.93)], self._STATE)
        assert r.status == "PASS"

    def test_base_value_mismatch_fails_value_mismatch(self):
        # 正文「较2024年21.93%下滑」但申报基期 28.0（错值）→ FAIL
        (r,) = verify_claims([self._claim(stated_value_b=28.0)], self._STATE)
        assert r.status == "FAIL"
        assert r.bucket == "value_mismatch"

    def test_base_not_declared_fails(self):
        # 基期裸奔：field_ref_b 设而 stated_value_b 缺 → FAIL（拦截）
        (r,) = verify_claims([self._claim()], self._STATE)
        assert r.status == "FAIL"
        assert r.bucket == "path_unresolvable"


class TestComparativeDifferenceRecompute:
    """① 比较型差值重算（close-citation-coverage-gaps）：「A 较 B 低约 X」数值差值申报。

    此前 stated_value 为数值 → 一律 UNVERIFIABLE（既不判对也不判错、不计缺口），
    归因表长期挂着该形态（metrics.md §3 follow-up ①）。
    """

    _STATE = {"profitability_metrics": {"净利率": {"2025": 19.07, "2024": 21.93}}}

    def _claim(self, **kw):
        params = {
            "claim_type": "comparative",
            "source_type": "data",
            "field_ref": "profitability_metrics.净利率.2025",
            "field_ref_b": "profitability_metrics.净利率.2024",
            "stated_value": 2.86,
            "interpretation": "2025 净利率较 2024 低约 2.86 个百分点",
            "direction": "negative",
        }
        params.update(kw)
        return Claim(**params)  # type: ignore[arg-type]

    def test_difference_within_tolerance_passes(self):
        (r,) = verify_claims([self._claim()], self._STATE)
        assert r.status == "PASS", r
        assert abs(r.ground_truth - (-2.86)) < 0.01

    def test_difference_over_tolerance_fails_value_mismatch(self):
        (r,) = verify_claims([self._claim(stated_value=5.0)], self._STATE)
        assert r.status == "FAIL"
        assert r.bucket == "value_mismatch"

    def test_direction_sign_mismatch_fails(self):
        # 实际 2025 低于 2024（差值为负），申报 direction=positive → 方向不符 FAIL
        (r,) = verify_claims([self._claim(direction="positive")], self._STATE)
        assert r.status == "FAIL"

    def test_direction_undeclared_verifies_magnitude_with_gap(self):
        # 未申报方向 → 仅量级比对（显式降级：PASS 但计覆盖缺口，不静默）
        (r,) = verify_claims([self._claim(direction=None)], self._STATE)
        assert r.status == "PASS"
        assert r.coverage_gap is True

    def test_direction_gap_flag_isolated_from_global_gap(self):
        """隔离验证：metric_name/period 申报齐全（全局缺口口径不触发）时，
        差值型的方向缺口标记由本分支负责——已申报→无缺口，未申报→计缺口。"""
        declared = {"metric_name": "净利率", "period": "2025"}
        (r_ok,) = verify_claims([self._claim(**declared)], self._STATE)
        assert r_ok.status == "PASS"
        assert r_ok.coverage_gap is False

        (r_gap,) = verify_claims([self._claim(direction=None, **declared)], self._STATE)
        assert r_gap.status == "PASS"
        assert r_gap.coverage_gap is True

    def test_difference_claim_does_not_require_base_value(self):
        # 差值型申报对象是差值本身：stated_value_b 缺省不判「基期裸奔」（方向型仍保持 FAIL）
        (r,) = verify_claims([self._claim()], self._STATE)
        assert r.status == "PASS"

    def test_base_value_still_checked_when_declared(self):
        # 差值型若申报了基期值，仍按既有容差校验（错值 → FAIL）
        (r,) = verify_claims([self._claim(stated_value_b=28.0)], self._STATE)
        assert r.status == "FAIL"
        assert r.bucket == "value_mismatch"


class TestDerivedRootAlias:
    """前缀契约一致性：context 提示 LLM 用 `derived.`，state 根键是 `derived_series`。

    回归（close-citation-coverage-gaps 随同发现）：图通道修好后「常用派生值」节首次
    真正渲染（此前 derived_series 被图静默丢弃），LLM 照提示写 `derived.chg_5d` 时
    正确数值会被判 path_unresolvable 误 FAIL。别名归一后两种前缀都合法。
    """

    _STATE = {"derived_series": {"chg_5d": -0.0412}}

    def _claim(self, ref: str):
        return Claim(
            claim_type="numerical",
            source_type="data",
            field_ref=ref,
            stated_value=-0.0412,
            interpretation="近 5 日涨跌幅 -4.12%",
            metric_name="chg_5d",
            period="2026-09-14",
            direction="negative",
        )

    def test_alias_root_resolves_and_passes(self):
        (r,) = verify_claims([self._claim("derived.chg_5d")], self._STATE)
        assert r.status == "PASS", r

    def test_canonical_root_unchanged(self):
        (r,) = verify_claims([self._claim("derived_series.chg_5d")], self._STATE)
        assert r.status == "PASS", r


class TestClaimDirectionField:
    """ehr-style-claim-direction：Claim.direction 字段模型测试。"""

    def test_direction_accepts_enum_values(self):
        for d in ("positive", "negative", "flat"):
            claim = Claim(
                claim_type="numerical",
                source_type="data",
                field_ref="solvency_metrics.资产负债率.2024",
                stated_value=40.0,
                interpretation="资产负债率 40%",
                direction=d,
            )
            assert claim.direction == d

    def test_direction_defaults_to_none(self):
        claim = Claim(
            claim_type="numerical",
            source_type="data",
            field_ref="solvency_metrics.资产负债率.2024",
            stated_value=40.0,
            interpretation="资产负债率 40%",
        )
        assert claim.direction is None

    def test_direction_rejects_invalid_value(self):
        from pydantic import ValidationError

        # model_validate 走 dict 路径，绕过 mypy 字面量静态检查（运行时拒绝是本测试靶点）
        with pytest.raises(ValidationError):
            Claim.model_validate(
                {
                    "claim_type": "numerical",
                    "source_type": "data",
                    "field_ref": "solvency_metrics.资产负债率.2024",
                    "stated_value": 40.0,
                    "interpretation": "资产负债率 40%",
                    "direction": "down",
                }
            )


class TestDirectionVerification:
    """direction 申报与方向一致性校验（ehr-style-claim-direction）。

    语义：negative = 正文以正向数值表述负向事实（「下滑 10.05%」↔ gt=-10.05），
    判定用 sign(stated)×dir_sign 对齐 gt 后再走既有容差。
    """

    _STATE = {"growth_rates": {"profitability": {"net_profit_growth": {"yoy": {"2024": -10.05}}}}}

    def _claim(self, direction, stated=10.05):
        return Claim(
            claim_type="numerical",
            source_type="data",
            field_ref="growth_rates.profitability.net_profit_growth.yoy.2024",
            stated_value=stated,
            interpretation="净利润同比变化表现",
            metric_name="净利润同比增速",
            period="2024",
            direction=direction,
        )

    def test_negative_modifier_matches_negative_truth(self):
        # 「下滑 10.05%」→ stated=10.05 + direction=negative ↔ gt=-10.05 → PASS
        # gap 注记：#123 注册表扩容后 growth_rates 根走重算，最小 fixture 无原始
        # 报表 → 降级直读计覆盖缺口（同 test_term_match_passes 契约）；本测试主题
        # 是方向对齐语义，非降级语义
        (r,) = verify_claims([self._claim("negative")], self._STATE)
        assert r.status == "PASS"
        assert r.coverage_gap is True

    def test_positive_modifier_against_negative_truth_fails(self):
        # 申报 positive 但真值为负 → FAIL，新桶 direction_mismatch
        (r,) = verify_claims([self._claim("positive")], self._STATE)
        assert r.status == "FAIL"
        assert r.bucket == "direction_mismatch"

    def test_undeclared_direction_value_fail_counts_gap(self):
        # direction=None（旧格式）：跳过方向检查，值级照常判，且计覆盖缺口
        (r,) = verify_claims([self._claim(None)], self._STATE)
        assert r.status == "FAIL"
        assert r.bucket == "value_mismatch"
        assert r.coverage_gap is True

    def test_undeclared_direction_value_pass_still_counts_gap(self):
        # 旧格式 signed 表述值级可过，但缺 direction 申报 → 显式降级计缺口
        (r,) = verify_claims([self._claim(None, stated=-10.05)], self._STATE)
        assert r.status == "PASS"
        assert r.coverage_gap is True

    def test_flat_declared_skips_sign_check_and_gap(self):
        # flat = 申报了但断言无方向语义：跳过符号检查。与 None（未申报）的对比：
        # 两者值级均可 PASS，但缺口语义不同——None 计缺口（方向语义缺失），
        # flat 是显式申报（方向语义完整）。#123 注册表扩容后本 fixture 的
        # growth_rates 根降级直读叠加「重算不可得」缺口（恒 True），方向语义的
        # 区分由 None/flat 的 PASS 语义与上条 negative 用例承载
        (r,) = verify_claims([self._claim("flat", stated=-10.05)], self._STATE)
        assert r.status == "PASS"

    def test_declared_direction_skips_text_direction_words(self):
        # 双路径二义消除：已申报 direction 的 claim 不再走正文方向词核对
        # （旧路径会因「下滑」+ stated>0 判 internal_inconsistency）
        claim = self._claim("negative")
        claim.interpretation = "净利润下滑10.05%，盈利承压"
        (r,) = verify_claims([claim], self._STATE)
        assert r.status == "PASS"
        assert r.bucket != "internal_inconsistency"


class TestPercentUnitNormalization:
    """fix(percent-unit)：growth_rates/费率小数真值 vs 百分比申报的 100 倍归一。

    真实缺陷（2026-09-08 拓荆科技 trace 04b872ae）：state 真值存小数比率
    （营收增速 0.5887 = 58.87%、研发费用率 0.118 = 11.8%），LLM 以百分比
    申报（58.87 / 11.8），校验器直接 |0.5887 - 58.87| = 58.28 判 value_mismatch
    ——正确数据被误报 FAIL，抬高幻觉率、触发无谓重试。
    """

    def _claim(
        self,
        stated_value,
        field_ref,
        metric_name=None,
        interpretation="",
        direction="flat",
        period="2025",
    ):
        return Claim(
            claim_type="numerical",
            source_type="data",
            field_ref=field_ref,
            stated_value=stated_value,
            interpretation=interpretation or f"{stated_value}",
            metric_name=metric_name,
            period=period,
            direction=direction,
        )

    def test_growth_rate_percent_vs_decimal_ratio_passes(self):
        """营收增速：state 存 0.5887（小数），LLM 报 58.87（百分比）→ PASS。"""
        state = {"growth_rates": {"profitability": {"营业收入": 0.5887}}}
        claim = self._claim(
            58.87, "growth_rates.profitability.营业收入", "营业收入", direction="positive"
        )
        results = verify_claims([claim], state)
        assert results[0].status == "PASS", results[0]

    def test_rd_expense_rate_percent_vs_decimal_passes(self):
        """研发费用率：dupont 存 0.118（小数），LLM 报 11.80（百分比）→ PASS。"""
        state = {"dupont_tree": {"L3": {"2025": {"研发费用率": 0.118}}}}
        claim = self._claim(11.8, "dupont_tree.L3.2025.研发费用率", period="2025")
        results = verify_claims([claim], state)
        assert results[0].status == "PASS", results[0]

    def test_true_mismatch_still_fails(self):
        """真错值不受归一影响：增速实际 58.87%，LLM 报 30% → 仍 FAIL。"""
        state = {"growth_rates": {"profitability": {"营业收入": 0.5887}}}
        claim = self._claim(
            30.0, "growth_rates.profitability.营业收入", "营业收入", direction="positive"
        )
        results = verify_claims([claim], state)
        assert results[0].status == "FAIL", results[0]
        assert results[0].bucket == "value_mismatch"


class TestComparativeEchoSkipped:
    """fix(comparative-echo)：comparative claim 跳过单值回声检查。

    真实缺陷：PMI claim「8月 49.8 较 7月 49.2 回升 0.6」（stated=基准值，
    interpretation 是差值 0.6）被 _check_internal_echo 拿 49.8 匹配 0.6
    误判 internal_inconsistency——数据完全正确。
    """

    def test_comparative_echo_not_checked(self):
        """comparative claim 走方向比较校验（stated 为方向枚举），回声不误杀。

        修复前被 _check_internal_echo 拿 49.8 匹配差值 0.6 误判 FAIL
        internal_inconsistency；修复后跳过回声、走值级比较校验 → PASS。
        """
        state = {
            "macro_indicators": {
                "pmi": {
                    "records": [
                        {"月份": "2026-08-01", "制造业-指数": 49.8},
                        {"月份": "2026-07-01", "制造业-指数": 49.2},
                    ]
                }
            }
        }
        claim = Claim(
            claim_type="comparative",
            source_type="data",
            field_ref="macro_indicators.pmi.0.制造业-指数",
            # stated_value 为比较方向（_verify_comparative 契约）；
            # 值两侧经 field_ref/field_ref_b 取真值 49.8/49.2 比较
            stated_value="greater_than",
            interpretation="8月制造业PMI较7月回升0.6个点",
            field_ref_b="macro_indicators.pmi.1.制造业-指数",
            stated_value_b=49.2,
            metric_name="PMI",
            period="2026-08",
            direction="positive",
        )
        results = verify_claims([claim], state)
        assert results[0].status == "PASS", results[0]

    def test_comparative_numeric_direction_field_not_false_fail(self):
        """兼容：LLM 偶发以数值填 stated_value（如 49.8）→ 不误判 FAIL。

        修复前回声检查把它误判为 internal_inconsistency FAIL（假阳性抬高幻觉率）；
        修复后跳过回声，值级校验对非方向枚举返回 UNVERIFIABLE（未知语义不武断判错）。
        """
        state = {
            "macro_indicators": {
                "pmi": {
                    "records": [
                        {"月份": "2026-08-01", "制造业-指数": 49.8},
                        {"月份": "2026-07-01", "制造业-指数": 49.2},
                    ]
                }
            }
        }
        claim = Claim(
            claim_type="comparative",
            source_type="data",
            field_ref="macro_indicators.pmi.0.制造业-指数",
            stated_value=49.8,  # 真实 trace 形态：LLM 以数值申报
            interpretation="8月制造业PMI较7月回升0.6个点",
            field_ref_b="macro_indicators.pmi.1.制造业-指数",
            stated_value_b=49.2,
            metric_name="PMI",
            period="2026-08",
            direction="positive",
        )
        results = verify_claims([claim], state)
        assert results[0].status in ("PASS", "UNVERIFIABLE"), results[0]
        assert results[0].status != "FAIL", results[0]


class TestPercentUnitNormalizationComputational:
    """fix(percent-unit)：计算型重算指标（dupont 费率/ROE）同样归一。"""

    def test_rd_expense_rate_computational_percent_passes(self, balance_sheet, income_statement):
        """研发费用率：state 重算 0.03（小数），LLM 报 3.0（百分比）→ PASS。"""
        dupont_tree = calc_dupont(balance_sheet, income_statement)
        state = {
            "balance_sheet": balance_sheet,
            "income_statement": income_statement,
            "dupont_tree": dupont_tree,
        }
        claim = Claim(
            claim_type="computational",
            source_type="data",
            field_ref="dupont_tree.L3.2024.研发费用率",
            stated_value=3.0,
            interpretation="2024年研发费用率约3%",
            metric_name="研发费用率",
            period="2024",
            direction="flat",
        )
        results = verify_claims([claim], state)
        assert results[0].status == "PASS", results[0]

    def test_roe_percent_stated_passes(self, balance_sheet, income_statement):
        """杜邦 ROE：state 0.2833（小数），LLM 报 28.33（百分比）→ PASS。"""
        dupont_tree = calc_dupont(balance_sheet, income_statement)
        state = {
            "balance_sheet": balance_sheet,
            "income_statement": income_statement,
            "dupont_tree": dupont_tree,
        }
        claim = Claim(
            claim_type="computational",
            source_type="data",
            field_ref="dupont_tree.L1.2024.ROE",
            stated_value=28.33,
            interpretation="杜邦分解 ROE 为 28.33%",
            metric_name="ROE",
            period="2024",
            direction="flat",
        )
        results = verify_claims([claim], state)
        assert results[0].status == "PASS", results[0]

    def test_computational_true_mismatch_still_fails(self, balance_sheet, income_statement):
        """真错值不受归一影响：ROE 实际 28.33%，LLM 报 15% → 仍 FAIL。"""
        dupont_tree = calc_dupont(balance_sheet, income_statement)
        state = {
            "balance_sheet": balance_sheet,
            "income_statement": income_statement,
            "dupont_tree": dupont_tree,
        }
        claim = Claim(
            claim_type="computational",
            source_type="data",
            field_ref="dupont_tree.L1.2024.ROE",
            stated_value=15.0,
            interpretation="杜邦分解 ROE 为 15%",
            metric_name="ROE",
            period="2024",
            direction="flat",
        )
        results = verify_claims([claim], state)
        assert results[0].status == "FAIL", results[0]
        assert results[0].bucket == "value_mismatch"


class TestSynonymColumnDisambiguation:
    """同义列名消歧（eval-driven-contract-fixes 任务 1；601318 A4 自然腿终裁案例：
    「归属于母公司的净利润」与「归母净利润」两列并存且数值不同，短名精确命中曾静默取错值）。"""

    @staticmethod
    def _state(extra: dict[str, float]) -> dict:
        import pandas as pd

        return {"income_statement": pd.DataFrame({"报告日": ["20251231"], **extra})}

    def test_short_name_redirects_to_official_full_column(self):
        from finance_agent.citation import _resolve_field_ref

        state = self._state({"归属于母公司的净利润": 100.0, "归母净利润": 200.0})
        assert _resolve_field_ref("income_statement.20251231.归母净利润", state) == 100.0

    def test_official_full_name_direct_hit_kept(self):
        from finance_agent.citation import _resolve_field_ref

        state = self._state({"归属于母公司的净利润": 100.0, "归母净利润": 200.0})
        assert _resolve_field_ref("income_statement.20251231.归属于母公司的净利润", state) == 100.0

    def test_single_column_still_resolves(self):
        from finance_agent.citation import _resolve_field_ref

        state = self._state({"归母净利润": 200.0})
        assert _resolve_field_ref("income_statement.20251231.归母净利润", state) == 200.0

    def test_rowkey_omitted_path_also_disambiguates(self):
        from finance_agent.citation import _resolve_field_ref

        state = self._state({"归属于母公司的净利润": 100.0, "归母净利润": 200.0})
        assert _resolve_field_ref("income_statement.归母净利润", state) == 100.0


class TestClaimContractError:
    """值槽类型校验（eval-driven-contract-fixes 任务 2；600276 A4 自然腿终裁：
    PMI 环比变化 0.6 填进水平值槽 49.8，正文算术正确，claim 契约错——不算分析师幻觉、不触发修复）。"""

    @staticmethod
    def _state() -> dict:
        return {
            "macro_indicators": {
                "pmi": {
                    "records": [
                        {"报告日": "2026-08-31", "制造业-指数": 49.8},
                        {"报告日": "2026-07-31", "制造业-指数": 49.2},
                    ]
                }
            }
        }

    def test_delta_in_level_slot_is_contract_error_not_value_mismatch(self):
        claim = Claim(
            claim_type="numerical",
            source_type="data",
            field_ref="macro_indicators.pmi.0.制造业-指数",
            stated_value=0.6,
            interpretation="制造业 PMI 由 7月 49.2 回升至 8月 49.8，环比改善 0.6 个百分点",
        )
        (r,) = verify_claims([claim], self._state())
        assert r.status == "FAIL"
        assert r.bucket == "claim_contract_error"  # 不进 value_mismatch（幻觉口径）

    def test_level_value_in_level_slot_unaffected(self):
        claim = Claim(
            claim_type="numerical",
            source_type="data",
            field_ref="macro_indicators.pmi.0.制造业-指数",
            stated_value=49.8,
            interpretation="制造业 PMI 8月为 49.8",
        )
        (r,) = verify_claims([claim], self._state())
        assert r.status == "PASS"
        assert r.bucket is None

    def test_delta_wording_with_consistent_magnitude_not_contract_error(self):
        """双信号判据：环比措辞但量级一致（真值本就是变化量字段）→ 走正常比对。"""
        state = {"growth": {"records": [{"指标": 0.55}]}}
        claim = Claim(
            claim_type="numerical",
            source_type="data",
            field_ref="growth.records.0.指标",
            stated_value=0.6,
            interpretation="环比改善 0.6 个百分点",
        )
        (r,) = verify_claims([claim], state)
        assert r.bucket in (None, "value_mismatch")  # 正常路径，非契约错误
        assert r.bucket != "claim_contract_error"
