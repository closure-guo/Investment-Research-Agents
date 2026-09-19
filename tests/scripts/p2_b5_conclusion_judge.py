"""B5c 结论级盲评驱动（预登记 §10，b5c-v1；owner 定稿判据）。

只比两臂的最终交易指令（「交易决策」章节），六准则 + 双防排除；20 对真对照 + 2 对同臂
暗对照（应判 tie，抓位置偏差）。缓存 judged-b5-conclusion-v1.jsonl 按 unit_id 续跑。

用法：
    uv run python tests/scripts/p2_b5_conclusion_judge.py --pilot   # 前 2 对试跑（6 次）
    uv run python tests/scripts/p2_b5_conclusion_judge.py           # 全量（60+6 次）
    uv run python tests/scripts/p2_b5_conclusion_judge.py --report  # 只读缓存
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from evals.causal_ablation import family_b_judge as fj  # noqa: E402
from evals.causal_ablation import family_b_materials as fb  # noqa: E402

MATERIALS_DIR = _ROOT / "reports/ablation/p2/materials"
CACHE = _ROOT / "reports/ablation/p2/judged-b5-conclusion-v1.jsonl"
CALIBRATION_CSV = _ROOT / "tests/validation/2026-09-19-p2-calibration-b5c.csv"

ARM_DRAFT = "full_no_riskfm"  # 逻辑臂 a：Trader 初稿（风控层缺席）
ARM_FINAL = "full"  # 逻辑臂 b：风控辩论+FM 修改后终稿
DARK_CONTROL_TICKERS = ("600519", "002415")  # 同臂对（a=a），评审不可知，应判 tie


def _decision_block(report: str) -> str:
    m = re.search(r"(#+[^\n]*交易决策[^\n]*\n)(.*?)(?=\n#+ |\Z)", report, re.S)
    return m.group(2).strip() if m else ""


def _pairs(tickers: list[str]) -> list[dict]:
    out: list[dict] = []
    for t in tickers:
        draft = _decision_block(
            str(fb.load_material(MATERIALS_DIR, t, ARM_DRAFT)["state"].get("final_report") or "")
        )
        final = _decision_block(
            str(fb.load_material(MATERIALS_DIR, t, ARM_FINAL)["state"].get("final_report") or "")
        )
        if not draft.strip() or not final.strip():
            print(f"[b5c] {t}: 决策段提取为空，跳过", flush=True)
            continue
        out.append(
            {
                "unit_id": f"{t}::b5c",
                "pair_key": t,
                "ticker": t,
                "instruction_a": draft,
                "instruction_b": final,
            }
        )
    for t in DARK_CONTROL_TICKERS:
        text = _decision_block(
            str(fb.load_material(MATERIALS_DIR, t, ARM_DRAFT)["state"].get("final_report") or "")
        )
        out.append(
            {
                "unit_id": f"{t}::b5c-dark",
                "pair_key": f"{t}-dark",
                "ticker": t,
                "instruction_a": text,
                "instruction_b": text,
            }
        )
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pilot", action="store_true", help="只跑前 2 对（试跑检理由质量）")
    ap.add_argument("--report", action="store_true", help="只读缓存")
    args = ap.parse_args()

    done: dict[str, dict] = {}
    if CACHE.exists():
        done = {
            str(json.loads(x).get("unit_id")): json.loads(x)
            for x in CACHE.read_text(encoding="utf-8").splitlines()
            if x.strip()
        }
    wanted = _pairs(
        ["600519", "000001"]
        if args.pilot
        else sorted({p.name.split(".")[0] for p in MATERIALS_DIR.glob("*.full.pkl")})
    )
    todo = [p for p in wanted if str(p["unit_id"]) not in done]
    print(f"[b5c] 目标 {len(wanted)} 对｜缓存 {len(done)}｜待判 {len(todo)}", flush=True)
    if todo and not args.report:
        from dotenv import load_dotenv

        load_dotenv()
        fresh = fj.run_b5_conclusion(todo)
        with CACHE.open("a", encoding="utf-8") as fh:
            for r in fresh:
                fh.write(json.dumps(r, ensure_ascii=False) + chr(10))
        done.update({str(r["unit_id"]): r for r in fresh})
        print(f"[b5c] 新判 {len(fresh)} 行落盘 {CACHE.name}", flush=True)

    real = [r for r in done.values() if not str(r.get("unit_id")).endswith("-dark")]
    dark = [r for r in done.values() if str(r.get("unit_id")).endswith("-dark")]
    from collections import Counter

    vc = Counter(str(r.get("verdict")) for r in real)
    dc = Counter(str(r.get("verdict")) for r in dark)
    crit = Counter(
        c for r in real if r.get("verdict") == "b" for c in (r.get("decisive_criteria") or []) if c
    )
    print(
        json.dumps(
            {
                "real_pairs": len(real),
                "verdicts(a=初稿,b=风控终稿,tie)": dict(vc),
                "dark_controls(expected tie)": dict(dc),
                "decisive_criteria_on_b_wins": dict(crit.most_common()),
            },
            ensure_ascii=False,
            indent=1,
        )
    )
    for r in sorted(real, key=lambda x: str(x.get("unit_id"))):
        print(f"  {r['unit_id']}: {r.get('verdict')} | {str(r.get('judge_reason'))[:90]}")
    if not args.pilot and real:
        sample_rows = [r for r in real if r.get("verdict") != "tie"][:4] + [
            r for r in real if r.get("verdict") == "tie"
        ][:1]
        try:
            with CALIBRATION_CSV.open("w", encoding="utf-8-sig", newline="") as fh:
                cols = [
                    "unit_id",
                    "ticker",
                    "机器判定(初稿a/风控终稿b/tie)",
                    "决定性准则",
                    "机器理由",
                    "初稿指令",
                    "风控终稿指令",
                    "人工判定(a/b/tie)",
                ]
                w = csv.DictWriter(fh, fieldnames=cols)
                w.writeheader()
                by_id = {str(p["unit_id"]): p for p in wanted}
                for r in sample_rows:
                    p = by_id.get(str(r["unit_id"]), {})
                    w.writerow(
                        {
                            "unit_id": r["unit_id"],
                            "ticker": r["ticker"],
                            "机器判定(初稿a/风控终稿b/tie)": r.get("verdict"),
                            "决定性准则": "；".join(
                                str(c) for c in (r.get("decisive_criteria") or [])
                            ),
                            "机器理由": str(r.get("judge_reason") or "")[:200],
                            "初稿指令": str(p.get("instruction_a") or "")[:500],
                            "风控终稿指令": str(p.get("instruction_b") or "")[:500],
                        }
                    )
            print(f"[b5c] 校准样本 {len(sample_rows)} 行 → {CALIBRATION_CSV.name}")
        except PermissionError:
            print(f"[拒绝写入] {CALIBRATION_CSV}")
            return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
