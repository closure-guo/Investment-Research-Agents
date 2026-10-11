"""回测深历史执法（issue #172 / decision-backtest「深历史批次定位标注」）。

metrics.md §2 泄漏控制切点（Δ4）：深历史（2025 年前样本）永久定位通路验证，
不产 skill 结论。修复前 resolve_positioning 只查 batch_kind/clean_window/probe，
深历史 formal 批仍拿 skill 定位；stratified_sample 从 1500 天历史起点扫描，
「每 regime 首个窗口」必然落在 2020-2021 深历史。
"""

from __future__ import annotations

import pandas as pd
import pytest
from evals.backtest.report import build_conclusion, resolve_positioning
from evals.backtest.sampling import stratified_sample

CLEAN = {"passed": True, "reason": "ok"}
PROBE_OK = {"direction_hit_rate": 0.3, "downgraded": False}


class TestResolvePositioningDeepHistory:
    def test_deep_history_formal_batch_forced_pathway(self):
        """formal + 干净窗口过 + 探针可测，但决策日 2023 → 仍 pathway。"""
        assert (
            resolve_positioning(
                batch_kind="formal",
                clean_window=CLEAN,
                probe=PROBE_OK,
                decision_dates=["2023-06-15"],
            )
            == "pathway"
        )

    def test_post_cutoff_formal_keeps_skill(self):
        assert (
            resolve_positioning(
                batch_kind="formal",
                clean_window=CLEAN,
                probe=PROBE_OK,
                decision_dates=["2025-06-15"],
            )
            == "skill"
        )

    def test_mixed_batch_with_any_deep_date_is_pathway(self):
        assert (
            resolve_positioning(
                batch_kind="formal",
                clean_window=CLEAN,
                probe=PROBE_OK,
                decision_dates=["2026-03-01", "2024-12-31", "2026-05-01"],
            )
            == "pathway"
        )

    def test_cutoff_boundary(self):
        # 2024-12-31 深历史 → pathway；2025-01-01 起可及 → skill
        assert (
            resolve_positioning(
                batch_kind="formal",
                clean_window=CLEAN,
                probe=PROBE_OK,
                decision_dates=["2024-12-31"],
            )
            == "pathway"
        )
        assert (
            resolve_positioning(
                batch_kind="formal",
                clean_window=CLEAN,
                probe=PROBE_OK,
                decision_dates=["2025-01-01"],
            )
            == "skill"
        )

    def test_dates_omitted_preserves_legacy_behavior(self):
        """不传 decision_dates 的既有调用方行为不变（skill 判定不受日期影响）。"""
        assert (
            resolve_positioning(batch_kind="formal", clean_window=CLEAN, probe=PROBE_OK) == "skill"
        )


class TestBuildConclusionDeepHistory:
    def test_deep_history_strips_skill_sentence(self):
        sentence = build_conclusion(
            "显著优于基线",
            batch_kind="formal",
            clean_window=CLEAN,
            probe=PROBE_OK,
            decision_dates=["2023-06-15"],
        )
        assert "通路验证" in sentence
        assert "显著优于基线" not in sentence
        assert "赚钱能力主张成立" not in sentence


def _kline_with_regimes_after_cutoff() -> pd.DataFrame:
    """2000 根指数日线（2020-01 → ~2027-08）：每 250 根换相，牛/熊/震荡循环。

    相位：0-249 牛、250-499 熊、500-749 平、750-999 牛、1000-1249 熊、
    1250-1499 平（覆盖 2025-01 切点）、1500-1749 牛、1750-1999 熊。
    无 min_decision_date 时首个 bull 窗口落在 2020 深历史；
    有 min_decision_date=2025-01-01 时须跳到 2025 后窗口。
    """
    dates = pd.bdate_range("2020-01-01", periods=2000).strftime("%Y-%m-%d")
    closes: list[float] = []
    price = 100.0
    for i in range(2000):
        phase = (i // 250) % 3
        drift = 0.0016 if phase == 0 else (-0.0016 if phase == 1 else 0.0)
        price *= 1 + drift
        closes.append(round(price, 4))
    return pd.DataFrame({"日期": dates, "收盘": closes})


class TestStratifiedSampleMinDecisionDate:
    POOL = [f"{600000 + i}" for i in range(30)]

    def test_without_min_date_lands_deep_history(self):
        """修复前行为基线：首个 regime 窗口落在 2020-2021 深历史。"""
        sample = stratified_sample(_kline_with_regimes_after_cutoff(), self.POOL, per_regime=6)
        earliest = min(s["decision_date"] for s in sample)
        assert earliest < "2025-01-01"

    def test_min_decision_date_skips_deep_windows(self):
        sample = stratified_sample(
            _kline_with_regimes_after_cutoff(),
            self.POOL,
            per_regime=6,
            min_decision_date="2025-01-01",
        )
        assert {s["regime"] for s in sample} == {"bull", "bear", "sideways"}
        assert min(s["decision_date"] for s in sample) >= "2025-01-01"

    def test_min_decision_date_unsatisfiable_raises(self):
        """cutoff 之后凑不齐三 regime → 抛错（禁止静默单边汇报，沿用既有语义）。"""
        with pytest.raises(ValueError):
            stratified_sample(
                _kline_with_regimes_after_cutoff(),
                self.POOL,
                per_regime=6,
                min_decision_date="2027-01-01",
            )
