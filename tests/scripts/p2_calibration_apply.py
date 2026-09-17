"""P2 校准回读（零 LLM）：人工标注 → 一致率 → 门控判词（+ B1 阈值标定）。

**这一步才是「校准门控」的执行体**（spec：nli/judge 判定的 rubric 与人工一致率 ≥0.80
才允许进消融结论）。本脚本读 `tests/validation/2026-09-17-p2-calibration-*.csv` 的人工列：

- B1：`人工判定(被吸收?是/否)` → 吸收判定的机器-人工一致率 → `calibration_gate.gate_dimension`；
  另读 `人工判定(是否新增?是/否)` → **标定「新增」的字面相似度阈值**（扫阈值取与人工最一致者）；
- B2：`人工判定(被修正?是/否)` → 修正判定的一致率 → 门控判词。

口径：
- 人工列只认「是 / 否」（大小写、全半角归一）；空值 = **未标注**，不进一致率（不得当"不一致"）；
- 一致率 <0.80 → 该批 nli/judge 判定**作废并重校准**（判词由 `gate_dimension` 给出，机器不代改）；
- B5 不参与（口径已判作废：analysts 臂无决策章节，同义反复），脚本对缺表/空表不报错。

用法：
    uv run python tests/scripts/p2_calibration_apply.py            # 出判词
    uv run python tests/scripts/p2_calibration_apply.py --json out.json
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections.abc import Sequence
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from evals.causal_ablation.calibration_gate import gate_dimension  # noqa: E402

from evals.causal_ablation import family_b_text as ft  # noqa: E402

CALIBRATION_DIR = Path("tests/validation")
MATERIALS_DIR = Path("reports/ablation/p2/materials")

# 列名 → 判定键（与 p2_calibration_export.py 的导出列一致）
LEGS: dict[str, dict[str, str]] = {
    "b1": {
        "file": "2026-09-17-p2-calibration-b1.csv",
        "human": "人工判定(被吸收?是/否)",
        "machine": "机器判定(被吸收?是/否)",
        "method": "nli",
        "newness_human": "人工判定(是否新增?是/否)",
        "risk_point": "风险点",
    },
    "b2": {
        "file": "2026-09-17-p2-calibration-b2.csv",
        "human": "人工判定(被修正?是/否)",
        "machine": "机器判定(被修正?是/否)",
        "method": "judge",
    },
}
_TRUE = {"是", "y", "yes", "true", "一致", "1"}
_FALSE = {"否", "n", "no", "false", "不一致", "0"}


def _norm(value: object) -> str | None:
    """人工列归一：是/否 → "是"/"否"；空或无法识别 → None（不进一致率）。"""
    text = str(value or "").strip().lower().replace(" ", "")
    if not text:
        return None
    if text in _TRUE:
        return "是"
    if text in _FALSE:
        return "否"
    return None


def _machine_norm(value: object) -> str | None:
    text = str(value or "").strip()
    if text in ("是", "否"):
        return text
    return None


def read_leg(path: Path, spec: dict[str, str]) -> list[dict]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8-sig", newline="") as fh:
        rows = list(csv.DictReader(fh))
    out: list[dict] = []
    for row in rows:
        human = _norm(row.get(spec["human"]))
        machine = _machine_norm(row.get(spec["machine"]))
        item = {
            "unit_id": row.get("unit_id"),
            "ticker": row.get("ticker"),
            "machine": machine,
            "human": human,
        }
        if spec.get("newness_human"):
            item["human_newness"] = _norm(row.get(spec["newness_human"]))
            item["risk_point"] = row.get(spec.get("risk_point", ""))
        out.append(item)
    return out


def gate_for(rows: Sequence[dict], *, method: str) -> dict:
    """一致率 + 门控判词（只对**已标注**行计；未标注行单列）。"""
    paired = [r for r in rows if r["machine"] and r["human"]]
    if not paired:
        return {
            "gated": True,
            "passed": None,
            "agreement": None,
            "reason": "尚无已标注行（人工列未填）：一致率不可算，门控未裁决",
            "rows_total": len(rows),
            "rows_labeled": 0,
        }
    result = gate_dimension(
        method,
        [str(r["machine"]) for r in paired],
        [str(r["human"]) for r in paired],
    )
    result.update(
        {
            "rows_total": len(rows),
            "rows_labeled": len(paired),
            "rows_unlabeled": len(rows) - len(paired),
            "disagreements": [
                {
                    "unit_id": r["unit_id"],
                    "ticker": r["ticker"],
                    "machine": r["machine"],
                    "human": r["human"],
                }
                for r in paired
                if r["machine"] != r["human"]
            ][:20],
        }
    )
    return result


def newness_threshold_sweep(rows: Sequence[dict], *, tickers: Sequence[str]) -> dict:
    """B1「新增」阈值标定：扫阈值算与人工的**一致率**（新增 = 相似度 < 阈值）。

    相似度由 `family_b_text.text_similarity` 现算（材料在本地，零 LLM）——
    人工只看得到风险点与最相似的分析师发现，故判据与人工所见一致。
    """
    from evals.causal_ablation import family_b_materials as fb

    labeled = [r for r in rows if r.get("human_newness") and r.get("ticker")]
    if not labeled:
        return {"status": "无可标定样本（人工「是否新增」列为空）", "samples": 0}
    states = {t: fb.load_material(MATERIALS_DIR, t)["state"] for t in sorted(tickers)}
    samples: list[tuple[float, str]] = []
    for row in labeled:
        ticker = str(row["ticker"])
        state = states.get(ticker)
        if state is None:
            continue
        refs = ft.analyst_reference_points(state)
        point = str(row.get("risk_point") or "")
        best = max((ft.text_similarity(point, ref) for ref in refs), default=0.0)
        samples.append((best, str(row["human_newness"])))
    if not samples:
        return {"status": "样本与材料未匹配上", "samples": 0}
    sweep: list[dict] = []
    for threshold in (0.03, 0.05, 0.08, 0.10, 0.15, 0.20, 0.25, 0.30, 0.35):
        agree = sum(1 for sim, human in samples if ("是" if sim < threshold else "否") == human)
        sweep.append(
            {
                "threshold": threshold,
                "agreement": agree / len(samples),
                "predicted_new": sum(1 for sim, _ in samples if sim < threshold),
            }
        )
    best = max(sweep, key=lambda r: (r["agreement"], -r["threshold"]))
    return {
        "status": "ok",
        "samples": len(samples),
        "sweep": sweep,
        "best": best,
        "note": "best = 与人工一致率最高（并列取更严阈值）；标定值须写入预登记后生效",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="P2 校准回读（零 LLM）")
    parser.add_argument("--dir", type=Path, default=CALIBRATION_DIR)
    parser.add_argument("--materials-dir", type=Path, default=MATERIALS_DIR)
    parser.add_argument("--json", type=Path, default=None, help="判词另存 JSON")
    parser.add_argument("--tickers", nargs="+", default=None)
    args = parser.parse_args()

    report: dict = {"legs": {}}
    for name, spec in LEGS.items():
        rows = read_leg(Path(args.dir) / spec["file"], spec)
        verdict = gate_for(rows, method=spec["method"])
        report["legs"][name] = {"file": spec["file"], **verdict}
        print(
            f"[{name}] 行 {verdict['rows_total']}｜已标注 {verdict['rows_labeled']}"
            f"｜一致率 {verdict['agreement']}｜{verdict['reason']}"
        )
        if verdict.get("disagreements"):
            print(f"      不一致 {len(verdict['disagreements'])} 条（前 20 列在 JSON）")
        if name == "b1":
            tickers = args.tickers or sorted({str(r["ticker"]) for r in rows if r.get("ticker")})
            sweep = newness_threshold_sweep(rows, tickers=tickers)
            report["b1_newness_threshold"] = sweep
            if sweep.get("status") == "ok":
                print(
                    f"[b1 新增阈值] 样本 {sweep['samples']}｜最佳阈值 "
                    f"{sweep['best']['threshold']}（一致率 {sweep['best']['agreement']:.2f}）"
                )
            else:
                print(f"[b1 新增阈值] {sweep['status']}")
    if args.json:
        Path(args.json).write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"[产物] {args.json.as_posix()}")


if __name__ == "__main__":
    main()
