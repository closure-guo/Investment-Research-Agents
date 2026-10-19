"""风格暴露披露（factor-library「只读查询」扩展，add-factor-style-exposure）。

**只读诊断，不参与任何准入裁决**。把因子的逐日 RankIC 序列与 builtin 风格代理篮子
（动量 / 反转 / 量比换手代理 / 波动率 / 规模代理）的 IC 序列做相关，回答「这个因子
其实是什么风格」。

设计立场（2026-10-19 治理裁决 D4 相邻事项）：

- 风格（价值 / 低波 / 动量 / 规模）是**合法收益源**，判定「与某风格同源」不等于
  「不合格」——因此本模块只披露、不设闸；
- 信息重复由闸④池内冗余拦截（已入库代表存在时），本模块的 ``flagged``（|ρ| ≥ 0.7）
  仅作标注，供人判读与组合复核；
- 诚实边界：风格代理是价量六字段内的**代理**（无股本/市值原始数据，见 ``builtin.py``
  的口径披露）；共同 IC 样本不足/序列常量 → 该风格记 ``None``，MUST NOT 用 0 冒充。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pandas as pd

from finance_agent.factors.builtin import BUILTIN_FACTORS
from finance_agent.factors.dsl import parse
from finance_agent.factors.eval_engine import (
    MarketPanel,
    evaluate,
    forward_returns,
    rank_ic_series,
)

# 与闸④冗余同一尺度：|ρ| ≥ 0.7 视为同一信息
STYLE_CORRELATION_THRESHOLD: float = 0.7
# 相关所需最少共同 IC 日（与闸④冗余比对同一门槛：2 点相关恒 ±1）
MIN_STYLE_SAMPLES: int = 30


@dataclass(frozen=True)
class StyleExposure:
    """风格暴露读数：逐风格 IC 相关 + 主导风格 + 标注（不构成裁决）。"""

    correlations: dict[str, float | None]
    dominant: str | None
    dominant_correlation: float | None
    flagged: bool
    n_days: int


def style_basket() -> tuple[tuple[str, str], ...]:
    """风格代理篮子（名称, 表达式）——与 builtin 注册包同一真源。"""
    return tuple((item.name, item.expression) for item in BUILTIN_FACTORS)


def compute_style_exposure(
    factor_ic: pd.Series,
    panel: MarketPanel,
    *,
    fwd: pd.DataFrame | None = None,
    min_samples: int = MIN_STYLE_SAMPLES,
) -> StyleExposure:
    """因子 IC 序列 × 风格代理 IC 序列 → 风格暴露读数。

    逐风格取共同日期上的 Pearson 相关（IC 序列相关，非因子值相关——与闸④冗余比对
    同口径）；共同样本 < ``min_samples``、任一侧常量/全 NaN → ``None``（不臆造 0）。
    主导风格 = |ρ| 最大者；``flagged`` = |主导 ρ| ≥ ``STYLE_CORRELATION_THRESHOLD``。
    """
    if min_samples < 2:
        raise ValueError(f"min_samples 必须是 ≥2 的整数: {min_samples!r}")
    resolved_fwd = fwd if fwd is not None else forward_returns(panel.fields["close"])
    factor_series = factor_ic.dropna()
    correlations: dict[str, float | None] = {}
    dominant: str | None = None
    dominant_corr: float | None = None
    dominant_days = 0
    for name, expression in style_basket():
        style_ic = rank_ic_series(evaluate(parse(expression), panel), resolved_fwd).dropna()
        common = factor_series.index.intersection(style_ic.index)
        if len(common) < min_samples:
            correlations[name] = None
            continue
        left = factor_series.loc[common]
        right = style_ic.loc[common]
        if left.std() == 0 or right.std() == 0:
            correlations[name] = None
            continue
        value = float(left.corr(right))
        correlations[name] = value
        if dominant_corr is None or abs(value) > abs(dominant_corr):
            dominant = name
            dominant_corr = value
            dominant_days = int(len(common))
    return StyleExposure(
        correlations=correlations,
        dominant=dominant,
        dominant_correlation=dominant_corr,
        flagged=dominant_corr is not None and abs(dominant_corr) >= STYLE_CORRELATION_THRESHOLD,
        n_days=dominant_days,
    )


def style_exposure_payload(exposure: StyleExposure) -> dict[str, Any]:
    """API/脚本载荷（JSON 友好）。"""
    return {
        "correlations": dict(exposure.correlations),
        "dominant": exposure.dominant,
        "dominant_correlation": exposure.dominant_correlation,
        "flagged": exposure.flagged,
        "n_days": exposure.n_days,
        "threshold": STYLE_CORRELATION_THRESHOLD,
        "basket": [name for name, _expression in style_basket()],
    }
