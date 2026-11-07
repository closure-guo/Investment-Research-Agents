"""合成案例 fixture v3.1（2026-11-06 零买入第二轮 P0）。

数据完全离线：以 ``finance_agent.nodes.fetch._stub_fetch_data`` 的键集为底
（与真实 fetch_data 输出同构，键集由 TestStubRealKeysetParity 守卫），仅替换数值。
两套数据自洽：

- perfect：低负债（25%）、现金流覆盖利润（经营现金流/归母净利 > 1）、
  三年营收利润双增、带真实波动的上升趋势 + 尾部回调修复、成交活跃、
  无新闻/公告/事件（**没有日历催化剂——这正是要测的真空期场景**）；
- defect：完美数据 + 资不抵债（负债 1100 > 资产 1000）+ 成交枯竭（量 50）
  + 经营现金流为负 —— 回归门禁「必回避」用例的底稿。

v2（Task 4 首轮 run 揭露 fixture 退化后重建）：

- 三表改为 A 股财报原生「元」口径（原为亿元），比率不变、恒等式严格成立；
- ``stock_quote`` 补 market_cap/PE/PB、``industry_pe`` 补 avg_pe，
  估值链路不再因字段缺失整体判空；
- K 线改为带真实波动的随机游走，尾部 12 日回调 + 13 日修复，
  收在年内高点略下方、站上 20 日均线。

v3（独立随机流导致 beta 为负、被风控辩论读作数据异常）：个股收益改由基准驱动——
``stock_ret = 1.13 * bench_ret + 0.019 * rng42 + 0.0035``，基准为
``0.0004 + 0.010 * rng7``（日波动 1.0%，年化约 15%，CSI300 量级）。
``risk_metrics.beta`` 实测约 0.89，落在验收区间 [0.6, 1.2] 且贴合目标 ≈0.9，
不再是被读作占位值的 1.0 或负值。

参数标定说明：规格给的 0.95 系数与 idio 0.004 无法同时满足既有测试——
idio 0.004 时个股日波动仅约 1.03%（低于下限 0.015）；而 0.95 系数在尾部 25 日
固定形态替换掉基准成分、叠加 seed 42 抽样协方差后，实测 beta 仅约 0.73。
故 idio 取 0.019（日波动约 1.9%，年化约 30%）、系数标定到 1.13，使实测 beta
≈0.89；``drift_adj = 0.0035`` 用于维持既有上行断言（末收 / first60 均值
≈1.47）。尾部回调 -0.7%/日；修复斜率 +0.6%/日 是使末收盘低于年内高点的约束
（+0.9% 会反超年内高点）。

私有函数 ``_stub_fetch_data`` 仅在实验代码内引用；其键集有专门守卫测试，
若上游增删键本模块测试会先红。
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from finance_agent.nodes.fetch import _stub_fetch_data

SYN_CODE = "999999.SH"
SYN_NAME = "合成案例"
_DECISION_DATE = "2026-10-30"

# ── K 线 v3 参数（确定性：固定 seed，无时间/网络依赖；个股由基准驱动）──
_BENCH_SEED = 7
_BENCH_DRIFT = 0.0004
_BENCH_VOL = 0.010  # 日波动 1.0% → 年化约 15%
_STOCK_BENCH_LOADING = 1.13  # 标定值：实测 beta（含尾部替换/抽样噪声）≈0.89
_STOCK_IDIO_SEED = 42
_STOCK_IDIO_VOL = 0.019  # 见 docstring：0.004 时日波动 1.03%，低于测试下限
_STOCK_DRIFT_ADJ = 0.0035
_TAIL_DAYS = 25
_PULLBACK_DAYS = 12
_PULLBACK_RET = -0.007
_REPAIR_RET = 0.006
# ── v4 冷却尾（2026-11-06 严格二分补测：只剥离超买残差，不动窄止损）──
# 12 日回调（同 v3.1）+ 6 日修复（+1.2%/日）+ 7 日横盘微跌
# （-0.6/-0.6/-0.5/-0.4/-0.3/+1.0/+0.9）。末收 ≈17.66（距 250 日高点
# -2.27%）、RSI14≈62 / KDJ-J≈58（v3.1 为 71.3 / 95.8），但最近 10 日低点
# 仅 -1.87%——「止损窄于噪声」的条件原样保留，用于把超买残差从行为层
# 结论中剥离：出 buy→超买是主因；仍 watch→止损结构缺陷坐实。
_COOL_REPAIR_DAYS = 6
_COOL_REPAIR_RET = 0.012
_COOL_OSC = (-0.006, -0.006, -0.005, -0.004, -0.003, 0.010, 0.009)
# ── v5 窄影线尾（2026-11-06 H2 直接测试：只缩 ATR，收盘价与 v4 逐字相同）──
# 收盘序列与 ``cooled`` 完全一致（RSI/趋势/波动/beta 全部不变），仅把尾部
# 25 日影线从 ±0.8% 收窄到 ±0.2%：ATR ≈ mean|ret|+0.4%（原 +1.6%）→
# 代码止损带 [close−2ATR, close−1ATR] 整条落入噪声区（≈0.7σ~1.3σ），
# trader 无论选带内哪条边都是「止损窄于噪声」。
# stop_band = [close−2×ATR, close−1×ATR]（metrics/levels.py 确定性公式）。
_TIGHT_SHADOW_ROWS = 25
_TIGHT_SPAN_HI = 1.002
_TIGHT_SPAN_LO = 0.998
_ILLIQUID_DAYS = 30
_ILLIQUID_VOLUME = 50.0


def _benchmark_returns(rows: int) -> np.ndarray:
    """基准日收益（seed 7）——个股收益由它驱动，两条序列共享同一市场成分。"""
    rng = np.random.default_rng(_BENCH_SEED)
    return _BENCH_DRIFT + _BENCH_VOL * rng.standard_normal(rows)


def _ohlcv_from_returns(
    dates: pd.Index,
    returns: np.ndarray,
    start_price: float,
    volumes: list[float],
    *,
    tight_tail_rows: int = 0,
) -> pd.DataFrame:
    """由逐日收益派生 OHLCV（open=前收，high/low=max/min×1.008/0.992）。

    ``tight_tail_rows``：末 N 行影线改用 ±0.2% 振幅（H2 测试：缩 ATR 缩止损带，
    收盘价不受影响）。只应用 ``returns[1:]``：首日收盘即 ``start_price``，
    ``returns[0]`` 不参与复利。
    """
    closes = [start_price]
    for ret in returns[1:]:
        closes.append(round(closes[-1] * (1.0 + ret), 2))
    opens = [closes[0]] + closes[:-1]
    n = len(closes)
    tight_from = n - tight_tail_rows
    highs: list[float] = []
    lows: list[float] = []
    for i, (o, c) in enumerate(zip(opens, closes, strict=True)):
        if i >= tight_from:
            span_hi, span_lo = _TIGHT_SPAN_HI, _TIGHT_SPAN_LO
        else:
            span_hi, span_lo = 1.008, 0.992
        highs.append(round(max(o, c) * span_hi, 2))
        lows.append(round(min(o, c) * span_lo, 2))
    return pd.DataFrame(
        {
            "日期": dates,
            "开盘": opens,
            "收盘": closes,
            "最高": highs,
            "最低": lows,
            "成交量": volumes,
        }
    )


def _kline(
    rows: int = 300,
    *,
    end: str = _DECISION_DATE,
    illiquid: bool = False,
    tail: str = "repair",
    tight_tail: bool = False,
) -> pd.DataFrame:
    """确定性 K 线 v3：基准驱动（loading 1.13，实测 beta≈0.89）+ 特质波动 + 尾部回调/修复。

    ``tail="repair"``（v3.1）：13 日修复收在年高略下方，RSI≈71（超买）。
    ``tail="cooled"``（v4）：6 日修复 + 7 日横盘微跌，RSI≈62 / J≈58，
    末收仍贴近年高、最近 10 日低点 -1.9%（窄止损条件保留）。
    ``tight_tail=True``（v5，H2 测试）：收盘价与 cooled 逐字相同，仅末 25 日
    影线 ±0.2%——ATR 塌缩 → 代码止损带 [close−2ATR, close−1ATR] 整条落入
    日波动噪声区，而收盘口径的日 σ（风控引用的「噪声」基准）不变。
    ``illiquid=True`` 时末 30 日量降至 50（枯竭，覆盖修复段的 ×1.2）。
    """
    benchmark = _benchmark_returns(rows)
    idio = np.random.default_rng(_STOCK_IDIO_SEED).standard_normal(rows)
    returns = _STOCK_BENCH_LOADING * benchmark + _STOCK_IDIO_VOL * idio + _STOCK_DRIFT_ADJ
    returns[-_TAIL_DAYS : -_TAIL_DAYS + _PULLBACK_DAYS] = _PULLBACK_RET
    if tail == "cooled":
        repair_start = rows - _TAIL_DAYS + _PULLBACK_DAYS
        returns[repair_start : repair_start + _COOL_REPAIR_DAYS] = _COOL_REPAIR_RET
        returns[repair_start + _COOL_REPAIR_DAYS :] = _COOL_OSC
    elif tail == "repair":
        returns[-_TAIL_DAYS + _PULLBACK_DAYS :] = _REPAIR_RET
    else:
        raise ValueError(f"未知尾部形态: {tail!r}（仅支持 repair/cooled）")
    dates = pd.bdate_range(end=end, periods=rows).strftime("%Y-%m-%d")
    return _ohlcv_from_returns(
        dates,
        returns,
        12.0,
        _kline_volumes(rows, illiquid),
        tight_tail_rows=_TIGHT_SHADOW_ROWS if tight_tail else 0,
    )


def _kline_volumes(rows: int, illiquid: bool) -> list[float]:
    volumes = [8000.0 + (i % 5) * 200.0 for i in range(rows)]
    repair_start = rows - _TAIL_DAYS + _PULLBACK_DAYS
    for idx in range(repair_start, rows):
        volumes[idx] *= 1.2
    if illiquid:
        for idx in range(rows - _ILLIQUID_DAYS, rows):
            volumes[idx] = _ILLIQUID_VOLUME
    return volumes


def _benchmark_kline(rows: int = 300, *, end: str = _DECISION_DATE) -> pd.DataFrame:
    """基准序列（seed 7）——与个股共享市场成分，beta 由二者协方差估出。"""
    returns = _benchmark_returns(rows)
    volumes = [8000.0 + (i % 5) * 200.0 for i in range(rows)]
    dates = pd.bdate_range(end=end, periods=rows).strftime("%Y-%m-%d")
    return _ohlcv_from_returns(dates, returns, 3000.0, volumes)


def _balance_sheet(*, insolvent: bool) -> pd.DataFrame:
    """三年资产负债表，单位=元（A 股财报原生口径），资产=负债+权益 严格成立。

    行数须与利润表三年对齐：``calc_profitability`` 按利润表年份逐行索引
    ``balance_sheet.iloc[i]``（消费端冒烟暴露过 2 行越界）。
    """
    if insolvent:  # 负债 1100 > 资产 1000，权益 -100
        liabilities, equity = 1100.0e8, -100.0e8
    else:  # 负债 250 / 资产 1000 = 25%
        liabilities, equity = 250.0e8, 750.0e8
    return pd.DataFrame(
        {
            "报告日": ["20251231", "20241231", "20231231"],
            "资产总计": [1000.0e8, 900.0e8, 800.0e8],
            "负债合计": [liabilities, 270.0e8, 240.0e8],
            "所有者权益(或股东权益)合计": [equity, 630.0e8, 560.0e8],
            "归母所有者权益": [equity, 630.0e8, 560.0e8],
            "流动资产合计": [600.0e8, 540.0e8, 480.0e8],
            "流动负债合计": [150.0e8, 135.0e8, 120.0e8],
            "存货": [60.0e8, 54.0e8, 48.0e8],
            "货币资金": [400.0e8, 360.0e8, 320.0e8],
            "短期借款": [0.0, 0.0, 0.0],
            "长期借款": [0.0, 0.0, 0.0],
            "应付债券": [0.0, 0.0, 0.0],
            "一年内到期的非流动负债": [0.0, 0.0, 0.0],
            "累计折旧": [40.0e8, 36.0e8, 32.0e8],
            "未分配利润": [739.5e8, 444.5e8, 200.0e8],  # 期初+净利-分红 勾稽
        }
    )


def _income_statement() -> pd.DataFrame:
    """三年利润表，单位=元，净利润=利润总额−所得税 严格成立；毛利率 45%/43%/41%。"""
    return pd.DataFrame(
        {
            "报告日": ["20251231", "20241231", "20231231"],
            "营业收入": [1200.0e8, 1000.0e8, 850.0e8],
            "营业成本": [660.0e8, 570.0e8, 501.5e8],
            "利润总额": [420.0e8, 350.0e8, 297.5e8],
            "所得税费用": [105.0e8, 87.5e8, 74.4e8],
            "净利润": [315.0e8, 262.5e8, 223.1e8],
            "归母净利润": [315.0e8, 262.5e8, 223.1e8],
            "利息费用": [2.0e8, 2.0e8, 2.0e8],
        }
    )


def _cash_flow(*, negative_operating: bool) -> pd.DataFrame:
    """三年现金流量表，单位=元，经营+投资+筹资=净增加额 严格成立。"""
    operating = -200.0e8 if negative_operating else 350.0e8
    return pd.DataFrame(
        {
            "报告日": ["20251231", "20241231", "20231231"],
            "经营活动产生的现金流量净额": [operating, 290.0e8, 250.0e8],
            "投资活动产生的现金流量净额": [-80.0e8, -70.0e8, -60.0e8],
            "筹资活动产生的现金流量净额": [-30.0e8, -25.0e8, -20.0e8],
            "现金及现金等价物净增加额": [operating - 110.0e8, 195.0e8, 170.0e8],
            "分配股利、利润或偿付利息所支付的现金": [20.0e8, 18.0e8, 16.0e8],
        }
    )


def _latest_period_snapshot(*, insolvent: bool) -> dict[str, Any]:
    """与三表一致的最近报告期快照（亿元口径，供 PE/同比推导）。"""
    return {
        "报告日": "2025-12-31",
        "期类型": "年报",
        "营业总收入(累计)": 1200.0,
        "归母净利润(累计)": 315.0,
        "营业成本(累计)": 660.0,
        "毛利率(%)": 45.0,
        "资产负债率(%)": 110.0 if insolvent else 25.0,
        "存货": 60.0,
        "合同负债": 80.0,
        "上年同期营业总收入": 1000.0,
        "上年同期归母净利润": 262.5,
        "营收同比(%)": 20.0,
        "归母净利同比(%)": 20.0,
        "missing": [],
    }


def build_synthetic_state(case: str, *, decision_date: str = _DECISION_DATE) -> dict[str, Any]:
    """构造合成案例的 fetch_data 同构状态（fixture v3）。

    Args:
        case: ``"perfect"``（v3.1 完美案例）、``"perfect_cooled"``（v4 超买残差
            已剥离：RSI≈62 但最近 10 日低点仍 -1.9%，窄止损条件保留）、
            ``"perfect_tight"``（v5 H2 直接测试：收盘价与 perfect_cooled 逐字
            相同、仅尾部影线 ±0.2%——ATR 塌缩使代码止损带整条落入噪声区）或
            ``"defect"``（资不抵债+枯竭）。
        decision_date: K 线截止日（须为交易日字符串）。

    Returns:
        键集 = ``_stub_fetch_data`` 输出 ∪ ``{stock_code, stock_name}``。
    """
    if case not in ("perfect", "perfect_cooled", "perfect_tight", "defect"):
        raise ValueError(
            f"未知合成案例: {case!r}（仅支持 perfect/perfect_cooled/perfect_tight/defect）"
        )
    defect = case == "defect"
    kline = _kline(
        end=decision_date,
        illiquid=defect,
        tail="cooled" if case in ("perfect_cooled", "perfect_tight") else "repair",
        tight_tail=case == "perfect_tight",
    )
    state: dict[str, Any] = {
        "stock_code": SYN_CODE,
        "stock_name": SYN_NAME,
        **_stub_fetch_data({"stock_code": SYN_CODE, "stock_name": SYN_NAME}),
        "balance_sheet": _balance_sheet(insolvent=defect),
        "income_statement": _income_statement(),
        "cash_flow_statement": _cash_flow(negative_operating=defect),
        "financial_indicators": None,
        "industry_info": {"industry": "电子元件", "name": SYN_NAME},
        "stock_quote": {
            "price": float(kline["收盘"].iloc[-1]),
            "market_cap": 3780.0e8,  # 元：3780 亿；÷ 归母净利 315 亿 = PE 12
            "PE": 12.0,
            "PB": None if defect else 5.0,  # 资不抵债（权益为负）时 PB 无意义，置 None
            "name": SYN_NAME,
            "code": SYN_CODE,
        },
        "kline": kline,
        "benchmark_kline": _benchmark_kline(end=decision_date),
        "industry_pe": {"avg_pe": 18.0},  # GARP 视角：PE 12 < 行业 18
        "quarterly_income": None,
        "peer_financials": None,
        "latest_period_snapshot": _latest_period_snapshot(insolvent=defect),
        "news_list": [],
        "announcements": [],
        "key_events": [],
    }
    return state
