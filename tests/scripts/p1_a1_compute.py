"""A1（确定性指标注入）补测跑批（族 A 最后一格；登记项见 `openspec/changes/BACKLOG.md`）。

薄壳（同 `p1_injection_pilot.py` 的驱动约定）：实验逻辑全在库侧
`evals/causal_ablation/compute_a1.py`——本脚本只保留四件事：
1. 预登记门禁（`assert_launch_allowed(name_contains=...)`，跑批第一动作）；
2. 读既有材料产物（`--materials-dir`；本 CLI **不生成材料**：快照直接复用，零新增 fetch）；
3. 成本记账（每标的 2 趟分析师，按 usage 台账差量归属；meter 不可用如实记 None）；
4. 落盘报告（JSON 全量）。

用法：
    uv run python tests/scripts/p1_a1_compute.py --tickers 600519 000001 ... --limit 200
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

from evals.causal_ablation import compute_a1 as a1  # noqa: E402
from evals.causal_ablation import pilot_runner as pr  # noqa: E402

# 与 P1 同源材料：10 标的 pilot（预登记样本量依据）
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
)
MATERIALS_DIR = Path("reports/ablation/p1/materials")
OUT_DIR = Path("reports/ablation/p1-a1")
PREREG_DIR = Path("evals/ablation/preregister")
# 同目录已有 P1/P1-A4/P2 文档：门禁按文件名子串锁定本实验
PREREG_NAME = "p1-a1-compute"
DEFAULT_LIMIT = 400


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="A1 补测跑批（无 compute 变体 vs 对照）")
    parser.add_argument("--tickers", nargs="+", default=list(TICKERS), help="标的列表")
    parser.add_argument("--materials-dir", type=Path, default=MATERIALS_DIR, help="产物目录")
    parser.add_argument("--out-dir", type=Path, default=OUT_DIR, help="报告目录")
    parser.add_argument("--prereg-dir", type=Path, default=PREREG_DIR, help="预登记目录（门禁）")
    parser.add_argument(
        "--limit", type=int, default=DEFAULT_LIMIT, help="LLM 调用上限（超限停跑，预登记停止规则③）"
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

        if pilot_cli._install_cost_meter() is None:
            return None
    except Exception as exc:  # noqa: BLE001 - 计量不可用不阻断跑批，成本记 unknown
        print(f"[警告] usage meter 未接线（{type(exc).__name__}: {exc}）：成本记 unknown")
        return None
    return (lambda: len(pilot_util._usage_ledger)), (lambda: list(pilot_util._usage_ledger))


def run(
    tickers: Sequence[str],
    *,
    materials_dir: Path,
    limit: int,
    meter: tuple[Callable[[], int], Callable[[], list[dict]]] | None,
) -> list[dict]:
    from p1_injection_pilot import _default_analysts_runner

    products = pr.load_products(Path(materials_dir), tickers)
    units: list[dict] = []
    spent = 0
    for ticker in tickers:
        if spent >= limit:
            print(f"[停跑] 累计 {spent} 次调用已达上限 {limit}（预登记停止规则③）", flush=True)
            break
        print(f"[A1] {ticker}：OFF/ON 两趟分析师…", flush=True)
        unit = a1.run_a1_case(
            products[ticker],
            graph_runner=_default_analysts_runner,
            llm_meter=None if meter is None else meter[0],
        )
        units.append(unit)
        spent += int(unit.get("llm_calls") or 0)
        print(
            f"      {unit.get('status')} | off={unit.get('recomputable_error_rate_off')} "
            f"on={unit.get('recomputable_error_rate_on')} | 调用 {unit.get('llm_calls')}",
            flush=True,
        )
    return units


def _dump(units: list[dict], *, tickers: Sequence[str], out_dir: Path) -> Path:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    report = a1.a1_report(units, tickers=tickers)
    report["generated_at"] = datetime.now().isoformat(timespec="seconds")
    report["units"] = units
    path = out_dir / f"p1-a1-compute-{datetime.now().strftime('%Y%m%d-%H%M%S')}.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
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
        meter = _install_meter()
        units = run(
            args.tickers,
            materials_dir=args.materials_dir,
            limit=args.limit,
            meter=meter,
        )
    except (pr.MissingProductError, pr.InjectionError) as exc:
        print(f"[拒绝启动] {exc}", file=sys.stderr)
        raise SystemExit(2) from exc
    path = _dump(units, tickers=args.tickers, out_dir=args.out_dir)
    report = a1.a1_report(units, tickers=args.tickers)
    print(f"[产物] {path.as_posix()}", flush=True)
    print(
        json.dumps(
            {k: v for k, v in report.items() if k not in ("units", "per_ticker")},
            ensure_ascii=False,
            indent=1,
        )
    )


if __name__ == "__main__":
    main()
