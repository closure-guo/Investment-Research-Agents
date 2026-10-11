"""TDD tests for metrics/traffic_light.py — 双重阈值红黄绿灯 + 健康度评分。

双重阈值评判：
- 绝对值水平：优良🟢 / 关注🟡 / 警告🔴（各指标阈值不同）
- 同比变化率：<20%🟢 / 20-50%🟡 / >50%🔴
- 最终灯色 = max(绝对值灯, 变化率灯)

评分：四维度各 25 分，🟢=满分 🟡=半分 🔴=零分
85-100=健康 | 60-84=关注 | <60=警告

指标分组：
- 偿债(5): 资产负债率, 流动比率, 速动比率, 利息覆盖倍数, 净债务/EBITDA
- 盈利(5): 毛利率, 净利率, ROE, ROA, ROIC
- 运营(4): 存货周转率, 应收账款周转率, 总资产周转率, 应付账款周转率
- 现金流(6): 经营现金流/净利润, FCF, 资本支出/折旧, 现金流覆盖比率, FCF收益率, 留存现金流比率
"""

from math import isclose

import pytest

from finance_agent.metrics.traffic_light import (
    _apply_safety_floor,
    assess_change_rate,
    assess_traffic_lights,
    compute_health_score,
)

# ── 变化率评判 ──


class TestChangeRate:
    def test_stable(self):
        assert assess_change_rate(0.10) == "green"

    def test_moderate(self):
        assert assess_change_rate(0.30) == "yellow"

    def test_volatile(self):
        assert assess_change_rate(0.60) == "red"

    def test_negative_change(self):
        """负变化率取绝对值评判。"""
        assert assess_change_rate(-0.10) == "green"
        assert assess_change_rate(-0.30) == "yellow"
        assert assess_change_rate(-0.60) == "red"

    def test_boundary_20_percent(self):
        assert assess_change_rate(0.20) == "yellow"

    def test_boundary_50_percent(self):
        assert assess_change_rate(0.50) == "yellow"

    def test_just_above_50(self):
        assert assess_change_rate(0.501) == "red"


# ── 红黄绿灯矩阵 ──


class TestTrafficLights:
    @pytest.fixture
    def sample_metrics(self):
        return {
            "solvency": {
                "资产负债率": {"2024": 35.0, "2023": 40.0, "2022": 45.0},
                "流动比率": {"2024": 2.5, "2023": 2.0, "2022": 1.8},
                "速动比率": {"2024": 2.0, "2023": 1.6, "2022": 1.4},
                "利息覆盖倍数": {"2024": 11.0, "2023": 10.0, "2022": 9.0},
                "净债务/EBITDA": {"2024": -0.08, "2023": 0.5, "2022": 1.0},
            },
            "profitability": {
                "毛利率": {"2024": 40.0, "2023": 38.89, "2022": 37.5},
                "净利率": {"2024": 17.0, "2023": 17.0, "2022": 17.0},
                "ROE": {"2024": 28.33, "2023": 27.82, "2022": 28.33},
                "ROA": {"2024": 17.0, "2023": 17.0, "2022": 17.0},
                "ROIC": {"2024": 23.97, "2023": 22.0, "2022": 20.0},
            },
            "efficiency": {
                "存货周转率": {"2024": 6.32, "2023": 6.47, "2022": 6.58},
                "应收账款周转率": {"2024": None, "2023": None, "2022": None},
                "总资产周转率": {"2024": 1.05, "2023": 1.06, "2022": 1.05},
                "应付账款周转率": {"2024": 10.0, "2023": 11.0, "2022": 11.11},
            },
            "cashflow": {
                "经营现金流/净利润": {"2024": 1.47, "2023": 1.44, "2022": 1.47},
                "FCF": {"2024": 170.0, "2023": 150.0, "2022": 140.0},
                "资本支出/折旧": {"2024": 4.0, "2023": 3.5, "2022": 3.0},
                "现金流覆盖比率": {"2024": 1.7, "2023": 1.5, "2022": 1.4},
                "FCF收益率": {"2024": 0.17, "2023": 0.167, "2022": 0.175},
                "留存现金流比率": {"2024": 0.706, "2023": 0.70, "2022": 0.714},
            },
        }

    def test_returns_all_dimensions(self, sample_metrics):
        result = assess_traffic_lights(sample_metrics)
        expected_dims = {"solvency", "profitability", "efficiency", "cashflow"}
        assert set(result.keys()) == expected_dims

    def test_solvency_debt_ratio_green(self, sample_metrics):
        """资产负债率 35% → 绝对值<40% → 🟢"""
        result = assess_traffic_lights(sample_metrics)
        assert result["solvency"]["资产负债率"]["2024"]["absolute"] == "green"

    def test_solvency_debt_ratio_change(self, sample_metrics):
        """资产负债率 35→40→45, 2024同比=(35-40)/40=-12.5% → 🟢"""
        result = assess_traffic_lights(sample_metrics)
        assert result["solvency"]["资产负债率"]["2024"]["change"] == "green"

    def test_debt_ratio_yellow_abs(self, sample_metrics):
        """修改为 50%（40-65%区间）→ 🟡"""
        m = sample_metrics.copy()
        m["solvency"]["资产负债率"]["2024"] = 50.0
        result = assess_traffic_lights(m)
        assert result["solvency"]["资产负债率"]["2024"]["absolute"] == "yellow"

    def test_debt_ratio_red_abs(self, sample_metrics):
        """修改为 70%（>65%）→ 🔴"""
        m = sample_metrics.copy()
        m["solvency"]["资产负债率"]["2024"] = 70.0
        result = assess_traffic_lights(m)
        assert result["solvency"]["资产负债率"]["2024"]["absolute"] == "red"

    def test_final_is_max(self, sample_metrics):
        """最终灯色 = max(绝对值灯, 变化率灯)。"""
        m = sample_metrics.copy()
        m["solvency"]["资产负债率"]["2024"] = 70.0  # 绝对值🔴
        # 变化率 = (70-40)/40 = 75% → 🔴
        result = assess_traffic_lights(m)
        assert result["solvency"]["资产负债率"]["2024"]["final"] == "red"

    def test_none_metric_skipped(self, sample_metrics):
        """应收账款周转率为 None 时跳过。"""
        result = assess_traffic_lights(sample_metrics)
        # None 值不应有评判结果，或应有明确标记
        entry = result["efficiency"]["应收账款周转率"]["2024"]
        assert entry["final"] is None or "absolute" not in entry or entry["absolute"] is None


# ── 健康度评分 ──


class TestHealthScore:
    def test_all_green(self):
        lights = {
            "solvency": {
                "指标1": {"2024": {"final": "green"}},
                "指标2": {"2024": {"final": "green"}},
                "指标3": {"2024": {"final": "green"}},
                "指标4": {"2024": {"final": "green"}},
                "指标5": {"2024": {"final": "green"}},
            },
            "profitability": {
                "指标1": {"2024": {"final": "green"}},
                "指标2": {"2024": {"final": "green"}},
                "指标3": {"2024": {"final": "green"}},
                "指标4": {"2024": {"final": "green"}},
                "指标5": {"2024": {"final": "green"}},
            },
            "efficiency": {
                "指标1": {"2024": {"final": "green"}},
                "指标2": {"2024": {"final": "green"}},
                "指标3": {"2024": {"final": "green"}},
                "指标4": {"2024": {"final": "green"}},
            },
            "cashflow": {
                "指标1": {"2024": {"final": "green"}},
                "指标2": {"2024": {"final": "green"}},
                "指标3": {"2024": {"final": "green"}},
                "指标4": {"2024": {"final": "green"}},
                "指标5": {"2024": {"final": "green"}},
                "指标6": {"2024": {"final": "green"}},
            },
        }
        score = compute_health_score(lights, "2024")
        assert isclose(score["total"], 100.0)
        assert score["rating"] == "healthy"

    def test_all_red(self):
        lights = {
            "solvency": {
                "指标1": {"2024": {"final": "red"}},
                "指标2": {"2024": {"final": "red"}},
                "指标3": {"2024": {"final": "red"}},
                "指标4": {"2024": {"final": "red"}},
                "指标5": {"2024": {"final": "red"}},
            },
            "profitability": {
                "指标1": {"2024": {"final": "red"}},
                "指标2": {"2024": {"final": "red"}},
                "指标3": {"2024": {"final": "red"}},
                "指标4": {"2024": {"final": "red"}},
                "指标5": {"2024": {"final": "red"}},
            },
            "efficiency": {
                "指标1": {"2024": {"final": "red"}},
                "指标2": {"2024": {"final": "red"}},
                "指标3": {"2024": {"final": "red"}},
                "指标4": {"2024": {"final": "red"}},
            },
            "cashflow": {
                "指标1": {"2024": {"final": "red"}},
                "指标2": {"2024": {"final": "red"}},
                "指标3": {"2024": {"final": "red"}},
                "指标4": {"2024": {"final": "red"}},
                "指标5": {"2024": {"final": "red"}},
                "指标6": {"2024": {"final": "red"}},
            },
        }
        score = compute_health_score(lights, "2024")
        assert isclose(score["total"], 0.0)
        assert score["rating"] == "warning"

    def test_mixed_gives_middle_score(self):
        lights = {
            "solvency": {
                "指标1": {"2024": {"final": "green"}},
                "指标2": {"2024": {"final": "yellow"}},
                "指标3": {"2024": {"final": "red"}},
                "指标4": {"2024": {"final": "green"}},
                "指标5": {"2024": {"final": "yellow"}},
            },
            "profitability": {
                "指标1": {"2024": {"final": "green"}},
                "指标2": {"2024": {"final": "green"}},
                "指标3": {"2024": {"final": "green"}},
                "指标4": {"2024": {"final": "green"}},
                "指标5": {"2024": {"final": "green"}},
            },
            "efficiency": {
                "指标1": {"2024": {"final": "yellow"}},
                "指标2": {"2024": {"final": "yellow"}},
                "指标3": {"2024": {"final": "yellow"}},
                "指标4": {"2024": {"final": "yellow"}},
            },
            "cashflow": {
                "指标1": {"2024": {"final": "red"}},
                "指标2": {"2024": {"final": "red"}},
                "指标3": {"2024": {"final": "red"}},
                "指标4": {"2024": {"final": "red"}},
                "指标5": {"2024": {"final": "red"}},
                "指标6": {"2024": {"final": "red"}},
            },
        }
        score = compute_health_score(lights, "2024")
        # 偿债: (1+0.5+0+1+0.5)/5 * 25 = 15.0
        # 盈利: 5*1.0/5 * 25 = 25.0
        # 运营: 4*0.5/4 * 25 = 12.5
        # 现金流: 0/6 * 25 = 0.0
        # 总计: 15+25+12.5+0 = 52.5
        assert isclose(score["total"], 52.5)
        assert score["rating"] == "warning"

    def test_per_dimension_scores(self):
        lights = {
            "solvency": {
                "指标1": {"2024": {"final": "green"}},
                "指标2": {"2024": {"final": "green"}},
                "指标3": {"2024": {"final": "green"}},
                "指标4": {"2024": {"final": "green"}},
                "指标5": {"2024": {"final": "green"}},
            },
            "profitability": {
                "指标1": {"2024": {"final": "yellow"}},
                "指标2": {"2024": {"final": "yellow"}},
                "指标3": {"2024": {"final": "yellow"}},
                "指标4": {"2024": {"final": "yellow"}},
                "指标5": {"2024": {"final": "yellow"}},
            },
            "efficiency": {
                "指标1": {"2024": {"final": "red"}},
                "指标2": {"2024": {"final": "red"}},
                "指标3": {"2024": {"final": "red"}},
                "指标4": {"2024": {"final": "red"}},
            },
            "cashflow": {
                "指标1": {"2024": {"final": "green"}},
                "指标2": {"2024": {"final": "yellow"}},
                "指标3": {"2024": {"final": "red"}},
                "指标4": {"2024": {"final": "green"}},
                "指标5": {"2024": {"final": "yellow"}},
                "指标6": {"2024": {"final": "red"}},
            },
        }
        score = compute_health_score(lights, "2024")
        assert isclose(score["dimensions"]["solvency"], 25.0)
        assert isclose(score["dimensions"]["profitability"], 12.5)
        assert isclose(score["dimensions"]["efficiency"], 0.0)
        # 现金流: 2绿(2*4.167) + 2黄(2*2.083) + 2红(0) = 12.5
        assert isclose(score["dimensions"]["cashflow"], 12.5)


# ── 安全地板规则 ──


class TestSafetyFloor:
    """绝对值远超优良阈值时，变化率灯色降为绿色。"""

    def test_extreme_value_unchanged_by_decline(self):
        """利息覆盖倍数 3994 vs 8266，下降 51.7%，但 3994 >> 60(6*10)→ 变化率覆盖为绿。"""
        m = {
            "solvency": {
                "利息覆盖倍数": {"2024": 3994.0, "2023": 8266.0},
            },
            "profitability": {},
            "efficiency": {},
            "cashflow": {},
        }
        result = assess_traffic_lights(m)
        assert result["solvency"]["利息覆盖倍数"]["2024"]["change"] == "green"
        assert result["solvency"]["利息覆盖倍数"]["2024"]["final"] == "green"

    def test_not_applied_near_threshold(self):
        """利息覆盖倍数 7 vs 15，下降 53%，但 7 < 60 → 安全地板不生效。"""
        m = {
            "solvency": {
                "利息覆盖倍数": {"2024": 7.0, "2023": 15.0},
            },
            "profitability": {},
            "efficiency": {},
            "cashflow": {},
        }
        result = assess_traffic_lights(m)
        assert result["solvency"]["利息覆盖倍数"]["2024"]["change"] == "red"
        assert result["solvency"]["利息覆盖倍数"]["2024"]["final"] == "red"

    def test_not_applied_when_abs_yellow(self):
        """绝对值黄色时，安全地板不介入。"""
        m = {
            "solvency": {
                "利息覆盖倍数": {"2024": 4.0, "2023": 9.0},
            },
            "profitability": {},
            "efficiency": {},
            "cashflow": {},
        }
        result = assess_traffic_lights(m)
        assert result["solvency"]["利息覆盖倍数"]["2024"]["absolute"] == "yellow"
        # 变化率 (4-9)/9 = -55.6% → red
        assert result["solvency"]["利息覆盖倍数"]["2024"]["change"] == "red"

    def test_applied_for_lower_is_better(self):
        """资产负债率 1.0% (green_thresh=40, 40/10=4, 1.0<=4) → 覆盖。"""
        m = {
            "solvency": {
                "资产负债率": {"2024": 1.0, "2023": 3.0},
            },
            "profitability": {},
            "efficiency": {},
            "cashflow": {},
        }
        result = assess_traffic_lights(m)
        assert result["solvency"]["资产负债率"]["2024"]["change"] == "green"
        assert result["solvency"]["资产负债率"]["2024"]["final"] == "green"

    def test_preserves_green_change(self):
        """变化率已是绿色时直接返回。"""
        result = _apply_safety_floor("利息覆盖倍数", 3994.0, "green", "green")
        assert result == "green"

    def test_zero_green_threshold_skip(self):
        """净债务/EBITDA green_threshold=0，跳过安全地板。"""
        # green_thresh=0, higher_is_better=False → 除以零风险，应跳过
        result = _apply_safety_floor("净债务/EBITDA", -0.5, "green", "red")
        assert result == "red"


class TestIndustryOverride:
    """行业特定阈值覆盖。"""

    def test_liquor_inventory_turnover(self):
        """白酒行业存货周转率 0.3 次 → 行业覆盖阈值下应为 🟢。"""
        m = {
            "efficiency": {
                "存货周转率": {"2024": 0.3, "2023": 0.28},
            },
            "solvency": {},
            "profitability": {},
            "cashflow": {},
        }
        # 无行业 → 通用阈值 (>=5🟢) → 0.3 < 2 → 🔴
        result_generic = assess_traffic_lights(m)
        assert result_generic["efficiency"]["存货周转率"]["2024"]["absolute"] == "red"

        # 白酒行业 → 覆盖阈值 (>=0.5🟢) → 0.3 >= 0.2 且 < 0.5 → 🟡
        result_liquor = assess_traffic_lights(m, industry="白酒")
        assert result_liquor["efficiency"]["存货周转率"]["2024"]["absolute"] == "yellow"


class TestSemiconductorEquipmentCoverage:
    """Task 9：半导体设备行业阈值覆盖 + health_score 口径披露。"""

    def test_inventory_turnover_industry_thresholds(self):
        from finance_agent.metrics.traffic_light import _assess_absolute

        # 拓荆 0.56：通用阈值红灯，行业覆盖黄灯（>=0.5）
        assert _assess_absolute("存货周转率", 0.56, industry="半导体设备") == "yellow"
        assert _assess_absolute("存货周转率", 0.56, industry=None) == "red"

    def test_quick_ratio_industry_thresholds(self):
        from finance_agent.metrics.traffic_light import _assess_absolute

        assert _assess_absolute("速动比率", 0.74, industry="半导体设备") == "yellow"
        assert _assess_absolute("速动比率", 0.30, industry="半导体设备") == "red"

    def test_ap_turnover_industry_thresholds(self):
        from finance_agent.metrics.traffic_light import _assess_absolute

        assert _assess_absolute("应付账款周转率", 2.45, industry="半导体设备") == "yellow"

    def test_matched_overrides_listing(self):
        from finance_agent.metrics.traffic_light import matched_industry_overrides

        m = matched_industry_overrides("半导体设备")
        assert set(m) == {"存货周转率", "速动比率", "应付账款周转率"}
        assert matched_industry_overrides("白酒")["存货周转率"] == (0.5, 0.2, True)
        assert matched_industry_overrides(None) == {}

    def test_health_score_carries_industry_override(self):
        from finance_agent.metrics.traffic_light import compute_health_score

        lights = {
            "solvency": {
                "速动比率": {"2025": {"absolute": "yellow", "change": None, "final": "yellow"}}
            }
        }
        hs = compute_health_score(lights, "2025", industry="半导体设备")
        assert hs["industry_override"]["industry"] == "半导体设备"
        assert "速动比率" in hs["industry_override"]["metrics"]

        hs_generic = compute_health_score(lights, "2025", industry=None)
        assert hs_generic["industry_override"]["industry"] is None
        assert hs_generic["industry_override"]["metrics"] == []


class TestCoverageFallthroughUncoveredMetric:
    """终审 M2：spec Scenario「未命中指标沿用通用阈值」的组合原义用例。"""

    def test_uncovered_metric_uses_generic_thresholds_under_industry(self):
        from finance_agent.metrics.traffic_light import _assess_absolute

        # ROE 无半导体设备覆盖 → 与通用阈值同判（16→green，9→yellow）
        assert _assess_absolute("ROE", 16.0, industry="半导体设备") == "green"
        assert _assess_absolute("ROE", 16.0, industry=None) == "green"
        assert _assess_absolute("ROE", 9.0, industry="半导体设备") == "yellow"
        assert _assess_absolute("ROE", 9.0, industry=None) == "yellow"


# ── 银行业口径覆盖（add-banking-industry-calibration，issue #241）──


class TestBankingIndustryCalibration:
    """银行业阈值覆盖：不适用排除 + 换阈值 + 维度剔除满分缩放。

    光大 601818 形态：负债率 91% 通用阈值恒红（死规则）、ROA 0.61% 通用阈值恒红、
    OCF/净利 4.16 被当含金量信号——全部为银行商业模式误判。
    """

    @pytest.fixture
    def bank_metrics(self):
        return {
            "solvency": {
                "资产负债率": {"2024": 91.0, "2023": 91.5},
                "流动比率": {"2024": None, "2023": None},
                "速动比率": {"2024": None, "2023": None},
            },
            "profitability": {
                "毛利率": {"2024": None, "2023": None},
                "净利率": {"2024": 30.6, "2023": 29.8},
                "ROE": {"2024": 9.0, "2023": 8.7},
                "ROA": {"2024": 0.61, "2023": 0.58},
                "ROIC": {"2024": 3.2, "2023": 3.1},
            },
            "efficiency": {
                "总资产周转率": {"2024": 0.03, "2023": 0.03},
                "存货周转率": {"2024": None, "2023": None},
            },
            "cashflow": {
                "经营现金流/净利润": {"2024": 4.16, "2023": 3.9},
                "资本支出/折旧": {"2024": 2.0, "2023": 1.8},
            },
        }

    def test_is_banking_industry(self):
        from finance_agent.metrics.traffic_light import is_banking_industry

        assert is_banking_industry("银行") is True
        assert is_banking_industry("国有大型银行") is True
        assert is_banking_industry("货币金融服务") is True  # cninfo 降级形态
        assert is_banking_industry("白酒") is False
        assert is_banking_industry(None) is False

    def test_bank_debt_ratio_excluded_not_red(self, bank_metrics):
        """负债率 91% 对银行不适用：全 None，MUST NOT 用 (40, 65) 评红。"""
        result = assess_traffic_lights(bank_metrics, industry="银行")
        entry = result["solvency"]["资产负债率"]["2024"]
        assert entry["absolute"] is None and entry["change"] is None
        assert entry["final"] is None

    def test_bank_roe_new_threshold(self, bank_metrics):
        """ROE 9.0%：通用 (15, 8) 下黄（>8），银行业 (13, 6) 下仍黄——语义钉死。"""
        result = assess_traffic_lights(bank_metrics, industry="银行")
        assert result["profitability"]["ROE"]["2024"]["absolute"] == "yellow"

    def test_bank_roa_new_threshold(self, bank_metrics):
        """ROA 0.61%：通用 (10, 3) 恒红 → 银行业 (0.9, 0.5) 黄。"""
        result = assess_traffic_lights(bank_metrics, industry="银行")
        assert result["profitability"]["ROA"]["2024"]["absolute"] == "yellow"
        # 非银行业同值仍红（零回归对照）
        result_generic = assess_traffic_lights(bank_metrics)
        assert result_generic["profitability"]["ROA"]["2024"]["absolute"] == "red"

    def test_bank_ocf_multiple_excluded(self, bank_metrics):
        """OCF/净利 4.16 对银行不适用——MUST NOT 产出绿灯被引为含金量高。"""
        result = assess_traffic_lights(bank_metrics, industry="银行")
        assert result["cashflow"]["经营现金流/净利润"]["2024"]["final"] is None

    def test_bank_capex_dep_still_assessed(self, bank_metrics):
        """资本支出/折旧不经 OCF——银行业保留评灯。"""
        result = assess_traffic_lights(bank_metrics, industry="银行")
        assert result["cashflow"]["资本支出/折旧"]["2024"]["final"] is not None

    def test_non_bank_debt_ratio_unchanged(self, bank_metrics):
        """非银行业负债率 91% 仍按通用阈值红（零回归）。"""
        result = assess_traffic_lights(bank_metrics, industry="白酒")
        assert result["solvency"]["资产负债率"]["2024"]["absolute"] == "red"

    def test_bank_health_cap50_and_scaled_bands(self, bank_metrics):
        """偿债/效率两维全排除 → 满分 50，rating 阈值缩放 42.5/30。"""
        lights = assess_traffic_lights(bank_metrics, industry="银行")
        health = compute_health_score(lights, "2024", industry="银行")
        assert health["score_cap"] == 50
        # 全绿形态：盈利（净利率/ROE/ROA 满灯）+ 现金流（资本支出/折旧）→ 50/50 → healthy
        all_green = {
            dim: {
                name: {"2024": {"final": "green" if v is not None else None}}
                for name, v in metrics.items()
            }
            for dim, metrics in bank_metrics.items()
        }
        health_green = compute_health_score(all_green, "2024", industry="银行")
        assert health_green["score_cap"] == 50
        assert health_green["total"] == 50.0
        assert health_green["rating"] == "healthy"

    def test_generic_health_cap100_unchanged(self, bank_metrics):
        """非银行业满分恒 100、阈值恒 85/60（零回归）。"""
        all_green = {
            dim: {
                name: {"2024": {"final": "green" if v is not None else None}}
                for name, v in metrics.items()
            }
            for dim, metrics in bank_metrics.items()
        }
        health = compute_health_score(all_green, "2024")
        assert health["score_cap"] == 100
        assert health["rating"] == "healthy"

    def test_data_missing_dim_not_excluded(self):
        """数据缺失维度（非行业排除）不触发剔除：记 0 分、满分维持 100。"""
        lights = {
            "solvency": {"资产负债率": {"2024": {"final": None}}},
            "profitability": {"净利率": {"2024": {"final": "green"}}},
            "efficiency": {"总资产周转率": {"2024": {"final": None}}},
            "cashflow": {"FCF": {"2024": {"final": "green"}}},
        }
        health = compute_health_score(lights, "2024", industry="白酒")
        assert health["score_cap"] == 100
        assert health["total"] == pytest.approx(50.0)
