"""消融实验驱动（评估体系开机任务清单 · 任务 3）。

配置：标的 × 3 变体（analysts / plus_debate / full）× N 次重复（默认 3 标的 / 3 重复），
管线模型见 `_pin_pipeline_model`（ark-plan）。统计沿用 evals/ablation.aggregate_results
（同标的中位 + 配对 bootstrap B=10,000，95% CI）。

在设施主流程外补三件测量（不改 evals/ablation.py）：
1. citation_coverage：包装 verify_citations 逐次记录（设施聚合报告未含 coverage）；
2. token 成本：包装 run_variant_once + run_judge 按 run 归属计量
   （复用 backtest_pilot_2023 的 usage meter）；
3. **judge 材料落盘**（delta judge-enumeration-cap-and-ablation-materials）：每次
   run 的 judge 输入材料（judge_vars）落 `reports/ablation/judge_vars/<variant>-<ticker>-<repeat>.json`，
   run 记录携带材料路径与 judge 明细（分数/纯定性条数/是否封顶/枚举是否缺失）——
   rubric 变更后可**离线重判**（不重跑管线、不依赖 Langfuse 反解材料）。

**断点续跑**（2026-09-02 增加）：ark burst 限流已两次在临近结束时打崩整段试跑。
自驱循环 + 每完成一条 run 即落盘 resume.json，重启时跳过已完成
(variant, ticker, repeat)，不重烧 token；材料落盘与 resume 落盘同批完成。

产物：reports/ablation/pilot-<ts>.json（聚合报告 + coverage + 成本台账 + 材料索引）。
pilot.md（人读版）由试跑记录手写落于 evals/ablation/results/pilot.md。

用法：
    uv run python tests/scripts/ablation_pilot.py                    # 默认 3 标的 × 3 重复
    uv run python tests/scripts/ablation_pilot.py --tickers 600519 000001 --repeats 10
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "tests" / "scripts"))

import backtest_pilot_2023 as pilot_util  # noqa: E402

TICKERS = ["002412", "600519", "300308"]  # 汉森制药 / 贵州茅台 / 中际旭创（默认值）
REPEATS = 3
JUDGE_REPEATS = 3  # judge 单维度重复次数（取均值；round11 实测单次调用 5/4 翻转 ~46%）
QUERY = "综合评估投资价值"
RESUME_PATH = Path("reports/ablation/resume.json")
MATERIALS_DIR = Path("reports/ablation/judge_vars")
OUT_DIR = Path("reports/ablation")


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """CLI 参数（标的与重复次数参数化，默认值保持历史口径）。"""
    parser = argparse.ArgumentParser(description="数据对齐消融实验驱动（材料落盘 + 参数化）")
    parser.add_argument("--tickers", nargs="+", default=list(TICKERS), help="标的列表")
    parser.add_argument("--repeats", type=int, default=REPEATS, help="每变体每标的重复次数")
    parser.add_argument(
        "--judge-repeats",
        type=int,
        default=JUDGE_REPEATS,
        help="judge 每维度重复次数（取均值；单次调用在 5/4 边界有 ±1 噪声）",
    )
    parser.add_argument("--query", default=QUERY, help="统一查询（变体间一致）")
    parser.add_argument("--resume", type=Path, default=RESUME_PATH, help="断点续跑台账路径")
    parser.add_argument("--materials-dir", type=Path, default=MATERIALS_DIR, help="judge 材料目录")
    parser.add_argument("--out-dir", type=Path, default=OUT_DIR, help="产物目录")
    return parser.parse_args(argv)


def material_filename(variant: str, ticker: str, repeat: int) -> str:
    """材料文件名（按 run 三元组）。"""
    return f"{variant}-{ticker}-{repeat}.json"


def persist_materials(
    materials_dir: Path, variant: str, ticker: str, repeat: int, judge_vars: dict
) -> str:
    """落盘单条 run 的 judge 输入材料，返回相对路径（POSIX 风格，跨平台可复现）。"""
    materials_dir.mkdir(parents=True, exist_ok=True)
    path = materials_dir / material_filename(variant, ticker, repeat)
    path.write_text(json.dumps(judge_vars, ensure_ascii=False, indent=2), encoding="utf-8")
    return path.as_posix()


def _load_resume(path: Path) -> dict:
    if path.exists():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}
        if isinstance(loaded, dict):
            return loaded
    return {}


def judge_detail(result: dict) -> dict:
    """judge 结果 → run 记录用明细。

    含 debate v6 封顶/枚举遥测，以及中位数协议的每次分数与极差（round11 实测
    单次调用在 5/4 边界翻转 1/5——噪声须随 run 可见，不静默平均掉）。
    """
    detail: dict[str, Any] = {"score": result.get("score")}
    if result.get("reason") is not None:
        detail["reason"] = str(result.get("reason"))[:300]
    for key in ("scores", "score_spread", "judge_repeats", "judge_failures"):
        if result.get(key) is not None:
            detail[key] = result[key]
    for key in ("qualitative_points", "cap_applied", "enumeration_missing"):
        if key in result:
            detail[key] = result[key]
    return detail


def run_pilot(
    *,
    tickers: Sequence[str],
    repeats: int,
    query: str = QUERY,
    judge_repeats: int = JUDGE_REPEATS,
    resume_path: Path = RESUME_PATH,
    materials_dir: Path = MATERIALS_DIR,
    out_dir: Path = OUT_DIR,
    ablation_mod: Any | None = None,
    meter: Any | None = None,
    prompt_versions_fn: Any | None = None,
) -> Path:
    """消融主流程（可注入 ablation 模块/meter/prompt 版本函数，便于零 LLM 测试）。

    实际跑批消耗 LLM token（3 标的 × 3 变体 × N 次），属人工触发的评估动作；
    本函数不做静默降级。
    """
    if ablation_mod is None:
        import evals.ablation as _ablation

        ablation_mod = _ablation
    if meter is None:
        meter = pilot_util
    if prompt_versions_fn is None:
        from evals.run import _collect_prompt_versions

        prompt_versions_fn = _collect_prompt_versions
    # mypy 对「Optional 参数 + 函数内重绑定」不做收窄（重绑定后窄化失效）：
    # 显式取 Any 别名，换取本文件类型检查全绿；运行语义不变
    abl: Any = ablation_mod
    versions_fn: Any = prompt_versions_fn

    coverage_ledger: list[dict[str, Any]] = []
    run_costs: list[dict[str, Any]] = []

    original_verify = ablation_mod.verify_citations

    def metered_verify(state: dict) -> dict:
        out: dict = original_verify(state)
        coverage_ledger.append(
            {
                "citation_pass": out.get("citation_pass"),
                "citation_coverage": out.get("citation_coverage"),
            }
        )
        return out

    abl.verify_citations = metered_verify

    original_run_once = abl.run_variant_once

    def run_one_costed(variant: str, snapshot: dict, q: str) -> tuple[dict, dict]:
        """单次变体运行 + judge，返回 (out, cost_row)。"""
        before_calls = len(meter._usage_ledger)
        before_cov = len(coverage_ledger)
        out: dict = original_run_once(variant, snapshot, q)
        new_entries = meter._usage_ledger[before_calls:]
        cov_entries = coverage_ledger[before_cov:]

        judge_scores: dict[str, float | None] = {}
        judge_details: dict[str, dict] = {}
        applicable = abl._applicable_dims(variant)  # 唯一实现在库侧（#112）
        for dim in abl.JUDGE_DIMS:
            if dim not in applicable:
                judge_scores[dim] = None
                continue
            result = abl.run_judge_mean(dim, out["judge_vars"], repeats=judge_repeats)
            judge_scores[dim] = float(result["score"]) if result["score"] is not None else None
            judge_details[dim] = judge_detail(result)

        cost_row = {
            "variant": variant,
            "llm_calls": len(new_entries),
            "prompt_tokens": sum(r["prompt_tokens"] for r in new_entries),
            "completion_tokens": sum(r["completion_tokens"] for r in new_entries),
            "estimated_calls": sum(1 for r in new_entries if r["estimated"]),
            "citation_coverage": (
                sum(
                    c["citation_coverage"]
                    for c in cov_entries
                    if c["citation_coverage"] is not None
                )
                / max(1, sum(1 for c in cov_entries if c["citation_coverage"] is not None))
                if cov_entries
                else None
            ),
            "citation_pass": out.get("citation_pass"),
            # 论点锚点覆盖率 value（delta add-debate-argument-anchors 4.4）：确定性指标，
            # 由 run_variant_once 从 state 通道 debate_anchor_checks 提取；analysts 无辩论层
            # -> None（不得伪造成 0）
            "argument_anchor_coverage": (out["anchor_coverage"] or {}).get("value"),
            # 拆项（去 value）同落 run 记录/resume 台账：spec「value 与拆项」，
            # 口径与 evals/task.py 的 argument_anchor_coverage_detail 一致
            "argument_anchor_coverage_detail": (
                {k: v for k, v in out["anchor_coverage"].items() if k != "value"}
                if out["anchor_coverage"]
                else None
            ),
            "judge": judge_scores,
            "judge_detail": judge_details,
        }
        return out, cost_row

    # ── 断点续跑状态 ──
    resume = _load_resume(resume_path)
    done_keys = {tuple(k) for k in resume.get("done_keys", [])}
    runs: list[dict[str, Any]] = resume.get("runs", [])
    snap_digests: dict[str, str] = resume.get("snapshot_digests", {})
    print(
        f"消融试跑: {list(tickers)} × 3 变体 × {repeats} 重复"
        f" | 续跑起点: 已完成 {len(done_keys)} 条",
        flush=True,
    )

    def _persist() -> None:
        resume_path.parent.mkdir(parents=True, exist_ok=True)
        resume_path.write_text(
            json.dumps(
                {
                    "done_keys": sorted(done_keys),
                    "runs": runs,
                    "snapshot_digests": snap_digests,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

    for ticker in tickers:
        for variant in abl._VARIANTS:
            for i in range(repeats):
                key = (variant, ticker, i)
                if key in done_keys:
                    print(f"跳过已完成: {key}", flush=True)
                    continue
                print(f"[{datetime.now():%H:%M:%S}] 运行 {key}", flush=True)
                # 每轮重建快照，并逐次核验 digest 与本次跑批登记值一致——跨交易日续跑
                # 若数据源已刷新（行情/新闻按日变），新旧 run 输入不同，层增量即不可归因
                # 于编排架构。不一致时显式失败，不静默混批（不自动重建、不自动冻结）。
                snapshot = abl.build_snapshot(ticker)
                digest = abl.snapshot_digest(snapshot)
                registered = snap_digests.get(ticker)
                if registered is None:
                    snap_digests[ticker] = digest
                elif registered != digest:
                    raise RuntimeError(
                        f"快照 digest 与本次跑批登记值不一致（{ticker}）："
                        f"登记 {registered} / 重建 {digest}。续跑会混入不同输入，"
                        f"层增量不可归因于编排架构。重跑请换 --resume 新文件或清空台账。"
                    )
                out, cost_row = run_one_costed(variant, snapshot, query)
                materials_path = persist_materials(
                    materials_dir, variant, ticker, i, out["judge_vars"]
                )
                runs.append(
                    {
                        "variant": variant,
                        "ticker": ticker,
                        "repeat": i,
                        "citation_pass": out["citation_pass"],
                        "judge": out.get("judge") or cost_row["judge"],
                        "materials_path": materials_path,
                        **cost_row,
                    }
                )
                done_keys.add(key)
                _persist()

    report = abl.aggregate_results(runs)
    report["prompt_versions"] = versions_fn()
    report["snapshot_digests"] = snap_digests
    report["generated_at"] = datetime.now().isoformat(timespec="seconds")

    # 按变体归集 coverage 与成本
    per_variant: dict[str, dict[str, Any]] = {}
    for rc in runs:
        v = per_variant.setdefault(
            rc["variant"],
            {
                "runs": 0,
                "llm_calls": 0,
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "estimated_calls": 0,
                "coverages": [],
                "passes": [],
            },
        )
        v["runs"] += 1
        v["llm_calls"] += rc["llm_calls"]
        v["prompt_tokens"] += rc["prompt_tokens"]
        v["completion_tokens"] += rc["completion_tokens"]
        v["estimated_calls"] += rc["estimated_calls"]
        if rc["citation_coverage"] is not None:
            v["coverages"].append(rc["citation_coverage"])
        v["passes"].append(bool(rc["citation_pass"]))
    for v in per_variant.values():
        v["citation_coverage_mean"] = (
            round(sum(v["coverages"]) / len(v["coverages"]), 4) if v["coverages"] else None
        )
        v["total_tokens"] = v["prompt_tokens"] + v["completion_tokens"]
        v["citation_pass_rate"] = sum(v["passes"]) / len(v["passes"]) if v["passes"] else None
        del v["coverages"]
        del v["passes"]

    out = {
        "pilot": "ablation-pilot",
        "config": {
            "tickers": list(tickers),
            "repeats": repeats,
            "judge_repeats": judge_repeats,
            "query": query,
            "pipeline_model": os.environ.get("LLM_MODEL"),
            "judge_model": os.environ.get("JUDGE_MODEL"),
            "materials_dir": materials_dir.as_posix(),
            "resume_path": resume_path.as_posix(),
        },
        "report": report,
        "per_variant_cost": per_variant,
        "run_costs": runs,
        "usage_summary": {
            "llm_calls_total": len(meter._usage_ledger),
            "prompt_tokens": sum(r["prompt_tokens"] for r in meter._usage_ledger),
            "completion_tokens": sum(r["completion_tokens"] for r in meter._usage_ledger),
            "estimated_calls": sum(1 for r in meter._usage_ledger if r["estimated"]),
        },
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"pilot-{datetime.now().strftime('%Y%m%d-%H%M%S')}.json"
    path.write_text(json.dumps(out, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(f"消融试跑产物已写入 {path}")
    print(
        json.dumps(
            {"per_variant_cost": per_variant, "usage_summary": out["usage_summary"]},
            ensure_ascii=False,
            indent=2,
        )
    )
    return path


def main() -> None:
    from dotenv import load_dotenv

    args = parse_args()
    load_dotenv()
    pilot_util._pin_pipeline_model()
    pilot_util.install_usage_meter()
    run_pilot(
        tickers=args.tickers,
        repeats=args.repeats,
        query=args.query,
        resume_path=args.resume,
        materials_dir=args.materials_dir,
        out_dir=args.out_dir,
    )


if __name__ == "__main__":
    main()
