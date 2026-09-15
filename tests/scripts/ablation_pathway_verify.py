"""G5 通路验证脚本：薄壳化后 3 标的 stub 全链路（零 LLM）。

（revamp-ablation-v2-causal-claims G5；产物目录 reports/ablation/g5-verify/）

- TESTING=1：fetch/LLM stub（全链路）
- judge 以进程内确定性 stub 注入（evals.judges 无 TESTING 分支）
- 覆盖：首跑 9 run / 断点续跑零新增 / digest 不一致 RuntimeError
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

WORKTREE = Path(__file__).resolve().parents[2]
if str(WORKTREE) not in sys.path:
    sys.path.insert(0, str(WORKTREE))
sys.path.insert(0, str(WORKTREE / "tests" / "scripts"))

os.environ["TESTING"] = "1"

import ablation_pilot as pilot  # noqa: E402
import evals.ablation as abl  # noqa: E402

# ── 确定性 judge stub（按 dim 定分，形状与 run_judge_mean 契约一致）──
SCORES = {
    "report_relevance": 5,
    "debate_quality": 4,
    "decision_grounding": 4,
    "consistency": 4,
}


def fake_judge_mean(dimension: str, variables: dict, *, repeats: int = 3) -> dict:
    s = SCORES.get(dimension, 4)
    return {
        "name": dimension,
        "score": float(s),
        "reason": "stub",
        "confidence": 0.8,
        "scores": [s] * repeats,
        "score_spread": 0,
        "judge_repeats": repeats,
        "judge_failures": 0,
    }


abl.run_judge_mean = fake_judge_mean  # type: ignore[assignment]

base = WORKTREE / "reports" / "ablation" / "g5-verify"
resume = base / "resume.json"
materials = base / "judge_vars"
out = base / "out"

TICKERS = ["600519", "000001", "002412"]

print("=== 首跑（3 标的 × 3 变体 × 1 重复）===")
p1 = pilot.run_pilot(
    tickers=TICKERS,
    repeats=1,
    judge_repeats=1,
    resume_path=resume,
    materials_dir=materials,
    out_dir=out,
)
runs = json.loads(resume.read_text(encoding="utf-8"))["runs"]
print("runs 条数:", len(runs))
by_variant: dict[str, int] = {}
for r in runs:
    by_variant[r["variant"]] = by_variant.get(r["variant"], 0) + 1
print("按变体:", by_variant)
sample = runs[-1]
print(
    "样本键含 citation_buckets:",
    "citation_buckets" in sample,
    "| argument_anchor_coverage:",
    sample.get("argument_anchor_coverage"),
    "| materials_path:",
    bool(sample.get("materials_path")),
)

print("=== 断点续跑（同参数重跑，应零新增）===")
p2 = pilot.run_pilot(
    tickers=TICKERS,
    repeats=1,
    judge_repeats=1,
    resume_path=resume,
    materials_dir=materials,
    out_dir=out,
)
runs2 = json.loads(resume.read_text(encoding="utf-8"))["runs"]
print("runs 条数:", len(runs2), "（零新增 =", len(runs2) == len(runs), "）")

print("=== digest 核验（篡改登记 digest 后新增 run 应 RuntimeError）===")
data = json.loads(resume.read_text(encoding="utf-8"))
first_ticker = TICKERS[0]
data["snapshot_digests"][first_ticker] = "tampered-digest"
resume.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
try:
    pilot.run_pilot(
        tickers=TICKERS,
        repeats=2,
        judge_repeats=1,
        resume_path=resume,
        materials_dir=materials,
        out_dir=out,
    )
    print("!! 未抛错（核验未生效）")
except RuntimeError as e:
    print("RuntimeError 生效:", str(e)[:140])
