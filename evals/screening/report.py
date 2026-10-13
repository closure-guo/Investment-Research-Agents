"""选股前向跟踪与消融报告渲染（add-screening-evaluation Task 2/3 / 计划 V5）。

Markdown 渲染（纯模板，无 IO）：分组表 + 配对 CI + **有效 n 与适用口径披露**；
措辞守卫为**结构校验**（fail loud）——CI 含 0 的报告只写「无显著差异」，报告任何
位置出现「无效/无价值/无用/没有价值」类引申 → ``ValueError``（坏措辞不进报告）。

payload 各节可选（缺节不渲染）：``forward``（``forward_pool_returns`` 输出）、
``ablation``（``compare_ablation_variants`` 输出）、``l3``（``l3_keep_drop_compare``
输出）、``stratify``（``stratify_by_factor_source`` 输出）；``data_source="synthetic"``
时强制渲染合成数据声明（样本报告不冒充真实读数）。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from evals.screening.ablation import ABLATION_VARIANTS

# 禁用措辞（CI 含 0 不得引申为否定性结论；渲染前结构校验，fail loud）
_FORBIDDEN_INTERPRETATIONS: tuple[str, ...] = ("无效", "无价值", "无用", "没有价值")

_VARIANT_LABELS: dict[str, str] = {
    "factor": "因子打分（L2 composite Top-N）",
    "random": "固定种子随机",
    "equal_weight": "等权指数（全 L2 名单等权）",
}
_DIFF_LABELS: dict[str, str] = {
    "factor_minus_random": "因子打分 − 固定种子随机",
    "factor_minus_equal_weight": "因子打分 − 等权指数",
    "llm_minus_builtin": "仅 LLM 因子打分 − 仅 builtin 打分",
    "keep_minus_drop": "keep 组 − drop 组",
}


def _fmt_return(value: Any) -> str:
    """收益 → 带符号百分比（4 位小数精度内）；None → 「—」。"""
    if value is None:
        return "—"
    return f"{float(value) * 100:+.2f}%"


def _fmt_win(value: Any) -> str:
    """胜率/踩雷率 → 百分比；None → 「—」。"""
    if value is None:
        return "—"
    return f"{float(value) * 100:.1f}%"


def _fmt_ci(ci: Sequence[float] | None) -> str:
    """95% CI → ``[lo%, hi%]``；不可得 → 「—」。"""
    if not ci:
        return "—"
    lo, hi = ci
    return f"[{float(lo) * 100:.2f}%, {float(hi) * 100:.2f}%]"


def _fmt_counts(stats: Mapping[str, Any]) -> str:
    """剔除/未成交披露：原因码×计数（未成交为撮合原因码子集，单独标注）。"""
    excluded = stats.get("excluded_counts") or {}
    unfilled = stats.get("unfilled_counts") or {}
    if not excluded:
        return "—"
    text = "、".join(f"{reason}×{count}" for reason, count in sorted(excluded.items()))
    if unfilled:
        text += f"（未成交 {sum(int(count) for count in unfilled.values())}）"
    return text


def _methodology_bullets(methodology: Mapping[str, Any] | None) -> list[str]:
    """口径披露：``{名: 文案}`` → markdown 列表（按键升序，确定性）。"""
    if not methodology:
        return []
    lines = ["**口径披露**", ""]
    for name in sorted(methodology):
        text = str(methodology[name]).strip()
        if text:
            lines.append(f"- {name}：{text}")
    lines.append("")
    return lines


def _stats_table(
    rows: Sequence[tuple[str, Mapping[str, Any]]], *, extra_columns: Sequence[str] = ()
) -> list[str]:
    """分组表：有效 n / 均值 / 胜率 / 有样本池日 / 剔除（+ 可扩展列）。"""
    header = "| 组 | 有效 n | 均值 | 胜率 | 有样本池日 | 剔除/未成交 |"
    separator = "|---|---|---|---|---|---|"
    extras = [*extra_columns]
    if extras:
        header = (
            "| 组 | 有效 n | 均值 | 胜率 | 有样本池日 | 剔除/未成交 |"
            + "|".join(f" {name} " for name in extras)
            + "|"
        )
        separator = "|---|---|---|---|---|---" + "|---" * len(extras) + "|"
    lines = [header, separator]
    for label, stats in rows:
        cells = [
            label,
            str(stats.get("n", 0)),
            _fmt_return(stats.get("mean")),
            _fmt_win(stats.get("win_rate")),
            str(stats.get("days", 0)),
            _fmt_counts(stats),
        ]
        for name in extras:
            cells.append(_fmt_win(stats.get(name)))
        lines.append("| " + " | ".join(cells) + " |")
    return lines


def _render_forward_section(section: Mapping[str, Any]) -> list[str]:
    pool_dates = [str(day) for day in (section.get("pool_dates") or [])]
    as_of = section.get("as_of")
    window = (
        f"{pool_dates[0]} .. {pool_dates[-1]}（{len(pool_dates)} 个池日）"
        if pool_dates
        else "（无池日）"
    )
    lines = ["## 候选池前向命中率（入选组 vs 落选组）", ""]
    lines.append(f"- 池日窗口：{window}；as_of = {as_of or '面板全量（无截断）'}")
    lines.append("")
    horizons = section.get("horizons") or {}
    for key in sorted(horizons, key=lambda item: int(item)):
        entry = horizons[key]
        lines.append(f"### horizon = {key} 交易日")
        lines.append("")
        lines += _stats_table(
            [
                ("Top-N 入选组", entry.get("selected") or {}),
                ("落选对照组", entry.get("rejected") or {}),
            ]
        )
        lines.append("")
        diff = entry.get("diff") or {}
        ci_text = _fmt_ci(diff.get("ci95"))
        if diff.get("ci95"):
            ci_detail = f"（B={diff.get('bootstrap_B')}，seed={diff.get('seed')}）"
        else:
            ci_detail = "（配对日 < 2 不产 CI）"
        lines.append(
            f"- 配对（池日）均值差：{_fmt_return(diff.get('diff_mean'))}，95% CI {ci_text}，"
            f"配对日数 {diff.get('paired_days', 0)}{ci_detail}"
        )
        unfilled = entry.get("unfilled_counts") or {}
        if unfilled:
            lines.append(
                "- 未成交（撮合原因码，剔除出收益序列）："
                + "、".join(f"{reason}×{count}" for reason, count in sorted(unfilled.items()))
            )
        lines.append("")
    lines += _methodology_bullets(section.get("methodology"))
    return lines


def _render_ablation_section(section: Mapping[str, Any]) -> list[str]:
    pool_dates = [str(day) for day in (section.get("pool_dates") or [])]
    as_of = section.get("as_of")
    window = (
        f"{pool_dates[0]} .. {pool_dates[-1]}（{len(pool_dates)} 个池日）"
        if pool_dates
        else "（无池日）"
    )
    lines = ["## 选股消融三变体（因子打分 / 固定种子随机 / 等权指数）", ""]
    lines.append(
        f"- 持有窗口：入场后 {section.get('horizon')} 个交易日收盘；池日窗口：{window}；"
        f"as_of = {as_of or '面板全量（无截断）'}"
    )
    lines.append(f"- 随机臂种子：{section.get('seed')}")
    lines.append("")
    variants = section.get("variants") or {}
    lines += _stats_table(
        [
            (_VARIANT_LABELS.get(name, name), variants.get(name) or {})
            for name in ABLATION_VARIANTS
            if name in variants
        ]
    )
    lines.append("")
    for key, diff in (section.get("diffs") or {}).items():
        label = _DIFF_LABELS.get(key, key)
        text = (
            f"- {label}：{_fmt_return(diff.get('diff_mean'))}，95% CI {_fmt_ci(diff.get('ci95'))}，"
            f"配对日数 {diff.get('paired_days', 0)}"
        )
        conclusion = diff.get("conclusion")
        if conclusion:
            text += f" → {conclusion}"
        lines.append(text)
    lines.append("")
    lines += _methodology_bullets(section.get("methodology"))
    return lines


def _render_l3_section(section: Mapping[str, Any]) -> list[str]:
    pool_dates = [str(day) for day in (section.get("pool_dates") or [])]
    window = (
        f"{pool_dates[0]} .. {pool_dates[-1]}（{len(pool_dates)} 个池日）"
        if pool_dates
        else "（无池日）"
    )
    threshold = section.get("blowup_threshold")
    if isinstance(threshold, (int, float)) and not isinstance(threshold, bool):
        label = f"踩雷率（{float(threshold) * 100:.0f}% 档）"
    else:
        label = "踩雷率"
    lines = ["## ±L3 粗筛变体（keep vs drop）", ""]
    lines.append(
        f"- 持有窗口：入场后 {section.get('horizon')} 个交易日收盘；池日窗口：{window}；"
        f"as_of = {section.get('as_of') or '面板全量（无截断）'}"
    )
    lines.append("")
    lines += _stats_table(
        [
            ("keep 组（粗筛保留 ∩ 入池）", section.get("keep") or {}),
            ("drop 组（粗筛剔除）", section.get("drop") or {}),
        ],
        extra_columns=(label,),
    )
    lines.append("")
    diff = section.get("diff") or {}
    lines.append(
        f"- 配对（池日）均值差（keep − drop）：{_fmt_return(diff.get('diff_mean'))}，"
        f"95% CI {_fmt_ci(diff.get('ci95'))}，配对日数 {diff.get('paired_days', 0)} → "
        f"**{section.get('conclusion', '样本不足，无法判定')}**"
    )
    lines.append("")
    lines += _methodology_bullets(section.get("methodology"))
    return lines


def _render_stratify_section(section: Mapping[str, Any]) -> list[str]:
    pool_dates = [str(day) for day in (section.get("pool_dates") or [])]
    window = (
        f"{pool_dates[0]} .. {pool_dates[-1]}（{len(pool_dates)} 个池日）"
        if pool_dates
        else "（无池日）"
    )
    lines = ["## 因子来源分层（builtin vs LLM 挖掘）", ""]
    lines.append(
        f"- 持有窗口：入场后 {section.get('horizon')} 个交易日收盘；池日窗口：{window}；"
        f"as_of = {section.get('as_of') or '面板全量（无截断）'}"
    )
    lines.append("")
    strata = section.get("strata") or {}
    lines += _stats_table(
        [
            ("仅 builtin 打分", strata.get("builtin") or {}),
            ("仅 LLM 因子打分", strata.get("llm") or {}),
        ]
    )
    lines.append("")
    diff = section.get("diff") or {}
    lines.append(
        f"- 配对（池日）均值差（LLM − builtin）：{_fmt_return(diff.get('diff_mean'))}，"
        f"95% CI {_fmt_ci(diff.get('ci95'))}，配对日数 {diff.get('paired_days', 0)} → "
        f"**{section.get('conclusion', '样本不足，无法判定')}**"
    )
    lines.append("")
    lines += _methodology_bullets(section.get("methodology"))
    return lines


def render_screening_report_md(payload: Mapping[str, Any]) -> str:
    """选股前向跟踪与消融报告（markdown）。

    各节按 payload 键可选渲染；末尾统一执行禁用措辞守卫（fail loud）。
    """
    lines: list[str] = [f"# {payload.get('title') or '选股前向跟踪与消融报告'}", ""]
    if payload.get("data_source") == "synthetic":
        lines += [
            "> **合成数据声明**：本报告由合成面板 / 合成 screening 库生成（非真实市场数据），",
            "> 仅用于链路与口径演示，不构成任何真实市场结论。",
            "",
        ]
    if payload.get("generated_at"):
        lines += [f"- 生成时间：{payload['generated_at']}", ""]
    if payload.get("forward"):
        lines += _render_forward_section(payload["forward"])
    if payload.get("ablation"):
        lines += _render_ablation_section(payload["ablation"])
    if payload.get("l3"):
        lines += _render_l3_section(payload["l3"])
    if payload.get("stratify"):
        lines += _render_stratify_section(payload["stratify"])
    lines += [
        "## 通用口径",
        "",
        "- 收益口径：入场走系统唯一撮合引擎（D+1 开盘 ± 滑点，一字涨停不成交记原因码剔除），",
        "  出场为 horizon 日收盘；统计只消费 ≤ as_of 的数据。",
        "- CI 含 0 只写「无显著差异」；不得据此作否定性引申（本渲染器结构校验强制）。",
        "- 有效 n 与适用口径随各节披露；样本不足时不产结论。",
    ]
    text = "\n".join(lines) + "\n"
    for forbidden in _FORBIDDEN_INTERPRETATIONS:
        if forbidden in text:
            raise ValueError(
                f"选股评估报告出现禁用措辞 {forbidden!r}：CI 含 0 不得引申为「无效/无价值」"
            )
    return text
