"""issue #243 子项1：601818 beta≈0.004 多源交叉复跑。

对照设计（与生产 calc_risk 同口径：收盘 pct_change、尾部 min_len 对齐、cov/var）：
- A: 个股新浪 qfq × 指数新浪
- B: 个股腾讯 qfq（生产事故路径）× 指数新浪
- 另比对两个个股源之间的收益相关性 / 收盘差异天数，定位数据源质量问题。
"""

from __future__ import annotations

import sys

import akshare as ak
import numpy as np
import pandas as pd


def beta(stock_close: pd.Series, bench_close: pd.Series) -> float:
    df = pd.concat(
        [stock_close.rename("s"), bench_close.rename("b")], axis=1, join="inner"
    ).dropna()
    r_s = df["s"].pct_change().dropna()
    r_b = df["b"].pct_change().dropna()
    min_len = min(len(r_s), len(r_b))
    r_s, r_b = r_s.iloc[-min_len:], r_b.iloc[-min_len:]
    cov = float(np.cov(r_s, r_b)[0, 1])
    var_b = float(np.var(r_b, ddof=1))
    return cov / var_b if var_b else float("nan"), len(df), float(r_s.std()), float(r_b.std())


def main() -> None:
    print("fetch 个股新浪 sh601818 qfq ...", flush=True)
    sina = ak.stock_zh_a_daily(symbol="sh601818", adjust="qfq")
    sina["date"] = pd.to_datetime(sina["date"])
    sina = sina.set_index("date").sort_index()

    print("fetch 个股腾讯 sh601818 qfq ...", flush=True)
    tx = ak.stock_zh_a_hist_tx(symbol="sh601818", adjust="qfq")
    tx["date"] = pd.to_datetime(tx["date"])
    tx = tx.set_index("date").sort_index()

    print("fetch 指数新浪 sh000300 ...", flush=True)
    idx = ak.stock_zh_index_daily(symbol="sh000300")
    idx["date"] = pd.to_datetime(idx["date"])
    idx = idx.set_index("date").sort_index()

    # 对齐事故窗口：近 250 个交易日（以指数为基准）
    win_idx = idx["close"].dropna().iloc[-260:]

    for name, s_close in [("新浪", sina["close"]), ("腾讯", tx["close"])]:
        b, n, vol_s, vol_b = beta(s_close, win_idx)
        print(
            f"[{name}个股 × 新浪指数] beta={b:.4f}  对齐天数={n}  个股日σ={vol_s:.4%}  指数日σ={vol_b:.4%}"
        )

    # 两个个股源互比
    both = (
        pd.concat([sina["close"].rename("sina"), tx["close"].rename("tx")], axis=1, join="inner")
        .dropna()
        .iloc[-260:]
    )
    diff = (both["sina"] - both["tx"]).abs()
    rel_diff = (diff / both["tx"]).iloc[-250:]
    r_s = both["sina"].pct_change().dropna()
    r_t = both["tx"].pct_change().dropna()
    print(f"\n个股源互比（近 {len(both)} 对齐交易日）:")
    print(f"  收盘价完全一致天数: {(diff == 0).sum()}/{len(both)}")
    print(f"  相对差 >0.5% 天数: {(rel_diff > 0.005).sum()}  >2% 天数: {(rel_diff > 0.02).sum()}")
    print(f"  最大相对差: {rel_diff.max():.2%}  中位相对差: {rel_diff.median():.4%}")
    print(f"  日收益相关系数: {r_s.corr(r_t):.4f}")
    # 收益符号不一致 top5
    disagree = (np.sign(r_s) != np.sign(r_t)) & (r_s.abs() > 0.002) & (r_t.abs() > 0.002)
    print(f"  收益反向且幅度>0.2% 天数: {disagree.sum()}")
    if disagree.sum():
        print(
            both.loc[disagree[disagree].index[:5]].assign(
                r_sina=r_s[disagree].head(), r_tx=r_t[disagree].head()
            )
        )


if __name__ == "__main__":
    sys.exit(main())
