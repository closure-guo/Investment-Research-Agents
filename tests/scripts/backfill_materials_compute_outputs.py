"""材料快照回填：把缺失的 compute 输出键补进既有产物（**零 LLM**，同一实现）。

**为什么需要**：材料落盘的白名单（`product_from_state` 的 `snapshot_keys`）曾手抄漂移，
两次丢测量面——① `price_levels`（A5 sanity 消费面，§15）；② compute 输出 6 键
（anomalies / garp_result / health_score / price_levels / risk_metrics / traffic_lights，
A1 补测建单元时才发现）。白名单已改排除式 + 完整性守卫（`p1_injection_pilot._snapshot_keys`），
但**既有产物**仍缺面，本脚本按 `compute_metrics` 单一实现补齐（确定性、可复现，非估算）。

口径：
- 只补**缺失**键，不覆盖已有值（已有值来自当轮真实运行，回填值来自同一实现的重算）；
- 补后重算 `snapshot_digest` 并在产物里留 `snapshot_backfill`（补了哪些键 / 前后摘要），
  摘要差异可审计——不得静默改产物而不留痕；
- `compute_metrics` 在产物上跑不动（原始输入不全）→ 该产物跳过并打印，不写半份产物。

用法：
    uv run python tests/scripts/backfill_materials_compute_outputs.py --dry-run
    uv run python tests/scripts/backfill_materials_compute_outputs.py
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from evals.causal_ablation import pilot_runner as pr  # noqa: E402

MATERIALS_DIR = Path("reports/ablation/p1/materials")
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


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="材料快照 compute 输出键回填（零 LLM）")
    parser.add_argument("--materials-dir", type=Path, default=MATERIALS_DIR)
    parser.add_argument("--tickers", nargs="+", default=list(TICKERS))
    parser.add_argument("--dry-run", action="store_true", help="只报告缺键，不写盘")
    return parser.parse_args(argv)


def backfill_product(product: dict, *, digest_fn=None) -> dict:
    """补齐 compute 输出键；返回 {keys_added, digest_before, digest_after, written}。"""
    from evals.ablation import snapshot_digest

    from finance_agent.nodes.compute import compute_metrics

    digest = digest_fn or snapshot_digest
    snapshot = dict(product.get("snapshot") or {})
    computed = compute_metrics(dict(snapshot))  # type: ignore[arg-type]
    missing = sorted(k for k in computed if k not in snapshot)
    before = product.get("snapshot_digest")
    if not missing:
        return {"keys_added": [], "digest_before": before, "digest_after": before, "written": False}
    for key in missing:
        snapshot[key] = computed[key]
    product = dict(product)
    product["snapshot"] = snapshot
    product["snapshot_digest"] = digest(snapshot)
    product["snapshot_backfill"] = {
        "keys_added": missing,
        "digest_before": before,
        "digest_after": product["snapshot_digest"],
        "note": "compute 输出键补齐（同一实现 compute_metrics 重算；只补缺键不覆盖已有值）",
    }
    return {
        "keys_added": missing,
        "digest_before": before,
        "digest_after": product["snapshot_digest"],
        "written": True,
        "product": product,
    }


def backfill_path(path: Path, *, dry_run: bool = False) -> dict:
    product = pr.load_product(path)
    result = backfill_product(product)
    if result["written"] and not dry_run:
        pr.save_product(path, result["product"])
    return result


def main() -> None:
    args = parse_args()
    total = 0
    for ticker in args.tickers:
        path = pr.product_path(Path(args.materials_dir), ticker)
        if not path.exists():
            print(f"[跳过] {ticker}: 无产物（{path.as_posix()}）")
            continue
        try:
            result = backfill_path(path, dry_run=args.dry_run)
        except Exception as exc:  # noqa: BLE001 - 单个产物失败不阻断其余回填
            print(f"[失败] {ticker}: {type(exc).__name__}: {exc}")
            continue
        added = result["keys_added"]
        total += len(added)
        if added:
            flag = "（dry-run，未写盘）" if args.dry_run else "（已写盘）"
            print(f"[回填] {ticker}: +{len(added)} 键 {added} {flag}")
            print(f"        digest {result['digest_before']} → {result['digest_after']}")
        else:
            print(f"[跳过] {ticker}: 无缺键")
    print(f"[合计] 补键 {total} 个" + ("（dry-run）" if args.dry_run else ""))


if __name__ == "__main__":
    main()
