"""judge 端点切换 A/B 探针（2026-10-17 bigmodel → opencode Go）。

目的：同材料、同 rubric、同 K=3 协议，在旧端点（bigmodel coding plan）与
新端点（opencode Go）各跑一遍，比对分数分布 / 解析失败率 / 调用耗时。
材料来源：reports/ablation/observation/*.full.pkl（2026-09-22 variant=full 的真实
管线状态），经 extract_judge_vars 重建——两相共用同一份材料对象。
Langfuse 标记：environment=judge-endpoint-swap-ab（与生产 judge 流量区分）。
"""

import json
import os
import pickle
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")


def parse_env(p: Path) -> dict[str, str]:
    d: dict[str, str] = {}
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        d[k.strip()] = v.strip()
    return d


old = parse_env(ROOT / ".env.bak-judge-bigmodel-20261017")

import evals.judges as J  # noqa: E402, N812 - 探针脚本惯用别名
from evals.extract import extract_judge_vars  # noqa: E402

J.JUDGE_ENV = "judge-endpoint-swap-ab"  # 测试流量标记（monkeypatch 模块常量）

TICKERS = ["000001", "300750", "600887"]
DIMS = ["report_relevance", "debate_quality", "decision_grounding", "consistency"]
K = 3

MATS: dict[str, dict[str, str]] = {}
for t in TICKERS:
    with open(ROOT / f"reports/ablation/observation/{t}.full.pkl", "rb") as f:
        st = pickle.load(f)["state"]  # noqa: S301 - 本地冻结实验状态自产自读
    MATS[t] = extract_judge_vars(st, query=st.get("focus") or "")
    sizes = {k: len(v.encode("utf-8")) for k, v in MATS[t].items() if isinstance(v, str) and v}
    print("material sizes", t, sizes, flush=True)

results: list[dict[str, object]] = []
phases = [
    ("opencode", os.environ["JUDGE_BASE_URL"], os.environ["JUDGE_API_KEY"]),
    ("bigmodel", old["JUDGE_BASE_URL"], old["JUDGE_API_KEY"]),
]
for phase, base, key in phases:
    os.environ["JUDGE_BASE_URL"] = base
    os.environ["JUDGE_API_KEY"] = key
    for t in TICKERS:
        for dim in DIMS:
            t0 = time.time()
            r = J.run_judge_mean(dim, MATS[t], repeats=K)
            rec = {
                "phase": phase,
                "ticker": t,
                "dim": dim,
                "score": r.get("score"),
                "scores": r.get("scores"),
                "spread": r.get("score_spread"),
                "failures": r.get("judge_failures"),
                "confidence": r.get("confidence"),
                "dt": round(time.time() - t0, 1),
            }
            print(json.dumps(rec, ensure_ascii=False), flush=True)
            results.append(rec)

with open(ROOT / "log/judge_ab_result.json", "w", encoding="utf-8") as f:
    json.dump(results, f, ensure_ascii=False, indent=1)
print("DONE", len(results), flush=True)
