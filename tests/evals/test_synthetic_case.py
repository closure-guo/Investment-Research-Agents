"""合成案例 fixture 单测（2026-11-06 零买入第二轮 P0）。

口径：数据键集必须与 TESTING stub 同构（下游节点按真实键集消费）；
三表会计恒等式严格成立；K 线为确定性上升趋势且日期不越界。
"""

from __future__ import annotations

import numpy as np
import pytest
from evals.synthetic_case import SYN_CODE, SYN_NAME, build_synthetic_state

from finance_agent.metrics.risk import calc_risk
from finance_agent.metrics.validate import validate_financials
from finance_agent.nodes.fetch import _stub_fetch_data


def test_key_parity_with_stub():
    stub = _stub_fetch_data({"stock_code": "X", "stock_name": "X"})
    state = build_synthetic_state("perfect")
    assert set(state) == set(stub) | {"stock_code", "stock_name"}
    assert state["stock_code"] == SYN_CODE
    assert state["stock_name"] == SYN_NAME


def test_perfect_case_financials_are_sound():
    s = build_synthetic_state("perfect")
    bs = s["balance_sheet"].iloc[0]
    assert bs["资产总计"] == bs["负债合计"] + bs["所有者权益(或股东权益)合计"]
    assert bs["负债合计"] / bs["资产总计"] < 0.35  # 低负债
    inc = s["income_statement"].iloc[0]
    assert inc["净利润"] == inc["利润总额"] - inc["所得税费用"]
    assert inc["归母净利润"] > 0
    cf = s["cash_flow_statement"].iloc[0]
    assert cf["经营活动产生的现金流量净额"] > inc["归母净利润"]  # 现金流覆盖利润
    cf_prev = s["cash_flow_statement"].iloc[1]
    assert (
        cf["经营活动产生的现金流量净额"]
        + cf["投资活动产生的现金流量净额"]
        + cf["筹资活动产生的现金流量净额"]
        == cf["现金及现金等价物净增加额"]
    )
    assert cf_prev is not None
    assert s["latest_period_snapshot"]["资产负债率(%)"] == 25.0


def test_perfect_kline_uptrend_bounded_and_quote_consistent():
    s = build_synthetic_state("perfect")
    k = s["kline"]
    assert len(k) == 300
    assert k["日期"].max() <= "2026-10-30"
    assert k["收盘"].iloc[-1] > k["收盘"].iloc[:60].mean() * 1.3  # 明显上行
    assert (k["最高"] >= k["收盘"]).all() and (k["最低"] <= k["收盘"]).all()
    assert s["stock_quote"]["price"] == float(k["收盘"].iloc[-1])


def test_statements_use_yuan_units():
    """三表为 A 股财报原生「元」口径：所有非零绝对金额 ≥ 1e7（亿元口径仅 ~1e2）。"""
    s = build_synthetic_state("perfect")
    for key in ("balance_sheet", "income_statement", "cash_flow_statement"):
        values = s[key].drop(columns=["报告日"]).to_numpy(dtype=float)
        nonzero = values[values != 0]
        assert nonzero.size > 0
        assert np.abs(nonzero).min() >= 1e7


def test_statement_periods_are_aligned():
    """三表年期对齐（3/3/3）——消费端按利润表年份逐行索引资产负债表。"""
    for case in ("perfect", "defect"):
        s = build_synthetic_state(case)
        assert (
            len(s["balance_sheet"])
            == len(s["income_statement"])
            == len(s["cash_flow_statement"])
            == 3
        )


def test_financial_validator_reports_no_warnings():
    """项目勾稽校验器零告警（试算平衡/利润表/现金流/留存收益）。"""
    for case in ("perfect", "defect"):
        s = build_synthetic_state(case)
        report = validate_financials(
            s["balance_sheet"],
            s["income_statement"],
            s["cash_flow_statement"],
        )
        assert report["result"] == "PASS"
        assert report["warnings"] == []


def test_quote_and_industry_valuation_fields():
    """估值链路字段齐备：market_cap 元口径、PE/PB、行业 PE 均值。

    defect 案例资不抵债（归母权益 -100 亿）→ PB 无意义，须为 None。
    """
    s = build_synthetic_state("perfect")
    q = s["stock_quote"]
    assert q["market_cap"] == 3780e8
    assert q["PE"] == 12.0
    assert q["PB"] == 5.0
    assert s["industry_pe"] == {"avg_pe": 18.0}

    d = build_synthetic_state("defect")["stock_quote"]
    assert d["PB"] is None
    assert d["market_cap"] == 3780e8  # 市值/PE 保持原值（本轮不调整）
    assert d["PE"] == 12.0


def test_kline_has_real_volatility_and_ends_below_year_high():
    """真实波动 + 收在年内高点下方 + 站上近 20 日均线（避高位死叉形态）。"""
    s = build_synthetic_state("perfect")
    k = s["kline"]
    ret_std = k["收盘"].pct_change().dropna().std()
    assert 0.015 <= ret_std <= 0.030
    assert k["收盘"].iloc[-1] < k["收盘"].max()  # 不再收在年内最高
    assert k["收盘"].iloc[-1] > k["收盘"].iloc[-20:].mean()


def test_benchmark_is_independent_series():
    """基准与个股不是同一收益序列——beta 可估，不再恒等于 1。"""
    s = build_synthetic_state("perfect")
    stock_ret = s["kline"]["收盘"].pct_change().dropna()
    bench_ret = s["benchmark_kline"]["收盘"].pct_change().dropna()
    assert (bench_ret.to_numpy() != stock_ret.to_numpy()).all()


def test_risk_metrics_beta_in_expected_range():
    """风控 beta 落在验收区间 [0.6, 1.2]（不再是独立随机流的 -0.13）。"""
    s = build_synthetic_state("perfect")
    beta = calc_risk(s["kline"], s["benchmark_kline"])["beta"]
    assert 0.6 <= beta <= 1.2


def test_cooled_tail_strips_overbought_but_keeps_narrow_stop():
    """严格二分 fixture：RSI/J 降温（剥超买），但最近 10 日低点仍 ~-2%（窄止损保留）。

    判据（补跑报告用）：RSI14 ∈ [55,65]、J ≤ 90、末收距 250 日高点 -4.5%~-1%
    （仍贴近年高）、站上 MA20、最近 10 日低点 -3%~-1.5%（自然止损仍落噪声带）。
    同时锁住 v3.1 原 fixture 不放宽：其 RSI 仍在超买区（>65）——两组只差尾部形态。
    """
    from finance_agent.metrics.technical import calc_technical

    cooled = build_synthetic_state("perfect_cooled")
    k = cooled["kline"]
    close = k["收盘"].astype(float)
    tech = calc_technical(k)
    rsi = tech["RSI"]["14"][-1]
    j = tech["KDJ"]["J"][-1]
    assert 55.0 <= rsi <= 65.0, f"RSI14={rsi}"
    assert j <= 90.0, f"J={j}"
    last = float(close.iloc[-1])
    dd_high = last / float(close.tail(250).max()) - 1
    assert -0.045 <= dd_high <= -0.010, f"距年高={dd_high:.4f}"
    vs_ma20 = last / float(close.iloc[-20:].mean()) - 1
    assert 0.002 <= vs_ma20 <= 0.040, f"距MA20={vs_ma20:.4f}"
    low10 = last / float(close.iloc[-10:].min()) - 1
    assert 0.015 <= low10 <= 0.030, f"最近10日低点距现价={-low10:.4f}"

    # v3.1 对照臂保持超买（补跑只改尾部形态这一个变量）
    base = build_synthetic_state("perfect")
    base_rsi = calc_technical(base["kline"])["RSI"]["14"][-1]
    assert base_rsi > 65.0, f"v3.1 RSI={base_rsi}（对照臂应仍在超买区）"


def test_tight_tail_collapses_stop_band_but_keeps_closes():
    """H2 直接测试 fixture：收盘价与 perfect_cooled 逐字一致，仅缩影线 → ATR 腰斩。

    锁四件事：①收盘序列与 cooled 完全一致（RSI/收盘口径波动/趋势/beta 不变，
    单变量=影线）；②尾部 ATR 明显变小；③代码止损带 [close−2ATR, close−1ATR]
    两条边距现价都 ≤1.5×日σ（trader 带内任选都是「窄于噪声」），而 v4 对照臂
    下沿 ≥2σ（上次 buy 依据的宽止损仍可得）；④止损带落在 sanity 放宽带内
    （不触发价位校验的带外打回）。
    """
    from finance_agent.metrics.levels import calc_price_levels

    tight = build_synthetic_state("perfect_tight")
    base = build_synthetic_state("perfect_cooled")
    kt, kb = tight["kline"], base["kline"]
    assert kt["收盘"].tolist() == kb["收盘"].tolist()  # 单变量：只差影线

    ct = kt["收盘"].astype(float)
    sigma = float(ct.pct_change().dropna().std())
    assert 0.015 <= sigma <= 0.030

    pl_t, pl_b = calc_price_levels(kt), calc_price_levels(kb)
    assert pl_t["atr"] <= 0.6 * pl_b["atr"]  # ATR 明显塌缩

    last = pl_t["entry_ref"]
    for edge in (pl_t["stop_band_long"]["low"], pl_t["stop_band_long"]["high"]):
        dist_sigma = (last - edge) / last / sigma
        assert 0 < dist_sigma <= 1.5, f"tight 止损带边 {edge}={dist_sigma:.2f}σ"
    low_b = (pl_b["entry_ref"] - pl_b["stop_band_long"]["low"]) / pl_b["entry_ref"] / sigma
    assert low_b >= 2.0, f"cooled 对照臂带下沿={low_b:.2f}σ（应仍可得宽止损）"

    lo, hi = pl_t["full_band"]
    assert lo <= pl_t["stop_band_long"]["low"] and pl_t["stop_band_long"]["high"] <= hi
    assert (kt["最高"] >= kt[["开盘", "收盘"]].max(axis=1)).all()
    assert (kt["最低"] <= kt[["开盘", "收盘"]].min(axis=1)).all()


def test_defect_case_is_insolvent_and_illiquid():
    d = build_synthetic_state("defect")
    bs = d["balance_sheet"].iloc[0]
    assert bs["负债合计"] > bs["资产总计"]  # 资不抵债
    assert d["latest_period_snapshot"]["资产负债率(%)"] == 110.0
    assert d["kline"]["成交量"].iloc[-1] <= 100  # 成交枯竭
    assert d["cash_flow_statement"].iloc[0]["经营活动产生的现金流量净额"] < 0


def test_no_catalyst_events():
    for case in ("perfect", "defect"):
        s = build_synthetic_state(case)
        assert s["news_list"] == [] and s["announcements"] == []
        assert s["key_events"] == []


def test_unknown_case_raises():
    with pytest.raises(ValueError):
        build_synthetic_state("nope")
