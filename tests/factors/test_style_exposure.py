"""factors/style_exposure.py 单测（add-factor-style-exposure，零网络）。

覆盖：与风格代理同表达式 → ρ≈1 且 flagged；样本不足/常量 → 全部 None（不臆造 0）；
载荷形状（basket/threshold 齐备）。
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from finance_agent.factors.dsl import parse
from finance_agent.factors.eval_engine import (
    MarketPanel,
    evaluate,
    forward_returns,
    rank_ic_series,
)
from finance_agent.factors.style_exposure import (
    STYLE_CORRELATION_THRESHOLD,
    compute_style_exposure,
    style_basket,
    style_exposure_payload,
)

_DATES = 61
_CODES = 40
_SEED = 20261013


def _style_panel() -> MarketPanel:
    """合成面板：volume 预测次日收益（其余字段派生），风格篮子可在其上求值。"""
    rng = np.random.default_rng(_SEED)
    signal = rng.uniform(-1.0, 1.0, size=(_DATES, _CODES))
    noise = rng.uniform(-1.0, 1.0, size=(_DATES, _CODES))
    returns = np.zeros((_DATES, _CODES))
    returns[1:] = 0.05 * signal[:-1] + 0.01 * noise[:-1]
    close = 100.0 * np.cumprod(1.0 + returns, axis=0)
    dates = [f"2026-01-{day:02d}" for day in range(1, _DATES + 1)]
    codes = [f"c{index:03d}" for index in range(_CODES)]

    def frame(values: np.ndarray) -> pd.DataFrame:
        return pd.DataFrame(values, index=dates, columns=codes)

    return MarketPanel(
        fields={
            "open": frame(close),
            "high": frame(close),
            "low": frame(close),
            "close": frame(close),
            "volume": frame(signal),
            "amount": frame(signal * 1e6),
        }
    )


def _ic(expression: str, panel: MarketPanel) -> pd.Series:
    return rank_ic_series(
        evaluate(parse(expression), panel), forward_returns(panel.fields["close"])
    )


def test_style_basket_matches_builtin_package():
    """篮子 = builtin 注册包同一真源（名称与表达式逐字）。"""
    basket = style_basket()
    assert len(basket) == 5
    assert dict(basket)["turnover_proxy"] == "div(volume,ts_mean(volume,20))"


def test_same_expression_as_style_is_fully_flagged():
    """因子与某风格代理同表达式 → 该风格 ρ≈1、主导=该风格、flagged。"""
    panel = _style_panel()
    ic = _ic("div(volume,ts_mean(volume,20))", panel)
    exposure = compute_style_exposure(ic, panel)
    assert exposure.correlations["turnover_proxy"] == 1.0
    assert exposure.dominant == "turnover_proxy"
    assert exposure.dominant_correlation == 1.0
    assert exposure.flagged is True
    assert exposure.n_days >= 30


def test_insufficient_samples_returns_none_not_zero():
    """共同 IC 日 < min_samples → 全部 None（MUST NOT 用 0 冒充）。"""
    panel = _style_panel()
    ic = _ic("div(volume,ts_mean(volume,20))", panel).iloc[:10]
    exposure = compute_style_exposure(ic, panel)
    assert set(exposure.correlations.values()) == {None}
    assert exposure.dominant is None
    assert exposure.flagged is False
    assert exposure.n_days == 0


def test_constant_factor_returns_none():
    """常量因子（IC 全 NaN）→ 全部 None、不标注。"""
    panel = _style_panel()
    exposure = compute_style_exposure(_ic("sub(close,close)", panel), panel)
    assert set(exposure.correlations.values()) == {None}
    assert exposure.dominant is None
    assert exposure.flagged is False


def test_payload_shape():
    panel = _style_panel()
    exposure = compute_style_exposure(_ic("div(volume,ts_mean(volume,20))", panel), panel)
    payload = style_exposure_payload(exposure)
    assert payload["threshold"] == STYLE_CORRELATION_THRESHOLD
    assert payload["basket"] == [name for name, _ in style_basket()]
    assert isinstance(payload["correlations"], dict)
    assert payload["flagged"] is True
