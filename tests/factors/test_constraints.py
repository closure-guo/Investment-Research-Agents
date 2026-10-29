"""因子约束常量守护（关键阈值既有值不得静默漂移）。"""

from __future__ import annotations

from finance_agent.factors.constraints import (
    MIN_ABS_ICIR,
    MIN_DECILE_MONOTONICITY,
    REVIVAL_COOLDOWN_DAYS,
    REVIVAL_DAILY_CAP,
)


def test_revival_constants() -> None:
    # 复苏重评（add-rejected-factor-revival）：静默线与闸②同值（常量复用而非
    # 复制，单一真相源由 revival.py import 保证）；窗口复用 tools.IC_WINDOW。
    assert REVIVAL_COOLDOWN_DAYS == 30
    assert REVIVAL_DAILY_CAP == 5
    assert MIN_ABS_ICIR == 0.3
    assert MIN_DECILE_MONOTONICITY == 0.6
