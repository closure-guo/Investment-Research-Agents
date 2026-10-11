"""TDD tests for metrics/garp.py — GARP 筛选。

GARP = Growth at a Reasonable Price
条件：PE < 行业平均 ∧ 净利润增长率 > 15% ∧ ROE > 15% ∧ 负债率 < 60%
全部满足 → pass, 否则 → fail（附带哪些条件未满足）
"""

import numpy as np
import pytest

from finance_agent.metrics.garp import calc_garp


class TestGARP:
    @pytest.fixture
    def passing_data(self):
        return {
            "PE": 20.0,
            "industry_avg_PE": 25.0,
            "net_profit_growth": 0.25,
            "ROE": 0.20,
            "debt_ratio": 0.45,
        }

    def test_all_conditions_met(self, passing_data):
        result = calc_garp(passing_data)
        assert result["pass"] is True
        assert len(result["failures"]) == 0

    def test_pe_too_high(self, passing_data):
        passing_data["PE"] = 30.0  # > industry_avg_PE=25
        result = calc_garp(passing_data)
        assert result["pass"] is False
        assert "PE >= 行业平均" in result["failures"]

    def test_growth_too_low(self, passing_data):
        passing_data["net_profit_growth"] = 0.10  # < 15%
        result = calc_garp(passing_data)
        assert result["pass"] is False
        assert "净利润增长率 <= 15%" in result["failures"]

    def test_roe_too_low(self, passing_data):
        passing_data["ROE"] = 0.10  # < 15%
        result = calc_garp(passing_data)
        assert result["pass"] is False
        assert "ROE <= 15%" in result["failures"]

    def test_debt_too_high(self, passing_data):
        passing_data["debt_ratio"] = 0.65  # > 60%
        result = calc_garp(passing_data)
        assert result["pass"] is False
        assert "负债率 >= 60%" in result["failures"]

    def test_multiple_failures(self, passing_data):
        passing_data["PE"] = 30.0
        passing_data["ROE"] = 0.10
        result = calc_garp(passing_data)
        assert result["pass"] is False
        assert len(result["failures"]) == 2

    def test_none_values(self):
        result = calc_garp(
            {
                "PE": None,
                "industry_avg_PE": 25.0,
                "net_profit_growth": None,
                "ROE": 0.20,
                "debt_ratio": 0.45,
            }
        )
        assert result["pass"] is False
        assert len(result["failures"]) == 2  # PE None and growth None

    def test_pe_missing_honest_message(self):
        # 数据缺失 ≠ 比较失败：缺 PE 不得谎报「PE >= 行业平均」
        data = {
            "PE": None,
            "industry_avg_PE": 25.0,
            "net_profit_growth": 0.25,
            "ROE": 0.20,
            "debt_ratio": 0.45,
        }
        result = calc_garp(data)
        assert "PE 数据缺失（未参与比较）" in result["failures"]
        assert "PE >= 行业平均" not in result["failures"]
        assert result["details"]["PE_missing"] is True

    def test_industry_pe_missing_honest_message(self):
        data = {
            "PE": 20.0,
            "industry_avg_PE": None,
            "net_profit_growth": 0.25,
            "ROE": 0.20,
            "debt_ratio": 0.45,
        }
        result = calc_garp(data)
        assert "行业平均 PE 数据缺失（未参与比较）" in result["failures"]
        assert result["details"]["PE_missing"] is True
        # PE 本身有值 → 口径一并记录（未传时为 None）
        assert result["details"]["PE_caliber"] is None

    def test_real_comparison_failure_kept(self):
        # 真实比较失败仍保留原文案，且不标 missing
        data = {
            "PE": 30.0,
            "industry_avg_PE": 25.0,
            "net_profit_growth": 0.25,
            "ROE": 0.20,
            "debt_ratio": 0.45,
        }
        result = calc_garp(data)
        assert "PE >= 行业平均" in result["failures"]
        assert "PE_missing" not in result["details"]
        # 口径未传时 PE 有值分支仍写入 PE_caliber，值为 None（锁定约定）
        assert result["details"]["PE_caliber"] is None

    def test_pe_caliber_recorded_when_present(self):
        # spec：details SHALL 记录 PE 与口径——PE 有值分支透传 PE_caliber
        data = {
            "PE": 20.0,
            "industry_avg_PE": 25.0,
            "PE_caliber": "static",
            "net_profit_growth": 0.25,
            "ROE": 0.20,
            "debt_ratio": 0.45,
        }
        result = calc_garp(data)
        assert result["details"]["PE"] == 20.0
        assert result["details"]["PE_caliber"] == "static"

    def test_boundary_values(self):
        # Exactly at boundaries should pass
        result = calc_garp(
            {
                "PE": 25.0,  # == industry_avg → should NOT pass (need <)
                "industry_avg_PE": 25.0,
                "net_profit_growth": 0.15,  # == 15% → should NOT pass (need >)
                "ROE": 0.15,  # == 15% → should NOT pass (need >)
                "debt_ratio": 0.60,  # == 60% → should NOT pass (need <)
            }
        )
        assert result["pass"] is False
        assert len(result["failures"]) == 4


class TestGarpHonestBucketAllInputs:
    """delta clear-valuation-chain-debts D1：诚实分桶从 PE 推广到全部输入。"""

    def _data(self, **overrides):
        base = {
            "PE": 20.0,
            "industry_avg_PE": 25.0,
            "net_profit_growth": 0.25,
            "ROE": 0.20,
            "debt_ratio": 0.45,
        }
        base.update(overrides)
        return base

    def test_roe_missing_honest(self):
        result = calc_garp(self._data(ROE=None))
        assert "ROE 数据缺失（未参与比较）" in result["failures"]
        assert "ROE <= 15%" not in result["failures"]
        assert result["details"]["ROE_missing"] is True

    def test_debt_nan_treated_as_missing_not_pass(self):
        result = calc_garp(self._data(debt_ratio=float("nan")))
        assert "负债率 数据缺失（未参与比较）" in result["failures"]
        assert "负债率 >= 60%" not in result["failures"]
        assert result["details"]["负债率_missing"] is True

    def test_growth_missing_honest(self):
        result = calc_garp(self._data(net_profit_growth=None))
        assert "净利润增长率 数据缺失（未参与比较）" in result["failures"]
        assert "净利润增长率 <= 15%" not in result["failures"]
        assert result["details"]["净利润增长率_missing"] is True

    def test_real_comparison_failures_unchanged(self):
        result = calc_garp(self._data(net_profit_growth=0.10, ROE=0.10, debt_ratio=0.70))
        assert "净利润增长率 <= 15%" in result["failures"]
        assert "ROE <= 15%" in result["failures"]
        assert "负债率 >= 60%" in result["failures"]
        for key in ("净利润增长率_missing", "ROE_missing", "负债率_missing"):
            assert key not in result["details"]


class TestCleanNumNumpyScalars:
    """终审 nit 收口：numpy 标量 NaN（np.float32/np.float64）不得穿透守卫伪装通过。"""

    @pytest.mark.parametrize("nan", [float("nan"), np.float32("nan"), np.float64("nan")])
    def test_numpy_nan_debt_treated_as_missing(self, nan):
        data = {
            "PE": 20.0,
            "industry_avg_PE": 25.0,
            "net_profit_growth": 0.25,
            "ROE": 0.20,
            "debt_ratio": nan,
        }
        result = calc_garp(data)
        assert "负债率 数据缺失（未参与比较）" in result["failures"]
        assert result["details"]["负债率_missing"] is True


# ── 银行业负债率豁免（add-banking-industry-calibration，issue #241）──


class TestGARPBankingExemption:
    """银行业负债率行业不适用：退出 failures、保留数值、不吞缺失分桶。"""

    def test_bank_debt_ratio_industry_excluded(self):
        from finance_agent.metrics.garp import calc_garp

        result = calc_garp(
            {
                "PE": 5.0,
                "industry_avg_PE": 6.0,
                "net_profit_growth": 0.05,
                "ROE": 0.09,
                "debt_ratio": 0.91,
                "industry": "银行",
            }
        )
        assert not any("负债率" in f for f in result["failures"])
        assert result["details"].get("负债率_行业不适用") is True
        assert result["details"].get("负债率") == 0.91

    def test_non_bank_debt_ratio_still_fails(self):
        from finance_agent.metrics.garp import calc_garp

        result = calc_garp(
            {
                "PE": 5.0,
                "industry_avg_PE": 6.0,
                "net_profit_growth": 0.05,
                "ROE": 0.09,
                "debt_ratio": 0.91,
                "industry": "白酒",
            }
        )
        assert "负债率 >= 60%" in result["failures"]

    def test_bank_debt_nan_still_missing_bucket(self):
        from finance_agent.metrics.garp import calc_garp

        result = calc_garp(
            {
                "PE": 5.0,
                "industry_avg_PE": 6.0,
                "net_profit_growth": 0.05,
                "ROE": 0.09,
                "debt_ratio": float("nan"),
                "industry": "银行",
            }
        )
        assert "负债率 数据缺失（未参与比较）" in result["failures"]
        assert result["details"].get("负债率_missing") is True
