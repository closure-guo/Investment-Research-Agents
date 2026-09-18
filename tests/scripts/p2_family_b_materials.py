"""P2 族 B 材料跑批（full 变体 × 20 标的，同快照对照 analysts 臂）。

薄壳（同 P1 驱动约定）：实验逻辑在库侧 `evals/causal_ablation/family_b_materials.py`——
本脚本只做：门禁 → 读 P1 快照 → 逐标的跑 full 变体（续跑跳过）→ 落材料 + B3/B4 描述性读数。

用法：
    uv run python tests/scripts/p2_family_b_materials.py            # 全量（续跑安全）
    uv run python tests/scripts/p2_family_b_materials.py --ticks 3  # 先跑 3 只看通路
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

from evals.causal_ablation import family_b_materials as fb  # noqa: E402
from evals.causal_ablation import pilot_runner as pr  # noqa: E402

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
SNAPSHOT_DIR = Path("reports/ablation/p1/materials")
OUT_DIR = Path("reports/ablation/p2/materials")
PREREG_DIR = Path("evals/ablation/preregister")
PREREG_NAME = "p2-family-b"


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="P2 族 B 材料跑批（full 变体）")
    parser.add_argument("--tickers", nargs="+", default=list(TICKERS))
    parser.add_argument("--snapshot-dir", type=Path, default=SNAPSHOT_DIR)
    parser.add_argument("--out-dir", type=Path, default=OUT_DIR, help="材料目录（续跑看它）")
    parser.add_argument("--prereg-dir", type=Path, default=PREREG_DIR)
    parser.add_argument("--ticks", type=int, default=0, help="只跑前 N 只（0=全部；通路验证用）")
    parser.add_argument(
        "--variant",
        choices=("full", "analysts", "through_trader"),
        default="full",
        help="跑哪个变体（full=层间对照臂；analysts/through_trader=B5 盲评对照臂的渲染报告；"
        "through_trader 含 Trader 决策层——B5 修正口径，预登记 §9）",
    )
    return parser.parse_args(argv)


def _install_meter() -> Callable[[], int] | None:
    scripts_dir = str(_ROOT / "tests" / "scripts")
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    try:
        import backtest_pilot_2023 as pilot_util
        import p1_injection_pilot as pilot_cli

        if pilot_cli._install_cost_meter() is None:
            return None
    except Exception as exc:  # noqa: BLE001 - 计量不可用不阻断跑批
        print(f"[警告] usage meter 未接线（{type(exc).__name__}: {exc}）：成本记 unknown")
        return None
    return lambda: len(pilot_util._usage_ledger)


def main() -> None:
    from dotenv import load_dotenv

    args = parse_args()
    load_dotenv()
    tickers = args.tickers[: args.ticks] if args.ticks else args.tickers
    try:
        prereg = pr.assert_launch_allowed(args.prereg_dir, name_contains=PREREG_NAME)
    except Exception as exc:  # noqa: BLE001 - 预登记缺失/无效：明确拒绝，不静默跑
        print(f"[拒绝启动] 预登记门禁：{exc}", file=sys.stderr)
        raise SystemExit(2) from exc
    print(
        f"[门禁] 预登记 {prereg.path}（主指标：{prereg.fields.get('主指标', '—')[:60]}）",
        flush=True,
    )

    from evals.ablation import build_variant_graph
    from p1_injection_pilot import _default_analysts_runner  # noqa: F401 - 保持导入面一致

    def runner(*, variant: str, snapshot: dict, query: str) -> dict:
        graph = build_variant_graph(variant)  # type: ignore[arg-type]
        return dict(graph.invoke({**snapshot, "focus": query}))

    if args.variant != "full":
        print(
            f"[说明] 本趟跑 {args.variant} 变体（B5 盲评对照臂）；B3/B4 读数仍以 full 材料为准",
            flush=True,
        )

    meter = _install_meter()
    units = fb.run_materials(
        tickers,
        snapshot_materials_dir=args.snapshot_dir,
        out_dir=args.out_dir,
        graph_runner=runner,
        llm_meter=meter,
        variant=args.variant,
    )
    report = fb.family_b_code_report(units)
    report["generated_at"] = datetime.now().isoformat(timespec="seconds")
    out = (
        Path(args.out_dir).parent
        / f"p2-family-b-code-{datetime.now().strftime('%Y%m%d-%H%M%S')}.json"
    )
    out.write_text(
        json.dumps(report | {"units": units}, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"[产物] {out.as_posix()}", flush=True)
    print(
        json.dumps(
            {k: v for k, v in report.items() if k not in ("reading_notes",)},
            ensure_ascii=False,
            indent=1,
        )
    )
    print(
        "[说明] B1/B2/B5（nli/judge）须过校准门控后另跑；B3/B4 为描述性读数（n≈5 无分辨率）",
        flush=True,
    )


if __name__ == "__main__":
    main()
