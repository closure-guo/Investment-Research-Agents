"""回测批次准入与报告四段披露（spec decision-backtest「历史离线回放」「知识泄漏探针」
「分层市场状态抽样」「回测批次预登记与报告生命周期」，delta add-backtest-leakage-controls）。

本模块只做**判定与组装**（纯函数，便于单测），落盘由 run_backtest 承担、md 渲染由
`render_backtest_report_md` 承担。四段职责：

1. `assert_clean_window`：干净窗口硬条件——每个决策日距跑批日（as_of）≥ 主评估窗口
   （默认 20）个交易日，全部样本可完成结算。以**交易日**计（基准指数日期序列），
   不是自然日；基准不可得 / 空样本 → 保守判不通过（不静默放行）。
2. `build_disclosures`：报告五键（batch_kind / preregister / clean_window /
   leakage_probe / regime_coverage），探针段须能表达三态——可测且低于阈值 /
   超阈（降级）/ 不可测（`direction_hit_rate is None`），并披露 `probe_window`。
3. `build_conclusion`：结论句式唯一生成点（便于 Task 5 与 Δ1 收口纪律对接）。
   通路验证批不得出现 skill / 赚钱能力结论句；超阈降级为「泄漏污染下的上界证据」；
   探针不可测按通路验证处理。
4. `render_backtest_report_md`：批次报告 md 渲染（头部含生命周期 status 头，
   渲染后经 `evals.outcome.report.assert_outcome_report` 自校）。
"""

from __future__ import annotations

from bisect import bisect_right
from typing import Any

import pandas as pd

from evals.outcome.report import assert_outcome_report

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

# 渲染层状态文案（三态 + 缺失）；渲染自校与断言以此为准。
PROBE_STATE_TEXT: dict[str, str] = {
    PROBE_MEASURABLE: "可测（低于阈值）",
    PROBE_DOWNGRADED: "超阈（降级：泄漏污染下的上界证据）",
    PROBE_UNMEASURABLE: "不可测（探针不可测）",
    PROBE_MISSING: "未执行（无探针读数）",
}


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
        # D3（Task 3 审查）：空样本不得真空真——无样本可判 → 不通过（与「基准不可得」同属
        # 保守处置，避免 formal 批在零样本时以「窗口干净」身份放行）。
        return {
            "passed": False,
            "reason": (
                f"无样本可判（空决策日集合）；as_of={as_of}，window_days={window_days}，"
                "保守判不通过"
            ),
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
    if probe is None:
        return {
            "state": PROBE_MISSING,
            "probe_n": None,
            "questions_per_ticker": None,
            "direction_hit_rate": None,
            "magnitude_hit_rate": None,
            "event_hit_rate": None,
            "unknown_ratio": None,
            "threshold": None,
            "downgraded": False,
            "probe_window": None,
            "per_window": None,
            "probes_missing": None,
        }
    # 显式 state 优先（`aggregate_probes` 的最差态汇总）；否则按读数三态推导。
    state = probe.get("state") or probe_state(probe)
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
        # 探针窗口（D2）：批内 distinct 决策日各探一次时列出全部窗口；单窗口批亦列。
        "probe_window": list(probe.get("probe_window") or []) or None,
        # 逐窗口读数（审查①）：汇总不掩盖单窗口异常——每项带**它自己**的窗口。
        "per_window": list(probe.get("per_window") or []) or None,
        # 未返回读数的窗口条目数（审查⑥）：None 条目计数披露，不静默丢弃。
        "probes_missing": probe.get("probes_missing"),
    }


def aggregate_probes(
    probes: list[dict[str, Any] | None],
    *,
    windows: list[str] | tuple[str, ...] = (),
) -> dict[str, Any] | None:
    """按批内 distinct 决策日分别探针 → 最差态汇总（D2，Task 3 审查收口）。

    `stratified_sample` 每 regime 取一个决策日，批内决策日可能不唯一；只探首个
    窗口会低估其余窗口的泄漏风险。故逐 distinct 决策日探针后取**最差态**：

    - 任一窗口不可测（`direction_hit_rate is None`）→ 汇总态 `unmeasurable`
      （读数置 None，`resolve_positioning`/`build_conclusion` 按通路验证处理）；
    - 否则任一窗口超阈（`downgraded`）→ `downgraded`，读数为触发降级的最差（最大）命中率；
    - 否则 `measurable`，读数为各窗口最大命中率（保守取高）。

    `probe_window` 列出全部被探窗口，`per_window` 保留逐窗口读数（每项带**它自己**的窗口），
    汇总不掩盖单窗口异常；未返回读数的 `None` 条目计入 `probes_missing`，不静默丢弃。
    成本：探针次数 = distinct 决策日数（≤ regime 数），相对回放 LLM 成本可忽略。
    """
    probes_missing = sum(1 for p in probes if p is None)
    usable = [p for p in probes if p is not None]
    if not usable:
        return None
    states = [probe_state(p) for p in usable]
    rates = [p.get("direction_hit_rate") for p in usable]
    measurable_rates = [r for r in rates if isinstance(r, (int, float))]
    if any(s == PROBE_UNMEASURABLE for s in states):
        # 不可测态：三率一并置 None（审查⑥）——「不可测」却带子读数会误导；
        # unknown_ratio 仍保留（未知占比正是不可测的证据）。
        state = PROBE_UNMEASURABLE
        direction_rate: float | None = None
        magnitude_rate: float | None = None
        event_rate: float | None = None
        downgraded = False
    elif any(bool(p.get("downgraded")) for p in usable):
        state = PROBE_DOWNGRADED
        direction_rate = max(measurable_rates) if measurable_rates else None
        magnitude_rate = _max_optional(p.get("magnitude_hit_rate") for p in usable)
        event_rate = _max_optional(p.get("event_hit_rate") for p in usable)
        downgraded = True
    else:
        state = PROBE_MEASURABLE
        direction_rate = max(measurable_rates) if measurable_rates else None
        magnitude_rate = _max_optional(p.get("magnitude_hit_rate") for p in usable)
        event_rate = _max_optional(p.get("event_hit_rate") for p in usable)
        downgraded = False
    windows_list = [str(w)[:10] for w in windows if str(w).strip()]
    return {
        "state": state,
        "probe_n": sum(int(p.get("probe_n") or 0) for p in usable),
        "questions_per_ticker": usable[0].get("questions_per_ticker"),
        "direction_hit_rate": direction_rate,
        "magnitude_hit_rate": magnitude_rate,
        "event_hit_rate": event_rate,
        "unknown_ratio": _max_optional(p.get("unknown_ratio") for p in usable),
        "threshold": usable[0].get("threshold"),
        "downgraded": downgraded,
        "probe_window": windows_list or None,
        "probes_missing": probes_missing or None,
        "per_window": [
            {
                # 每项带它自己的窗口（审查②）：调用方（run_batch_probe）逐窗口注入；
                # 未标注则 None，不兜底成全部窗口列表（那会把「单窗口读数」冒充成每窗都有）。
                "probe_window": list(p.get("probe_window") or []) or None,
                "state": probe_state(p),
                "direction_hit_rate": p.get("direction_hit_rate"),
                "magnitude_hit_rate": p.get("magnitude_hit_rate"),
                "event_hit_rate": p.get("event_hit_rate"),
                "probe_n": p.get("probe_n"),
                "unknown_ratio": p.get("unknown_ratio"),
                "downgraded": bool(p.get("downgraded")),
            }
            for p in usable
        ],
    }


def _max_optional(values: Any) -> float | None:
    nums = [v for v in values if isinstance(v, (int, float))]
    return max(nums) if nums else None


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
    - regime 覆盖受限 → 结论限定于已覆盖 regime，不外推（**降级句同样限定**：
      上界证据仍是结论句，「不外推」限定不得因换用降级句式而丢失）。
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
    regime_suffix = _regime_suffix(regime_covered, regime_limited)
    if probe is not None and probe.get("downgraded"):
        rate = probe.get("direction_hit_rate")
        threshold = probe.get("threshold")
        rate_text = f"{rate:.0%}" if isinstance(rate, (int, float)) else "未知"
        threshold_text = f"{threshold:.0%}" if isinstance(threshold, (int, float)) else "预登记值"
        return (
            f"泄漏污染下的上界证据（真实 skill ≤ 读数）：{base_conclusion}；"
            f"探针方向命中率 {rate_text} > 阈值 {threshold_text}"
            f"（n={probe.get('probe_n')}），不得单独作为赚钱能力主张{regime_suffix}"
        )
    return base_conclusion + regime_suffix


def _regime_suffix(regime_covered: list[str] | tuple[str, ...], regime_limited: bool) -> str:
    """regime 限定后缀；未受限或未覆盖 → 空串（不产出无意义的「仅覆盖」句）。"""
    if not (regime_limited and regime_covered):
        return ""
    covered = ", ".join(sorted({str(r) for r in regime_covered}))
    return f"（本批仅覆盖 {covered} regime，结论不外推至未覆盖 regime）"


# ---------------------------------------------------------------------------
# md 渲染层（Δ4 Task 4 A）：报告 dict → 带生命周期 status 头的 md
# ---------------------------------------------------------------------------

NOT_PROVIDED = "未提供"
PERF_COLUMNS: tuple[str, ...] = ("CR", "ARR", "Sharpe", "MDD")


def _pct(value: Any) -> str:
    """比例 → 百分数字符串（0.3 → 30%，0.125 → 12.5%）；非数值 → 未提供。"""
    if not isinstance(value, (int, float)):
        return NOT_PROVIDED
    text = f"{value * 100:.1f}".rstrip("0").rstrip(".")
    return f"{text}%"


def _num(value: Any) -> str:
    return f"{value:.4f}" if isinstance(value, (int, float)) else NOT_PROVIDED


def _preregister_text(preregister: dict[str, Any] | None) -> str:
    if not preregister:
        return "未绑定"
    path = preregister.get("path") or NOT_PROVIDED
    if preregister.get("valid"):
        return f"{path}（valid）"
    return f"{path}（无效：valid=False，不得作为正式批身份）"


def _clean_window_lines(clean_window: dict[str, Any] | None) -> list[str]:
    if not clean_window:
        return [f"- 判定：{NOT_PROVIDED}（未判定；pathway 批可不判）"]
    verdict = "通过" if clean_window.get("passed") else "不通过"
    reason = clean_window.get("reason") or NOT_PROVIDED
    return [f"- 判定：{verdict}", f"- 理由：{reason}"]


def _probe_lines(report: dict[str, Any]) -> list[str]:
    raw = report.get("leakage_probe")
    if raw is None:
        # 审查③：无探针时不得渲染「probe_window：未提供（…仅覆盖该窗口）」这类自相矛盾句。
        return [
            f"- 状态：{PROBE_STATE_TEXT[PROBE_MISSING]}",
            "- 处置：本批未执行探针，泄漏风险未测——不得以 skill 定位下结论。",
        ]
    probe: dict[str, Any] = raw
    state = probe.get("state") or probe_state(probe)
    lines = [
        f"- 状态：{PROBE_STATE_TEXT.get(state, state)}",
        (
            f"- 抽样标的数 probe_n："
            f"{probe.get('probe_n') if probe.get('probe_n') is not None else NOT_PROVIDED}"
            f"；题目构成 questions_per_ticker："
            f"{probe.get('questions_per_ticker') if probe.get('questions_per_ticker') is not None else NOT_PROVIDED}"
            "（方向 / 幅度桶 / 事件各一，主指标为方向题）"
        ),
        (
            f"- 方向命中率 direction_hit_rate：{_pct(probe.get('direction_hit_rate'))}"
            f"（阈值 threshold：{_pct(probe.get('threshold'))}）"
        ),
        (
            f"- 幅度桶命中率 magnitude_hit_rate：{_pct(probe.get('magnitude_hit_rate'))}"
            f"；事件题命中率 event_hit_rate：{_pct(probe.get('event_hit_rate'))}"
            "（辅证层，不计入主指标）"
        ),
        f"- 未知占比 unknown_ratio：{_pct(probe.get('unknown_ratio'))}",
    ]
    window = probe.get("probe_window")
    if window:
        lines.append(
            f"- 探针窗口 probe_window：{', '.join(str(w) for w in window)}"
            "（批内 distinct 决策日各探一次，取最差态汇总；仅覆盖该窗口）"
        )
    else:
        lines.append("- 探针窗口 probe_window：未标注（单窗口/未提供）")
    missing = probe.get("probes_missing")
    if missing:
        lines.append(f"- 未返回读数的窗口条目：{missing} 条（计数披露，未静默丢弃）")
    per_window = probe.get("per_window")
    if per_window:
        lines += [
            "",
            "逐窗口读数（聚合规则：不可测 > 超阈 > 可测；不可测态三率一并置空）：",
            "",
            "| 窗口 | 状态 | direction_hit_rate | probe_n | unknown_ratio |",
            "|---|---|---|---|---|",
        ]
        for item in per_window:
            item_window = item.get("probe_window")
            item_window_text = (
                ", ".join(str(w) for w in item_window) if item_window else NOT_PROVIDED
            )
            lines.append(
                f"| {item_window_text} | {PROBE_STATE_TEXT.get(item.get('state'), item.get('state'))} "
                f"| {_pct(item.get('direction_hit_rate'))} "
                f"| {item.get('probe_n') if item.get('probe_n') is not None else NOT_PROVIDED} "
                f"| {_pct(item.get('unknown_ratio'))} |"
            )
    if state == PROBE_DOWNGRADED:
        lines.append(
            "- 处置：超阈 → 结论降级为「泄漏污染下的上界证据（真实 skill ≤ 读数）」，"
            "不得单独作为赚钱能力主张。"
        )
    elif state == PROBE_UNMEASURABLE:
        lines.append(
            "- 处置：探针不可测（无有效答题样本）→ 按通路验证处理，不产出 skill / 赚钱能力结论句。"
        )
    elif state == PROBE_MISSING:
        lines.append("- 处置：本批未执行探针，泄漏风险未测——不得以 skill 定位下结论。")
    return lines


def _regime_lines(report: dict[str, Any]) -> list[str]:
    coverage = report.get("regime_coverage") or {}
    covered = coverage.get("covered") or []
    covered_text = ", ".join(str(r) for r in covered) if covered else NOT_PROVIDED
    limited = bool(coverage.get("limited"))
    verdict = "受限（结论不外推至未覆盖 regime）" if limited else "全覆盖"
    return [f"- covered：{covered_text}", f"- 是否受限：{verdict}"]


def _perf_table_lines(report: dict[str, Any]) -> list[str]:
    table = report.get("perf_table") or {}
    if not table:
        return [f"绩效表：{NOT_PROVIDED}"]
    lines = [
        f"| 策略 | {' | '.join(PERF_COLUMNS)} |",
        f"|{'---|' * (len(PERF_COLUMNS) + 1)}",
    ]
    for name, metrics in table.items():
        cells = " | ".join(_num((metrics or {}).get(col)) for col in PERF_COLUMNS)
        lines.append(f"| {name} | {cells} |")
    best = report.get("best_baseline")
    if best:
        lines += ["", f"（最优基线 best_baseline：{best}）"]
    return lines


def _methodology_lines(report: dict[str, Any]) -> list[str]:
    methodology = report.get("methodology") or {}
    labels = (
        ("entry", "入场/结算语义"),
        ("daily_returns", "日收益口径"),
        ("benchmark", "基准"),
        ("adjust", "复权口径（hfq）"),
        ("system_population", "非可执行决策排除"),
    )
    lines = [f"- {label}：{methodology.get(key) or NOT_PROVIDED}" for key, label in labels]
    lines.append("- 复权：结算/回测区间收益走后复权（hfq），非可执行决策（hold/watch）整条排除。")
    return lines


def render_backtest_report_md(report: dict, *, name: str) -> str:
    """回测报告 dict → md（头部含生命周期 status 头；渲染后自校契约）。

    固定段落顺序：① 干净窗口 ② 泄漏探针（三态 + unknown_ratio + probe_window + 逐窗口读数）
    ③ regime 覆盖 ④ 绩效表（system + 各基线）⑤ 结论（逐字）⑥ 口径说明。
    缺数据如实写「未提供/不适用」，不静默省略。

    Args:
        report: `run_backtest` 返回的报告 dict（或测试构造的最小 dict）。
        name: 报告名（文件名主干，如 `formal-20260923-120000`）。

    Returns:
        md 文本；末尾调用 `assert_outcome_report` 自校 status 头，缺头/非法取值抛错。
    """
    report = report or {}
    conclusion = report.get("conclusion")
    lines = [
        f"# 回测报告：{name}",
        "",
        "**status**: active",
        f"**日期**: {report.get('generated_at') or NOT_PROVIDED}",
        f"**批次类型**: {report.get('batch_kind') or NOT_PROVIDED}",
        f"**预登记**: {_preregister_text(report.get('preregister'))}",
        "",
        "## 1. 干净窗口判定",
        "",
        *_clean_window_lines(report.get("clean_window")),
        "",
        "## 2. 泄漏探针",
        "",
        *_probe_lines(report),
        "",
        "## 3. regime 覆盖",
        "",
        *_regime_lines(report),
        "",
        "## 4. 绩效表",
        "",
        *_perf_table_lines(report),
        "",
        "## 5. 结论",
        "",
        conclusion if conclusion else NOT_PROVIDED,
        "",
        "## 6. 口径说明",
        "",
        *_methodology_lines(report),
        "",
    ]
    text = "\n".join(lines)
    assert_outcome_report(text)  # 渲染自校：缺 status 头须抛错
    return text
