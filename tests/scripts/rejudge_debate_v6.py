#!/usr/bin/env python
"""debate_quality rubric v6 离线重判：round9 同批材料，验「结构化枚举 + 程序封顶」。

背景（2026-09-14 round10 预登记收口）：v5 的 prompt 内强制枚举把 8 行重判准确率
从 2/8 提到 6/8，但仍有漏判（宁德 04baff5c 仍满分）与回归（招行 53448e4b 由 4 抬到 5）
——LLM 自我枚举随机。v6 把判据搬到代码：judge 输出 `points`（论点标头 + data/qualitative），
`run_judge` 按枚举把分数压到 ≤4。

做法（与 v5 同批材料，纯归因：只有 rubric/判定机制变）：
1. 从 round9 trace 映射取 deep trace；
2. 逐条拉 debate_quality judge 观测 input，extract_sections 反解 debate_history；
3. 用当前（v6）rubric run_judge 重评，记录分数 + 枚举遥测（纯定性条数/是否封顶/枚举是否缺失）；
4. 与 round10 落库的 v5 分对比，输出 jsonl。

只读：默认 dry-run 只打印；--write 才 POST 新 score（comment 前缀 [rubric-v6]）。

用法:
    uv run python tests/scripts/rejudge_debate_v6.py \
        --trace-map tmp/round9-trace-map.json \
        --v5-scores evals/judge_calibration/data/judge-sample-round10-debate-v5.jsonl \
        --out evals/judge_calibration/data/judge-sample-round11-debate-v6.jsonl \
        [--write]
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import sys
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

LANGFUSE_HOST = os.environ.get("LANGFUSE_HOST", "http://localhost:3000")


def _load_env() -> None:
    try:
        from dotenv import load_dotenv

        load_dotenv(ROOT / ".env")
    except Exception:  # noqa: BLE001, S110
        pass


def _auth() -> str:
    pk = os.environ.get("LANGFUSE_PUBLIC_KEY")
    sk = os.environ.get("LANGFUSE_SECRET_KEY")
    if not (pk and sk):
        raise RuntimeError("LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY 未配置")
    return base64.b64encode(f"{pk}:{sk}".encode()).decode()


def _load_v5_scores(path: Path) -> dict[str, dict]:
    rows: dict[str, dict] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        rows[row["trace_id"]] = row
    return rows


def main() -> None:
    _load_env()
    ap = argparse.ArgumentParser()
    ap.add_argument("--trace-map", required=True)
    ap.add_argument("--v5-scores", required=True, help="round10 v5 重判 jsonl（同批 trace）")
    ap.add_argument("--out", required=True)
    ap.add_argument("--write", action="store_true")
    ap.add_argument(
        "--judge-repeats",
        type=int,
        default=3,
        help="每行重复判分次数，取均值（round11 实测单次调用在 5/4 边界双峰翻转，约半数）",
    )
    args = ap.parse_args()

    from evals.judge_calibration.material import (
        detect_dimension,
        extract_sections,
        prompt_from_observation_input,
    )
    from evals.judges import RUBRIC_VERSIONS, run_judge_mean

    auth = _auth()
    trace_map = json.loads(Path(args.trace_map).read_text(encoding="utf-8"))
    v5 = _load_v5_scores(Path(args.v5_scores))
    deep = {tid: trace_map.get(tid, "") for tid in v5}
    print(
        f"debate rubric v{RUBRIC_VERSIONS['debate_quality']} 重判 {len(deep)} 条 round9 deep trace"
        f"（材料同 round10）"
    )

    # 逐行落盘 + 跳过已完成（长跑防超时丢进度：单次 judge 调用 40-60s，
    # 8 行 × K=3 可达 25 分钟；已判行重跑会白烧 token）
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    rows: list[dict] = []
    if out.exists():
        rows = [
            json.loads(line)
            for line in out.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
    done_traces = {r["trace_id"] for r in rows}

    for tid, query in sorted(deep.items()):
        if tid in done_traces:
            print(f"  [skip] {tid[:8]} 已完成（断点续判）")
            continue
        resp = requests.get(
            f"{LANGFUSE_HOST}/api/public/observations",
            params={"traceId": tid, "limit": 100},
            headers={"Authorization": f"Basic {auth}"},
            timeout=30,
        )
        resp.raise_for_status()
        debate_text = ""
        for o in resp.json().get("data") or []:
            if str(o.get("name") or "") != "judge":
                continue
            prompt = prompt_from_observation_input(o.get("input"))
            if prompt and detect_dimension(prompt) == "debate_quality":
                debate_text = extract_sections(prompt, "debate_quality").get("【多空辩论记录】", "")
                break
        if not debate_text:
            print(f"  [skip] {tid[:8]} 无 debate 材料")
            continue
        result = run_judge_mean(
            "debate_quality", {"debate_history": debate_text}, repeats=args.judge_repeats
        )
        v6_score = result.get("score")
        v5_score = v5.get(tid, {}).get("v5_score")
        qual_headers = [
            p["header"] for p in result.get("points") or [] if p.get("type") == "qualitative"
        ]
        row = {
            "trace_id": tid,
            "query": query,
            "dimension": "debate_quality",
            "v4_score": v5.get(tid, {}).get("v4_score"),
            "v5_score": v5_score,
            "v6_score": v6_score,
            "v6_reason": (result.get("reason") or "")[:200],
            "qualitative_points": result.get("qualitative_points"),
            "cap_applied": result.get("cap_applied"),
            "enumeration_missing": result.get("enumeration_missing"),
            "qualitative_headers": qual_headers[:6],
            "scores": result.get("scores"),
            "score_spread": result.get("score_spread"),
            "judge_repeats": result.get("judge_repeats"),
        }
        rows.append(row)
        with out.open("w", encoding="utf-8") as f:  # 逐行落盘（防超时丢进度）
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        flag = "↓" if (v5_score or 0) > (v6_score or 0) else ("=" if v5_score == v6_score else "↑")
        cap = (
            "cap"
            if result.get("cap_applied")
            else ("MISS" if result.get("enumeration_missing") else "—")
        )
        print(
            f"  {tid[:8]} {query[:16]:<16} v5={v5_score} → v6={v6_score} {flag} "
            f"[{cap} qual={result.get('qualitative_points')} K={result.get('judge_repeats')} "
            f"scores={result.get('scores')} spread={result.get('score_spread')}] "
            f"{row['v6_reason'][:40]}"
        )
        if qual_headers:
            print(f"            纯定性标头: {qual_headers[:3]}")

        if args.write and v6_score is not None:
            requests.post(
                f"{LANGFUSE_HOST}/api/public/scores",
                headers={"Authorization": f"Basic {auth}", "Content-Type": "application/json"},
                json={
                    "traceId": tid,
                    "name": "debate_quality",
                    "value": float(v6_score),
                    "comment": (
                        f"[rubric-v6]{'[cap]' if result.get('cap_applied') else ''} "
                        f"{row['v6_reason']}"
                    ),
                    "dataType": "NUMERIC",
                },
                timeout=30,
            )

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    fives_v5 = [r for r in rows if r["v5_score"] == 5]
    fives_v6 = [r for r in rows if r["v6_score"] == 5]
    caps = [r for r in rows if r["cap_applied"]]
    missing = [r for r in rows if r["enumeration_missing"]]
    print(f"\n→ {out}")
    spreads = [r["score_spread"] for r in rows if r.get("score_spread")]
    print(
        f"v5 满分 {len(fives_v5)} 行 → v6 满分 {len(fives_v6)} 行；程序封顶 {len(caps)} 行；"
        f"枚举缺失 {len(missing)} 行；K={args.judge_repeats}，"
        f"极差>0 的行 {sum(1 for s in spreads if s)}/{len(rows)}"
    )
    for r in caps:
        print(
            f"  [cap] {r['trace_id'][:8]} {r['v5_score']}→{r['v6_score']} {r['qualitative_headers'][:2]}"
        )


if __name__ == "__main__":
    main()
