"""因子 IC 就绪度纯函数单测（delta add-factor-ic-visibility）。"""

from __future__ import annotations

import pytest

from finance_agent.factors.readiness import (
    add_weekdays,
    expected_factor_mode_date,
    factor_signal_payload,
)


def test_expected_date_skips_weekend_and_waits_one_extra_screen() -> None:
    """4 条样本、门槛 10、最新 2026-10-21（周三）→ 2026-11-02（周一）。"""
    assert expected_factor_mode_date("2026-10-21", 4, 10) == "2026-11-02"


def test_expected_date_returns_none_when_already_eligible() -> None:
    assert expected_factor_mode_date("2026-10-21", 10, 10) is None
    assert expected_factor_mode_date("2026-10-21", 12, 10) is None


def test_expected_date_returns_none_without_ic_history() -> None:
    """无 IC 记录 → 不臆造日期。"""
    assert expected_factor_mode_date(None, 0, 10) is None


def test_expected_date_rejects_invalid_inputs() -> None:
    with pytest.raises(ValueError):
        expected_factor_mode_date("2026-10-21", 4, 0)
    with pytest.raises(ValueError):
        expected_factor_mode_date("2026-10-21", -1, 10)


def test_add_weekdays_crosses_weekend() -> None:
    from datetime import date

    assert add_weekdays(date(2026, 10, 23), 1) == date(2026, 10, 26)
    assert add_weekdays(date(2026, 10, 21), 0) == date(2026, 10, 21)


def test_factor_signal_payload_reports_progress_and_earliest_date() -> None:
    factors = [{"id": 1}, {"id": 2}, {"id": 3}]
    payload = factor_signal_payload(
        factors,
        {1: 4, 2: 3, 3: 0},
        {1: "2026-10-21", 2: "2026-10-21", 3: "2026-10-21"},
        selection_mode="fallback",
        min_samples=10,
    )

    assert payload["selection_mode"] == "fallback"
    assert payload["min_samples"] == 10
    assert payload["reason"] == "IC 样本不足（门槛 10）"
    assert [row["ic_samples"] for row in payload["factors"]] == [4, 3, 0]
    assert all(row["eligible"] is False for row in payload["factors"])
    assert [row["expected_eligible_date"] for row in payload["factors"]] == [
        "2026-11-02",
        "2026-11-03",
        "2026-11-06",
    ]
    assert payload["expected_factor_mode_date"] == "2026-11-02"


def test_factor_signal_payload_without_active_factors() -> None:
    payload = factor_signal_payload([], {}, {}, selection_mode="fallback", min_samples=10)

    assert payload["factors"] == []
    assert payload["reason"] == "无 active 因子"
    assert payload["expected_factor_mode_date"] is None


def test_factor_signal_payload_all_eligible() -> None:
    payload = factor_signal_payload(
        [{"id": 1}, {"id": 2}],
        {1: 10, 2: 30},
        {1: "2026-10-21", 2: "2026-10-21"},
        selection_mode="factor",
        min_samples=10,
    )

    assert payload["reason"] == "全部 active 因子 IC 样本达标"
    assert all(row["eligible"] is True for row in payload["factors"])
    assert payload["expected_factor_mode_date"] is None
