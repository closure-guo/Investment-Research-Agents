"""无源增量 grounding 扫描驱动（bg-v1，预登记 2026-09-18-p2-b1-grounding-scan.md）。

宇宙 = 20 标的 × 第 1 轮 kind=data 空方论点（64 单元）。判定即时落盘按 unit_id 缓存。
产物：
- reports/ablation/p2/judged-bg-v1.jsonl（判定缓存）
- tests/validation/2026-09-18-p2-grounding-calibration.csv（采样协议 v2 分层样本，人工列留空）

用法：
    uv run python tests/scripts/p2_bear_grounding_scan.py            # 全量判定 + 校准样本
    uv run python tests/scripts/p2_bear_grounding_scan.py --report   # 只读缓存
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from evals.causal_ablation import bear_grounding as bg  # noqa: E402
from evals.causal_ablation import family_b_materials as fb  # noqa: E402

MATERIALS_DIR = _ROOT / "reports/ablation/p2/materials"
CACHE = _ROOT / "reports/ablation/p2/judged-bg-v1.jsonl"
CALIBRATION_CSV = _ROOT / "tests/validation/2026-09-18-p2-grounding-calibration.csv"

COLUMNS = (
    "unit_id",
    "ticker",
    "被审论点(第1轮 kind=data)",
    "辩手的全部输入(分析师摘要)",
    "机器判定(有源?是/否)",
    "无源断言(机器摘出)",
    "机器理由",
    "机器把握",
    "该论点挂的锚点",
    "人工判定(有源?是/否)",
    "人工无源断言确认",
)


def _units() -> list[dict]:
    out: list[dict] = []
    for path in sorted(MATERIALS_DIR.glob("*.full.pkl")):
        ticker = path.name.split(".")[0]
        state = fb.load_material(MATERIALS_DIR, ticker)["state"]
        out.extend(bg.grounding_units(ticker, state))
    return out


def _load_cache() -> dict[str, dict]:
    if not CACHE.exists():
        return {}
    return {
        str(json.loads(x).get("unit_id")): json.loads(x)
        for x in CACHE.read_text(encoding="utf-8").splitlines()
        if x.strip()
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--report", action="store_true", help="只读缓存，不调 LLM")
    args = ap.parse_args()

    rows = _units()
    done = _load_cache()
    todo = [r for r in rows if str(r.get("unit_id")) not in done]
    print(f"[bg] 宇宙 {len(rows)} 单元｜缓存 {len(done)}｜待判 {len(todo)}", flush=True)
    if todo and not args.report:
        from dotenv import load_dotenv

        load_dotenv()
        fresh = bg.run_grounding(todo)
        with CACHE.open("a", encoding="utf-8") as fh:
            for r in fresh:
                fh.write(json.dumps(r, ensure_ascii=False) + chr(10))
        done.update({str(r["unit_id"]): r for r in fresh})
        print(f"[bg] 新判 {len(fresh)} 行已落盘 {CACHE.name}", flush=True)

    judged = [done[str(r["unit_id"])] for r in rows if str(r["unit_id"]) in done]
    report = bg.grounding_report(judged)
    print(
        json.dumps(
            {
                k: report[k]
                for k in ("rows", "labeled", "parse_failed", "unsupported_count", "rate")
            },
            ensure_ascii=False,
        )
    )
    for uid, part, anchors in report["unsupported"]:
        print(f"  无源 {uid}: {part[:60]}｜锚点 {list(anchors)}")

    sample = bg.calibration_sample(judged)
    by_id = {str(r.get("unit_id")): r for r in judged}
    try:
        with CALIBRATION_CSV.open("w", encoding="utf-8-sig", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=list(COLUMNS))
            writer.writeheader()
            for uid in [str(r.get("unit_id")) for r in sample]:
                r = by_id[uid]
                writer.writerow(
                    {
                        "unit_id": uid,
                        "ticker": r.get("ticker"),
                        "被审论点(第1轮 kind=data)": r.get("argument"),
                        "辩手的全部输入(分析师摘要)": str(r.get("summaries") or "").replace(
                            "\n", " ｜ "
                        ),
                        "机器判定(有源?是/否)": "是"
                        if r.get("judge_label") is True
                        else "否"
                        if r.get("judge_label") is False
                        else "解析失败",
                        "无源断言(机器摘出)": r.get("unsupported_part") or "",
                        "机器理由": r.get("judge_reason") or "",
                        "机器把握": r.get("confidence") or "",
                        "该论点挂的锚点": "；".join(r.get("anchors") or []) or "（无锚）",
                    }
                )
        print(f"[bg] 校准样本 {len(sample)} 行 → {CALIBRATION_CSV.name}（人工列留空）")
    except PermissionError:
        print(f"[拒绝写入] {CALIBRATION_CSV}（Excel 占用？）")
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
