"""参考价护栏（delta update-track-record-data-integrity / incident 032 根因 B）。

quote 实时价与 K 线收盘可能因数据源异常大幅偏离（生产事故：茅台 quote 1800 vs
实际收盘 1330，偏离 35%）。阈值与校验在此单点定义，ingest（落库）与 marking
（盯市）共用，禁止各自再写拷贝。
"""

from __future__ import annotations

import os


def max_reference_deviation() -> float:
    """参考价偏离阈值；env TRACK_ENTRY_PRICE_MAX_DEVIATION 可配（默认 0.30）。

    默认覆盖各板块涨跌幅限制（主板 10% / 创业板科创板 20% / 北交所 30%）。
    """
    try:
        return float(os.getenv("TRACK_ENTRY_PRICE_MAX_DEVIATION", "0.30"))
    except ValueError:
        return 0.30


def reference_price_ok(price: float, reference: float) -> bool:
    """|price/reference - 1| 未超阈值 → True。

    price 非正 → False（不是可用价格，调用方应走兜底）；reference 非正 →
    True（无法校验，放行由调用方另行兜底）。
    """
    if price <= 0:
        return False
    if reference <= 0:
        return True
    return abs(price / reference - 1.0) <= max_reference_deviation()
