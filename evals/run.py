"""实验回归工作流(spec Requirement「实验回归工作流」)。

用法:
    uv run python -m evals.run "<实验名>"   # 经 langfuse dataset.run_experiment 执行

run_experiment 是实验唯一执行入口:无 langfuse(Langfuse 未配置/不可达)时
打印明确错误并以非零退出码终止,不提供本地循环降级路径。

产出:终端结果表 + reports/evals/<name>-<ts>.json(per-item 明细 + 均值 +
judge 失败数 + prompt_versions)。该 JSON 是 Judge 校准(Req 5,人工)的输入。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

from evals.dataset_seed import DATASET_NAME
from evals.evaluators import make_evaluation, section_coverage, ticker_match
from evals.judges import run_judge
from evals.task import run_task
from finance_agent.langfuse_tracing import get_langfuse

_PROMPT_NAMES = [
    "macro_analyst",
    "fundamental_analyst",
    "technical_analyst",
    "sentiment_analyst",
    "bull_debater",
    "bear_debater",
    "research_manager",
    "trader",
    "risk_debater",
    "risk_judge",
    "fund_manager",
    "quick_mode",
    "deep_mode",
    "follow_up_mode",
]
_JUDGE_DIMS = ["report_relevance", "debate_quality", "decision_grounding", "consistency"]
# quick 模式无辩论/决策层:只有 report_relevance 适用(design §7 过滤器)
_JUDGE_DEEP_ONLY = {"debate_quality", "decision_grounding", "consistency"}
# 本地 prompts/*.md（git 跟踪）是唯一权威源（模块级常量便于测试注入）
_PROMPTS_DIR = Path(__file__).resolve().parents[1] / "src/finance_agent/prompts"


def _collect_prompt_versions() -> dict[str, str]:
    """记录实验所用 prompt 版本(production label;load_prompt_with_meta 只读复用)。"""
    from finance_agent.prompts.loader import load_prompt_with_meta

    versions: dict[str, str] = {}
    for name in _PROMPT_NAMES:
        try:
            versions[name] = str(load_prompt_with_meta(name).prompt_version)
        except Exception:
            versions[name] = "unknown"
    return versions


def _verify_prompt_sync(client) -> list[str]:
    """校验 Langfuse production prompt 与本地 prompts/*.md 一致（CRLF 归一化逐字比对）。

    本地 .md（git 跟踪）是提示词唯一权威源；Langfuse production 是部署产物。
    不一致说明「改了未发布」，eval 前必须拦截（防测错版本造成不可对比的分数）。
    返回不一致的 prompt 名列表；空列表 = 全部一致。
    """
    mismatched: list[str] = []
    for name in _PROMPT_NAMES:
        try:
            # newline="" 保留原文行尾,使 .replace 成为真实归一化路径(否则 universal-newlines 读入即转 LF,replace 成死代码);
            # 用 open() 而非 Path.read_text:newline 形参 3.13+ 才加入 read_text,repo mypy 锁定 3.12
            with open(_PROMPTS_DIR / f"{name}.md", encoding="utf-8", newline="") as f:
                local = f.read().replace("\r\n", "\n")
        except OSError:
            mismatched.append(f"{name} (本地文件缺失)")
            continue
        try:
            remote = str(getattr(client.get_prompt(name), "prompt", "")).replace("\r\n", "\n")
        except Exception:  # noqa: BLE001 - 拉取失败归为不一致,保守拦截
            mismatched.append(f"{name} (Langfuse 拉取失败)")
            continue
        if local != remote:
            mismatched.append(name)
    return mismatched


# ── langfuse evaluator 适配器(签名 (*, input, output, expected_output, metadata))──


def eval_section_coverage(*, input, output, expected_output, metadata):
    result = section_coverage(output.get("report") if output else None, expected_output or {})
    return make_evaluation(result) if result else None


def eval_ticker_match(*, input, output, expected_output, metadata):
    result = ticker_match(output.get("ticker") if output else None, expected_output or {})
    return make_evaluation(result) if result else None


def eval_citation_pass(*, input, output, expected_output, metadata):
    """citation_pass（管线 trace 级布尔）经 output 透传为实验 Score。"""
    value = (output or {}).get("citation_pass")
    if value is None:
        return None
    return make_evaluation({"name": "citation_pass", "value": float(value), "comment": None})


def eval_citation_counter(name: str, comment: str):
    def _eval(*, input, output, expected_output, metadata):
        value = (output or {}).get(name)
        if value is None:
            return None
        return make_evaluation({"name": name, "value": float(value), "comment": comment})

    _eval.__name__ = f"eval_{name}"
    return _eval


def eval_argument_anchor_coverage(*, input, output, expected_output, metadata):
    """论点锚点覆盖率（零 LLM，add-debate-argument-anchors）：value 透传，
    四个拆项随 comment 落库（spec Scenario：comment 含
    unanchored_inference=… / unresolved=… / missing_required=… / unspecified=…）。

    不复用 eval_citation_counter：其 comment 为静态串，而拆项随 item 变化；
    为避免改共享工厂（citation 家族）签名，此处独立实现同形评估器。
    """
    value = (output or {}).get("argument_anchor_coverage")
    if value is None:
        return None
    comment = "辩论论点锚点覆盖率（anchored/total，零 LLM）"
    detail = (output or {}).get("argument_anchor_coverage_detail") or {}
    if detail:
        comment += (
            f"；unanchored_inference={detail.get('unanchored_inference', 0)}"
            f" / unresolved={detail.get('unresolved', 0)}"
            f" / missing_required={detail.get('missing_required', 0)}"
            f" / unspecified={detail.get('unspecified', 0)}"
        )
    return make_evaluation(
        {"name": "argument_anchor_coverage", "value": float(value), "comment": comment}
    )


def eval_citation_blocked(*, input, output, expected_output, metadata):
    """阻断层：归一后残余 FAIL > 0（incident 026 拆报——此前的 citation_pass 把
    校验器误报算在分析师头上）。"""
    value = (output or {}).get("citation_blocked")
    if value is None:
        return None
    return make_evaluation({"name": "citation_blocked", "value": float(value), "comment": None})


def eval_citation_coverage(*, input, output, expected_output, metadata):
    """citation_coverage（正文数字普查覆盖率，harden-citation-semantic-coverage）。"""
    value = (output or {}).get("citation_coverage")
    if value is None:
        return None
    return make_evaluation({"name": "citation_coverage", "value": float(value), "comment": None})


def _citation_ci(
    vals: list[float],
    B: int = 10_000,  # noqa: N803 - B 为 bootstrap 重采样次数的数学惯例命名
    seed: int = 42,
) -> tuple[float, float]:
    """均值的 bootstrap 95% CI（非配对；配对显著性由 compare.py 契约承担）。"""
    import numpy as np

    if not vals:
        return (0.0, 0.0)
    arr = np.asarray(vals, dtype=float)
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, len(arr), size=(B, len(arr)))
    means = arr[idx].mean(axis=1)
    return (float(np.percentile(means, 2.5)), float(np.percentile(means, 97.5)))


def _judge_adapter(dimension: str):
    def _eval(*, input, output, expected_output, metadata):
        mode = (output or {}).get("mode") or (input or {}).get("mode")
        if mode == "quick" and dimension in _JUDGE_DEEP_ONLY:
            return None  # quick 无辩论,跳过
        if not (output or {}).get("report"):
            return None  # skipped item
        result = run_judge(dimension, (output or {}).get("judge_vars") or {})
        if result["score"] is None:
            # score=null:解析失败,记入失败率(已实测 langfuse 4.13
            # Evaluation.value 接受 None,无需 _failed 占位 fallback)
            return make_evaluation({"name": dimension, "value": None, "comment": result["reason"]})
        # confidence 随 comment 落库（round5 校准实证：残缺输入上 judge 幻觉高分
        # 无信号可辨——置信度使「高分+低置信」组合可识别）
        conf = result.get("confidence")
        comment = result["reason"]
        if conf is not None:
            comment = f"[conf={conf:.2f}] {comment}"
        # debate_quality v6 封顶证据随分数落库（可审计：机制是否生效、由谁扣的分）
        if result.get("enumeration_missing"):
            comment = f"[enum-missing] {comment}"
        elif result.get("cap_applied"):
            comment = f"[cap=qualitative×{result.get('qualitative_points', 0)}] {comment}"
        return make_evaluation(
            {"name": dimension, "value": float(result["score"]), "comment": comment}
        )

    _eval.__name__ = f"eval_{dimension}"
    return _eval


def all_evaluators() -> list:
    return [
        eval_section_coverage,
        eval_ticker_match,
        eval_citation_pass,
        eval_citation_coverage,
        eval_citation_blocked,
        eval_citation_counter(
            "citation_analyst_true_fail", "分析师真错数（残余 FAIL + 单点修复回填）"
        ),
        eval_citation_counter(
            "citation_verifier_normalized", "归一后由 FAIL 转 PASS（校验器解析债的量化）"
        ),
        eval_citation_counter("citation_unverifiable_text", "文本 claim 分型排除（不进阻断分母）"),
        eval_citation_counter(
            "citation_unverifiable_unregistered", "未注册/空值 UNVERIFIABLE（跟踪指标）"
        ),
        eval_citation_counter(
            "citation_unverifiable_comparative_delta",
            "比较型差值申报（非三枚举 stated_value，不计阻断分母）",
        ),
        eval_citation_counter(
            "citation_surgical_repaired",
            "单点修复成功回填数（稀疏 value_mismatch；已计入真错，单独计数观测触发/成功率）",
        ),
        # add-debate-argument-anchors：论点锚点覆盖率（零 LLM）——拆项随 comment
        # 落库，故用同形独立评估器而非静态 comment 的 eval_citation_counter
        eval_argument_anchor_coverage,
    ] + [_judge_adapter(d) for d in _JUDGE_DIMS]


def _rows_from_results(item_results: list) -> list[dict]:
    """实验结果 → 汇总行。skipped 取自 task 输出（harness 有意跳过的项带原因），
    此前硬编码 None 使跳过项在汇总表里显示成「跑了但没分」。"""
    rows = []
    for r in item_results:
        output = getattr(r, "output", None) or {}
        skipped = output.get("skipped") if isinstance(output, dict) else None
        rows.append(
            {
                "item": str(r.item.input.get("query")),
                "mode": r.item.input.get("mode"),
                "skipped": skipped or None,
                "scores": {e.name: e.value for e in r.evaluations if e.value is not None},
                "judge_failures": sum(1 for e in r.evaluations if e.value is None),
            }
        )
    return rows


def _mean_rows(rows: list[dict]) -> dict:
    """各 Score 均值(None 不计入)+ judge 失败总数。"""
    buckets: dict[str, list[float]] = {}
    failures = 0
    for row in rows:
        failures += row.get("judge_failures", 0)
        for name, value in (row.get("scores") or {}).items():
            if value is not None:
                buckets.setdefault(name, []).append(float(value))
    means = {name: round(sum(vals) / len(vals), 4) for name, vals in buckets.items() if vals}
    means["judge_failures"] = failures
    return means


def _print_table(rows: list[dict], means: dict) -> None:
    for row in rows:
        if row.get("skipped"):
            print(f"[skip] {row['item']} ({row['mode']}): {row['skipped']}")
            continue
        score_str = " ".join(f"{k}={v}" for k, v in row["scores"].items())
        print(f"[{row['mode']:9}] {row['item'][:30]:32} {score_str}")
    print("─" * 60)
    print("均值:", json.dumps(means, ensure_ascii=False))


def _resolve_eval_model() -> str:
    """评测模型解析（与 agent_factory 同优先级）：LLM_MODEL env → 默认值。

    归档必须记录模型——跨批对比（防退化门禁）的前提是模型可比；
    模型变更属于事件，须重跑基线并在归档中可见。
    """
    return os.getenv("LLM_MODEL") or "deepseek/deepseek-chat"


def _write_report(
    rows: list[dict], means: dict, name: str, prompt_versions: dict, citation_ci: dict
) -> Path:
    out_dir = Path("reports/evals")
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = out_dir / f"{name}-{ts}.json"
    path.write_text(
        json.dumps(
            {
                "experiment": name,
                "timestamp": ts,
                "model": _resolve_eval_model(),
                "prompt_versions": prompt_versions,
                "means": means,
                "citation_ci": citation_ci,
                "rows": rows,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return path


def main() -> None:
    # 与 api.py 一致：CLI 入口加载 .env（否则 shell 无 LANGFUSE/LLM key，误判「未配置」）
    from dotenv import load_dotenv

    load_dotenv()
    parser = argparse.ArgumentParser(description="evals 实验回归")
    parser.add_argument("name", help="实验名(如 baseline-v1)")
    parser.add_argument(
        "--dataset",
        default=DATASET_NAME,
        help=f"评估 dataset 名(默认 {DATASET_NAME}；rotating 轮换池用 seed 建库后的独立名)",
    )
    args = parser.parse_args()

    # run_experiment 是实验唯一执行入口(spec「实验回归工作流」Scenario「无 Langfuse 时显式报错」):
    # langfuse 不可用时显式报错并退出,绝不降级为本地循环产出不可对比的分数。
    client = get_langfuse()
    if client is None:
        sys.exit(
            "错误: Langfuse 未配置/不可达，实验必须经 run_experiment 执行。"
            "请配置 LANGFUSE_PUBLIC_KEY/SECRET_KEY 且服务可达。"
        )

    prompt_versions = _collect_prompt_versions()
    print("prompt_versions:", json.dumps(prompt_versions, ensure_ascii=False))

    mismatched = _verify_prompt_sync(client)
    if mismatched:
        if all("拉取失败" in m for m in mismatched):
            hint = "请检查 Langfuse 连通性/凭证后重试（所有 prompt 均拉取失败）。"
        else:
            hint = "请先执行 `uv run python scripts/deploy_prompts.py` 发布后再运行。"
        sys.exit(
            "错误: 以下 prompt 的 Langfuse 当前版本（运行时将加载的版本）与本地 .md 不一致，"
            "拒绝运行实验（防测错版本）:\n  - " + "\n  - ".join(mismatched) + "\n" + hint
        )

    dataset = client.get_dataset(args.dataset)
    result = dataset.run_experiment(
        name=args.name,
        task=run_task,
        evaluators=all_evaluators(),
        max_concurrency=1,  # 管线分钟级,禁高并发
        metadata={"prompt_versions": prompt_versions, "dataset": args.dataset},
    )
    rows = _rows_from_results(result.item_results)

    means = _mean_rows(rows)
    _print_table(rows, means)
    # citation 指标均值 CI（harden-citation-semantic-coverage spec：均值与 95% CI）
    citation_ci = {}
    for metric in ("citation_pass", "citation_coverage"):
        vals = [
            float(row["scores"][metric])
            for row in rows
            if (row.get("scores") or {}).get(metric) is not None
        ]
        if vals:
            lo, hi = _citation_ci(vals)
            citation_ci[metric] = [round(lo, 4), round(hi, 4)]
    path = _write_report(rows, means, args.name, prompt_versions, citation_ci)
    print(f"结果已写入 {path}")
    client.flush()


if __name__ == "__main__":
    main()
