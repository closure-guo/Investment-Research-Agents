"""定向变异探针（验证钉子 2）：向闸门机器注入定点变异，量化测试杀伤率。

方法：对 factors/screening 关键路径逐条应用**人工策展的定点变异**（一行级语义
篡改），运行聚焦测试子集；「被杀死」= 子集失败；「存活」= 测试盲区（输出清单）。
比 mutmut 类全自动工具更可控（Windows 无 fork；变异集即审计对象），代价是变异
集非穷举——报告中如实标注。

运行::

    uv run python tests/scripts/mutation_probe.py [--out reports/mutation-probe-20261017.json]
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class Mutation:
    mutation_id: str
    file: str
    old: str
    new: str
    tests: str
    note: str


MUTATIONS: list[Mutation] = [
    # ── eval_engine（IC 机器） ──
    Mutation(
        "eval-mean-warmup",
        "src/finance_agent/factors/eval_engine.py",
        "return frame.rolling(window, min_periods=window).mean()",
        "return frame.rolling(window, min_periods=1).mean()",
        "tests/factors/test_eval_engine.py",
        "ts_mean 热身期口径（min_periods=window → 1）",
    ),
    Mutation(
        "eval-delta-shift",
        "src/finance_agent/factors/eval_engine.py",
        "return frame - frame.shift(window)",
        "return frame - frame.shift(1)",
        "tests/factors/test_eval_engine.py",
        "delta 后移窗宽（window → 1）",
    ),
    Mutation(
        "eval-forward-horizon",
        "src/finance_agent/factors/eval_engine.py",
        "returns = close_qfq.shift(-horizon) / close_qfq - 1.0",
        "returns = close_qfq.shift(-(horizon - 1)) / close_qfq - 1.0",
        "tests/factors/test_eval_engine.py",
        "远期收益 horizon（-h → -(h-1)）",
    ),
    Mutation(
        "eval-ic-min-samples",
        "src/finance_agent/factors/eval_engine.py",
        "ic = ic.where(counts >= min_samples)",
        "ic = ic.where(counts > min_samples)",
        "tests/factors/test_eval_engine.py",
        "IC 有效样本下限边界（>= → >）",
    ),
    Mutation(
        "eval-tsrank-first",
        "src/finance_agent/factors/eval_engine.py",
        "return float(pd.Series(values).rank(pct=True).iloc[-1])",
        "return float(pd.Series(values).rank(pct=True).iloc[0])",
        "tests/factors/test_eval_engine.py",
        "ts_rank 取窗口首值而非当前值",
    ),
    # ── validate（五道闸） ──
    Mutation(
        "gate-icir-threshold",
        "src/finance_agent/factors/constraints.py",
        "MIN_ABS_ICIR: float = 0.3",
        "MIN_ABS_ICIR: float = 0.03",
        "tests/factors/test_validate.py",
        "闸②阈值量级（0.3 → 0.03）",
    ),
    Mutation(
        "gate-missing-rate-boundary",
        "src/finance_agent/factors/validate.py",
        "passed = missing_rate <= MAX_MISSING_RATE",
        "passed = missing_rate < MAX_MISSING_RATE",
        "tests/factors/test_validate.py",
        "闸①缺失率边界（<= → <）",
    ),
    Mutation(
        "gate-train-split",
        "src/finance_agent/factors/validate.py",
        "cut = int(len(ordered) * fraction)",
        "cut = int(len(ordered) * fraction) + 1",
        "tests/factors/test_validate.py",
        "闸④⑤切点（+1 偏移）",
    ),
    Mutation(
        "gate-bh-scale",
        "src/finance_agent/factors/validate.py",
        "running = min(running, p_values[key] * total / rank)",
        "running = min(running, p_values[key] * total)",
        "tests/factors/test_validate.py",
        "BH 调整公式（丢 /rank 因子）",
    ),
    # ── screening（降级通道 + 打分器） ──
    Mutation(
        "ranker-weight-normalize",
        "src/finance_agent/screening/ranker.py",
        "return {factor_id: ic_mean / total for factor_id, ic_mean in ic_means.items()}",
        "return {factor_id: ic_mean for factor_id, ic_mean in ic_means.items()}",
        "tests/screening/test_ranker.py",
        "权重归一化去除（Σw≠1）",
    ),
    Mutation(
        "ranker-scorable-boundary",
        "src/finance_agent/screening/ranker.py",
        "return total >= _MIN_ABSOLUTE_IC_TOTAL",
        "return total > _MIN_ABSOLUTE_IC_TOTAL",
        "tests/screening/test_ranker.py",
        "降级谓词边界（>= → >）",
    ),
    Mutation(
        "ranker-fallback-sort",
        "src/finance_agent/screening/ranker.py",
        "scored.sort(key=lambda item: (-item[1], item[0]))",
        "scored.sort(key=lambda item: (item[1], item[0]))",
        "tests/screening/test_ranker.py",
        "降级排序方向（降序 → 升序）",
    ),
    Mutation(
        "pipeline-fallback-condition",
        "src/finance_agent/screening/pipeline.py",
        "if has_scorable_factors(factors, ic_stats):",
        "if True:",
        "tests/screening/test_pipeline.py",
        "降级触发条件恒假（永不降级）",
    ),
    Mutation(
        "store-selection-mode-guard",
        "src/finance_agent/screening/store.py",
        "if selection_mode not in SELECTION_MODES:",
        "if False:",
        "tests/screening/test_screening_store.py",
        "selection_mode 校验去除",
    ),
]


def _run_tests(selector: str) -> bool:
    """运行测试子集；True = 通过（变异存活），False = 失败（变异被杀死）。"""
    # selector 来自本文件内的策展变异表（非外部输入）——S603 不适用
    proc = subprocess.run(  # noqa: S603
        [
            sys.executable,
            "-m",
            "pytest",
            selector,
            "-x",
            "-q",
            "--no-header",
            "-p",
            "no:cacheprovider",
        ],
        cwd=_ROOT,
        capture_output=True,
        text=True,
        timeout=900,
    )
    return proc.returncode == 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="定向变异探针")
    parser.add_argument("--out", default=None)
    args = parser.parse_args(argv)

    results: list[dict] = []
    with tempfile.TemporaryDirectory(prefix="mutation-probe-") as tmp:
        for mutation in MUTATIONS:
            path = _ROOT / mutation.file
            backup = Path(tmp) / f"{mutation.mutation_id}.bak"
            shutil.copy2(path, backup)
            text = path.read_text(encoding="utf-8")
            if mutation.old not in text:
                results.append(
                    {"id": mutation.mutation_id, "status": "ANCHOR-MISS", "note": mutation.note}
                )
                print(f"[ANCHOR-MISS] {mutation.mutation_id}（锚点未命中，变异集需更新）")
                continue
            try:
                path.write_text(text.replace(mutation.old, mutation.new, 1), encoding="utf-8")
                passed = _run_tests(mutation.tests)
            finally:
                shutil.copy2(backup, path)
            status = "SURVIVED" if passed else "KILLED"
            results.append(
                {
                    "id": mutation.mutation_id,
                    "status": status,
                    "file": mutation.file,
                    "tests": mutation.tests,
                    "note": mutation.note,
                }
            )
            print(f"[{status}] {mutation.mutation_id} — {mutation.note}")

    killed = sum(1 for r in results if r["status"] == "KILLED")
    total = len(results)
    survivors = [r["id"] for r in results if r["status"] == "SURVIVED"]
    anchor_miss = [r["id"] for r in results if r["status"] == "ANCHOR-MISS"]
    print(
        f"\n杀伤率：{killed}/{total}；存活（测试盲区）：{survivors or '无'}；锚点未命中：{anchor_miss or '无'}"
    )
    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(
            json.dumps(
                {"killed": killed, "total": total, "survivors": survivors, "results": results},
                ensure_ascii=False,
                indent=1,
            ),
            encoding="utf-8",
        )
        print(f"产物：{out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
