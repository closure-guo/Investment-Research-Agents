"""P2 族 B 分析腿：材料 → B3/B4 描述性读数 + B1/B2/B5 判定（provisional）+ 校准材料。

前置：材料已跑齐
    uv run python tests/scripts/p2_family_b_materials.py                  # full 臂（B3/B4/B1/B2）
    uv run python tests/scripts/p2_family_b_materials.py --variant analysts  # B5 对照臂

本脚本（**唯一**产出读数的地方）：
1. B3/B4（code，不受校准门控）→ 描述性读数 + 逐标的明细；
2. B1 吸收（nli）/ B2 修正（judge）→ 判定 + 20% 校准抽样表（人工标注列留空）；
3. B5 pairwise（judge K=3，位置随机化）→ 多数决 + 位置分布（偏倚检验用）；
4. 全部判定标 `provisional`：**未过校准门控不得进结论**（spec「校准门控全覆盖」）。

成本（预登记 §6）：B1 ≤4 行/标的、B2 ≤4 行/标的、B5 = 1 对 × 3 票/标的。
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from evals.causal_ablation import family_b_judge as fj  # noqa: E402
from evals.causal_ablation import family_b_materials as fb  # noqa: E402
from evals.causal_ablation import family_b_text as ft  # noqa: E402

TICKERS: tuple[str, ...] = (
    "600519",
    "000001",
    "002415",
    "300750",
    "601318",
    "002594",
    "600036",
    "000858",
    "601899",
    "002304",
    "601398",
    "600030",
    "000333",
    "002352",
    "601012",
    "600887",
    "000651",
    "600276",
    "601888",
    "002027",
)
MATERIALS_DIR = Path("reports/ablation/p2/materials")
OUT_DIR = Path("reports/ablation/p2")
CALIBRATION_DIR = Path("tests/validation")


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="P2 族 B 分析腿（code 读数 + 判定 + 校准材料）")
    parser.add_argument("--tickers", nargs="+", default=list(TICKERS))
    parser.add_argument("--materials-dir", type=Path, default=MATERIALS_DIR)
    parser.add_argument("--out-dir", type=Path, default=OUT_DIR)
    parser.add_argument("--calibration-dir", type=Path, default=CALIBRATION_DIR)
    parser.add_argument("--skip-judge", action="store_true", help="只出 code 读数（零 LLM）")
    parser.add_argument("--max-judge-calls", type=int, default=400, help="判定调用总闸门")
    parser.add_argument(
        "--calibration-only",
        action="store_true",
        help="只判已有校准样本内的行（v2 先验证再全量，防 300+ 次白烧）",
    )
    parser.add_argument(
        "--no-calibration-export",
        action="store_true",
        help="不重写校准 CSV（人工已填的表绝不能被重跑覆盖——用 p2_calibration_export.py 重建）",
    )
    return parser.parse_args(argv)


def _install_meter():
    scripts_dir = str(_ROOT / "tests" / "scripts")
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    try:
        import backtest_pilot_2023 as pilot_util
        import p1_injection_pilot as pilot_cli

        if pilot_cli._install_cost_meter() is None:
            return None
    except Exception as exc:  # noqa: BLE001 - 计量不可用不阻断分析
        print(f"[警告] usage meter 未接线（{type(exc).__name__}: {exc}）")
        return None
    return lambda: len(pilot_util._usage_ledger)


def _judged_path(out_dir: Path, kind: str, version: str | None = None) -> Path:
    suffix = f"-{version}" if version else ""
    return Path(out_dir) / f"judged-{kind}{suffix}.jsonl"


def _load_judged(out_dir: Path, kind: str, version: str | None = None) -> dict[str, dict]:
    """已判行（按 unit_id）：崩溃/重跑都不重烧判定调用。rubric 换版必须换缓存文件（v1 判定不作数）。"""
    path = _judged_path(out_dir, kind, version)
    if not path.exists():
        return {}
    done: dict[str, dict] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            row = json.loads(line)
            done[str(row.get("unit_id") or "")] = row
    return done


def _append_judged(
    out_dir: Path, kind: str, rows: Sequence[dict], version: str | None = None
) -> None:
    path = _judged_path(out_dir, kind, version)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + chr(10))


def _run_with_cache(rows, kind, out_dir, runner, version: str | None = None) -> list[dict]:
    """只跑未判的行；新结果立即落盘（判定调用花钱，崩溃不重来）。"""
    done = _load_judged(out_dir, kind, version)
    todo = [r for r in rows if str(r.get("unit_id") or "") not in done]
    print(f"[判定] {kind}: 待跑 {len(todo)}/{len(rows)}（已缓存 {len(done)}）", flush=True)
    if todo:
        fresh = runner(todo)
        _append_judged(out_dir, kind, fresh, version)
        print(
            f"[判定] {kind}: 新判 {len(fresh)} 行已落盘 {_judged_path(out_dir, kind, version).name}",
            flush=True,
        )
        done.update({str(r.get("unit_id") or ""): r for r in fresh})
    # 按 unit_id 去重：输入行会重复（同一 rebuttal 目标被多条消息反驳），
    # 不去重会把同一条判定重复计入比率（分母虚高）
    picked: dict[str, dict] = {}
    for row in rows:
        uid = str(row.get("unit_id") or "")
        if uid in done:
            picked[uid] = done[uid]
    return list(picked.values())


def _write_csv(path: Path, rows: Sequence[dict], columns: Sequence[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(columns), extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def _calibration_ids(calib_dir: Path) -> dict[str, set[str]]:
    """已有校准表的 unit_id 集（--calibration-only 用：v2 先在人工已判的行上验证）。"""
    out: dict[str, set[str]] = {}
    for kind in ("b1", "b2"):
        path = calib_dir / f"2026-09-17-p2-calibration-{kind}.csv"
        ids: set[str] = set()
        if path.exists():
            with path.open(encoding="utf-8-sig", newline="") as fh:
                ids = {str(r.get("unit_id") or "") for r in csv.DictReader(fh)}
        out[kind] = {i for i in ids if i}
    return out


def main() -> None:
    from dotenv import load_dotenv

    args = parse_args()
    load_dotenv()
    tickers = [t for t in args.tickers if fb.material_path(Path(args.materials_dir), t).exists()]
    if not tickers:
        print("[拒绝] 无 full 材料：先跑 p2_family_b_materials.py", file=sys.stderr)
        raise SystemExit(2)
    print(f"[材料] full 臂 {len(tickers)}/{len(args.tickers)} 只可用", flush=True)

    states = {t: fb.load_material(Path(args.materials_dir), t)["state"] for t in tickers}
    units = [fb.unit_from_state(t, states[t], llm_calls=None) for t in tickers]
    report = fb.family_b_code_report(units, ticker_count=len(args.tickers))
    report["generated_at"] = datetime.now().isoformat(timespec="seconds")
    report["b1_b2_code"] = {
        t: {"b1": _b1_code(states[t]), "b2": ft.b2_unit(states[t])} for t in tickers
    }
    print(json.dumps(report["b3"], ensure_ascii=False, indent=1))
    print(json.dumps(report["b4"], ensure_ascii=False, indent=1))

    if args.skip_judge:
        report["judge"] = {"skipped": True}
        print("[跳过] 判定腿未跑（--skip-judge）")
    else:
        meter = _install_meter()
        before = meter() if meter else None
        # 判定材料要用**完整** b1_unit（含 all_points；报告里的 b1_b2_code 才用裁剪版）
        b1_rows = ft.judge_material_rows(
            [{"ticker": t, "_state": states[t], "b1": ft.b1_unit(states[t])} for t in tickers]
        )
        # v2 双问需要参照池（判「是否新增」用）：全量 16 条进材料，不截断
        refs_by_ticker = {t: ft.analyst_reference_points(states[t]) for t in tickers}
        for row in b1_rows:
            row["reference_points"] = refs_by_ticker[str(row.get("ticker") or "")]
        print(f"[判定] B1 材料行 {len(b1_rows)}（全空方论点，未按阈值预筛）", flush=True)
        b2_rows = ft.b2_material_rows([{"ticker": t, "_state": states[t]} for t in tickers])
        if args.calibration_only:
            keep = _calibration_ids(Path(args.calibration_dir))
            b1_rows = [r for r in b1_rows if str(r.get("unit_id")) in keep["b1"]]
            b2_rows = [r for r in b2_rows if str(r.get("unit_id")) in keep["b2"]]
            print(
                f"[判定] --calibration-only：B1 {len(b1_rows)} 行 / B2 {len(b2_rows)} 行",
                flush=True,
            )
        budget = args.max_judge_calls
        out_dir = Path(args.out_dir)
        b1_judged = (
            _run_with_cache(
                b1_rows,
                "b1",
                out_dir,
                fj.run_b1_judgments,
                # rubric 非默认版才用带后缀的缓存文件（v1 缓存 = 原 judged-b1.jsonl，已过门继续复用）
                version=None if fj.B1_RUBRIC.endswith("-v1") else fj.B1_RUBRIC.removeprefix("b1-"),
            )
            if budget > 0
            else []
        )
        b2_judged = _run_with_cache(
            b2_rows,
            "b2",
            out_dir,
            fj.run_b2_judgments,
            version=None if fj.B2_RUBRIC.endswith("-v1") else fj.B2_RUBRIC.removeprefix("b2-"),
        )
        after = meter() if meter else None
        report["judge"] = {
            "b1": fj.gate_verdict(b1_judged, method="nli"),
            "b2": fj.gate_verdict(b2_judged, method="judge"),
            "b1_summary": fj.summarize(b1_judged, key="absorbed"),
            "b1_newness_summary": fj.summarize(b1_judged, key="judge_new_risk_point"),
            "b2_summary": fj.summarize(b2_judged, key="corrected"),
            "llm_calls": None if before is None or after is None else after - before,
        }
        calib_dir = Path(args.calibration_dir)
        if args.no_calibration_export:
            print("[跳过] 校准 CSV 未重写（--no-calibration-export；人工表只能用 export 脚本重建）")
        else:
            _write_csv(
                calib_dir / "2026-09-17-p2-calibration-b1.csv",
                ft.calibration_sample(fj.calibration_rows(b1_judged, material_key="risk_point")),
                fj.CALIBRATION_COLUMNS,
            )
            _write_csv(
                calib_dir / "2026-09-17-p2-calibration-b2.csv",
                ft.calibration_sample(fj.calibration_rows(b2_judged, material_key="rebuttal_text")),
                fj.CALIBRATION_COLUMNS,
            )
            print(
                "[判定] B1/B2 已出（provisional）；校准抽样表已落 "
                f"{calib_dir.as_posix()}/2026-09-17-p2-calibration-b1.csv、-b2.csv"
            )

    # B5：两臂都含决策层（预登记 §9 修正口径，2026-09-18）——对照臂 through_trader
    # （analysts+辩论+研究经理+Trader，无风控辩论/FM）对 full，消掉 analysts 对 full 的
    # 同义反复（analysts 臂无决策章节而判据问决策）。缓存按臂分文件，防吃到旧臂判定。
    # 2026-09-19 外科手术对照臂（owner 原则彻底执行）：full 同一次 run 产物摘除风控辩论+FM
    # 后重新渲染——分析师/辩论/研究经理/Trader/导语全部 byte 级共享，差异 = 纯层增量
    b5_arm = "full_no_riskfm"
    arm_available = [
        t for t in tickers if fb.material_path(Path(args.materials_dir), t, b5_arm).exists()
    ]
    if len(arm_available) == len(tickers) and not args.skip_judge:
        pairs = [
            {
                "pair_key": t,
                "ticker": t,
                "report_a_id": b5_arm,
                "report_b_id": "full",
                "report_a": str(states[t].get("final_report") or ""),  # 占位，下面覆盖
                "report_b": str(states[t].get("final_report") or ""),
            }
            for t in tickers
        ]
        for pair in pairs:
            pair["report_a"] = str(
                fb.load_material(Path(args.materials_dir), pair["ticker"], b5_arm)["state"].get(
                    "final_report"
                )
                or ""
            )
        b5 = _run_with_cache(
            [dict(p, unit_id=f"{p['ticker']}::b5") for p in pairs],
            "b5",
            Path(args.out_dir),
            fj.run_b5_pairwise,
            version=b5_arm,
        )
        report["judge_b5"] = {
            "verdicts": {r["ticker"]: r["verdict"] for r in b5},
            "position_first": {r["ticker"]: r["position_first"] for r in b5},
            "full_wins": sum(1 for r in b5 if r["verdict"] == "full"),
            "analysts_wins": sum(1 for r in b5 if r["verdict"] == "analysts"),
            "ties": sum(1 for r in b5 if r["verdict"] == "tie"),
            "parse_failed_pairs": sum(1 for r in b5 if r["judge_parse_failed"]),
            "status": "provisional（judge 判定，须过校准门控 ≥0.80）",
        }
        if not args.no_calibration_export:
            _write_csv(
                Path(args.calibration_dir) / "2026-09-17-p2-calibration-b5.csv",
                [
                    {
                        "unit_id": f"{r['ticker']}::b5",
                        "ticker": r["ticker"],
                        "material": f"first={r['position_first']}｜votes={r['votes']}",
                        "judge_label": r["verdict"],
                        "judge_reason": "",
                        "human_label(与判定一致?是/否)": "",
                    }
                    for r in b5
                ],
                fj.CALIBRATION_COLUMNS,
            )
        print(f"[B5] 盲评 {len(b5)} 对：{report['judge_b5']}")
    else:
        report["judge_b5"] = {
            "status": "未跑",
            "reason": f"缺 {b5_arm} 臂材料（B5 需两臂渲染报告）或 --skip-judge",
            "missing": [t for t in tickers if t not in arm_available],
        }

    out = (
        Path(args.out_dir) / f"p2-family-b-analysis-{datetime.now().strftime('%Y%m%d-%H%M%S')}.json"
    )
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[产物] {out.as_posix()}")


def _b1_code(state: dict) -> dict:
    """报告用的 B1 code 读数（不含逐条明细 all_points——明细另存判定材料）。"""
    got = ft.b1_unit(state)
    return {k: v for k, v in got.items() if k != "all_points"}


if __name__ == "__main__":
    main()
