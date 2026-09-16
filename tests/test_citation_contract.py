"""fix-citation-contract-diseases delta：引用校验契约疾病修复的 TDD 测试。

汉森制药 002412 复盘（39/68 FAIL）三类根因的回归钉子：
- A 索引错位：context 裁剪窗口（60 期）与 state 完整序列的 index 语义断裂
  → 负索引约定（-1=最新，与长度解耦），context 明示约定
- B 词表分裂：context 中文段落标题 vs 校验器英文 state 键
  → context 内联标注英文键（单一真相源），resolver 支持 DataFrame 行键.列名
    与 [N] 括号索引
- C 容差失真：绝对 0.01 对亿元级数值假阴性 → |delta|<0.01 或相对 <0.5%
"""

from __future__ import annotations

import pandas as pd

from finance_agent.citation import Claim, verify_claims


def _num(ref: str, stated) -> Claim:
    return Claim(
        claim_type="numerical",
        source_type="data",
        field_ref=ref,
        stated_value=stated,
        interpretation="测试",
    )


class TestRecomputeRouting:
    """harden-recompute-routing：校验深度不得由 claim 的自我声明决定。

    冻结批实证（P1 round-3）：LLM 把指标字典引用标成 `numerical` → 校验器直读（被污染的）
    state → 真值 == 申报值 → 恒 PASS，重算路径永不触发。修复：field_ref 命中重算注册表根
    的 claim 一律走重算（真值来自原始数据而非可被污染的派生 dict）。
    """

    def test_numerical_label_on_recomputable_root_is_still_recomputed(self):
        import pandas as pd

        state = {
            "kline": pd.DataFrame({"日期": ["2026-08-01", "2026-08-04"], "收盘": [99.0, 100.0]}),
            "derived_series": {"chg_5d": 999.0},  # 与下面 claim 一致的「污染后」派生值
        }
        verdict = verify_claims([_num("derived_series.chg_5d", 999.0)], state)[0]
        assert verdict.status == "FAIL"  # 重算（来自 kline）≠ 999.0 → 抓出污染

    def test_unregistered_root_keeps_direct_read(self):
        state = {"income_statement": {"20251231": {"营业总收入": 1.0e9}}}
        verdict = verify_claims([_num("income_statement.20251231.营业总收入", 1.0e9)], state)[0]
        assert verdict.status == "PASS"  # 非注册表根 → 直读比对（原行为不变）


class TestNegativeIndex:
    """修 A：负索引 = 最新一期，与序列长度及裁剪窗口解耦。"""

    def test_minus_one_resolves_latest(self):
        state = {"technical_indicators": {"MA": {"5": [1.0, 2.0, 3.0, 11.216]}}}
        results = verify_claims([_num("technical_indicators.MA.5.-1", 11.216)], state)
        assert results[0].status == "PASS", results[0]

    def test_minus_n_length_independent(self):
        """任意长度序列下 -N 都取倒数第 N 个（窗口怎么裁都对）。"""
        for length in (60, 120, 250):
            series = [float(i) for i in range(length)]
            state = {"technical_indicators": {"RSI": {"14": series}}}
            r = verify_claims([_num("technical_indicators.RSI.14.-3", float(length - 3))], state)
            assert r[0].status == "PASS", f"length={length}: {r[0]}"

    def test_technical_context_note_documents_negative_index(self):
        """technical context 窗口说明 SHALL 明示负索引约定。"""
        from finance_agent.nodes.analysts import _build_technical_context

        state = {
            "stock_name": "汉森制药",
            "stock_code": "002412",
            "technical_indicators": {"MA": {"5": [1.0] * 250}},
        }
        ctx = _build_technical_context(state)
        assert "-1" in ctx and "最新" in ctx, (
            "窗口说明须写明负索引约定（-1=最新一期），LLM 才会按长度无关语义引用"
        )


class TestSingleVocabulary:
    """修 B：context 标注英文键（单一词表）+ resolver 结构解析能力。"""

    def test_fundamental_context_annotations(self):
        from finance_agent.nodes.analysts import _build_fundamental_context

        state = {
            "stock_name": "X",
            "stock_code": "002412",
            "income_statement": pd.DataFrame({"报告日": ["20251231"], "营业总收入": [1.0]}),
            "balance_sheet": pd.DataFrame({"报告日": ["20251231"]}),
            "cash_flow_statement": pd.DataFrame({"报告日": ["20251231"]}),
            "financial_indicators": pd.DataFrame({"日期": ["2025-12-31"]}),
            "profitability_metrics": {"毛利率": {"2025": 77.36}},
            "solvency_metrics": {"资产负债率": {"2025": 11.59}},
            "efficiency_metrics": {"存货周转率": {"2025": 1.09}},
            "cashflow_metrics": {"FCF": {"2025": 1.0}},
            "dupont_tree": {"L1": {"2025": {"ROE": 8.32}}},
            "growth_rates": {"revenue": [1.0]},
            "traffic_lights": {"a": "red"},
            "anomalies": ["x"],
            "health_score": {"total": 53.3},
            "peer_comparison": {"available": True},
            "relative_valuation": {"pe": 1.0},
            "garp_result": {"pass": False},
            "quarterly_trend": {"yoy": [19.04, 241.17]},
        }
        ctx = _build_fundamental_context(state)
        for key in (
            "income_statement",
            "balance_sheet",
            "cash_flow_statement",
            "financial_indicators",
            "profitability_metrics",
            "solvency_metrics",
            "efficiency_metrics",
            "cashflow_metrics",
            "dupont_tree",
            "growth_rates",
            "traffic_lights",
            "anomalies",
            "health_score",
            "peer_comparison",
            "relative_valuation",
            "garp_result",
            "quarterly_trend",
        ):
            assert key in ctx, f"context 段落标题须内联标注英文键 {key}"

    def test_dataframe_rowkey_colname_resolution(self):
        """income_statement.20251231.营业总收入 → DataFrame 行键 + 列名。"""
        state = {
            "income_statement": pd.DataFrame(
                {
                    "报告日": ["20251231", "20241231"],
                    "营业总收入": [1038756658.94, 900000000.0],
                }
            )
        }
        r = verify_claims([_num("income_statement.20251231.营业总收入", 1038756658.94)], state)
        assert r[0].status == "PASS", r[0]
        assert r[0].ground_truth == 1038756658.94

    def test_bracket_index_resolution(self):
        """quarterly_trend.yoy[1] → 列表下标展开。"""
        state = {"quarterly_trend": {"yoy": [19.04, 241.17]}}
        r = verify_claims([_num("quarterly_trend.yoy[1]", 241.17)], state)
        assert r[0].status == "PASS", r[0]

    def test_chinese_root_not_silently_mapped(self):
        """中文根键不做静默映射（单一真相源；历史归一由离线重判脚本负责）。"""
        state = {"income_statement": pd.DataFrame({"报告日": ["20251231"], "营业总收入": [1.0]})}
        r = verify_claims([_num("利润表.20251231.营业总收入", 1.0)], state)
        assert r[0].status == "FAIL"


class TestRelativeTolerance:
    """修 C：|delta|<0.01 或相对误差<0.5%。"""

    def test_large_value_rounding_passes(self):
        state = {"solvency_metrics": {"x": {"2025": 1038756658.94}}}
        r = verify_claims([_num("solvency_metrics.x.2025", 1038756659.44)], state)
        assert r[0].status == "PASS", f"亿元级 0.5 差额应按相对容差通过: {r[0]}"

    def test_small_value_abs_floor_unchanged(self):
        """绝对下限 0.01 仍然生效：delta<0.01 即便相对误差≥0.5% 也 PASS；
        相对与绝对双双超限才 FAIL（spec Scenario「显著偏离仍失败」为双条件）。"""
        state = {"solvency_metrics": {"x": {"2025": 0.5}}}
        # delta=0.008 < 0.01（相对 1.6% ≥ 0.5%，靠绝对下限通过）
        r1 = verify_claims([_num("solvency_metrics.x.2025", 0.508)], state)
        assert r1[0].status == "PASS"
        # delta=0.1 ≥ 0.01 且相对 20% ≥ 0.5% → FAIL
        r2 = verify_claims([_num("solvency_metrics.x.2025", 0.6)], state)
        assert r2[0].status == "FAIL"

    def test_significant_deviation_still_fails(self):
        state = {"solvency_metrics": {"资产负债率": {"2025": 11.59}}}
        r = verify_claims([_num("solvency_metrics.资产负债率.2025", 45.0)], state)
        assert r[0].status == "FAIL"
