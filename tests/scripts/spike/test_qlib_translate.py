"""「外部表达式翻译契约」TDD（unify-quant-research-architecture spike ④）。

契约（openspec delta factor-proposal「外部表达式翻译契约」）：

- 二值裁决：输出语义严格等价的合法 DSL 表达式，或显式「不可翻译」结论
  （附缺失/语义不等价算子清单）；MUST NOT 输出语义近似的猜测翻译；
- 逐算子映射 SHALL 可审计（映射表随产物留存）；字段与口径映射显式声明；
- 语义近似（同名算子语义不同）→ 拒绝并在映射表留档差异说明；
- 翻译产物经「外部研发供给接入」通道过闸；不可翻译候选计入 errors 桶。

等价基准 = formulation 语义（窗口含当日的滚动统计量定义）；实现级口径差异
（pyqlib 0.9.7 `min_periods=1` 热身期、除零/对数边界）在映射表逐条声明。

本模块为 spike 工具（design.md Non-Goals：不进生产代码）。运行方式：
``uv run pytest tests/scripts/spike/test_qlib_translate.py -q``
（tests/scripts 不在默认套件内；运行输出随 spike 验证报告留档）。
"""

from __future__ import annotations

import pytest
import qlib_translate
from qlib_translate import FIELD_MAPPINGS, OPERATOR_MAPPINGS, translate_qlib_expression

from finance_agent.factors.dsl import OPS, parse


class TestStrictEquivalence:
    """契约场景 1：严格等价翻译（输出 + 映射记录）。"""

    def test_mean_strict_equivalence(self):
        """spec 场景逐字：`Mean($close, 5)` → `ts_mean(close,5)` 且映射表记录。"""
        result = translate_qlib_expression("Mean($close, 5)")
        assert result.translatable
        assert result.dsl_expression == "ts_mean(close,5)"
        assert result.blockers == ()
        assert any("Mean" in entry and "ts_mean" in entry for entry in result.used_mappings)

    def test_arithmetic_precedence_and_constant(self):
        """乘除优先于加减；常量 -1 走二元 sub（非负字面量）。"""
        result = translate_qlib_expression("$close / Mean($close, 20) - 1")
        assert result.translatable
        assert result.dsl_expression == "sub(div(close,ts_mean(close,20)),1)"

    def test_parenthesized_close_position(self):
        result = translate_qlib_expression("($close - $low) / ($high - $low)")
        assert result.translatable
        assert result.dsl_expression == "div(sub(close,low),sub(high,low))"

    def test_std_corr_delta_abs_log_mappings(self):
        cases = {
            "Std($close, 20)": "ts_std(close,20)",
            "Corr($close, $volume, 10)": "ts_corr(close,volume,10)",
            "Delta($close, 5)": "delta(close,5)",
            "Abs($close)": "abs(close)",
            "Log($volume)": "log(volume)",
            "Mul($close, 2)": "mul(close,2)",
        }
        for source, expected in cases.items():
            result = translate_qlib_expression(source)
            assert result.translatable, (source, result.blockers)
            assert result.dsl_expression == expected

    def test_rank_is_rolling_equivalent(self):
        """pyqlib Rank = 滚动 pct 秩（ops.py L1156），与 ts_rank formulation 等价。"""
        result = translate_qlib_expression("Rank($close, 5)")
        assert result.translatable
        assert result.dsl_expression == "ts_rank(close,5)"

    def test_ref_strict_equivalence(self):
        """Ref = series.shift(N)（ops.py L793），与本 DSL ref 严格等价（无热身期口径差）。

        extend-factor-dsl-ref：Ref 从 missing 翻转为 equivalent——动量/反转族可翻译。
        """
        result = translate_qlib_expression("Ref($close, 20)")
        assert result.translatable
        assert result.dsl_expression == "ref(close,20)"
        composite = translate_qlib_expression("$close / Ref($close, 20) - 1")
        assert composite.translatable
        assert composite.dsl_expression == "sub(div(close,ref(close,20)),1)"
        assert any("Ref" in entry and "equivalent" in entry for entry in composite.used_mappings)

    def test_sum_strict_equivalence(self):
        """Sum = Rolling 求和（ops.py L847），与 ts_sum formulation 等价（热身期口径差同其他滚动算子）。

        add-factor-dsl-ts-sum-sqrt：Sum 从 missing 翻转为 equivalent——VWAP 偏离族可翻译。
        """
        result = translate_qlib_expression("Sum($volume, 5)")
        assert result.translatable
        assert result.dsl_expression == "ts_sum(volume,5)"
        assert any("Sum" in entry and "equivalent" in entry for entry in result.used_mappings)

    def test_vwap_deviation_composite_translatable(self):
        """VWAP 偏离复合式（本 delta 立项形态）：Sum 比值结构可翻译。"""
        result = translate_qlib_expression(
            "$close / (Sum($volume * $close, 5) / Sum($volume, 5)) - 1"
        )
        assert result.translatable
        assert result.dsl_expression == (
            "sub(div(close,div(ts_sum(mul(volume,close),5),ts_sum(volume,5))),1)"
        )

    def test_translated_output_is_legal_dsl(self):
        """产物必须能过生产解析器（含复杂度上限）——「合法 DSL 表达式」义务。"""
        for source in (
            "Mean($close, 5)",
            "$close / Mean($close, 20) - 1",
            "($close - $low) / ($high - $low)",
            "Rank($close, 5)",
            "Corr($close, $volume, 10)",
            "$close / Ref($close, 20) - 1",
        ):
            result = translate_qlib_expression(source)
            assert result.translatable
            parse(result.dsl_expression)  # 抛错即失败


class TestUntranslatable:
    """契约场景 2/3：显式不可翻译（算子清单 + 语义差异说明），MUST NOT 猜测。"""

    def test_quantile_missing_untranslatable(self):
        result = translate_qlib_expression("Quantile($close, 20)")
        assert not result.translatable
        assert result.dsl_expression is None
        assert any("Quantile" in blocker for blocker in result.blockers)

    def test_same_name_semantics_differ_refuses_guess(self):
        """同名算子语义不同（Max 滚动 vs 逐元素）→ 拒绝机械替换并留档差异。"""
        result = translate_qlib_expression("Max($close, 20)")
        assert not result.translatable
        assert result.dsl_expression is None  # 不输出 max(close,20) 猜测翻译
        assert any("Max" in blocker for blocker in result.blockers)
        assert any("Max" in entry and "逐元素" in entry for entry in result.used_mappings)

    def test_multiple_blockers_listed(self):
        """Quantile 阻塞、Ref 已可翻译——完整清单只列真阻塞项（extend-factor-dsl-ref 翻转）。"""
        result = translate_qlib_expression("Quantile(Ref($close, 3), 20)")
        assert not result.translatable
        joined = " ".join(result.blockers)
        assert "Quantile" in joined
        assert "算子 Ref" not in joined

    @pytest.mark.parametrize("source", ["Mean($close, 0)", "Mean($close, 0.5)"])
    def test_non_positive_int_window_refused(self, source):
        """N=0 为 expanding、0<N<1 为 ewm（pyqlib Rolling）——语义不同拒绝。"""
        result = translate_qlib_expression(source)
        assert not result.translatable
        assert any("窗口" in blocker for blocker in result.blockers)

    @pytest.mark.parametrize("source", ["Ref($close, 0)", "Ref($close, -5)"])
    def test_ref_non_positive_window_refused(self, source):
        """ref 窗口须正整数：N=0 恒等引用、N<0 前瞻引用（未来函数）——均拒收。"""
        result = translate_qlib_expression(source)
        assert not result.translatable
        assert any("窗口" in blocker for blocker in result.blockers)

    def test_negative_literal_refused(self):
        result = translate_qlib_expression("-1 * $close")
        assert not result.translatable
        assert any("负数" in blocker for blocker in result.blockers)

    def test_unknown_operator_refused(self):
        result = translate_qlib_expression("Foo($close, 3)")
        assert not result.translatable
        assert any("Foo" in blocker for blocker in result.blockers)

    def test_unknown_field_refused(self):
        result = translate_qlib_expression("$vwap / $close")
        assert not result.translatable
        assert any("$vwap" in blocker for blocker in result.blockers)

    @pytest.mark.parametrize("source", ["$close /", "$close > $open", ""])
    def test_syntax_error_refused(self, source):
        result = translate_qlib_expression(source)
        assert not result.translatable
        assert result.dsl_expression is None
        assert result.blockers

    def test_complexity_overflow_refused(self):
        """深度 7 > 上限 6（6 层嵌套 Mean）——超出 DSL 复杂度硬约束拒绝。"""
        result = translate_qlib_expression("Mean(Mean(Mean(Mean(Mean(Mean($close,2),2),2),2),2),2)")
        assert not result.translatable
        assert any("复杂度" in blocker for blocker in result.blockers)

    def test_depth_5_now_translatable(self):
        """raise-factor-dsl-depth-cap：深度 5 自然形态（滚动统计量套价量差值）现可翻译。"""
        result = translate_qlib_expression("Mean($open / Ref($close, 1) - 1, 5)")
        assert result.translatable
        assert result.dsl_expression == "ts_mean(sub(div(open,ref(close,1)),1),5)"

    def test_power_half_exponent_special_case(self):
        """Power(·,0.5) ≡ sqrt（特例映射，extend-factor-dsl-coverage）；其余指数拒绝。"""
        half = translate_qlib_expression("Power($close, 0.5)")
        assert half.translatable
        assert half.dsl_expression == "sqrt(close)"
        assert any("Power" in entry and "sqrt" in entry for entry in half.used_mappings)
        square = translate_qlib_expression("Power($close, 2)")
        assert not square.translatable
        assert any("Power" in blocker for blocker in square.blockers)

    def test_parkinson_vol_composite_translatable(self):
        """Parkinson 波动率复合式：Power(·,0.5) 特例 + 平方 mul 改写 + 深度 6 恰通过。"""
        result = translate_qlib_expression(
            "Power(Mean(Log($high / $low) * Log($high / $low), 10), 0.5)"
        )
        assert result.translatable
        assert result.dsl_expression == (
            "sqrt(ts_mean(mul(log(div(high,low)),log(div(high,low))),10))"
        )


class TestMappingTableAudit:
    """契约：逐算子映射可审计；字段与口径映射显式声明。"""

    def test_every_equivalent_mapping_points_to_real_dsl_op(self):
        for name, mapping in OPERATOR_MAPPINGS.items():
            if mapping.status == "equivalent":
                assert mapping.dsl_name in OPS, name
                assert mapping.note, name
            else:
                assert mapping.dsl_name is None, name
                assert mapping.note, name

    def test_expected_statuses(self):
        assert OPERATOR_MAPPINGS["Ref"].status == "equivalent"  # extend-factor-dsl-ref 翻转
        assert OPERATOR_MAPPINGS["Ref"].dsl_name == "ref"
        assert OPERATOR_MAPPINGS["Sum"].status == "equivalent"  # extend-factor-dsl-coverage 翻转
        assert OPERATOR_MAPPINGS["Sum"].dsl_name == "ts_sum"
        assert OPERATOR_MAPPINGS["Max"].status == "not_equivalent"
        assert OPERATOR_MAPPINGS["Min"].status == "not_equivalent"
        assert OPERATOR_MAPPINGS["Mean"].status == "equivalent"
        assert OPERATOR_MAPPINGS["Power"].status == "missing"  # 可变指数无对应；0.5 走特例分支

    def test_field_mappings_declare_caliber(self):
        assert FIELD_MAPPINGS["$close"].dsl_field == "close"
        assert "前复权" in FIELD_MAPPINGS["$close"].note
        assert FIELD_MAPPINGS["$factor"].dsl_field is None

    def test_dump_mapping_table(self):
        text = qlib_translate.dump_mapping_table()
        assert "ts_mean" in text
        assert "Ref" in text
        assert "$close" in text
