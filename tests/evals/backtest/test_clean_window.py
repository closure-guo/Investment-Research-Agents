"""干净窗口判定 + 报告四段披露组装 + 结论句式三态（delta add-backtest-leakage-controls）。

纯单元：基准日期序列用构造帧注入，零网络零 LLM。
"""

import pandas as pd
import pytest
from evals.backtest.report import (
    ALL_REGIMES,
    assert_clean_window,
    build_conclusion,
    build_disclosures,
    resolve_positioning,
)
from evals.causal_ablation.preregister import OUTCOME_REQUIRED_FIELDS, parse_preregister


def _benchmark(dates: list[str]) -> pd.DataFrame:
    return pd.DataFrame({"日期": dates, "收盘": [100.0] * len(dates)})


def _days(start: str, n: int) -> list[str]:
    return pd.date_range(start, periods=n, freq="D").strftime("%Y-%m-%d").tolist()


def _valid_preregistration():
    text = "\n".join(
        [
            "- 主指标: 逐决策 T+20 相对沪深300 超额收益均值与胜率",
            "- MDE: n=30 → 5.1pp（换算依据见 §4）",
            "- 决策阈值: 均值超额 95% CI 下限 > 0；依据：标的簇 bootstrap CI",
            "- 样本量依据: forward ≥10 / ≥30 / ≥100（MDE 反算）",
            "- 停止规则: 健康检查不过作废；探针 >0.60 降级",
            "- 成本分型: forward 每标的 1 次 deep；回测 回放 ×3 + 探针",
            "- 泄漏控制: 干净窗口 + 探针披露（阈值 0.60）",
        ]
    )
    return parse_preregister(text, required_fields=OUTCOME_REQUIRED_FIELDS)


class TestAssertCleanWindow:
    def test_all_decision_dates_far_enough_passed(self):
        bench = _benchmark(_days("2024-01-01", 28))
        result = assert_clean_window(
            ["2024-01-01", "2024-01-02"], as_of="2024-01-28", window_days=20, benchmark=bench
        )
        assert result["passed"] is True
        assert isinstance(result["reason"], str) and result["reason"]

    def test_exactly_window_days_trading_days_passes(self):
        # 2024-01-01 之后到 2024-01-21 共 20 个交易日 → 恰好达标
        bench = _benchmark(_days("2024-01-01", 21))
        result = assert_clean_window(
            ["2024-01-01"], as_of="2024-01-21", window_days=20, benchmark=bench
        )
        assert result["passed"] is True

    def test_one_decision_date_too_close_fails_with_date_in_reason(self):
        bench = _benchmark(_days("2024-01-01", 28))
        result = assert_clean_window(
            ["2024-01-01", "2024-01-25"], as_of="2024-01-28", window_days=20, benchmark=bench
        )
        assert result["passed"] is False
        assert "2024-01-25" in result["reason"]
        # 达标的决策日不列入失败原因
        assert "2024-01-01" not in result["reason"]

    def test_decision_date_after_as_of_fails(self):
        bench = _benchmark(_days("2024-01-01", 30))
        result = assert_clean_window(
            ["2024-02-01"], as_of="2024-01-28", window_days=20, benchmark=bench
        )
        assert result["passed"] is False
        assert "2024-02-01" in result["reason"]

    def test_missing_benchmark_fails_conservatively(self, monkeypatch):
        # 基准不可得 → 无法判定交易日距离 → 保守判不通过（不静默放行）
        monkeypatch.setattr("evals.backtest.report._load_benchmark", lambda: None)
        result = assert_clean_window(["2024-01-01"], as_of="2024-02-01", window_days=20)
        assert result["passed"] is False
        assert result["reason"]

    def test_default_benchmark_loaded_when_not_injected(self, monkeypatch):
        bench = _benchmark(_days("2024-01-01", 28))
        monkeypatch.setattr("evals.backtest.report._load_benchmark", lambda: bench)
        result = assert_clean_window(["2024-01-01"], as_of="2024-01-28", window_days=20)
        assert result["passed"] is True

    def test_empty_decision_dates_fails_no_sample(self):
        """空样本不得真空真（D3）：无样本可判 → passed False + reason「无样本可判」。"""
        bench = _benchmark(_days("2024-01-01", 28))
        result = assert_clean_window([], as_of="2024-01-28", window_days=20, benchmark=bench)
        assert result["passed"] is False
        assert "无样本可判" in result["reason"]


class TestBuildDisclosures:
    def test_five_keys_with_preregister_path_and_validity(self):
        prereg = _valid_preregistration()
        disclosures = build_disclosures(
            preregistration=prereg,
            clean_window={"passed": True, "reason": "ok"},
            probe={
                "probe_n": 10,
                "questions_per_ticker": 3,
                "direction_hit_rate": 0.4,
                "magnitude_hit_rate": 0.3,
                "event_hit_rate": 0.5,
                "unknown_ratio": 0.1,
                "threshold": 0.60,
                "downgraded": False,
            },
            regime_covered=["bull", "bear"],
            regime_limited=True,
            batch_kind="formal",
        )
        assert set(disclosures) == {
            "batch_kind",
            "preregister",
            "clean_window",
            "leakage_probe",
            "regime_coverage",
        }
        assert disclosures["batch_kind"] == "formal"
        assert disclosures["preregister"]["valid"] is True
        assert disclosures["preregister"]["path"]
        assert disclosures["regime_coverage"]["covered"] == ["bear", "bull"]
        assert disclosures["regime_coverage"]["limited"] is True

    def test_probe_state_below_threshold_is_measurable(self):
        d = build_disclosures(probe={"direction_hit_rate": 0.4, "downgraded": False})
        assert d["leakage_probe"]["state"] == "measurable"
        assert d["leakage_probe"]["downgraded"] is False

    def test_probe_state_over_threshold_is_downgraded(self):
        d = build_disclosures(probe={"direction_hit_rate": 0.9, "downgraded": True})
        assert d["leakage_probe"]["state"] == "downgraded"
        assert d["leakage_probe"]["downgraded"] is True

    def test_probe_state_none_rate_is_unmeasurable(self):
        d = build_disclosures(probe={"direction_hit_rate": None, "downgraded": False})
        assert d["leakage_probe"]["state"] == "unmeasurable"
        assert d["leakage_probe"]["direction_hit_rate"] is None
        assert d["leakage_probe"]["downgraded"] is False

    def test_probe_missing_is_disclosed_not_silent(self):
        d = build_disclosures(probe=None)
        assert d["leakage_probe"]["state"] == "missing"

    def test_full_regime_coverage_not_limited(self):
        d = build_disclosures(regime_covered=list(ALL_REGIMES))
        assert d["regime_coverage"]["limited"] is False


class TestResolvePositioning:
    def test_formal_clean_measurable_is_skill(self):
        assert (
            resolve_positioning(
                batch_kind="formal",
                clean_window={"passed": True, "reason": "ok"},
                probe={"direction_hit_rate": 0.3, "downgraded": False},
            )
            == "skill"
        )

    def test_formal_downgraded_probe_still_skill_positioning(self):
        # 超阈是「降级为上界证据」，仍属可下结论的正式批
        assert (
            resolve_positioning(
                batch_kind="formal",
                clean_window={"passed": True, "reason": "ok"},
                probe={"direction_hit_rate": 0.9, "downgraded": True},
            )
            == "skill"
        )

    @pytest.mark.parametrize(
        "batch_kind,clean_window,probe",
        [
            ("pathway", {"passed": True, "reason": "ok"}, {"direction_hit_rate": 0.3}),
            ("formal", {"passed": False, "reason": "太近"}, {"direction_hit_rate": 0.3}),
            ("formal", {"passed": True, "reason": "ok"}, {"direction_hit_rate": None}),
            ("formal", {"passed": True, "reason": "ok"}, None),
        ],
    )
    def test_pathway_cases(self, batch_kind, clean_window, probe):
        assert (
            resolve_positioning(batch_kind=batch_kind, clean_window=clean_window, probe=probe)
            == "pathway"
        )


class TestBuildConclusionThreeStates:
    BASE = "显著优于基线"

    def test_measurable_below_threshold_keeps_skill_sentence(self):
        sentence = build_conclusion(
            self.BASE,
            batch_kind="formal",
            clean_window={"passed": True, "reason": "ok"},
            probe={"direction_hit_rate": 0.3, "downgraded": False, "threshold": 0.6},
        )
        assert sentence == self.BASE
        assert "通路验证" not in sentence

    def test_downgraded_uses_upper_bound_wording(self):
        sentence = build_conclusion(
            self.BASE,
            batch_kind="formal",
            clean_window={"passed": True, "reason": "ok"},
            probe={
                "direction_hit_rate": 0.9,
                "downgraded": True,
                "threshold": 0.6,
                "probe_n": 10,
            },
        )
        assert "上界证据" in sentence
        assert "赚钱能力主张成立" not in sentence

    def test_unmeasurable_has_no_skill_sentence(self):
        sentence = build_conclusion(
            self.BASE,
            batch_kind="formal",
            clean_window={"passed": True, "reason": "ok"},
            probe={"direction_hit_rate": None, "downgraded": False},
        )
        assert "通路验证" in sentence
        assert "探针不可测" in sentence
        assert "显著为正" not in sentence
        assert "显著为负" not in sentence
        assert "赚钱能力主张成立" not in sentence

    def test_pathway_strips_skill_sentence(self):
        sentence = build_conclusion(
            self.BASE,
            batch_kind="pathway",
            clean_window={"passed": True, "reason": "ok"},
            probe={"direction_hit_rate": 0.3, "downgraded": False},
        )
        assert "通路验证" in sentence
        assert "显著为正" not in sentence
        assert "显著为负" not in sentence

    def test_sample_insufficient_conclusion_preserved(self):
        # 无读数可下结论时保持旧句（不得凭空加定位，避免与红线「样本积累中」混淆）
        sentence = build_conclusion(
            "样本不足，无法判定",
            batch_kind="pathway",
            clean_window=None,
            probe=None,
        )
        assert sentence == "样本不足，无法判定"

    def test_failed_clean_window_degrades_to_pathway(self):
        sentence = build_conclusion(
            self.BASE,
            batch_kind="formal",
            clean_window={"passed": False, "reason": "决策日 2024-01-25 不足窗口"},
            probe={"direction_hit_rate": 0.3, "downgraded": False},
        )
        assert "通路验证" in sentence
        assert "显著为正" not in sentence

    def test_sanity_invalid_overrides(self):
        sentence = build_conclusion(
            self.BASE,
            batch_kind="formal",
            clean_window={"passed": True, "reason": "ok"},
            probe={"direction_hit_rate": 0.3, "downgraded": False},
            sanity="invalid",
        )
        assert sentence.startswith("invalid")

    def test_regime_limited_sentence_restricts_conclusion(self):
        sentence = build_conclusion(
            self.BASE,
            batch_kind="formal",
            clean_window={"passed": True, "reason": "ok"},
            probe={"direction_hit_rate": 0.3, "downgraded": False},
            regime_covered=["sideways"],
            regime_limited=True,
        )
        assert "仅覆盖" in sentence
        assert "sideways" in sentence
        assert "不外推" in sentence


class TestTradingDayCaliber:
    """干净窗口以**交易日**计（spec 条文），不是自然日。

    判别性构造：基准帧里挖掉一段日期，使「自然日差 ≥ window_days」但
    「交易日数 < window_days」——自然日实现会误判通过。变异实证：把
    `_trading_days_between` 改成 `(as_of - decision).days` 后本用例 RED。
    """

    def test_calendar_days_pass_but_trading_days_fail(self):
        # 决策日 2024-01-01；基准只在 01-02..01-06 有 5 个交易日，然后跳到 03-01
        # 自然日差 = 60 天（≥20），交易日数 = 6（<20）→ 必须判不通过
        bench = _benchmark(
            ["2024-01-02", "2024-01-03", "2024-01-04", "2024-01-05", "2024-01-06", "2024-03-01"]
        )
        result = assert_clean_window(
            ["2024-01-01"], as_of="2024-03-01", window_days=20, benchmark=bench
        )
        assert result["passed"] is False
        assert "仅 6 个交易日" in result["reason"]

    def test_calendar_days_fail_but_trading_days_pass(self):
        # 反向构造：基准每个自然日都是交易日（含周末）→ 交易日数与自然日数一致；
        # 此处只断言「交易日口径下按序列计数」——25 个交易日的连续序列必然通过
        bench = _benchmark(_days("2024-01-01", 26))
        result = assert_clean_window(
            ["2024-01-01"], as_of="2024-01-26", window_days=20, benchmark=bench
        )
        assert result["passed"] is True
        assert "最少 25" in result["reason"]


class TestDowngradedConclusionKeepsRegimeLimit:
    """降级句仍是结论句：regime 限定不得因换用降级句式而丢失（spec
    「干净窗口下的覆盖限定」）。变异实证：降级分支去掉 regime 后缀 → 本用例 RED。"""

    BASE = "无显著差异"

    def test_downgraded_sentence_still_carries_regime_limit(self):
        sentence = build_conclusion(
            self.BASE,
            batch_kind="formal",
            clean_window={"passed": True, "reason": "ok"},
            probe={"direction_hit_rate": 0.9, "downgraded": True, "threshold": 0.6, "probe_n": 12},
            regime_covered=["sideways"],
            regime_limited=True,
        )
        assert "上界证据" in sentence
        assert "仅覆盖" in sentence
        assert "sideways" in sentence
        assert "不外推" in sentence

    def test_downgraded_sentence_without_limit_has_no_regime_clause(self):
        sentence = build_conclusion(
            self.BASE,
            batch_kind="formal",
            clean_window={"passed": True, "reason": "ok"},
            probe={"direction_hit_rate": 0.9, "downgraded": True, "threshold": 0.6, "probe_n": 12},
            regime_covered=list(ALL_REGIMES),
            regime_limited=False,
        )
        assert "上界证据" in sentence
        assert "仅覆盖" not in sentence
