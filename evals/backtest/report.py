"""回测批次准入与报告四段披露（spec decision-backtest「历史离线回放」「知识泄漏探针」
「分层市场状态抽样」「回测批次预登记与报告生命周期」，delta add-backtest-leakage-controls）。

本模块只做**判定与组装**（纯函数，便于单测），落盘/渲染由 run_backtest 与
Task 4 的 md 渲染承担。三段职责：

1. `assert_clean_window`：干净窗口硬条件——每个决策日距跑批日（as_of）≥ 主评估窗口
   （默认 20）个交易日，全部样本可完成结算。以**交易日**计（基准指数日期序列），
   不是自然日；基准不可得 → 保守判不通过（不静默放行）。
2. `build_disclosures`：报告五键（batch_kind / preregister / clean_window /
   leakage_probe / regime_coverage），探针段须能表达三态——可测且低于阈值 /
   超阈（降级）/ 不可测（`direction_hit_rate is None`）。
3. `build_conclusion`：结论句式唯一生成点（便于 Task 5 与 Δ1 收口纪律对接）。
   通路验证批不得出现 skill / 赚钱能力结论句；超阈降级为「泄漏污染下的上界证据」；
   探针不可测按通路验证处理。
"""

from __future__ import annotations

from bisect import bisect_right
from typing import Any

import pandas as pd

BENCHMARK_INDEX_CODE = "000300"  # 沪深300（与 leakage_probe / 生产基准同源）
ALL_REGIMES: tuple[str, ...] = ("bull", "bear", "sideways")
NO_SAMPLE_CONCLUSION = "样本不足，无法判定"
INVALID_SANITY_CONCLUSION = "invalid: Sharpe>3 未附 sanity check 说明"

POSITIONING_SKILL = "skill"
POSITIONING_PATHWAY = "pathway"

# 探针段状态（三态 + 缺失）
PROBE_MEASURABLE = "measurable"
PROBE_DOWNGRADED = "downgraded"
PROBE_UNMEASURABLE = "unmeasurable"
PROBE_MISSING = "missing"


def _load_benchmark() -> pd.DataFrame | None:
    """默认基准来源：`fetch_index_kline("000300")`；取数异常 → None（保守判不通过）。"""
    try:
        from finance_agent.data.akshare_client import AKShareClient

        return AKShareClient().fetch_index_kline(BENCHMARK_INDEX_CODE, days=1500)
    except Exception:  # noqa: BLE001 - 基准不可得不应炸整批，由判定层保守处置
        return None


def _trading_dates(benchmark: pd.DataFrame | None) -> list[str] | None:
    """基准帧 → 升序去重交易日序列；帧不可用 → None。"""
    if benchmark is None or not isinstance(benchmark, pd.DataFrame) or benchmark.empty:
        return None
    if "日期" not in benchmark.columns:
        return None
    return sorted({str(x)[:10] for x in benchmark["日期"].tolist()})


def _trading_days_between(trading: list[str], decision_date: str, as_of: str) -> int:
    """decision_date 之后到 as_of（含）的交易日数；decision_date ≥ as_of → 0。"""
    start = bisect_right(trading, decision_date)
    end = bisect_right(trading, as_of)
    return max(0, end - start)


def assert_clean_window(
    decision_dates: list[str],
    *,
    as_of: str,
    window_days: int = 20,
    benchmark: pd.DataFrame | None = None,
) -> dict[str, Any]:
    """干净窗口判定（交易日口径）。

    Args:
        decision_dates: 本批全部决策日（ISO `YYYY-MM-DD`）。
        as_of: 跑批日（判定基准）。
        window_days: 主评估窗口交易日数（默认 20，与结算主窗口同源）。
        benchmark: 基准日 K 帧（需含「日期」列）；None → `fetch_index_kline("000300")`。

    Returns:
        `{"passed": bool, "reason": str}`；任一决策日不足窗口 → passed False，
        reason 含具体决策日与实测交易日数。基准不可得 → 保守判不通过。
    """
    frame = benchmark if benchmark is not None else _load_benchmark()
    trading = _trading_dates(frame)
    if not trading:
        return {
            "passed": False,
            "reason": (
                f"基准行情不可得（{BENCHMARK_INDEX_CODE}），无法判定交易日距离，"
                "保守判不通过（干净窗口未过 → 按通路验证处理）"
            ),
        }
    dates = [str(d)[:10] for d in decision_dates]
    if not dates:
        return {
            "passed": True,
            "reason": f"无决策日（空样本）；as_of={as_of}，window_days={window_days}",
        }
    counts = {d: _trading_days_between(trading, d, as_of) for d in dates}
    short = [d for d in dates if counts[d] < window_days]
    if short:
        detail = "；".join(f"{d} 距 as_of 仅 {counts[d]} 个交易日" for d in short)
        return {
            "passed": False,
            "reason": (
                f"决策日 {', '.join(short)} 距 as_of({as_of}) 不足 {window_days} 个交易日"
                f"（{detail}）"
            ),
        }
    return {
        "passed": True,
        "reason": (
            f"全部 {len(dates)} 个决策日距 as_of({as_of}) ≥ {window_days} 个交易日"
            f"（最少 {min(counts.values())}）"
        ),
    }


def probe_state(probe: dict[str, Any] | None) -> str:
    """探针读数三态：measurable / downgraded / unmeasurable（rate None）+ missing。"""
    if probe is None:
        return PROBE_MISSING
    rate = probe.get("direction_hit_rate")
    if rate is None:
        return PROBE_UNMEASURABLE
    if probe.get("downgraded"):
        return PROBE_DOWNGRADED
    return PROBE_MEASURABLE


def _probe_disclosure(probe: dict[str, Any] | None) -> dict[str, Any]:
    state = probe_state(probe)
    if probe is None:
        return {
            "state": state,
            "probe_n": None,
            "questions_per_ticker": None,
            "direction_hit_rate": None,
            "magnitude_hit_rate": None,
            "event_hit_rate": None,
            "unknown_ratio": None,
            "threshold": None,
            "downgraded": False,
        }
    return {
        "state": state,
        "probe_n": probe.get("probe_n"),
        "questions_per_ticker": probe.get("questions_per_ticker"),
        "direction_hit_rate": probe.get("direction_hit_rate"),
        "magnitude_hit_rate": probe.get("magnitude_hit_rate"),
        "event_hit_rate": probe.get("event_hit_rate"),
        "unknown_ratio": probe.get("unknown_ratio"),
        "threshold": probe.get("threshold"),
        "downgraded": bool(probe.get("downgraded")),
    }


def resolve_positioning(
    *,
    batch_kind: str | None,
    clean_window: dict[str, Any] | None,
    probe: dict[str, Any] | None,
) -> str:
    """批次定位：skill（可产出 skill 结论句）或 pathway（通路验证，剥离 skill 句）。

    仅正式批 + 干净窗口通过 + 探针可测（rate 非 None）→ skill；其余（含探针超阈降级，
    仍属可下结论的上界证据）保持 skill，但由 `build_conclusion` 换用降级句式。
    """
    if batch_kind != "formal":
        return POSITIONING_PATHWAY
    if clean_window is None or not clean_window.get("passed", False):
        return POSITIONING_PATHWAY
    if probe is None or probe.get("direction_hit_rate") is None:
        return POSITIONING_PATHWAY
    return POSITIONING_SKILL


def build_disclosures(
    *,
    preregistration: Any = None,
    clean_window: dict[str, Any] | None = None,
    probe: dict[str, Any] | None = None,
    regime_covered: list[str] | tuple[str, ...] = (),
    regime_limited: bool = False,
    batch_kind: str | None = "pathway",
) -> dict[str, Any]:
    """报告五键：batch_kind / preregister（path+valid）/ clean_window / leakage_probe /
    regime_coverage。探针段三态由 `_probe_disclosure` 表达。"""
    preregister: dict[str, Any] | None = None
    if preregistration is not None:
        preregister = {
            "path": str(preregistration.path),
            "valid": bool(preregistration.valid),
        }
    return {
        "batch_kind": batch_kind,
        "preregister": preregister,
        "clean_window": clean_window,
        "leakage_probe": _probe_disclosure(probe),
        "regime_coverage": {
            "covered": sorted({str(r) for r in regime_covered if r}),
            "limited": bool(regime_limited),
            "all_regimes": list(ALL_REGIMES),
        },
    }


def build_conclusion(
    base_conclusion: str,
    *,
    batch_kind: str | None = "pathway",
    clean_window: dict[str, Any] | None = None,
    probe: dict[str, Any] | None = None,
    sanity: str = "valid",
    regime_covered: list[str] | tuple[str, ...] = (),
    regime_limited: bool = False,
) -> str:
    """结论句式唯一生成点。

    - sanity 不通过 → 数据质量覆盖句（既有行为，优先级最高）。
    - 通路验证定位 → 无 skill / 赚钱能力结论句（无读数可下结论时保留原句）。
    - 探针超阈（可测且 downgraded）→ 「泄漏污染下的上界证据（真实 skill ≤ 读数）」。
    - 探针不可测 → 按通路验证处理（reason 写明「探针不可测」）。
    - regime 覆盖受限 → 结论限定于已覆盖 regime，不外推。
    """
    if sanity != "valid":
        return INVALID_SANITY_CONCLUSION
    positioning = resolve_positioning(batch_kind=batch_kind, clean_window=clean_window, probe=probe)
    if positioning == POSITIONING_PATHWAY:
        if base_conclusion == NO_SAMPLE_CONCLUSION:
            # 无读数可下结论：保持旧句（红线：样本积累中不得产出结论句）
            return base_conclusion
        reasons: list[str] = []
        if batch_kind != "formal":
            reasons.append(f"batch_kind={batch_kind or 'pathway'}")
        elif clean_window is None or not clean_window.get("passed", False):
            reasons.append("干净窗口未过")
        if probe is None:
            reasons.append("探针缺失")
        elif probe.get("direction_hit_rate") is None:
            reasons.append("探针不可测")
        suffix = f"（{'；'.join(reasons)}）" if reasons else ""
        return f"通路验证定位{suffix}：本批不产出 skill / 赚钱能力结论句，读数仅用于设施通路验证"
    if probe is not None and probe.get("downgraded"):
        rate = probe.get("direction_hit_rate")
        threshold = probe.get("threshold")
        rate_text = f"{rate:.0%}" if isinstance(rate, (int, float)) else "未知"
        threshold_text = f"{threshold:.0%}" if isinstance(threshold, (int, float)) else "预登记值"
        return (
            f"泄漏污染下的上界证据（真实 skill ≤ 读数）：{base_conclusion}；"
            f"探针方向命中率 {rate_text} > 阈值 {threshold_text}"
            f"（n={probe.get('probe_n')}），不得单独作为赚钱能力主张"
        )
    sentence = base_conclusion
    if regime_limited and regime_covered:
        sentence += (
            f"（本批仅覆盖 {', '.join(sorted({str(r) for r in regime_covered}))} regime，"
            "结论不外推至未覆盖 regime）"
        )
    return sentence
