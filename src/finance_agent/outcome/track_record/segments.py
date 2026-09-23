"""add-track-record-stage-c：四维切片指标（行业/市值桶/市场环境/持有期桶）。

每桶输出 {样本数, 胜率, 平均超额}（n<10 标注样本不足）。纯函数离线可测：
- industry：静态行业映射（无映射 → 未知桶；映射表配置化）
- market_cap：外部市值（亿）→ 桶；缺 → 未知
- market_environment：基准 250 日均线牛熊信号 map {prediction_id: bull|bear}
- holding_period：resolved_at - created_at 日历天数分桶

口径对齐 metrics.md §1.8①：胜率与平均超额仅统计 long/short 方向的观点
（avoidance 终态与 neutral 方向 resolved_* 存量行不进；计数/分桶分母仍含全量）。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from typing import Any

# 静态行业映射（覆盖常用标的，未知标的落「未知」桶；可扩展）
INDUSTRY_MAP: dict[str, str] = {
    "600519": "白酒",
    "300750": "电力设备",
    "300308": "电子",
    "603986": "电子",
    "301520": "电子",
    "688256": "计算机",
    "002585": "医药",
    "000858": "白酒",
    "601318": "非银金融",
    "600036": "银行",
}

MARKET_CAP_BUCKETS: tuple[tuple[str, Callable[[float], bool]], ...] = (
    ("<100亿", lambda v: v < 100),
    ("100-500亿", lambda v: 100 <= v < 500),
    ("500亿+", lambda v: v >= 500),
)

HOLDING_BUCKETS: tuple[tuple[str, Callable[[int], bool]], ...] = (
    ("0-5天", lambda d: d <= 5),
    ("6-20天", lambda d: 6 <= d <= 20),
    ("21-60天", lambda d: 21 <= d <= 60),
    ("61天+", lambda d: d > 60),
)


@dataclass
class SegmentBucket:
    name: str
    sample_size: int
    win_rate: float | None
    avg_excess: float | None
    insufficient: bool


@dataclass
class DimensionResult:
    dimension: str
    buckets: list[SegmentBucket]
    total: int
    settled: int


def _is_long_short(p: dict[str, Any]) -> bool:
    """主指标人口限可执行决策（long/short）；neutral 方向与 avoidance 终态不在其中（§1.8①）。"""
    return p.get("direction") in ("long", "short")


def _win_flag(p: dict[str, Any]) -> bool | None:
    """胜率标志只认 long/short 的 resolved_win/loss；其余（neutral 方向 / avoidance /
    resolved_neutral / open / unresolvable）返回 None，不进分子分母。"""
    if not _is_long_short(p):
        return None
    status = p.get("status")
    if status == "resolved_win":
        return True
    if status == "resolved_loss":
        return False
    return None


def _bucket_metrics(items: list[dict[str, Any]]) -> SegmentBucket:
    wins = [_win_flag(p) for p in items]
    decided = [w for w in wins if w is not None]
    n = len(items)
    win_rate = None
    if decided:
        win_rate = round(sum(1 for w in decided if w) / len(decided), 4)
    avg_excess = None
    # 超额人口 = long/short 三态（含带内 neutral），排除 avoidance/unresolvable（§1.8①）
    excesses = [
        p["excess_return"]
        for p in items
        if p.get("excess_return") is not None
        and _is_long_short(p)
        and p.get("status") in ("resolved_win", "resolved_loss", "resolved_neutral")
    ]
    if excesses:
        avg_excess = round(sum(excesses) / len(excesses), 4)
    return SegmentBucket(
        name="",
        sample_size=n,
        win_rate=win_rate,
        avg_excess=avg_excess,
        insufficient=n < 10,
    )


def _holding_days(p: dict[str, Any]) -> int | None:
    resolved = p.get("resolved_at")
    created = p.get("created_at")
    if not resolved or not created:
        return None
    try:
        return (date.fromisoformat(str(resolved)[:10]) - date.fromisoformat(str(created)[:10])).days
    except ValueError:
        return None


def segment_by_holding(predictions: list[dict[str, Any]]) -> DimensionResult:
    groups: dict[str, list[dict[str, Any]]] = {name: [] for name, _ in HOLDING_BUCKETS}
    groups["未知"] = []
    for p in predictions:
        d = _holding_days(p)
        if d is None:
            groups["未知"].append(p)
            continue
        for name, cond in HOLDING_BUCKETS:
            if cond(d):
                groups[name].append(p)
                break
    return _dimension("持有期", groups)


def segment_by_industry(
    predictions: list[dict[str, Any]], industry_map: dict[str, str] | None = None
) -> DimensionResult:
    mapping = industry_map or INDUSTRY_MAP
    groups: dict[str, list[dict[str, Any]]] = {}
    for p in predictions:
        ind = mapping.get(str(p.get("symbol", "")).split(".")[0], "未知")
        groups.setdefault(ind, []).append(p)
    return _dimension("行业", groups)


def segment_by_market_cap(
    predictions: list[dict[str, Any]], market_caps: dict[str, float] | None = None
) -> DimensionResult:
    groups: dict[str, list[dict[str, Any]]] = {name: [] for name, _ in MARKET_CAP_BUCKETS}
    groups["未知"] = []
    caps = market_caps or {}
    for p in predictions:
        pid = p.get("prediction_id")
        v = caps.get(pid) if isinstance(pid, str) else None
        if v is None:
            v = caps.get(str(p.get("symbol") or ""))
        if v is None:
            groups["未知"].append(p)
            continue
        for name, cond in MARKET_CAP_BUCKETS:
            if cond(v):
                groups[name].append(p)
                break
    return _dimension("市值", groups)


def segment_by_market_environment(
    predictions: list[dict[str, Any]], market_envs: dict[str, str] | None = None
) -> DimensionResult:
    groups: dict[str, list[dict[str, Any]]] = {"牛": [], "熊": [], "未知": []}
    envs = market_envs or {}
    norm = {"bull": "牛", "bear": "熊"}
    for p in predictions:
        pid = p.get("prediction_id")
        env = envs.get(pid) if isinstance(pid, str) else None
        key = norm.get(env) if isinstance(env, str) else None
        groups[key if key is not None else "未知"].append(p)
    return _dimension("市场环境", groups)


def _dimension(name: str, groups: dict[str, list[dict[str, Any]]]) -> DimensionResult:
    buckets: list[SegmentBucket] = []
    total = 0
    for group, items in groups.items():
        b = _bucket_metrics(items)
        b.name = group
        buckets.append(b)
        total += len(items)
    settled = sum(
        1 for p in (x for items in groups.values() for x in items) if _win_flag(p) is not None
    )
    return DimensionResult(dimension=name, buckets=buckets, total=total, settled=settled)


def market_env_signal(benchmark_closes: list[float], window: int = 250) -> str:
    """基准 250 日均线牛熊判定：最新收盘 > MA250 → bull，< → bear；数据不足 → unknown。"""
    if len(benchmark_closes) < window:
        return "unknown"
    last = benchmark_closes[-1]
    ma = sum(benchmark_closes[-window:]) / window
    return "bull" if last > ma else "bear"


def segment_all(predictions: list[dict[str, Any]], **kwargs: Any) -> list[DimensionResult]:
    return [
        segment_by_holding(predictions),
        segment_by_industry(predictions),
        segment_by_market_cap(predictions, kwargs.get("market_caps")),
        segment_by_market_environment(predictions, kwargs.get("market_envs")),
    ]
