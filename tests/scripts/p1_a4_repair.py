"""A4 单点修复回路补测跑批（族 A 最后一格；登记项见 `openspec/changes/BACKLOG.md`）。

薄壳（同 `p1_injection_pilot.py` 的驱动约定）：实验逻辑全在库侧
`evals/causal_ablation/repair_a4.py`——本脚本只保留四件事：
1. 预登记门禁（`assert_launch_allowed`，跑批第一动作）；
2. 读既有材料产物（`--materials-dir`，本 CLI **不生成材料**：分析师真跑成本须单独申报）；
3. 成本记账（修复回路真调用 LLM，按 usage 台账差量归属；meter 不可用如实记 None）；
4. 落盘报告（JSON 全量 + 自然腿人工终裁 CSV）。

用法：
    uv run python tests/scripts/p1_a4_repair.py                 # 20 标的：注入腿 + 自然腿
    uv run python tests/scripts/p1_a4_repair.py --modes injected
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable, Sequence
from datetime import datetime
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from evals.causal_ablation.units import UnitJudgment, write_units  # noqa: E402

from evals.causal_ablation import pilot_runner as pr  # noqa: E402
from evals.causal_ablation import repair_a4 as a4  # noqa: E402

# 与正式批同标的（20 只，`reports/ablation/p1-formal` 的 config.tickers）
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
MATERIALS_DIR = Path("reports/ablation/p1/materials")
OUT_DIR = Path("reports/ablation/p1-a4")
PREREG_DIR = Path("evals/ablation/preregister")
NATURAL_CSV = Path("tests/validation/2026-09-17-a4-natural-cases.csv")
# 本题自己的预登记（同目录已有 P1/P2 文档：门禁按文件名子串锁定本实验）
PREREG_NAME = "p1-a4-repair"
MODES = "both"


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="A4 单点修复回路补测跑批（注入腿 + 自然腿）")
    parser.add_argument("--tickers", nargs="+", default=list(TICKERS), help="标的列表")
    parser.add_argument("--materials-dir", type=Path, default=MATERIALS_DIR, help="产物目录")
    parser.add_argument("--out-dir", type=Path, default=OUT_DIR, help="报告目录")
    parser.add_argument("--prereg-dir", type=Path, default=PREREG_DIR, help="预登记目录（门禁）")
    parser.add_argument(
        "--modes",
        choices=("injected", "natural", "both"),
        default=MODES,
        help="跑哪些腿（injected = 正文一致注入；natural = 自然稀疏 value_mismatch）",
    )
    parser.add_argument(
        "--natural-csv", type=Path, default=NATURAL_CSV, help="自然腿人工终裁表输出路径"
    )
    return parser.parse_args(argv)


def _install_meter() -> tuple[Callable[[], int], Callable[[], list[dict]]] | None:
    """接线 usage meter（复用 pilot 驱动的同一实现：钉模型 + 包装适配器计量）。"""
    scripts_dir = str(_ROOT / "tests" / "scripts")
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    try:
        import backtest_pilot_2023 as pilot_util
        import p1_injection_pilot as pilot_cli

        if pilot_cli._install_cost_meter() is None:  # 钉模型 + 包装 raw_completion/raw_stream
            return None
    except Exception as exc:  # noqa: BLE001 - 计量不可用不阻断跑批，成本记 unknown
        print(f"[警告] usage meter 未接线（{type(exc).__name__}: {exc}）：成本记 unknown")
        return None
    return (lambda: len(pilot_util._usage_ledger)), (lambda: list(pilot_util._usage_ledger))


def run(tickers: Sequence[str], *, materials_dir: Path, modes: str) -> list[dict]:
    products = pr.load_products(Path(materials_dir), tickers)
    meter = _install_meter()
    units: list[dict] = []
    for position, ticker in enumerate(tickers):
        product = products[ticker]
        if modes in ("injected", "both"):
            print(f"[A4] {ticker} 注入腿（index={position}）…", flush=True)
            units.append(
                a4.run_repair_case(
                    product,
                    mode=a4.MODE_INJECTED,
                    index=position,
                    usage_reader=None if meter is None else meter[1],
                )
            )
        if modes in ("natural", "both"):
            natural = a4.sparse_vm_units(product)
            if not natural["sparse_gate_fired"]:
                print(
                    f"[A4] {ticker} 自然腿：value_mismatch={natural['vm_count']}，"
                    "不落在稀疏区间（跳过，不占成本）",
                    flush=True,
                )
                continue
            print(f"[A4] {ticker} 自然腿（vm={natural['vm_count']}）…", flush=True)
            units.append(
                a4.run_repair_case(
                    product,
                    mode=a4.MODE_NATURAL,
                    index=position,
                    usage_reader=None if meter is None else meter[1],
                )
            )
    return units


def _dump(units: list[dict], *, tickers: Sequence[str], out_dir: Path, natural_csv: Path) -> Path:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    report = a4.a4_report(units, tickers=tickers)
    report["generated_at"] = datetime.now().isoformat(timespec="seconds")
    report["units"] = units
    path = out_dir / f"p1-a4-repair-{datetime.now().strftime('%Y%m%d-%H%M%S')}.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    write_units(
        out_dir / "units.jsonl",
        [
            UnitJudgment(
                unit_id=str(u["unit_id"]),
                ticker=str(u.get("ticker") or ""),
                run="p1-a4-repair",
                variant=str(u.get("mode") or ""),
                unit_type="claim",
                judgment=(
                    f"{u.get('status')}｜true_fail_before={u.get('true_fail_before')}"
                    f"｜true_fail_after={u.get('true_fail_after')}"
                ),
                method="code",
                confidence=1.0,
            )
            for u in units
        ],
    )
    rows = a4.natural_cases_rows(units)
    if rows:
        natural_csv = Path(natural_csv)
        natural_csv.parent.mkdir(parents=True, exist_ok=True)
        natural_csv.write_text(a4.natural_cases_csv(rows), encoding="utf-8-sig")
    return path


def main() -> None:
    from dotenv import load_dotenv

    args = parse_args()
    load_dotenv()
    try:
        prereg = pr.assert_launch_allowed(args.prereg_dir, name_contains=PREREG_NAME)
        print(
            f"[门禁] 预登记 {prereg.path} 有效（主指标：{prereg.fields.get('主指标')}）", flush=True
        )
        units = run(args.tickers, materials_dir=args.materials_dir, modes=args.modes)
    except (pr.MissingProductError, pr.InjectionError) as exc:
        print(f"[拒绝启动] {exc}", file=sys.stderr)
        raise SystemExit(2) from exc
    path = _dump(units, tickers=args.tickers, out_dir=args.out_dir, natural_csv=args.natural_csv)
    report = a4.a4_report(units, tickers=args.tickers)
    print(f"[产物] {path.as_posix()}", flush=True)
    print(
        json.dumps(
            {k: v for k, v in report.items() if k != "reading_notes"}, ensure_ascii=False, indent=1
        )
    )
    print(f"[自然腿终裁表] {args.natural_csv.as_posix()}（待人工填 真错误?/误报?）", flush=True)
    print(f"[成本] {json.dumps(report['cost'], ensure_ascii=False)}", flush=True)


if __name__ == "__main__":
    main()
