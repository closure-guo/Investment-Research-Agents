"""技术指标计算 — MA / MACD / RSI / BOLL / KDJ。

输入为 AKShare 格式的 K 线 DataFrame（日期/开盘/收盘/最高/最低/成交量）。
输出为分组 dict，各指标值为与 K 线等长的 list（前 N 天为 None）。
"""

from __future__ import annotations

import pandas as pd


def _calc_ma(close: pd.Series) -> dict[str, list[float | None]]:
    """简单移动平均（5/10/20/60）；数据不足该周期全 None。

    calc_technical 与 calc_derived_series 共用此实现——均线口径只允许一份。
    """
    ma_result: dict[str, list[float | None]] = {}
    for period in (5, 10, 20, 60):
        if len(close) >= period:
            ma = close.rolling(window=period).mean()
            ma_result[str(period)] = [None if pd.isna(v) else float(v) for v in ma]
        else:
            ma_result[str(period)] = [None] * len(close)
    return ma_result


def calc_technical(kline: pd.DataFrame) -> dict[str, dict[str, list[float | None]]]:
    """计算全部技术指标。

    Parameters
    ----------
    kline : DataFrame
        AKShare K 线数据，需含 收盘 列。

    Returns
    -------
    dict
        {"MA": {"5": [...], "10": [...], ...}, ...}
    """
    close = kline["收盘"]
    result: dict[str, dict[str, list[float | None]]] = {}

    # ── MA（简单移动平均；与 calc_derived_series 共用 _calc_ma，口径唯一）──
    result["MA"] = _calc_ma(close)

    # ── MACD（指数移动平均收敛发散）──
    # DIF = EMA12 - EMA26, DEA = EMA9(DIF), histogram = 2*(DIF-DEA)
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    dif = ema12 - ema26
    dea = dif.ewm(span=9, adjust=False).mean()
    hist = 2 * (dif - dea)
    result["MACD"] = {
        "DIF": [float(v) for v in dif],
        "DEA": [float(v) for v in dea],
        "histogram": [float(v) for v in hist],
    }

    # ── RSI（相对强弱指数，Wilder 平滑）──
    delta = close.diff().fillna(0)
    gain = delta.clip(lower=0)
    loss = (-delta).clip(lower=0)
    avg_gain = gain.ewm(com=13, adjust=False).mean()
    avg_loss = loss.ewm(com=13, adjust=False).mean()
    rsi = pd.Series(100.0, index=close.index)
    mask = avg_loss > 0
    rsi[mask] = 100 - 100 / (1 + avg_gain[mask] / avg_loss[mask])
    rsi_period = 14
    rsi_list: list[float | None] = [
        None if i < rsi_period else float(rsi.iloc[i]) for i in range(len(close))
    ]
    result["RSI"] = {"14": rsi_list}

    # ── BOLL（布林带，20 日 ±2σ）──
    boll_period = 20
    ma20 = close.rolling(window=boll_period).mean()
    std20 = close.rolling(window=boll_period).std()
    upper = ma20 + 2 * std20
    lower = ma20 - 2 * std20
    result["BOLL"] = {
        "upper": [None if pd.isna(v) else float(v) for v in upper],
        "middle": [None if pd.isna(v) else float(v) for v in ma20],
        "lower": [None if pd.isna(v) else float(v) for v in lower],
    }

    # ── KDJ（随机指标，9 日）──
    kdj_period = 9
    low_9 = kline["最低"].rolling(window=kdj_period).min()
    high_9 = kline["最高"].rolling(window=kdj_period).max()
    denom = high_9 - low_9
    rsv = ((close - low_9) / denom.replace(0, float("nan")) * 100).fillna(50)
    # K/D 初始值 50，ewm(alpha=1/3) ≈ 2/3 前值 + 1/3 当前
    k = rsv.ewm(alpha=1 / 3, adjust=False).mean()
    d = k.ewm(alpha=1 / 3, adjust=False).mean()
    j = 3 * k - 2 * d
    kdj_first = kdj_period - 1  # 前 8 天无值
    result["KDJ"] = {
        "K": [None if i < kdj_first else float(k.iloc[i]) for i in range(len(close))],
        "D": [None if i < kdj_first else float(d.iloc[i]) for i in range(len(close))],
        "J": [None if i < kdj_first else float(j.iloc[i]) for i in range(len(close))],
    }

    return result


def _pct_spread(a: float | None, b: float | None) -> float | None:
    """(a − b) / b × 100（%）；任一缺失或 b 为 0 → None。

    round 后归零的极小负值归一为 +0.0，避免 context JSON 渲染成 "-0.0"。
    """
    if a is None or b is None or b == 0:
        return None
    spread = round((a - b) / b * 100, 2)
    return 0.0 if spread == 0 else spread


def calc_derived_series(
    kline: pd.DataFrame,
    windows: tuple[int, ...] = (5, 20, 60),
    long_window: int = 250,
) -> dict:
    """常用派生值预生成（toolize-price-levels）：区间涨跌幅 + 距高低点回撤/反弹 + 均线差幅。

    单位约定（有意混用，spec 规定）：chg_*d / drawdown_from_high_250d /
    rebound_from_low_250d 为小数比率（round 4 位）；ma_spread_*_pct /
    close_vs_ma*_pct 为百分比（round 2 位）。
    工具算好供 LLM 直接引用（field_ref: derived_series.*），避免 LLM 对序列心算。
    数据不足的派生项值为 None（如实标注缺失，不伪造）。kline 缺失时全 None。
    """
    if kline is None or len(kline) == 0:
        keys = [f"chg_{w}d" for w in windows] + [
            "drawdown_from_high_250d",
            "rebound_from_low_250d",
            "ma_spread_5_20_pct",
            "ma_spread_20_60_pct",
            "close_vs_ma20_pct",
            "close_vs_ma60_pct",
        ]
        return dict.fromkeys(keys)
    close = kline["收盘"].astype(float)
    n = len(close)
    result: dict = {}
    for w in windows:
        if n > w:
            base = float(close.iloc[-1 - w])
            result[f"chg_{w}d"] = round(float(close.iloc[-1]) / base - 1, 4) if base else None
        else:
            result[f"chg_{w}d"] = None

    tail = close.tail(long_window)
    high = float(tail.max())
    low = float(tail.min())
    last = float(close.iloc[-1])
    if n >= 2 and high > 0:
        result["drawdown_from_high_250d"] = round(last / high - 1, 4)
    else:
        result["drawdown_from_high_250d"] = None
    if n >= 2 and low > 0:
        result["rebound_from_low_250d"] = round(last / low - 1, 4)
    else:
        result["rebound_from_low_250d"] = None

    # 均线差幅（ground-comparative-delta-claims）：MA/收盘两个水平量的相对差（%），
    # 正 = 前者高于后者；复用 _calc_ma 同一口径，缺失/分母为 0 → None 如实标缺
    ma = _calc_ma(close)
    ma_last = {w: (ma[str(w)][-1] if ma[str(w)] else None) for w in (5, 20, 60)}
    result["ma_spread_5_20_pct"] = _pct_spread(ma_last[5], ma_last[20])
    result["ma_spread_20_60_pct"] = _pct_spread(ma_last[20], ma_last[60])
    result["close_vs_ma20_pct"] = _pct_spread(last, ma_last[20])
    result["close_vs_ma60_pct"] = _pct_spread(last, ma_last[60])
    return result
