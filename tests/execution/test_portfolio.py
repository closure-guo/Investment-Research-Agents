"""execution/portfolio.py 组合仓位聚合单测（add-signal-portfolio-risk-gate Task 2 / G3）。

覆盖计划 G3 与 delta spec trade-execution「组合仓位聚合」：

- 仅 direction=="long" 参与（flat 退出不计入分母）；
- 等权 = 1/n；强度加权 = strength/Σstrength（分母只含 long）；
- 先单票 clip（>20% → 20% + SINGLE_CAP_CLIP）；再总仓缩减（Σ>80% → 全体按
  80%/Σ 等比缩 + TOTAL_CAP_SCALE_DOWN；spec 场景「6 条 long 强度加权合计 96% → 80%」）；
- 输出按权重降序、symbol 升序 tie-break；权重和勾稽 Σ ≤ total_cap + 1e-9；
- 空信号 → 空列表；未知 method → ValueError。

数值断言独立手写算术（逐式见各用例注释），不复用被测实现的计算。
"""

from __future__ import annotations

import pytest

from finance_agent.execution.portfolio import (
    ADJUSTMENT_SINGLE_CAP_CLIP,
    ADJUSTMENT_TOTAL_CAP_SCALE_DOWN,
    TargetPosition,
    aggregate_targets,
)
from finance_agent.execution.signals import Signal


def _signal(symbol: str, strength: float = 0.5, direction: str = "long") -> Signal:
    return Signal(
        signal_id=f"{symbol}:2026-10-13",
        symbol=symbol,
        direction=direction,  # type: ignore[arg-type]  # 测试构造，取值受用例约束
        strength=strength,
        horizon_days=20,
        source="risk_judge",
        decision_id=f"dec-{symbol}",
        created_at="2026-10-13",
    )


def _weights(result: list[TargetPosition]) -> list[tuple[str, float]]:
    return [(p.symbol, p.weight) for p in result]


# ── 等权档 ─────────────────────────────────────────────────────────────────


def test_equal_weight_formula_three_signals():
    """等权 1/n：3 票各 1/3（上限放宽以隔离公式本身）。"""
    result = aggregate_targets(
        [_signal("600001.SH"), _signal("600002.SH"), _signal("600003.SH")],
        method="equal",
        single_cap=1.0,
        total_cap=1.0,
    )
    assert [p.symbol for p in result] == [
        "600001.SH",
        "600002.SH",
        "600003.SH",
    ]  # 同权 → symbol 升序
    for _, weight in _weights(result):
        assert weight == pytest.approx(1.0 / 3.0, rel=1e-12)  # 1/3 = 0.3333…
    assert sum(p.weight for p in result) == pytest.approx(1.0, rel=1e-12)
    assert all(p.adjustments == () for p in result)


def test_equal_weight_is_default_method():
    signals = [_signal("600001.SH"), _signal("600002.SH")]
    assert aggregate_targets(signals, single_cap=1.0, total_cap=1.0) == aggregate_targets(
        signals, method="equal", single_cap=1.0, total_cap=1.0
    )


def test_equal_weight_default_caps_clip_each_at_20pct():
    """默认单票上限 20%：3 票 1/3（=33.3%）各 clip 至 0.2，Σ=0.6 ≤ 0.8 不再缩减。"""
    result = aggregate_targets([_signal("600001.SH"), _signal("600002.SH"), _signal("600003.SH")])
    for _, weight in _weights(result):
        assert weight == pytest.approx(0.2, abs=1e-12)
    assert sum(p.weight for p in result) == pytest.approx(0.6, abs=1e-12)
    assert all(p.adjustments == (ADJUSTMENT_SINGLE_CAP_CLIP,) for p in result)


# ── 强度加权档 ─────────────────────────────────────────────────────────────


def test_strength_weighted_formula():
    """强度加权 = strength/Σstrength：0.6/0.3/0.1（Σ=1.0）→ 同份额。"""
    signals = [
        _signal("600001.SH", strength=0.6),
        _signal("600002.SH", strength=0.3),
        _signal("600003.SH", strength=0.1),
    ]
    result = aggregate_targets(signals, method="strength", single_cap=1.0, total_cap=1.0)
    assert _weights(result) == [
        ("600001.SH", pytest.approx(0.6, rel=1e-12)),
        ("600002.SH", pytest.approx(0.3, rel=1e-12)),
        ("600003.SH", pytest.approx(0.1, rel=1e-12)),
    ]


def test_total_cap_scale_down_spec_scenario_96_to_80():
    """spec 场景逐数：6 条 long 强度加权合计 96% → 等比缩至 80% + TOTAL_CAP_SCALE_DOWN。

    手算：strengths (0.24,0.16,0.16,0.16,0.14,0.14)，Σ=1.00 → 份额同 strengths；
    ① 单票 clip：0.24 > 0.20 → 0.20（SINGLE_CAP_CLIP）→ Σ=0.96（题面「合计 96%」）；
    ② 总仓缩减：0.96 > 0.80 → 缩放系数 = 0.80/0.96 = 5/6：
       600001 0.20×5/6 = 1/6；0.16×5/6 = 2/15；0.14×5/6 = 7/60；
       Σ = 1/6 + 3×2/15 + 2×7/60 = 10/60 + 24/60 + 14/60 = 48/60 = 0.80 ✓
    """
    signals = [
        _signal("600002.SH", strength=0.16),
        _signal("600001.SH", strength=0.24),
        _signal("600004.SH", strength=0.14),
        _signal("600003.SH", strength=0.16),
        _signal("600005.SH", strength=0.16),
        _signal("600006.SH", strength=0.14),
    ]
    result = aggregate_targets(signals, method="strength")
    assert _weights(result) == [
        ("600001.SH", pytest.approx(1.0 / 6.0, rel=1e-12)),
        ("600002.SH", pytest.approx(2.0 / 15.0, rel=1e-12)),
        ("600003.SH", pytest.approx(2.0 / 15.0, rel=1e-12)),
        ("600005.SH", pytest.approx(2.0 / 15.0, rel=1e-12)),
        ("600004.SH", pytest.approx(7.0 / 60.0, rel=1e-12)),
        ("600006.SH", pytest.approx(7.0 / 60.0, rel=1e-12)),
    ]
    assert result[0].adjustments == (
        ADJUSTMENT_SINGLE_CAP_CLIP,
        ADJUSTMENT_TOTAL_CAP_SCALE_DOWN,
    )
    assert all(p.adjustments == (ADJUSTMENT_TOTAL_CAP_SCALE_DOWN,) for p in result[1:])
    assert sum(p.weight for p in result) == pytest.approx(0.8, abs=1e-9)


def test_single_clip_applies_before_total_scale():
    """先单票 clip 再总仓缩减（顺序敏感形态）：(0.9, 0.1) → (0.2, 0.1)，Σ=0.3 不触发缩减。

    若先总仓缩减（1.0 → ×0.8）则份额为 (0.72, 0.08)，clip 后 (0.2, 0.08)：与期望不符。
    """
    result = aggregate_targets(
        [_signal("600001.SH", strength=0.9), _signal("600002.SH", strength=0.1)],
        method="strength",
    )
    assert _weights(result) == [
        ("600001.SH", pytest.approx(0.2, abs=1e-12)),
        ("600002.SH", pytest.approx(0.1, abs=1e-12)),
    ]
    assert result[0].adjustments == (ADJUSTMENT_SINGLE_CAP_CLIP,)
    assert result[1].adjustments == ()


def test_strength_weighted_zero_total_rejected():
    """全部 long 强度为 0 → 强度加权无定义（拒收，不静默回退）。"""
    with pytest.raises(ValueError, match="strength"):
        aggregate_targets([_signal("600001.SH", strength=0.0)], method="strength")


# ── flat 不参与 / 空输入 / 排序 / 勾稽 ─────────────────────────────────────


def test_flat_signals_excluded_from_weights():
    """flat（退出敞口）不参与聚合，且不计入强度加权分母。"""
    signals = [
        _signal("600001.SH", strength=0.5),
        _signal("600002.SH", strength=0.5, direction="flat"),
    ]
    result = aggregate_targets(signals, method="strength", single_cap=1.0, total_cap=1.0)
    assert _weights(result) == [("600001.SH", pytest.approx(1.0, rel=1e-12))]

    equal = aggregate_targets(signals, method="equal", single_cap=1.0, total_cap=1.0)
    assert _weights(equal) == [("600001.SH", pytest.approx(1.0, rel=1e-12))]


def test_empty_and_flat_only_return_empty_list():
    assert aggregate_targets([]) == []
    assert aggregate_targets([_signal("600001.SH", direction="flat")]) == []


def test_order_is_weight_desc_then_symbol_asc():
    result = aggregate_targets(
        [
            _signal("600003.SH", strength=0.2),
            _signal("600002.SH", strength=0.5),
            _signal("600001.SH", strength=0.2),
            _signal("600004.SH", strength=0.1),
        ],
        method="strength",
        single_cap=1.0,
        total_cap=1.0,
    )
    assert [p.symbol for p in result] == ["600002.SH", "600001.SH", "600003.SH", "600004.SH"]


@pytest.mark.parametrize(
    ("strengths", "method"),
    [
        ((0.9, 0.9, 0.9), "strength"),
        ((0.5, 0.5, 0.5, 0.5), "strength"),
        ((0.2, 0.2, 0.2, 0.2, 0.2), "strength"),
        ((0.4, 0.3, 0.2, 0.1), "strength"),
        ((0.5, 0.5, 0.5), "equal"),
    ],
)
def test_default_caps_invariants(strengths, method):
    """默认上限下恒有：单票 ≤ 20%+1e-9、Σ ≤ 80%+1e-9（spec 勾稽容差）。"""
    signals = [_signal(f"60000{i}.SH", strength=s) for i, s in enumerate(strengths)]
    result = aggregate_targets(signals, method=method)  # type: ignore[arg-type]
    assert all(p.weight <= 0.2 + 1e-9 for p in result)
    assert sum(p.weight for p in result) <= 0.8 + 1e-9
    assert all(p.weight >= 0.0 for p in result)


def test_unknown_method_rejected():
    with pytest.raises(ValueError, match="method"):
        aggregate_targets([_signal("600001.SH")], method="bogus")  # type: ignore[arg-type]


def test_default_caps_single_source_from_constraints():
    """默认上限单一来源 screening/constraints.py（值逐字 0.2/0.8，非本地副本）。"""
    from finance_agent.screening import constraints

    assert constraints.MAX_SINGLE_POSITION == 0.2
    assert constraints.MAX_TOTAL_POSITION == 0.8
    signals = [_signal(f"60000{i}.SH", strength=0.5) for i in range(3)]
    assert aggregate_targets(signals) == aggregate_targets(
        signals,
        single_cap=constraints.MAX_SINGLE_POSITION,
        total_cap=constraints.MAX_TOTAL_POSITION,
    )
