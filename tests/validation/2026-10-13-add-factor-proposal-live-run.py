"""add-factor-proposal-agent Task 4 真跑脚本：2 批 broad 提案（各 25 条 ≥20）+ 统计落盘。

用法：
    uv run python tests/validation/2026-10-13-add-factor-proposal-live-run.py --dry
    uv run python tests/validation/2026-10-13-add-factor-proposal-live-run.py

- 读取仓库根 `.env`（LLM_*，与 `api.py` 的 `load_dotenv()` 同一来源）；
- 经生产路径 `GatewayProposalClient`（真实 gateway，零 mock、零 stub）发起
  两批广度变异提案（batch-1 带单桶占优 70% 触发跨桶指令；batch-2 均衡采样）；
- 独立复核每条提案（不依赖 propose 内部校验）：DSL 再解析 / hypothesis 字数 /
  category 分布 / alignment 分布；rejected 明细与 trace metadata 原样落 JSON；
- 打印**不包含任何密钥**：只报 key 存在性、endpoint host、模型名与结果统计；
- 成果落 `tests/validation/2026-10-13-add-factor-proposal-live-run.json`。
"""

from __future__ import annotations

import json
import sys
import time
from collections import Counter
from pathlib import Path
from urllib.parse import urlparse

from dotenv import find_dotenv, load_dotenv

WORKTREE_ROOT = Path(__file__).resolve().parents[2]
# 与 api.py 的无参 load_dotenv() 同源：向上查找到仓库根 .env（worktree 场景为宿主仓库）
ENV_FILE = find_dotenv(usecwd=False)
load_dotenv(ENV_FILE)

sys.path.insert(0, str(WORKTREE_ROOT / "src"))

from finance_agent.factors.dsl import parse  # noqa: E402
from finance_agent.factors.propose import (  # noqa: E402
    GatewayProposalClient,
    propose_broad_batch,
    route_proposal_call,
)

OUT_JSON = Path(__file__).resolve().with_name("2026-10-13-add-factor-proposal-live-run.json")

BATCH_N = 25

BATCHES = [
    {
        "name": "batch-1（动量占优 70% → 跨桶引导）",
        "bucket_stats": {"动量": 7, "价量": 2, "反转": 1},
        "operator_ic": {"ts_mean": 0.021, "delta": -0.004, "rank": 0.013, "ts_std": 0.008},
    },
    {
        "name": "batch-2（均衡采样）",
        "bucket_stats": {"价量": 5, "流动性": 4, "波动率": 3, "反转": 3, "动量": 2},
        "operator_ic": {"ts_corr": 0.017, "zscore": 0.011, "ts_rank": 0.006, "sub": -0.002},
    },
]


def _presence() -> dict[str, bool]:
    import os

    return {
        key: bool(os.environ.get(key))
        for key in ("LLM_API_KEY", "LLM_BASE_URL", "LLM_MODEL", "LLM_QUICK_MODEL")
    }


def _endpoint_host() -> str:
    import os

    raw = os.environ.get("LLM_BASE_URL", "")
    return urlparse(raw).netloc or "（registry 默认端点）"


def _review_proposals(proposals) -> dict:
    """独立复核（不依赖 propose 内部断言）：DSL 再解析 / 字数 / 分布。"""
    parseable = 0
    hypothesis_ok = 0
    categories: Counter = Counter()
    alignment: Counter = Counter()
    rows = []
    for proposal in proposals:
        try:
            parse(proposal.expression)
            parseable += 1
        except ValueError as exc:  # 独立复核发现不可解析 → 记录（不应发生）
            rows.append({"expression": proposal.expression, "独立复核": f"不可解析：{exc}"})
            continue
        hyp_len = len(proposal.hypothesis.strip())
        hypothesis_ok += int(hyp_len >= 30)
        categories[proposal.category] += 1
        score = proposal.alignment_score
        bucket = "缺失" if score is None else f"{score:.1f}"
        alignment[bucket] += 1
        rows.append(
            {
                "expression": proposal.expression,
                "hypothesis": proposal.hypothesis,
                "hypothesis_chars": hyp_len,
                "category": proposal.category,
                "alignment_score": score,
                "mode": proposal.mode,
            }
        )
    total = len(proposals)
    return {
        "total": total,
        "dsl_parseable": parseable,
        "dsl_parse_rate": f"{parseable}/{total}" if total else "0/0",
        "hypothesis_ok": hypothesis_ok,
        "hypothesis_rate": f"{hypothesis_ok}/{total}" if total else "0/0",
        "category_distribution": dict(categories),
        "alignment_distribution": dict(alignment),
        "rows": rows,
    }


def main() -> int:
    presence = _presence()
    print("env 文件：", ENV_FILE)
    print("凭据存在性（只报存在与否，不打印值）：", presence)
    print("endpoint host：", _endpoint_host())

    route = route_proposal_call("broad")
    print(
        "路由决策（broad 档）：",
        json.dumps(route.trace, ensure_ascii=False),
    )
    from finance_agent.llm.resolver import resolve_profile

    print(
        "双档 profile：",
        json.dumps(
            {
                "quick": resolve_profile(purpose="quick").model,
                "deep": resolve_profile(purpose="deep").model,
            },
            ensure_ascii=False,
        ),
    )

    if "--dry" in sys.argv:
        print("--dry：仅校验凭据与路由，不发起调用。")
        return 0

    missing = [k for k in ("LLM_API_KEY", "LLM_BASE_URL", "LLM_MODEL") if not presence[k]]
    if missing:
        print(f"缺关键配置 {missing}：真跑受阻，如实记录。")
        return 1

    client = GatewayProposalClient(mode="broad")
    report: dict = {
        "router_trace": route.trace,
        "endpoint_host": _endpoint_host(),
        "presence": presence,
        "batches": [],
    }
    failures = 0
    for spec in BATCHES:
        entry: dict = {"name": spec["name"], "bucket_stats": spec["bucket_stats"]}
        started = time.time()
        try:
            result = propose_broad_batch(
                n=BATCH_N,
                client=client,
                bucket_stats=spec["bucket_stats"],
                operator_ic=spec["operator_ic"],
            )
        except Exception as exc:  # noqa: BLE001 -- 真跑失败如实记录（网络/额度/端点）
            failures += 1
            entry["error"] = f"{type(exc).__name__}: {exc}"
            entry["elapsed_seconds"] = round(time.time() - started, 1)
            report["batches"].append(entry)
            print(f"[{spec['name']}] 调用失败：{entry['error']}")
            continue
        entry["elapsed_seconds"] = round(time.time() - started, 1)
        entry["proposal_count"] = len(result.proposals)
        entry["rejected_count"] = len(result.rejected)
        entry["trace_metadata"] = result.trace_metadata
        entry["rejected"] = [{"raw": r.raw, "error": r.error} for r in result.rejected]
        entry["review"] = _review_proposals(result.proposals)
        report["batches"].append(entry)
        review = entry["review"]
        print(
            f"[{spec['name']}] {entry['elapsed_seconds']}s "
            f"proposals={entry['proposal_count']} rejected={entry['rejected_count']} "
            f"DSL={review['dsl_parse_rate']} hypothesis={review['hypothesis_rate']} "
            f"category={review['category_distribution']} alignment={review['alignment_distribution']}"
        )
        for row in entry["rejected"]:
            print(
                "  rejected:",
                json.dumps(row["raw"], ensure_ascii=False)[:200],
                "|",
                row["error"][:200],
            )

    total = sum(int(b.get("review", {}).get("total", 0)) for b in report["batches"])
    rejected_total = sum(int(b.get("rejected_count", 0)) for b in report["batches"])
    print(f"\n合计：proposals={total} rejected={rejected_total} 批次失败={failures}")
    OUT_JSON.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"明细已落：{OUT_JSON}")
    return 1 if failures == len(BATCHES) else 0


if __name__ == "__main__":
    raise SystemExit(main())
