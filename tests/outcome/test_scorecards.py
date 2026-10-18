"""策略族成绩单测试（delta add-strategy-family-scorecards）：四槽纯函数 + 注册表。

口径权威：docs/evals/metrics.md §1.13（2026-10-18 登记）+ 变更 design.md。
测试分层：本文件先覆盖纯计算（Task 2），族数据读取与组装（Task 3）随后追加。
"""

from __future__ import annotations

import math

import pytest

from finance_agent.outcome.scorecards import (
    FAMILY_REGISTRY,
    FamilyContract,
    Sample,
    aggregate_daily_returns,
    build_family_payload,
    estimate_round_trip_cost_rate,
    max_drawdown_from_returns,
)


def _contract(win_rate: float | None = None, note: str = "口径注释") -> FamilyContract:
    return FamilyContract(win_rate=win_rate, caliber_note=note)


def _s(ticker: str, day: str, ret: float) -> Sample:
    return Sample(ticker, day, ret)


# ── 纯计算（Task 2）──


def test_registry_families() -> None:
    ids = [spec.family_id for spec in FAMILY_REGISTRY]
    assert ids == ["predictions", "execution"]


def test_cost_rate_matches_matching_config() -> None:
    rate = estimate_round_trip_cost_rate()
    # 0.00025*2 佣金 + 0.0005 印花税 + 0.001*2 中档滑点 = 0.003
    assert math.isclose(rate, 0.003, abs_tol=1e-9)


def test_max_drawdown_known_sequence() -> None:
    assert math.isclose(max_drawdown_from_returns([0.1, -0.2, 0.05, -0.3]), -0.412, abs_tol=1e-3)
    assert max_drawdown_from_returns([]) == 0.0
    assert max_drawdown_from_returns([0.01, 0.02]) == 0.0


def test_aggregate_daily_returns_equal_weight() -> None:
    samples = [
        Sample("600519", "2026-09-01", 0.10),
        Sample("000001", "2026-09-01", -0.02),
        Sample("600000", "2026-09-02", 0.04),
    ]
    assert aggregate_daily_returns(samples) == pytest.approx([0.04, 0.04])


def test_build_payload_null_semantics() -> None:
    """缺数据槽位一律 null + 原因码，MUST NOT 折算 0 或静默省略。"""
    spec = FAMILY_REGISTRY[0]
    payload = build_family_payload(spec, [], contract=_contract())
    assert payload["expectancy"] is None
    assert payload["expectancy_reason"] == "no_samples"
    assert payload["max_drawdown"] is None
    assert payload["max_drawdown_reason"] == "insufficient_history"
    assert payload["capacity"] is None
    assert payload["capacity_reason"] == "no_liquidity_data"
    assert payload["extreme_samples"] == []
    assert payload["win_rate"] is None


def test_build_payload_field_order() -> None:
    """四槽字段位于胜率类字段之前（spec 展示位次序）。"""
    spec = FAMILY_REGISTRY[0]
    samples = [_s("600519", "2026-09-01", 0.05)] * 3
    payload = build_family_payload(spec, samples, contract=_contract(win_rate=1.0))
    keys = list(payload.keys())
    assert (
        keys.index("expectancy")
        < keys.index("max_drawdown")
        < keys.index("capacity")
        < keys.index("extreme_samples")
    )
    assert keys.index("extreme_samples") < keys.index("win_rate")


def test_insufficient_sample_and_extreme() -> None:
    spec = FAMILY_REGISTRY[0]
    samples = [_s("600519", f"2026-09-{d:02d}", -0.20 if d == 1 else 0.01) for d in range(1, 7)]
    payload = build_family_payload(spec, samples, contract=_contract())
    assert payload["expectancy"] is None  # n=6 < 10
    assert payload["expectancy_reason"] == "insufficient_sample"
    assert [row["ticker"] for row in payload["extreme_samples"]] == ["600519"]


def test_execution_threshold_and_gross_equals_net() -> None:
    """执行线：组合级阈值 −5%、毛=净（净值已含费），样本足够时给双值。"""
    spec = FAMILY_REGISTRY[1]
    samples = [
        _s("<portfolio>", f"2026-09-{d:02d}", 0.01 if d > 1 else -0.06) for d in range(1, 12)
    ]
    payload = build_family_payload(spec, samples, contract=_contract())
    assert payload["expectancy"]["n"] == 11
    assert payload["expectancy"]["gross"] == payload["expectancy"]["net"]
    assert [row["ret"] for row in payload["extreme_samples"]] == [-0.06]


def test_capacity_slot_excludes_missing_symbols() -> None:
    """缺数据标的剔除并计数；最小值 × 参与率 = 建议资金上限。"""
    spec = FAMILY_REGISTRY[0]
    samples = [_s("600519", f"2026-09-{d:02d}", 0.01) for d in range(1, 12)]
    payload = build_family_payload(
        spec,
        samples,
        contract=_contract(),
        capacity_inputs={"600519": 100_000_000.0, "000001": None},
    )
    assert payload["capacity"]["min_adv20"] == pytest.approx(100_000_000.0)
    assert payload["capacity"]["excluded"] == 1
    assert payload["capacity"]["suggested_capital"] == pytest.approx(1_000_000.0)


def test_capacity_slot_all_missing_is_null() -> None:
    spec = FAMILY_REGISTRY[0]
    samples = [_s("600519", f"2026-09-{d:02d}", 0.01) for d in range(1, 12)]
    payload = build_family_payload(
        spec,
        samples,
        contract=_contract(),
        capacity_inputs={"600519": None, "000001": None},
    )
    assert payload["capacity"] is None
    assert payload["capacity_reason"] == "no_liquidity_data"
