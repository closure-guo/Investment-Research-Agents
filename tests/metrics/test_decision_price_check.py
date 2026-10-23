"""update-decision-integrity-gates Task 1：决策文本价位交叉校验器（TDD 先行）。

四 Scenario 逐条对应 openspec/changes/update-decision-integrity-gates/specs/
price-level-tooling/spec.md（触发价幻觉/空洞触发/正常直通/非价格量纲不误报），
另含 600015 反例族、risk_judge 接入测试（mock call_llm_streaming——同
tests/nodes/test_risk.py 既有模式）与「校验异常不中断管线」观测旁路护栏。

technical_indicators 手搓形态与 calc_technical 输出严格同构（分组 dict、
与 K 线等长的 list、末值=最新值、预热段 None），并有 calc_technical 全真
端到端测试钉死形态兼容。
"""

import json
import math
from unittest.mock import patch

import pandas as pd
import pytest

from finance_agent.metrics.decision_price_check import check_decision_prices
from finance_agent.metrics.levels import calc_price_levels
from finance_agent.metrics.technical import calc_technical
from finance_agent.models import TradeDecision
from finance_agent.nodes.risk import risk_judge


def _indicators(
    ma5: float | None = None,
    ma10: float | None = None,
    ma20: float | None = None,
    ma60: float | None = None,
    boll_upper: float | None = None,
    boll_middle: float | None = None,
    boll_lower: float | None = None,
) -> dict:
    """按 calc_technical 输出形态构造 technical_indicators（末值=给定值，预热段 None）。"""

    def seq(v: float | None) -> list[float | None]:
        return [None, None, v]

    return {
        "MA": {"5": seq(ma5), "10": seq(ma10), "20": seq(ma20), "60": seq(ma60)},
        "BOLL": {
            "upper": seq(boll_upper),
            "middle": seq(boll_middle),
            "lower": seq(boll_lower),
        },
    }


def _decision(**fields: object) -> TradeDecision:
    base: dict = {"action": "watch", "confidence": 0.5, "reasoning": "观望"}
    base.update(fields)
    return TradeDecision.model_validate(base)


# ── 600845 形态夹具：MA60=18.066，现价 18.10，18.8 落在全部参考带外 ──
TI_600845 = _indicators(
    ma5=18.12,
    ma10=18.09,
    ma20=18.05,
    ma60=18.066,
    boll_upper=18.60,
    boll_middle=18.05,
    boll_lower=17.95,
)
PL_600845 = {
    "available": True,
    "entry_ref": 18.10,
    "recent_high": 18.55,
    "recent_low": 17.95,
    "atr": 0.10,
    "stop_band_long": {"low": 17.90, "high": 18.00},
    "target_band_long": {"low": 18.30, "high": 18.50},
    "full_band": [17.75, 18.65],
}
CLOSE_600845 = 18.10

# ── 601066 形态夹具：现价 23.03，止损参考带上沿 22.61 ──
TI_601066 = _indicators(
    ma5=22.90,
    ma10=22.85,
    ma20=22.70,
    ma60=22.10,
    boll_upper=23.10,
    boll_middle=22.50,
    boll_lower=21.90,
)
PL_601066 = {
    "available": True,
    "entry_ref": 23.03,
    "recent_high": 23.50,
    "recent_low": 22.40,
    "atr": 0.35,
    "stop_band_long": {"low": 22.33, "high": 22.61},
    "target_band_long": {"low": 23.73, "high": 24.43},
    "full_band": [21.70, 24.20],
}
CLOSE_601066 = 23.03


class TestScenarioDeviation:
    """Scenario 1：触发价幻觉检测（偏差形态）——放量突破 MA60 并站稳 18.8。"""

    def test_hallucinated_ma60_price_registered(self):
        decision = _decision(
            inaction_reason="均线趋势未确认",
            reasoning="技术面等待信号确认，暂不行动",
            reeval_triggers=["放量突破 MA60 并站稳 18.8 以上"],
        )
        anomalies = check_decision_prices(decision, TI_600845, PL_600845, CLOSE_600845)
        assert len(anomalies) == 1
        a = anomalies[0]
        assert a["kind"] == "deviation"
        # 触发原文完整保留（Task 3 渲染按同源文本匹配）
        assert a["source_text"] == "放量突破 MA60 并站稳 18.8 以上"
        # 指标名 + 已验证值 + 偏差幅度三要素齐备
        assert a["indicator"] == "MA60"
        assert a["verified_value"] == pytest.approx(18.066)
        assert a["deviation_pct"] == pytest.approx(4.06, abs=0.01)
        assert "MA60" in a["message"]
        assert "18.066" in a["message"]
        assert "4.06" in a["message"]

    def test_decision_object_not_mutated(self):
        """纯观测：校验不得改动决策对象（放行语义的前置）。"""
        decision = _decision(reeval_triggers=["放量突破 MA60 并站稳 18.8 以上"])
        before = decision.model_dump()
        check_decision_prices(decision, TI_600845, PL_600845, CLOSE_600845)
        assert decision.model_dump() == before


class TestScenarioEmptyTrigger:
    """Scenario 2：空洞触发条件检测——站上 22.61 而现价 23.03，条件已满足。"""

    def test_breakout_below_close_registered(self):
        decision = _decision(
            inaction_reason="触发条件尚未满足",
            reeval_triggers=["价格放量站上止损参考带上沿 22.61"],
        )
        anomalies = check_decision_prices(decision, TI_601066, PL_601066, CLOSE_601066)
        assert len(anomalies) == 1
        a = anomalies[0]
        assert a["kind"] == "empty_trigger"
        assert a["source_text"] == "价格放量站上止损参考带上沿 22.61"
        # 上破触发价 22.61 不高于现价 23.03：现价即验证值
        assert a["verified_value"] == pytest.approx(23.03)
        assert a["deviation_pct"] is None
        assert "22.61" in a["message"]
        assert "23.03" in a["message"]
        assert "已满足" in a["message"]

    def test_breakdown_above_close_registered(self):
        """下破对称形态：失守 23.5 而现价 23.03——下破价不低于现价，条件已满足。"""
        decision = _decision(reeval_triggers=["放量失守 MA5 23.5 则观望"])
        anomalies = check_decision_prices(decision, TI_601066, PL_601066, CLOSE_601066)
        assert len(anomalies) == 1
        a = anomalies[0]
        assert a["kind"] == "empty_trigger"
        assert a["verified_value"] == pytest.approx(23.03)


class TestScenarioNormalPassThrough:
    """Scenario 3：正常触发价直通——偏差在阈值内且方向语义有效，零 anomaly。"""

    def test_valid_triggers_pass(self):
        decision = _decision(
            reeval_triggers=[
                "跌破近期低点 22.94 加速离场",
                "放量突破 24.60 后回踩不破可加仓",
            ]
        )
        ti = _indicators(
            ma5=23.10,
            ma10=23.00,
            ma20=22.85,
            ma60=22.40,
            boll_upper=23.60,
            boll_middle=22.90,
            boll_lower=22.20,
        )
        pl = {
            "available": True,
            "entry_ref": 23.03,
            "recent_high": 24.50,
            "recent_low": 22.94,
            "atr": 0.30,
            "stop_band_long": {"low": 22.43, "high": 22.73},
            "target_band_long": {"low": 23.63, "high": 24.23},
            "full_band": [22.34, 25.10],
        }
        assert check_decision_prices(decision, ti, pl, 23.03) == []

    def test_statement_of_fact_not_empty_trigger(self):
        """「已站上」是事实陈述而非再评估门槛——空洞形态不得误报（已前缀守卫）。"""
        decision = _decision(inaction_reason="股价已站上 MA20 18.9 之上，暂不追高")
        ti = _indicators(ma5=18.50, ma20=18.30, ma60=18.00)
        pl = {
            "available": True,
            "entry_ref": 19.00,
            "recent_high": 19.20,
            "recent_low": 17.90,
            "atr": 0.20,
            "stop_band_long": {"low": 18.60, "high": 18.80},
            "target_band_long": {"low": 19.40, "high": 19.80},
            "full_band": [17.50, 18.80],
        }
        anomalies = check_decision_prices(decision, ti, pl, 19.00)
        # 18.9 vs MA20 18.30 偏差 3.28% 且在参考带外 → 只登记偏差形态；
        # 「已站上」不构成空洞触发
        assert len(anomalies) == 1
        assert anomalies[0]["kind"] == "deviation"
        assert anomalies[0]["indicator"] == "MA20"
        assert "已站上" in anomalies[0]["source_text"]


class TestScenarioNonPriceDimensions:
    """Scenario 4 + 600015 反例：百分比/比值/非价格量纲 MUST NOT 误报。"""

    def test_percent_ratio_and_macro_numbers_pass(self):
        decision = _decision(
            reasoning="综合置信度 0.6；估值处于近三年 30% 分位",
            inaction_reason="赔率不足 1.2:1，单季净利降幅收敛至 18.35% 以下前观望",
            reeval_triggers=[
                "单季净利降幅收敛至 18.35% 以下",
                "赔率修复至 1:1 以上",
                "RSI 回落至 40 以下再介入",
                "成交量放大至 2.5 万手以上",
            ],
        )
        assert check_decision_prices(decision, TI_600845, PL_600845, CLOSE_600845) == []

    def test_kdj_j_value_not_price(self):
        """KDJ 的 J 值读数（80）是震荡指标，不是价位；不得与 MA20 做偏差比对。

        同花顺 10-20 生产实证：模型触发条件写「J 值回落至 80 以下」，校验器把 80
        当股价与 MA20 202.517 比对（偏差 60.5%）并阻断报告；模型改为定性表述后，
        reasoning 里的纠错说明仍含 80，二次校验再次误报。
        """
        decision = _decision(
            reasoning="上一稿把 KDJ 的 J 值读数 80 写入触发条件，现改为定性表述",
            reeval_triggers=["J 值回落至 80 以下且价格守住 MA20"],
        )
        assert check_decision_prices(decision, TI_600845, PL_600845, CLOSE_600845) == []

    def test_indicator_name_numbers_not_extracted(self):
        """「MA60」「60日均线」中的数字是指标名成分，不得当价位提取。"""
        decision = _decision(
            reeval_triggers=["放量突破 MA60 并站稳 18.8 以上"],
            inaction_reason="股价位于 60日均线下方",
        )
        anomalies = check_decision_prices(decision, TI_600845, PL_600845, CLOSE_600845)
        # 仅 18.8 一处偏差 anomaly；60 不得被提取为价位（否则 60 vs 18.066 会误报）
        assert len(anomalies) == 1
        assert anomalies[0]["kind"] == "deviation"
        assert all(a["source_text"] != "60" for a in anomalies)

    def test_dates_not_extracted(self):
        """日期（2026-09-30 / 9月30日）不得当价位提取。"""
        decision = _decision(
            reeval_triggers=["2026-09-30 前若放量突破 MA60 并站稳 18.8 以上则转积极"],
        )
        anomalies = check_decision_prices(decision, TI_600845, PL_600845, CLOSE_600845)
        assert len(anomalies) == 1
        assert anomalies[0]["deviation_pct"] == pytest.approx(4.06, abs=0.01)

    def test_dash_range_not_extracted(self):
        """破折号区间「2—3.5%」的 2 不得当价位提取（宝信软件 10-13 生产实证：
        「止损距离仅约 2—3.5%（估算值）」的 2 与止损参考带下沿 16.3771
        偏差 87.79% → 打回重试）。"""
        decision = _decision(reasoning="止损距离仅约 2—3.5%（估算值），在 38.61% 年化波动下。")
        anomalies = check_decision_prices(decision, TI_600845, PL_600845, CLOSE_600845)
        assert anomalies == []

    def test_real_price_after_dash_range_still_checked(self):
        """护栏：破折号区间后的真实价位仍须校验（排除区不得过宽）。

        13.0 与最近已验证指标偏差远超 2% 且落在全部参考带外 → SHALL 登记 deviation。
        """
        decision = _decision(reasoning="止损距离约 2—3.5%；若跌破 13.0 支撑则离场观望。")
        anomalies = check_decision_prices(decision, TI_600845, PL_600845, CLOSE_600845)
        assert len(anomalies) == 1
        assert anomalies[0]["kind"] == "deviation"


class TestCjkEmbeddedAliasesAndTrailingEnumerators:
    """10-07 生产误拦回归（issue #254）：中文行文嵌入的 MA 别名 + 「1) 2)」后括号序号。

    根因一：_alias_boundary_ok 用 str.isalnum() 拦「XMA5」前缀，但 CJK 汉字的
    isalnum() 亦为 True——「上穿MA60」的 ma60 别名被拒，60 被提取为价位、
    就近归属 MA20，偏差 243% 打回（600845 10-07 cohort 阻断逐字复现）。
    打回重试不可能修复：中文行文必然含汉字。

    根因二：_ENUM_SPAN_PATTERNS 只挡「（1）」前括号序号，600515 实证
    「；1) 相对近窗…；2) 止损距离…」的 1/2 被提为价位。
    """

    def test_ma60_preceded_by_cjk_not_extracted(self):
        """「上穿MA60」前邻汉字：60 不得当价位（600845 10-07 生产原文）。"""
        decision = _decision(
            reeval_triggers=["MA20上穿MA60确认中期趋势反转"],
            inaction_reason="中期技术趋势未反转（MA20低于MA60）",
        )
        anomalies = check_decision_prices(decision, TI_600845, PL_600845, CLOSE_600845)
        assert anomalies == []

    def test_ma5_preceded_by_cjk_not_extracted(self):
        """「若MA5上穿MA20」：MA5 前邻汉字同样中招（600026「价位 5」同桶）。"""
        decision = _decision(reeval_triggers=["若MA5上穿MA20则转积极"])
        anomalies = check_decision_prices(decision, TI_600845, PL_600845, CLOSE_600845)
        assert anomalies == []

    def test_ascii_prefix_still_rejects_alias(self):
        """守卫本意保留：「EMA5」「XMA60」前邻 ASCII 字母数字仍不得读作别名。"""
        from finance_agent.metrics.decision_price_check import _alias_occurrences

        assert _alias_occurrences("ema5金叉") == []
        assert _alias_occurrences("xma60压力") == []
        # 中文前邻不受影响（正向锚）
        assert len(_alias_occurrences("穿ma60确认")) == 1

    def test_trailing_paren_enumerators_not_extracted(self):
        """「1) … 2) …」后括号序号（600515 10-07 生产原文）不得当价位。"""
        decision = _decision(
            reeval_triggers=["放量跌破 17.90 确认技术破位"],
            inaction_reason="1) 相对近窗 neutral 前判翻转纪律不满足；2) 止损距离与 VaR 未分离",
        )
        anomalies = check_decision_prices(decision, TI_600845, PL_600845, CLOSE_600845)
        assert anomalies == []

    def test_slash_fraction_position_not_extracted(self):
        """「1/3仓位」斜杠分数（600515 10-07 补跑实证，同股第三形态）不得当价位。

        「/」不在量纲排除表：分子「1」后随斜杠、分母「3」前邻斜杠均被提取，
        1 vs 止损参考带下沿 2.6171 偏差 61.79% 阻断。豁免代价：斜杠并列价位
        （「19.99/20.10」）一并豁免——与连字区间同策，误杀由观测通道承担。
        """
        decision = _decision(
            reeval_triggers=["放量跌破 17.90 确认技术破位"],
            inaction_reason="激进方试多方案有纪律性（1/3仓位分批介入）",
        )
        anomalies = check_decision_prices(decision, TI_600845, PL_600845, CLOSE_600845)
        assert anomalies == []

    def test_price_before_closing_paren_not_masked(self):
        """守卫不得反向掩蔽：紧邻右括号的真实价位「（止损 9.99）」仍须受校验。"""
        decision = _decision(reeval_triggers=["跌破（止损 9.99）则离场观望"])
        anomalies = check_decision_prices(decision, TI_600845, PL_600845, CLOSE_600845)
        assert len(anomalies) == 1
        assert anomalies[0]["kind"] == "deviation"

    def test_ascii_letter_glued_digit_not_extracted(self):
        """「M2支撑扩表」的 2 不得当价位（601818 10-08 run 41 实证，偏差 31.51% 阻断）。

        数字紧贴 ASCII 字母（前邻，隔空格亦可）属标识符形态（M2/M1/RSI14），
        不是独立股价；股价在本语料中恒跟随 CJK 方向词/量词。
        """
        decision = _decision(
            reeval_triggers=["放量跌破 17.90 确认技术破位"],
            inaction_reason="宏观多空对冲（LPR压制息差 vs M2支撑扩表）、技术面仅企稳而非反转",
        )
        anomalies = check_decision_prices(decision, TI_600845, PL_600845, CLOSE_600845)
        assert anomalies == []


class TestBreakDirectionNearestWord:
    """方向词判定：最近词胜出（600845 10-08 run 41 实证）。

    旧实现先扫完上破类再扫下破类——「突破20.58确认/跌破16.73证伪」的 16.73
    距「跌破」2 字符、距「突破」11 字符（窗口 12 内）→ up 抢占 → 跌破触发
    （16.73 < 收盘，有效前瞻门槛）被误判「上破触发价不高于收盘」空洞形态阻断。
    """

    def test_breakdown_nearest_word_not_usurped_by_distant_breakout(self):
        decision = _decision(
            reeval_triggers=["多空共享风险框架（突破20.58确认/跌破16.73证伪）"],
        )
        anomalies = check_decision_prices(decision, TI_600845, PL_600845, CLOSE_600845)
        assert all(a["kind"] != "empty_trigger" for a in anomalies)

    def test_compound_pullback_exemption_still_fires(self):
        """复合回踩豁免不回归（688072 案）：同值既有下破又有上破语境 → 空洞跳过。"""
        decision = _decision(
            reeval_triggers=["先回撤至 17.90，再放量站上 17.90 确认趋势反转"],
        )
        anomalies = check_decision_prices(decision, TI_600845, PL_600845, CLOSE_600845)
        # 17.90 = stop_band_long 下沿（带内豁免偏差形态）+ 复合豁免空洞形态 → 全零
        assert anomalies == []


class TestFieldCoverage:
    """reeval_triggers 每条 + inaction_reason + reasoning 三字段全覆盖。"""

    def test_inaction_reason_and_reasoning_checked(self):
        decision = _decision(
            reasoning="回踩布林下轨 18.9 获支撑则转积极",
            reeval_triggers=[],
        )
        anomalies = check_decision_prices(decision, TI_600845, PL_600845, CLOSE_600845)
        # 布林下轨已验证值 17.95，18.9 偏差 5.29% 且在参考带外 → deviation
        assert len(anomalies) == 1
        a = anomalies[0]
        assert a["kind"] == "deviation"
        assert a["indicator"] == "布林下轨"
        assert a["verified_value"] == pytest.approx(17.95)
        # 非触发条目字段按局部片段摘录，保留原文可读形态
        assert "18.9" in a["source_text"]

    def test_buy_without_text_prices_yields_empty(self):
        decision = _decision(
            action="buy",
            confidence=0.6,
            reasoning="趋势向好，建议买入",
        )
        assert check_decision_prices(decision, TI_600845, PL_600845, CLOSE_600845) == []


class TestReferenceBandExemption:
    """参考带豁免：未点名价位落在支撑/压力带（含 sanity 放宽带）内不报偏差。"""

    def test_reasoning_stop_echo_inside_full_band_pass(self):
        """reasoning 复述止损/目标价（>2% 偏离均线但落 sanity 放宽带内）不得误报。"""
        decision = _decision(
            action="buy",
            confidence=0.6,
            reasoning="以 18.20 附近建仓，止损参考 17.50，目标参考 19.30；赔率约 1.4:1",
        )
        # 独立参考带夹具：17.50/19.30 均在 stop/target 带外、仅被放宽带豁免
        pl = {
            "available": True,
            "entry_ref": 18.10,
            "recent_high": 18.55,
            "recent_low": 17.60,
            "atr": 0.15,
            "stop_band_long": {"low": 17.80, "high": 18.00},
            "target_band_long": {"low": 18.40, "high": 18.80},
            "full_band": [16.90, 19.50],
        }
        anomalies = check_decision_prices(decision, TI_600845, pl, CLOSE_600845)
        assert anomalies == []

    def test_unnamed_price_outside_all_bands_flagged(self):
        decision = _decision(reeval_triggers=["回落至 16.80 全仓接回"])
        anomalies = check_decision_prices(decision, TI_600845, PL_600845, CLOSE_600845)
        # 16.80 距最近指标（MA60 18.066）7.0% 且落全部参考带外 → deviation
        assert len(anomalies) == 1
        assert anomalies[0]["kind"] == "deviation"


class TestMissingInputs:
    """latest_close 缺失只跳过空洞检测、仍做偏差；无任何已验证指标则整体跳过。"""

    def test_none_close_skips_empty_only(self):
        decision = _decision(
            reeval_triggers=["价格放量站上 17.50", "放量突破 MA60 并站稳 18.8 以上"]
        )
        anomalies = check_decision_prices(decision, TI_600845, PL_600845, None)
        assert len(anomalies) == 2
        assert all(a["kind"] == "deviation" for a in anomalies)
        assert all(a["verified_value"] is not None for a in anomalies)

    def test_no_verified_indicators_yields_empty(self):
        decision = _decision(reeval_triggers=["放量突破 MA60 并站稳 18.8 以上"])
        assert check_decision_prices(decision, {}, {}, None) == []
        assert (
            check_decision_prices(
                decision, {}, {"available": False, "reason": "insufficient_kline"}, None
            )
            == []
        )


class TestCalcTechnicalShapeCompatibility:
    """全真形态端到端：calc_technical/calc_price_levels 产出直接可用。"""

    def test_flat_kline_hallucinated_breakout(self):
        n = 80
        kline = pd.DataFrame(
            {
                "日期": pd.date_range("2026-06-01", periods=n),
                "开盘": [18.0] * n,
                "收盘": [18.0] * n,
                "最高": [18.05] * n,
                "最低": [17.95] * n,
                "成交量": [1000] * n,
            }
        )
        ti = calc_technical(kline)
        pl = calc_price_levels(kline)
        decision = _decision(reeval_triggers=["放量突破 MA60 并站稳 18.8 以上"])
        anomalies = check_decision_prices(decision, ti, pl, 18.0)
        assert len(anomalies) == 1
        a = anomalies[0]
        assert a["kind"] == "deviation"
        assert a["indicator"] == "MA60"
        assert a["verified_value"] == pytest.approx(18.0)
        assert a["deviation_pct"] == pytest.approx(4.44, abs=0.01)

    def test_warmup_none_values_fall_back_to_closest(self):
        """预热段（None 末值）指标不可用：点名指标缺值回退最接近已验证值，不抛异常。"""
        ti = {
            "MA": {"5": [None] * 3, "10": [None] * 3, "20": [None] * 3, "60": [None] * 3},
            "BOLL": {"upper": [None] * 3, "middle": [None] * 3, "lower": [None] * 3},
        }
        decision = _decision(reeval_triggers=["放量突破 MA60 并站稳 18.8 以上"])
        anomalies = check_decision_prices(decision, ti, {}, 18.10)
        # MA60 预热段缺值 → 回退唯一可用已验证值（最新收盘 18.10）做偏差核对
        assert len(anomalies) == 1
        assert anomalies[0]["indicator"] == "最新收盘价"
        assert anomalies[0]["verified_value"] == pytest.approx(18.10)


class TestRiskJudgeIntegration:
    """risk_judge 接入（update-decision-price-gate 门禁化）：anomaly → 打回重试一次，
    仍异常 gate fail、残留落 state；无 anomaly 直通。"""

    @staticmethod
    def _kline_601066() -> pd.DataFrame:
        return pd.DataFrame(
            {
                "日期": ["2026-09-29", "2026-09-30"],
                "开盘": [22.80, 22.95],
                "收盘": [22.90, 23.03],
                "最高": [23.00, 23.10],
                "最低": [22.70, 22.90],
                "成交量": [1000, 1200],
            }
        )

    @patch("finance_agent.nodes._llm_utils.call_llm_streaming")
    def test_risk_judge_returns_decision_price_anomalies(self, mock_llm):
        """watch 决策缺结构化触发位 + 含空洞触发价 → 两道 per-design 打回，stub 仍同输出。

        回路序：初始(1) → 触发位申报打回(2)（add-watch-trigger-tracking，同
        final_reeval_check 一次打回先例）→ 仍缺放行 + 标注 → 价位门禁检出空洞触发价
        → 门禁打回(3)（archive/2026-10-02-update-decision-price-gate）→ stub 同源残留
        未恶化 → 放行 + note 待终裁、残留照落 state
        （update-decision-price-gate-admission）。"""
        mock_llm.return_value = json.dumps(
            {
                "action": "watch",
                "confidence": 0.5,
                "reasoning": "等待趋势确认",
                "inaction_reason": "触发条件尚未满足",
                "reeval_triggers": ["价格放量站上止损参考带上沿 22.61"],
            },
            ensure_ascii=False,
        )
        state = {
            "trader_plan": {},
            "risk_debate_history": [],
            "technical_indicators": TI_601066,
            "price_levels": PL_601066,
            "kline": self._kline_601066(),
        }
        result = risk_judge(state)
        # 两次打回均 per-design：watch 缺结构化触发位先触发位申报打回一次
        # （add-watch-trigger-tracking，agent-node-contracts「同 final_reeval_check
        # 一次打回先例」）；随后价位门禁打回一次（archive/2026-10-02-update-decision-
        # price-gate 门禁回路）。stub 同源输出 → 残留未恶化放行。
        assert mock_llm.call_count == 3
        decision = result["final_trade_decision"]
        assert decision.action == "watch"  # risk_judge 层照常产出决策（阻断在 after_risk_judge）
        gate = result["decision_price_gate"]
        assert gate["result"] == "pass"
        assert "未恶化" in gate["note"]
        anomalies = result["decision_price_anomalies"]
        assert len(anomalies) == 1
        assert anomalies[0]["kind"] == "empty_trigger"
        assert anomalies[0]["verified_value"] == pytest.approx(23.03)

    @patch("finance_agent.nodes._llm_utils.call_llm_streaming")
    def test_risk_judge_buy_without_anomalies(self, mock_llm):
        mock_llm.return_value = json.dumps(
            {
                "action": "buy",
                "confidence": 0.6,
                "reasoning": "趋势向好，建议买入",
                "entry_price": 51.05,
                "stop_loss": 49.5,
                "target_price": 54.5,
            },
            ensure_ascii=False,
        )
        state = {
            "trader_plan": {},
            "risk_debate_history": [],
            "technical_indicators": TI_601066,
            "price_levels": PL_601066,
            "kline": self._kline_601066(),
        }
        result = risk_judge(state)
        assert result["decision_price_anomalies"] == []
        assert math.isfinite(result["final_trade_decision"].entry_price)

    @patch(
        "finance_agent.nodes.risk.check_decision_prices", side_effect=RuntimeError("checker boom")
    )
    @patch("finance_agent.nodes._llm_utils.call_llm_streaming")
    def test_checker_error_does_not_interrupt_pipeline(self, mock_llm, _mock_check):
        """校验器自身异常：登记为空并放行（spec：MUST NOT 硬中断管线）。"""
        mock_llm.return_value = json.dumps(
            {
                "action": "watch",
                "confidence": 0.5,
                "reasoning": "观望",
                "inaction_reason": "多因素均衡",
                "reeval_triggers": ["价格放量站上止损参考带上沿 22.61"],
            },
            ensure_ascii=False,
        )
        result = risk_judge({"trader_plan": {}, "risk_debate_history": []})
        assert result["final_trade_decision"].action == "watch"
        assert result["decision_price_anomalies"] == []


class TestFalsePositiveFix688072:
    """fix-decision-price-check-false-positives：688072 重跑实证的两个误报回归。

    案例背景（incident 034 / trace 7f43b9c9…）：reasoning 的「VaR95单日6.6%」
    曾被误读为股价 95 vs 近期低点 570 偏差 83.33%；触发条件「回撤至610以下…
    站稳610」的回踩确认结构曾被句法命中上破模式判空洞，导致门禁误拦。
    """

    STATE = {
        "available": True,
        "entry_ref": 640.0,
        "recent_low": 570.0,
        "recent_high": 945.0,
        "stop_band_long": {"low": 570.0, "high": 605.0},
        "target_band_long": {"low": 500.0, "high": 560.0},
    }

    GEN1_TRIGGER = "价格回撤至610以下且连续5日收盘站稳610并缩量企稳（量化原610企稳条件）"
    GEN2_TRIGGER = (
        "价格有效回撤至近期低点570-605止损带区域（需实际跌破610后连续5日收盘"
        "站稳610之上且成交量萎缩，形成回踩确认的企稳结构，而非当前640价位下直接满足）"
    )
    VAR_REASONING = (
        "(1)以PEG口径论证86倍PE便宜依赖+1324%单期增速可外推，属'线性外推'；"
        "(2)VaR95单日6.6%不能证明'高波动来自上行弹性'，最大回撤30.37%是已实现极值。"
    )

    def _decision(self, trigger: str, reasoning: str) -> TradeDecision:
        return TradeDecision.model_validate(
            {
                "action": "watch",
                "confidence": 0.6,
                "reasoning": reasoning,
                "inaction_reason": trigger,
                "reeval_triggers": [trigger],
            }
        )

    def test_var95_not_misread_as_price(self):
        """缺陷 A：VaR95 语境的 95 不再被当股价报偏差。"""
        anoms = check_decision_prices(
            self._decision(" MACD柱线重新翻正且MA5收复MA10 ".strip(), self.VAR_REASONING),
            {},
            self.STATE,
            640.0,
        )
        assert not [
            a for a in anoms if "95" in str(a.get("verified_value")) or "95" in a["message"]
        ]

    def test_var_percent_and_chinese_forms_not_misread(self):
        """VaR(95% / 在险价值95 变体形态同样不误报。"""
        for reasoning in ("VaR(95%置信)下单日6.6%", "在险价值95口径下回撤可控"):
            anoms = check_decision_prices(
                self._decision("跌破近期低点570重估", reasoning), {}, self.STATE, 640.0
            )
            assert not [a for a in anoms if "95" in a["message"]], reasoning

    def test_compound_retest_trigger_gen1_not_empty(self):
        """缺陷 B：gen1 复合回踩（回撤至610…站稳610）不判空洞。"""
        anoms = check_decision_prices(
            self._decision(self.GEN1_TRIGGER, "观望等待回踩确认"), {}, self.STATE, 640.0
        )
        assert not [a for a in anoms if a["kind"] == "empty_trigger"]

    def test_compound_retest_trigger_gen2_not_empty(self):
        """缺陷 B：gen2 复合回踩（跌破610后站稳610之上）不判空洞。"""
        anoms = check_decision_prices(
            self._decision(self.GEN2_TRIGGER, "观望等待回踩确认"), {}, self.STATE, 640.0
        )
        assert not [a for a in anoms if a["kind"] == "empty_trigger"]

    def test_simple_breakout_still_empty(self):
        """护栏：单上破无下破语境（spec 场景）照常判空洞——豁免不得扩大化。"""
        anoms = check_decision_prices(
            self._decision("价格放量站上止损参考带上沿605", "等待企稳"), {}, self.STATE, 640.0
        )
        assert [a for a in anoms if a["kind"] == "empty_trigger"]

    def test_real_hallucinated_price_still_reported(self):
        """护栏：真幻觉价位（无 var 语境的裸 95）照常报偏差——修复只豁免风险度量语境。"""
        anoms = check_decision_prices(
            self._decision("目标价看到 95 元附近减仓", "技术形态走弱"), {}, self.STATE, 640.0
        )
        assert [a for a in anoms if a["kind"] == "deviation" and "95" in a["message"]]


# ── 600026 复现夹具（2026-10-04 环境链验证跑实证，指标值为会话真实值）──
TI_600026 = _indicators(
    ma5=20.908,
    ma10=21.425,
    ma20=20.9565,
    ma60=18.218,
    boll_upper=22.10,
    boll_middle=20.95,
    boll_lower=19.80,
)
PL_600026 = {
    "available": True,
    "entry_ref": 20.56,
    "recent_high": 22.80,
    "recent_low": 14.06,
    "atr": 0.45,
    "stop_band_long": {"low": 18.60, "high": 19.40},
    "target_band_long": {"low": 22.50, "high": 23.40},
    "full_band": [14.06, 23.40],
}
CLOSE_600026 = 20.56

# risk_judge 打回重试后的终稿 reasoning 原文（Langfuse 逐字取回）：
# 「（1）（2）（3）」枚举序号被价位提取器误读为股价——生产阻断消息
# 「文本价位 3 与 近期低点 已验证值 14.06 偏差 78.66%」即出自此文。
REASONING_600026_RETRY = (
    "维持买入方向但下调仓位与置信度。硬事实（中报营收+30%、净利+143%、PE_ttm 16.76倍）"
    "站在多头一侧，技术面属中期趋势内回调，方向判断仍成立。风险辩论有效修正了方案三处问题："
    "（1）现金流与偿债压力（ROE 10.4%）是资产负债表既成事实而非未兑现情景，保守方归类纠正被采纳；"
    "（2）止损距离7.6%仅略高于VaR95单日5.3%，且波动率59.8%、历史最大回撤44%，止损被快速触及概率偏高，"
    "故仓位由moderate降为light；（3）赔率1.56:1（代码派生值）偏低，到达目标价后采用移动止盈而非固定截断。"
    "相对近窗前判（09-30 neutral），增量事实为中报景气反转已验证落地且估值处合理偏低水平，构成方向翻转依据。"
    "价位继承：入场20.56，止损19.0（无条件执行），第一目标23.0。"
)
TRIGGERS_600026_RETRY = [
    "收盘价触及19.0止损位即无条件执行价格纪律离场",
    "季报或经营数据显示营收/净利增速显著回落（如营收增速降至15%以下），或现金流、偿债指标进一步恶化时重估",
    "年化波动率显著抬升至70%以上或解禁窗口临近时触发降仓评估",
]


class TestEnumeratorFalsePositives:
    """枚举序号/量词/空格别名误提取修复（fix-decision-price-enumerators）。

    生产实证：risk_judge 打回重试后的 reasoning 以「（1）（2）（3）」枚举论点，
    「（3）」的 3 被当股价归属到近期低点 14.06（偏差 78.66%）→ 打回重试也换不掉
    自己的列表行文 → 恶化判定 → 报告阻断（cohort no_report_ready 根因）。
    """

    def test_production_retry_reasoning_zero_false_positives(self):
        """真实终稿全文 SHALL 零 anomaly（真实价位 20.56/19.0/23.0 均在参考带内）。"""
        decision = _decision(
            action="buy",
            confidence=0.55,
            reasoning=REASONING_600026_RETRY,
            entry_price=20.56,
            stop_loss=19.0,
            target_price=23.0,
            reeval_triggers=TRIGGERS_600026_RETRY,
        )
        anomalies = check_decision_prices(decision, TI_600026, PL_600026, CLOSE_600026)
        assert anomalies == []

    def test_spaced_ma_alias_not_treated_as_price(self):
        """「MA 60」（空格变体，别名表不命中）的 60 不得按价位校验（600845 191% 误报同族）。"""
        decision = _decision(reasoning="股价站稳 MA 60 上方，止损参考 19.0 一线。")
        anomalies = check_decision_prices(decision, TI_600026, PL_600026, CLOSE_600026)
        assert anomalies == []

    def test_count_measure_words_not_treated_as_price(self):
        """「3个月」类量词数字不得按价位校验。"""
        decision = _decision(reasoning="回踩近3个月低点区域后企稳，止损参考 19.0 一线。")
        anomalies = check_decision_prices(decision, TI_600026, PL_600026, CLOSE_600026)
        assert anomalies == []

    def test_institution_count_measure_word_not_treated_as_price(self):
        """「减少超 10 家」的 10 不得当价位提取（赛轮轮胎 10-13 生产实证：
        「机构二季度持股减少超 10 家」的 10 与止损参考带下沿 11.9029
        偏差 15.99% → 报告阻断）。"""
        decision = _decision(reasoning="机构二季度持股减少超 10 家、零正向催化。")
        anomalies = check_decision_prices(decision, TI_600026, PL_600026, CLOSE_600026)
        assert anomalies == []

    def test_real_price_after_measure_word_still_checked(self):
        """护栏：量词数字后的真实价位仍须校验（排除区不得过宽）。

        13.0 与近期低点 14.06 偏差 7.5% 且落在全部参考带外 → SHALL 登记 deviation。
        """
        decision = _decision(reasoning="机构减持超 10 家；若跌破 13.0 支撑则离场观望。")
        anomalies = check_decision_prices(decision, TI_600026, PL_600026, CLOSE_600026)
        assert len(anomalies) == 1
        assert anomalies[0]["kind"] == "deviation"
        assert anomalies[0]["indicator"] == "近期低点"

    def test_price_in_parens_with_context_still_checked(self):
        """护栏：括号内带小数点/上下文的真实价位仍须校验（排除区不得过宽）。

        13.0 与近期低点 14.06 偏差 7.5% 且落在全部参考带外 → SHALL 登记 deviation。
        """
        decision = _decision(reasoning="若跌破（13.0）支撑位则离场观望。")
        anomalies = check_decision_prices(decision, TI_600026, PL_600026, CLOSE_600026)
        assert len(anomalies) == 1
        assert anomalies[0]["kind"] == "deviation"
        assert anomalies[0]["indicator"] == "近期低点"


class TestStructuredTriggerLevels:
    """add-watch-trigger-tracking：结构化触发位（trigger_high/trigger_low）空洞校验。

    上破门槛须严格高于最新收盘、下破门槛须严格低于最新收盘；违反即空洞形态
    anomaly，与文本 empty_trigger 同构、同门禁（risk.py 零改动共用打回预算）。
    """

    def test_trigger_high_not_above_close_is_empty(self):
        decision = _decision(action="watch", trigger_high=22.61, trigger_low=22.0)
        anomalies = check_decision_prices(decision, {}, {}, latest_close=23.03)
        assert any("trigger_high" in a["source_text"] for a in anomalies)
        assert all(a["kind"] == "empty_trigger" for a in anomalies)

    def test_trigger_low_not_below_close_is_empty(self):
        decision = _decision(action="watch", trigger_high=25.0, trigger_low=24.6)
        anomalies = check_decision_prices(decision, {}, {}, latest_close=23.03)
        assert any("trigger_low" in a["source_text"] for a in anomalies)

    def test_valid_band_passes(self):
        decision = _decision(action="watch", trigger_high=24.6, trigger_low=22.91)
        assert check_decision_prices(decision, {}, {}, latest_close=23.03) == []

    def test_missing_triggers_pass(self):
        decision = _decision(action="watch", trigger_high=None, trigger_low=None)
        assert check_decision_prices(decision, {}, {}, latest_close=23.03) == []

    def test_no_latest_close_passes(self):
        decision = _decision(action="watch", trigger_high=24.6, trigger_low=22.91)
        assert check_decision_prices(decision, {}, {}, latest_close=None) == []

    def test_anomaly_shape_mirrors_text_empty_trigger(self):
        """结构化来源 anomaly 与文本 empty_trigger 键完全同构：risk.py 门禁对两类
        来源共用打回预算与 source_text 集合运算，不得因来源不同而异形。"""
        decision = _decision(action="watch", trigger_high=22.61, trigger_low=None)
        anomalies = check_decision_prices(decision, {}, {}, latest_close=23.03)
        assert len(anomalies) == 1
        a = anomalies[0]
        assert set(a) == {
            "kind",
            "source_text",
            "indicator",
            "verified_value",
            "deviation_pct",
            "message",
        }
        assert a["kind"] == "empty_trigger"
        assert a["indicator"] == "最新收盘价"
        assert a["verified_value"] == pytest.approx(23.03)
        assert a["deviation_pct"] is None
        assert "22.61" in a["message"]
        assert "23.03" in a["message"]
        assert "已满足" in a["message"]


# ── 600519 形态夹具（add-horizon-semantics E2E 实证）：近期低点 1170.28，
# 现价 1420 ——「沪深300」的 300 被提为价位偏差 74.37% 阻断报告 ──
TI_600519 = _indicators(
    ma5=1425.0,
    ma10=1418.0,
    ma20=1405.0,
    ma60=1380.0,
    boll_upper=1470.0,
    boll_middle=1415.0,
    boll_lower=1360.0,
)
PL_600519 = {
    "available": True,
    "entry_ref": 1420.0,
    "recent_high": 1560.0,
    "recent_low": 1170.28,
    "atr": 22.0,
    "stop_band_long": {"low": 1370.0, "high": 1395.0},
    "target_band_long": {"low": 1470.0, "high": 1520.0},
    "full_band": [1345.0, 1580.0],
}
CLOSE_600519 = 1420.0


class TestIndexNameFalsePositives:
    """指数名数字不得按个股价位校验（add-horizon-semantics E2E 实证）。

    决策层周期契约注入后，trader/risk_judge/FM reasoning 恒含
    「相对沪深300 超额收益」——300 被提为价位与近期低点 1170.28 偏差
    74.37%（|300-1170.28|/1170.28 精确吻合）→ 打回重试也无法去掉
    （prompt 要求写考核基准）→ 报告阻断（600519 10-11 22:44 实跑
    failure_reason 原文）。量纲守卫 ±10 倍抓不住（300 vs 1170 = 3.9x）。
    """

    def test_benchmark_name_number_not_treated_as_price(self):
        """「沪深300 超额收益」的 300 不进价位校验，真实价位 1180 正常放行。"""
        decision = _decision(
            reasoning=(
                "本决策按 T+20 个交易日相对沪深300 超额收益（±2% 中性带）考核，"
                "催化剂在窗口内兑现：股价回踩 1180 一线企稳。"
            )
        )
        anomalies = check_decision_prices(decision, TI_600519, PL_600519, CLOSE_600519)
        assert anomalies == []

    def test_other_index_prefixes_excluded(self):
        """中证500/上证50 同族指数名一并豁免。"""
        decision = _decision(reasoning="相对中证500 与上证50 的超额口径下，回踩 1180 一线企稳。")
        anomalies = check_decision_prices(decision, TI_600519, PL_600519, CLOSE_600519)
        assert anomalies == []

    def test_real_price_after_index_mention_still_checked(self):
        """护栏：指数名后隔开的真实价位仍须校验（排除区不得过宽）。"""
        decision = _decision(reasoning="相对沪深300 超额考核；若跌破（1130.0）支撑则离场观望。")
        anomalies = check_decision_prices(decision, TI_600519, PL_600519, CLOSE_600519)
        assert len(anomalies) == 1
        assert anomalies[0]["kind"] == "deviation"
        assert anomalies[0]["indicator"] == "近期低点"

    def test_index_name_with_multi_space_not_treated_as_price(self):
        """多空格排版变体「沪深  300」不得穿透指数名豁免（同花顺 10-13 生产实证：
        阻断消息「文本价位 300 与 近期高点 245.48 偏差 22.21%」同形——指数名
        与数字间多一个空格即从豁免区漏出）。"""
        decision = _decision(reasoning="按 T+20 相对沪深  300 超额收益考核，财报已披露。")
        anomalies = check_decision_prices(decision, TI_600519, PL_600519, CLOSE_600519)
        assert anomalies == []

    def test_real_price_after_multi_space_index_mention_still_checked(self):
        """护栏：多空格指数名后隔开的真实价位仍须校验（排除区不得过宽）。"""
        decision = _decision(reasoning="相对沪深  300 超额考核；若跌破（1130.0）支撑则离场观望。")
        anomalies = check_decision_prices(decision, TI_600519, PL_600519, CLOSE_600519)
        assert len(anomalies) == 1
        assert anomalies[0]["kind"] == "deviation"
        assert anomalies[0]["indicator"] == "近期低点"


# ── 300033 形态夹具（2026-10-14 cohort 实跑实证）：MA20 204.157 / MA60 218.87 /
# 近期低点 185.01 / 近期高点 245.48 / 现价与入场参考 190——「T+20」的 20 被提为
# 价位归属近期低点（偏差 89.19%，打回反馈原文），打回重试仍写周期契约标记 →
# 恶化判定 → 报告阻断（cohort no_report_ready 根因）──
TI_300033 = _indicators(
    ma5=197.0,
    ma10=199.0,
    ma20=204.157,
    ma60=218.87,
    boll_upper=212.0,
    boll_middle=199.0,
    boll_lower=186.0,
)
PL_300033 = {
    "available": True,
    "entry_ref": 190.0,
    "recent_high": 245.48,
    "recent_low": 185.01,
    "atr": 6.0,
    "stop_band_long": {"low": 186.0, "high": 188.0},
    "target_band_long": {"low": 210.0, "high": 220.0},
    "full_band": [180.5, 246.0],
}
CLOSE_300033 = 190.0


class TestTPlusTokenFalsePositives:
    """周期/结算记号（T+N）不得按个股价位校验（2026-10-13/14 cohort 实证）。

    agent-prompt-contracts 周期契约要求决策层 reasoning 写「T+20 个交易日」考核
    语义；规范形态被量词后缀（个/日）豁免，但模型自由文本缩写形态（「T+20内」
    「T+20考核」「T+20低概率尾部」「T+2（结算惯例）」）无豁免 → 20/2 被提为
    价位（同花顺 10-14 三处 89.19% 阻断；华夏银行/南方航空/海南机场 10-13
    同族）→ 打回重试仍然写 T+N（契约要求，换不掉）→ 恶化判定 → 报告阻断
    （cohort no_report_ready 根因）。
    """

    def test_production_reasoning_horizon_tokens_zero_anomalies(self):
        """生产打回反馈原文三条触发（T+20考核/T+20内/T+20低概率）SHALL 零 anomaly。"""
        decision = _decision(
            reasoning=(
                "①周期契约层面：T+20考核要求可兑现催化剂。放量收复并站稳MA20 204.16"
                "这一T+20内唯一可兑现的反弹前提未出现；46.23%年化波动下约合1.1个"
                "标准差，属T+20低概率尾部。"
            )
        )
        anomalies = check_decision_prices(decision, TI_300033, PL_300033, CLOSE_300033)
        assert anomalies == []

    def test_settlement_cycle_token_not_extracted(self):
        """「T+2（把执行交给日历而非结构）」的 2 不得当价位提取（海南机场 10-13 实证）。"""
        decision = _decision(reasoning="不接受保守方T+2（把执行交给日历而非结构）。")
        anomalies = check_decision_prices(decision, TI_600845, PL_600845, CLOSE_600845)
        assert anomalies == []

    def test_real_price_after_horizon_token_still_checked(self):
        """护栏：T+N 后的真实价位仍须校验（排除区不得过宽）。

        「T+20内跌破 179.0」的 179.0 与近期低点 185.01 偏差 3.25% 且落在
        参考带外 → SHALL 登记 deviation。
        """
        decision = _decision(reasoning="T+20内跌破 179.0 则重新评估。")
        anomalies = check_decision_prices(decision, TI_300033, PL_300033, CLOSE_300033)
        assert len(anomalies) == 1
        assert anomalies[0]["kind"] == "deviation"
        assert anomalies[0]["indicator"] == "近期低点"


# ── 601058 形态夹具：现价 15.0，近期高点 15.6（2026-10-22 赛轮轮胎阻断实证）──
TI_601058 = _indicators(
    ma5=15.05,
    ma10=15.02,
    ma20=14.90,
    ma60=15.10,
    boll_upper=15.50,
    boll_middle=15.00,
    boll_lower=14.50,
)
PL_601058 = {
    "available": True,
    "entry_ref": 15.00,
    "recent_high": 15.60,
    "recent_low": 10.20,
    "atr": 0.50,
    "stop_band_long": {"low": 14.20, "high": 14.60},
    "target_band_long": {"low": 16.40, "high": 16.80},
    "full_band": [10.20, 16.80],
}
CLOSE_601058 = 15.00

# ── 002916 形态夹具：现价 450，近期高点 459.8（2026-10-06 深南电路阻断实证）──
TI_002916 = _indicators(
    ma5=448.0,
    ma10=445.0,
    ma20=430.0,
    ma60=410.0,
    boll_upper=470.0,
    boll_middle=450.0,
    boll_lower=430.0,
)
PL_002916 = {
    "available": True,
    "entry_ref": 450.0,
    "recent_high": 459.8,
    "recent_low": 380.0,
    "atr": 12.0,
    "stop_band_long": {"low": 430.0, "high": 440.0},
    "target_band_long": {"low": 470.0, "high": 480.0},
    "full_band": [380.0, 480.0],
}
CLOSE_002916 = 450.0

# ── 600029 形态夹具：现价 4.67，止损参考带下沿 4.5314（2026-10-22 南方航空阻断实证）──
TI_600029 = _indicators(
    ma5=4.70,
    ma10=4.68,
    ma20=4.60,
    ma60=4.90,
    boll_upper=4.85,
    boll_middle=4.67,
    boll_lower=4.49,
)
PL_600029 = {
    "available": True,
    "entry_ref": 4.67,
    "recent_high": 5.29,
    "recent_low": 4.20,
    "atr": 0.12,
    "stop_band_long": {"low": 4.5314, "high": 4.62},
    "target_band_long": {"low": 4.80, "high": 4.90},
    "full_band": [4.20, 4.90],
}
CLOSE_600029 = 4.67


class TestScoreSigmaAndReportPeriodFalsePositives:
    """评分/σ 记号/年份+报告期不是价位（2026-10-22 cohort 生产阻断实证）。

    601058 赛轮轮胎 19:09：reasoning 含「系统健康度仅39.2（warning）」，39.2 被
    就近配到近期高点 15.6（偏差 151.28%）→ 报告阻断；600029 南方航空 18:40：
    「20 个交易日 1σ 约 9%」的 1 被配到止损参考带下沿 4.5314（77.93%）→ 阻断；
    002916 深南电路 10-06：「2026中报净利+65.55%」的年份 2026 被配到近期高点
    459.8（340.63%）→ 阻断。三类均属 spec「非价格量纲不进入价位校验」，属实现
    漏排除（意图不变），非决策幻觉。
    """

    def test_health_score_not_price(self):
        """「系统健康度仅39.2（warning）」是 0-100 评分量纲，不得与股价比对。"""
        decision = _decision(
            reasoning="杠杆抬升、自由现金流连续为负，系统健康度仅39.2（warning），仅作下行缓冲。"
        )
        assert check_decision_prices(decision, TI_601058, PL_601058, CLOSE_601058) == []

    def test_sigma_notation_not_price(self):
        """「1σ」「0.22σ」的标准差记号不是价位。"""
        decision = _decision(
            reasoning="按252个交易日折算，20个交易日1σ约9%，参考带内目标空间约0.22σ。"
        )
        assert check_decision_prices(decision, TI_600029, PL_600029, CLOSE_600029) == []

    def test_year_with_report_period_not_price(self):
        """「2026中报」「2025年报」的年份是报告期，不是价位。"""
        decision = _decision(
            reasoning="2026中报净利同比+65.55%，2025年报营收+15.69%，均为存量信息。"
        )
        assert check_decision_prices(decision, TI_002916, PL_002916, CLOSE_002916) == []

    def test_real_price_after_score_still_checked(self):
        """护栏：同段文本里的真实漂移价位仍须校验（排除不得过宽）。"""
        decision = _decision(reasoning="系统健康度仅39.2（warning）；放量站上 18.0 则重估。")
        anomalies = check_decision_prices(decision, TI_601058, PL_601058, CLOSE_601058)
        assert len(anomalies) == 1
        assert anomalies[0]["kind"] == "deviation"
        assert "18" in anomalies[0]["message"]


# ── 自引用夹具：现价 18.1，近期高点 20.58，MA60 18.05 ──
TI_SELFREF = _indicators(
    ma5=18.12,
    ma10=18.09,
    ma20=17.49,
    ma60=18.05,
    boll_upper=18.60,
    boll_middle=18.05,
    boll_lower=17.95,
)
PL_SELFREF = {
    "available": True,
    "entry_ref": 18.10,
    "recent_high": 20.58,
    "recent_low": 15.90,
    "atr": 0.40,
    "stop_band_long": {"low": 16.82, "high": 17.10},
    "target_band_long": {"low": 19.80, "high": 20.50},
    "full_band": [15.90, 21.00],
}
CLOSE_SELFREF = 18.10

# ── 自引用夹具 2：现价 240，近期高点 245.48（300033 同花顺量纲）──
TI_SELFREF2 = _indicators(
    ma5=238.0,
    ma10=236.0,
    ma20=230.0,
    ma60=215.0,
    boll_upper=248.0,
    boll_middle=240.0,
    boll_lower=232.0,
)
PL_SELFREF2 = {
    "available": True,
    "entry_ref": 240.0,
    "recent_high": 245.48,
    "recent_low": 210.0,
    "atr": 6.0,
    "stop_band_long": {"low": 228.0, "high": 232.0},
    "target_band_long": {"low": 250.0, "high": 255.0},
    "full_band": [210.0, 255.0],
}
CLOSE_SELFREF2 = 240.0


class TestSelfReferenceFalsePositives:
    """打回消息自引用不得二次误报（2026-10-05/06/13 生产阻断实证）。

    模型收到打回后会写「此前价位冲突系'MA60'（60日均线）被误读为数值价位60」
    「系统打回消息称『文本价位 300 与近期高点 245.48 偏差 22.21%』」——把上一轮
    的异常数值抄进 reasoning。校验器再扫这段自述，就对着同一个数字二次报警，
    形成「越改越错」的回路（600845 两连阻断、300033/600015 同族）。
    处置：**讨论校验本身的句子**（含 打回/误读/校验/修正/澄清…）不进入价位校验；
    正常行情陈述句（同段「放量突破 260」）仍照常校验。
    """

    def test_meta_sentence_with_ma60_correction_not_reflagged(self):
        decision = _decision(
            reasoning=(
                "此前价位冲突系'MA60'（60日均线）被误读为数值价位60所致，"
                "已澄清：该股近期高点约20.58元，MA60为均线指标而非价位。"
            )
        )
        assert check_decision_prices(decision, TI_SELFREF, PL_SELFREF, CLOSE_SELFREF) == []

    def test_quoted_rejection_message_not_reflagged(self):
        decision = _decision(
            reasoning=(
                "系统打回消息称「文本价位 300 与近期高点 245.48 偏差 22.21%」，"
                "据此把上破触发位改为 246.0。"
            )
        )
        assert check_decision_prices(decision, TI_SELFREF2, PL_SELFREF2, CLOSE_SELFREF2) == []

    def test_normal_trigger_in_same_text_still_checked(self):
        """护栏：同段「放量突破 25.0」是正常行情陈述，SHALL 照常登记偏差。"""
        decision = _decision(reasoning="此前曾被打回一次；现在放量突破 25.0 并站稳则重估。")
        anomalies = check_decision_prices(decision, TI_SELFREF, PL_SELFREF, CLOSE_SELFREF)
        assert len(anomalies) == 1
        assert anomalies[0]["kind"] == "deviation"
        assert "25" in anomalies[0]["message"]

    def test_rejected_proposal_quote_not_reflagged(self):
        """被驳回方案里引用的价位不是终稿主张（601066 10-20 生产实证）。

        reasoning 写「激进方的试探仓（止损 20.5、目标 26.0）…不予采纳」，20.5/26.0
        是**被否决**的第三方方案参数；校验器却把 20.5 当终稿价位、配到最新收盘价
        22.64 偏差 5.04% → 报告阻断。
        """
        decision = _decision(
            reasoning="激进方的试探仓（止损 20.5、目标 26.0）经 20 日 1σ 校验后赔率不成立，不予采纳。"
        )
        assert check_decision_prices(decision, TI_601066, PL_601066, CLOSE_601066) == []
