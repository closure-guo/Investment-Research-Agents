"""消融 90 条（3 标的 × 3 变体 × 10 重复）权威聚合报告。

读取 .worktrees/evals-boot/reports/ablation/resume.json（断点续跑台账），
复用 evals.ablation.aggregate_results（按 ticker 中位数配对的层间增量 bootstrap CI），
补充成本（llm_calls / prompt+completion token）与 coverage 中位统计，
产出 pilot.md 同款格式的 markdown 报告。

用法: uv run python tests/scripts/ablation_aggregate_90.py
"""

from __future__ import annotations

import json
import statistics
from datetime import datetime
from pathlib import Path

from evals.ablation import _VARIANTS, JUDGE_DIMS, aggregate_results

RESUME = Path(".worktrees/evals-boot/reports/ablation/resume.json")
OUT_DIR = Path(".worktrees/evals-boot/reports/ablation")

JUDGE_LABEL = {
    "report_relevance": "rel",
    "debate_quality": "辩论",
    "decision_grounding": "grounding",
    "consistency": "一致",
}

# citation 四桶人读标签（G7/⑦）：桶名域 = evals.causal_ablation.escape.VERIFIER_BUCKETS
CITATION_LABEL = {
    "blocked": "阻断",
    "analyst_true_fail": "分析师真错",
    "surgical_repaired": "单点修复",
    "verifier_normalized": "校验器归一",
}


def _median(values: list[float]) -> float | None:
    return round(statistics.median(values), 4) if values else None


def _pct(v: float | None, digits: int = 1) -> str:
    return "—" if v is None else f"{v * 100:.{digits}f}%"


def _citation_bucket_parts(layer: dict) -> list[str]:
    """层内 citation 四桶增量行（G7/⑦）：缺席的桶不产行（旧记录不伪造 0）。"""
    parts: list[str] = []
    for bucket, label in CITATION_LABEL.items():
        entry = layer.get(f"citation_{bucket}")
        if not entry:
            continue
        lo, hi = entry["ci"]
        parts.append(f"{label} Δ{entry['diff_mean']} CI[{lo},{hi}]")
    return parts


def render_report(agg: dict, runs: list[dict]) -> list[str]:
    """渲染人读版报告主体（纯函数、零 IO，可对合成 agg 直接测试；G7/⑦）。

    citation 腿的层增量结论只认四桶（`citation_<bucket>`）；标量 `citation_pass_rate`
    块自 G2 起不再带 CI/结论，仅作监控行并显式标注「不入层增量结论」。
    """
    n = len(runs)
    # 变体级:成本 + coverage 中位
    variant_stats: dict[str, dict] = {}
    for v in _VARIANTS:
        v_runs = [r for r in runs if r["variant"] == v]
        variant_stats[v] = {
            "calls": sum(r.get("llm_calls") or 0 for r in v_runs),
            "prompt": sum(r.get("prompt_tokens") or 0 for r in v_runs),
            "completion": sum(r.get("completion_tokens") or 0 for r in v_runs),
            "coverage_median": _median(
                [
                    r.get("citation_coverage")
                    for r in v_runs
                    if r.get("citation_coverage") is not None
                ]
            ),
        }

    total_calls = sum(v["calls"] for v in variant_stats.values())
    total_tokens = sum(v["prompt"] + v["completion"] for v in variant_stats.values())
    total_prompt = sum(v["prompt"] for v in variant_stats.values())
    total_completion = sum(v["completion"] for v in variant_stats.values())

    lines: list[str] = []
    lines.append("# 消融实验权威结果（n=10 重复，含 D6 现役管线）\n")
    lines.append(f"**日期**: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    lines.append(f"**来源**: `.worktrees/evals-boot/reports/ablation/resume.json`（{n} 条 run）")
    lines.append(
        "**配置**: 3 标的 × 3 变体 × 10 重复 = 90 run；分析模型 glm-5.3，judge deepseek-v4-flash；"
        "统计=同标的同变体 10 次取中位、层间差异配对 bootstrap B=10,000 95% CI\n"
    )

    lines.append("## 结果总表\n")
    lines.append(
        "| 变体 | citation_pass | coverage 中位 | judge 中位（rel/辩论/grounding/一致） | token 成本（调用次数） | 增量效果 vs 上一层（95% CI） |"
    )
    lines.append("|---|---|---|---|---|---|")
    for v in _VARIANTS:
        vs = agg["variants"][v]
        jm = vs["judge_medians"]
        jstr = " / ".join("—" if jm[d] is None else f"{jm[d]:.1f}" for d in JUDGE_DIMS)
        st = variant_stats[v]
        tokens = st["prompt"] + st["completion"]
        cost = f"{tokens:,}（{st['calls']} 次调用）"
        if v == "analysts":
            inc = "基线"
        else:
            layer = agg["layers"]["debate" if v == "plus_debate" else "full"]
            parts = []
            for d in JUDGE_DIMS:
                key = f"judge_{d}"
                if key in layer:
                    ci = layer[key]["ci"]
                    parts.append(f"{JUDGE_LABEL[d]} Δ{layer[key]['diff_mean']} CI[{ci[0]},{ci[1]}]")
            parts += _citation_bucket_parts(layer)
            inc = "；".join(parts) if parts else "—"
        lines.append(
            f"| {v} | {_pct(vs['citation_pass_rate'])} | {_pct(st['coverage_median'], 2)} | {jstr} | {cost} | {inc} |"
        )

    lines.append("\n## 层间增量结论（CI 纪律措辞）\n")
    for v in ("plus_debate", "full"):
        layer = agg["layers"]["debate" if v == "plus_debate" else "full"]
        name = "辩论层" if v == "plus_debate" else "决策+风控层"
        lines.append(f"**{name}（{v} − {'analysts' if v == 'plus_debate' else 'plus_debate'}）**\n")
        for d in JUDGE_DIMS:
            key = f"judge_{d}"
            if key in layer:
                it = layer[key]
                lines.append(
                    f"- {d}: 点估计 Δ{it['diff_mean']}, 95% CI [{it['ci'][0]}, {it['ci'][1]}] → {it['conclusion']}"
                )
        for bucket, label in CITATION_LABEL.items():
            entry = layer.get(f"citation_{bucket}")
            if not entry:
                continue
            # 方向元数据（G7/⑥）随条目落盘；旧产物无该键时不冒充「无方向」
            direction = (
                f"（lower_is_better={entry['lower_is_better']}）"
                if "lower_is_better" in entry
                else ""
            )
            lines.append(
                f"- citation_{bucket}（{label}）: 点估计 Δ{entry['diff_mean']}, "
                f"95% CI [{entry['ci'][0]}, {entry['ci'][1]}] → {entry['conclusion']}{direction}"
            )
        cpr = layer.get("citation_pass_rate", {})
        if cpr:
            lines.append(
                f"- citation_pass 率: {_pct(cpr.get('prev'))} → {_pct(cpr.get('current'))}"
                "（不入层增量结论）"
            )
        lines.append("")

    lines.append("## usage 汇总（真值）\n")
    lines.append(
        f"- 总调用: {total_calls} 次；总 token: {total_tokens:,}（输入 {total_prompt:,} + 输出 {total_completion:,}）"
    )
    lines.append(f"- 单 run 均值: {total_calls / n:.1f} 次调用 / {total_tokens / n:,.0f} token\n")
    lines.append("## 各变体成本\n")
    lines.append("| 变体 | 调用次数 | 输入 token | 输出 token | 总 token |")
    lines.append("|---|---|---|---|---|")
    for v in _VARIANTS:
        st = variant_stats[v]
        lines.append(
            f"| {v} | {st['calls']} | {st['prompt']:,} | {st['completion']:,} | {st['prompt'] + st['completion']:,} |"
        )

    lines.append("\n## 与 pilot（n=3，无 D6）对比\n")
    lines.append(
        "- pilot 层增量结论（judge 维度）因 #109/#111/#112 伪影全线挂起，本报告为修复后 n=10 权威版，"
        "不再与 pilot 做数字对比；citation_pass 口径同 pilot（契约噪声问题见 #105 归因，仍存在，解读时注意）。"
    )
    return lines


def main() -> None:
    data = json.loads(RESUME.read_text(encoding="utf-8"))
    runs: list[dict] = data["runs"]
    n = len(runs)
    print(f"resume.json runs = {n}")
    if n < 90:
        raise SystemExit(f"实验未完成: 期望 90 条,当前 {n} 条。完成后再跑本脚本。")

    agg = aggregate_results(runs)
    lines = render_report(agg, runs)

    out = OUT_DIR / f"ablation-90-{datetime.now().strftime('%Y%m%d-%H%M%S')}.md"
    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"\n报告已落盘: {out}")
    print("\n".join(lines[:40]))


if __name__ == "__main__":
    main()
