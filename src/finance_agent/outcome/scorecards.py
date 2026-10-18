"""策略族成绩单（delta add-strategy-family-scorecards）：注册表驱动 + 四槽计算。

口径权威：``docs/evals/metrics.md`` §1.13（2026-10-18 登记）与变更 design.md——
口径变更先改登记再动本模块。分层：四槽计算为纯函数（无业务 IO，可单测）；
族数据读取与组装见同模块下半部（调用期读 env，只读不写任何状态）。
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Any, NamedTuple

from finance_agent.execution.config import load_matching_config
from finance_agent.execution.matching import MatchingConfig

logger = logging.getLogger(__name__)

# ── 口径常量（design.md §已知边界：门槛/阈值/参与率/名义仓位集中定义）──
SCORECARD_NOTIONAL = 100_000.0  # 成本估算名义仓位（与撮合配置参数同源换算）
CAPACITY_PARTICIPATION = 0.01  # 参与率上限 1%
MIN_SAMPLES = 10  # 期望值样本门槛
MIN_HISTORY_DAYS = 20  # 最大回撤结算日门槛

# 槽位缺数据原因码（spec：null + 原因码，MUST NOT 折算 0 或静默省略）
REASON_NO_SAMPLES = "no_samples"
REASON_INSUFFICIENT_SAMPLE = "insufficient_sample"
REASON_INSUFFICIENT_HISTORY = "insufficient_history"
REASON_NO_LIQUIDITY_DATA = "no_liquidity_data"


class FamilySpec(NamedTuple):
    """族注册项：``family_id`` + 名称 + 极端样本阈值（组合级/单标的级）。"""

    family_id: str
    name: str
    extreme_threshold: float


class Sample(NamedTuple):
    """族样本：标的、结算日（前 10 字符即日期）、收益（该族口径）。"""

    ticker: str
    date: str
    ret: float


class FamilyContract(NamedTuple):
    """族契约：展示用胜率（数据层按各族口径算好）+ 随行口径注释。"""

    win_rate: float | None
    caliber_note: str


FAMILY_REGISTRY: tuple[FamilySpec, ...] = (
    FamilySpec("predictions", "观点线", extreme_threshold=-0.15),
    FamilySpec("execution", "执行线", extreme_threshold=-0.05),
)

# 虚拟结算族：毛=虚拟收益，净=毛−往返成本估算率；未列入者视为已含费（毛=净）
_COST_BEARING_FAMILIES = frozenset({"predictions"})


def estimate_round_trip_cost_rate(config: MatchingConfig | None = None) -> float:
    """往返成本估算率（design.md §口径细则）：佣金双边 + 印花税 + 中档滑点双边。

    参数同源 ``config/matching_engine.json``（缺文件回落内置默认），禁止复制常量；
    默认参数下 ≈ 0.30%（0.0005 + 0.0005 + 0.002）。
    """
    cfg = config if config is not None else load_matching_config()
    commission = max(cfg.commission_rate * SCORECARD_NOTIONAL, cfg.min_commission) / (
        SCORECARD_NOTIONAL
    )
    tiers = sorted(cfg.slippage_tiers, key=lambda tier: tier.quantile_max)
    slippage_mid = tiers[len(tiers) // 2].slippage_rate  # 三档配置下 index 1 = 0.001
    return commission * 2 + cfg.stamp_tax_rate + slippage_mid * 2


def max_drawdown_from_returns(daily_returns: Sequence[float]) -> float:
    """复利净值最大回撤：``MDD = min(nav/峰值 − 1)``（等价 design 的 ``1 − max(nav/峰值)``）。

    输出 ≤ 0（无回撤 = 0.0）；取符号形式而非 ``1 − nav/峰值``——后者恒 ≥ 0，
    再与 0 取 min 恒得 0（brief 骨架的注释方向与测试的 −0.412 冲突，以测试为准）。
    """
    nav = 1.0
    peak = 1.0
    mdd = 0.0
    for ret in daily_returns:
        nav *= 1.0 + ret
        peak = max(peak, nav)
        mdd = min(mdd, nav / peak - 1.0)
    return mdd


def aggregate_daily_returns(samples: Sequence[Sample]) -> list[float]:
    """按日聚合样本等权收益（日期取前 10 字符），返回升序日收益序列。"""
    by_date: dict[str, list[float]] = {}
    for sample in samples:
        by_date.setdefault(sample.date[:10], []).append(sample.ret)
    return [sum(rets) / len(rets) for _, rets in sorted(by_date.items())]


def _capacity_slot(capacity_inputs: dict[str, float | None] | None) -> dict[str, Any] | None:
    """容量槽主体：最小值 × 参与率 = 建议资金上限；全缺 → None（原因码由调用方给）。"""
    if not capacity_inputs:
        return None
    usable = [adv for adv in capacity_inputs.values() if adv is not None and adv > 0]
    if not usable:
        return None
    min_adv = min(usable)
    excluded = len(capacity_inputs) - len(usable)
    return {
        "suggested_capital": round(min_adv * CAPACITY_PARTICIPATION, 2),
        "min_adv20": min_adv,
        "excluded": excluded,
    }


def build_family_payload(
    spec: FamilySpec,
    samples: Sequence[Sample],
    *,
    contract: FamilyContract,
    capacity_inputs: dict[str, float | None] | None = None,
) -> dict[str, Any]:
    """单族四槽载荷（纯函数）：四槽 → 胜率 → 口径注释，键序即 spec 展示位次序。

    ``capacity_inputs``：请求标的 → 近 20 交易日日均成交额；值 ``None``/非正表示该
    标的数据缺失（计入 ``capacity["excluded"]``）。``None`` 或空 dict＝无任何流动性
    证据 → 容量槽 null + ``no_liquidity_data``（全缺不折算 0）。
    """
    payload: dict[str, Any] = {}

    # ① 期望值：毛/净双值 + 样本量；< 门槛一律 null + 原因码
    if not samples:
        payload["expectancy"] = None
        payload["expectancy_reason"] = REASON_NO_SAMPLES
    elif len(samples) < MIN_SAMPLES:
        payload["expectancy"] = None
        payload["expectancy_reason"] = REASON_INSUFFICIENT_SAMPLE
    else:
        gross = sum(sample.ret for sample in samples) / len(samples)
        cost_rate = (
            estimate_round_trip_cost_rate() if spec.family_id in _COST_BEARING_FAMILIES else 0.0
        )
        payload["expectancy"] = {
            "gross": round(gross, 4),
            "net": round(gross - cost_rate, 4),
            "n": len(samples),
            "insufficient": False,
        }

    # ② 最大回撤：族级日度等权序列 → 同一 MDD 函数；结算日 < 门槛 → null + 原因码
    daily_returns = aggregate_daily_returns(samples)
    if len(daily_returns) < MIN_HISTORY_DAYS:
        payload["max_drawdown"] = None
        payload["max_drawdown_reason"] = REASON_INSUFFICIENT_HISTORY
    else:
        payload["max_drawdown"] = {
            "value": max_drawdown_from_returns(daily_returns),
            "n_days": len(daily_returns),
        }

    # ③ 容量提示：缺数据标的剔除计数，全缺 → null + 原因码
    capacity = _capacity_slot(capacity_inputs)
    if capacity is None:
        payload["capacity"] = None
        payload["capacity_reason"] = REASON_NO_LIQUIDITY_DATA
    else:
        payload["capacity"] = capacity

    # ④ 极端样本明细（族阈值可配置）
    payload["extreme_samples"] = [
        {"ticker": sample.ticker, "date": sample.date[:10], "ret": sample.ret}
        for sample in samples
        if sample.ret <= spec.extreme_threshold
    ]

    # ⑤ 胜率：展示位（四槽之后）；MUST NOT 进入自动处置/门禁/告警
    payload["win_rate"] = contract.win_rate
    payload["caliber_note"] = contract.caliber_note
    return payload
