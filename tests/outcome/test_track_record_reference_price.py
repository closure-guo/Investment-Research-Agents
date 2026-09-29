"""参考价护栏单测（delta update-track-record-data-integrity / incident 032 根因 B）。"""

import pytest

from finance_agent.outcome.track_record.reference_price import (
    max_reference_deviation,
    reference_price_ok,
)


def test_default_threshold_30pct(monkeypatch):
    monkeypatch.delenv("TRACK_ENTRY_PRICE_MAX_DEVIATION", raising=False)
    assert max_reference_deviation() == pytest.approx(0.30)
    assert reference_price_ok(129.0, 100.0) is True  # +29% 通过
    assert reference_price_ok(133.0, 100.0) is False  # +33% 拒绝
    assert reference_price_ok(69.0, 100.0) is False  # -31% 拒绝


def test_env_override(monkeypatch):
    monkeypatch.setenv("TRACK_ENTRY_PRICE_MAX_DEVIATION", "0.05")
    assert reference_price_ok(104.0, 100.0) is True  # 4% 在 5% 内
    assert reference_price_ok(106.0, 100.0) is False  # 6% 超出 5%


def test_nonpositive_reference_passes():
    """reference 非正视为不可校验（放行，由调用方另行兜底）。"""
    assert reference_price_ok(100.0, 0.0) is True


def test_nonpositive_price_rejected():
    """price 非正 → 拒绝（ingest 落入 kline 兜底）；reference 非正 → 不可校验放行。"""
    assert reference_price_ok(0.0, 100.0) is False
    assert reference_price_ok(-1.0, 100.0) is False
    assert reference_price_ok(100.0, 0.0) is True
