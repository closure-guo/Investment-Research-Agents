"""行情取价口径 seam（add-settlement-price-caliber，C3：docs/architecture-debt/c3-settlement-prices.md）。

复权口径决策从调用方收回：结算=hfq fail-closed（incident 054 裁决：新浪锁定），
盯市=qfq 近似口径（如实披露）。调用方 SHALL NOT 自行传 adjust——「想拿错都拿不到」。
基准对齐收盘与中文列日期归一以本模块为唯一实现（#57 排序防御随迁）。

非目标：akshare_client 内部的源选择逻辑（新浪 fail-closed/腾讯回退）不搬——
本模块是口径 seam，不是源 seam。
"""

from __future__ import annotations

from typing import Any

import pandas as pd

from finance_agent.data.akshare_client import SETTLEMENT_ADJUST

MONITOR_ADJUST = "qfq"  # 盯市近似口径（marking 现状默认；与结算 hfq 分离如实披露）


def settlement_series(client: Any, symbol: str, *, days: int) -> pd.DataFrame:
    """结算专用行情序列（后复权 hfq，incident 054 口径）。

    fail-closed 语义 = 口径锁定 + 异常上抛：本函数 SHALL NOT 捕获取数异常
    （调用方的既有缺失处置路径承接——journal 留痕 / job 跳过 / marking 计数），
    且 SHALL NOT 出现「失败后换口径重试」的静默降级。复权参数不接受调用方输入。
    """
    frame: pd.DataFrame = client.fetch_kline(symbol, days=days, adjust=SETTLEMENT_ADJUST)
    return frame


def monitor_series(client: Any, symbol: str, *, days: int) -> pd.DataFrame:
    """盯市专用行情序列（qfq 近似口径；open 观点浮动收益展示用）。"""
    frame: pd.DataFrame = client.fetch_kline(symbol, days=days, adjust=MONITOR_ADJUST)
    return frame


def normalize_dates(df: pd.DataFrame) -> pd.DataFrame:
    """日期列归一化为 'YYYY-MM-DD' 字符串（幂等，不改动调用方 DataFrame）。

    akshare stock_zh_a_hist 的日期列是 datetime.date 对象（与 str 比较会
    TypeError）；pd.Timestamp 的 str() 带时间分量。astype(str).str[:10]
    对 str / datetime.date / pd.Timestamp 均成立。
    """
    df = df.copy()
    df["日期"] = df["日期"].astype(str).str[:10]
    return df


def benchmark_close_on_or_before(benchmark: pd.DataFrame, date: str) -> float | None:
    """基准在 date 或之前最后一个收盘（日期列须已归一化为 str）。

    #57:入口排序防御——升序本由 fetch_index_kline 保证,但本层不隐式依赖,
    乱序输入也按日期取最后收盘(此前 iloc[-1] 拿物理末行,乱序即错)。
    """
    eligible = benchmark[benchmark["日期"] <= date].sort_values("日期")
    if eligible.empty:
        return None
    return float(eligible.iloc[-1]["收盘"])
