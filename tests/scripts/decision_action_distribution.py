"""决策动作分布基线（族 B 暴露率输入）：对 20 标的快照真跑全图，记录决策动作。

背景（owner 裁决：机制价值 = 暴露率 × 拦截率）：A5（价位 sanity）在默认 query 下
3 标的实测全 watch → 暴露度 ≈0，注入法测不到 → 改读 B4。B4 需要「决策动作分布」作输入
（B3 数字出处率 / B5 pairwise 的 MDE 反算同样依赖它）。本脚本用已有材料快照自跑采集，
不依赖生产流量；每标的 1 趟全图（成本如实记 meter 差量）。

产物：`reports/ablation/decision-distribution/<ts>.jsonl`（每标的一行）+ 摘要打印。
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

TICKERS = (
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


def _action(obj: object) -> str | None:
    """决策对象的 action：真实运行返回 pydantic 模型（TradeDecision），测试产物是 dict。"""
    if obj is None:
        return None
    if hasattr(obj, "model_dump"):
        obj = obj.model_dump()
    if isinstance(obj, dict):
        value = obj.get("action")
        return str(value) if value is not None else None
    return None


def main() -> int:
    from dotenv import load_dotenv

    load_dotenv()
    from evals.causal_ablation import pilot_runner as pr

    meter = None
    try:
        sys.path.insert(0, str(_ROOT / "tests" / "scripts"))
        import backtest_pilot_2023 as util

        util._pin_pipeline_model()
        util.install_usage_meter()
        meter = lambda: len(util._usage_ledger)  # noqa: E731
    except Exception as exc:  # noqa: BLE001
        print(f"[警告] meter 未接线：{type(exc).__name__}: {exc}")

    out_dir = Path("reports/ablation/decision-distribution")
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    out_path = out_dir / f"{ts}.jsonl"
    rows: list[dict] = []
    for ticker in TICKERS:
        product = pr.load_product(pr.product_path(Path("reports/ablation/p1/materials"), ticker))
        snapshot = product.get("snapshot") or {}
        before = meter() if meter else None
        try:
            state = pr.default_graph_runner(
                variant="full", snapshot=snapshot, query=pr.DEFAULT_QUERY
            )
        except Exception as exc:  # noqa: BLE001 - 单标的失败不拖垮整批（无人值守 40 分钟）
            after = meter() if meter else None
            row = {
                "ticker": ticker,
                "error": f"{type(exc).__name__}: {exc}"[:200],
                "llm_calls": None if before is None or after is None else after - before,
            }
            rows.append(row)
            with out_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")
            print(f"[{ticker}] 失败：{row['error']}", flush=True)
            continue
        after = meter() if meter else None
        calls = None if before is None or after is None else after - before
        pc = state.get("price_check")
        if hasattr(pc, "model_dump"):
            pc = pc.model_dump()
        row = {
            "ticker": ticker,
            "trader_action": _action(state.get("trader_plan")),
            "final_action": _action(state.get("final_trade_decision")),
            "has_trader_plan": state.get("trader_plan") is not None,
            "price_check": (pc or {}).get("result") if isinstance(pc, dict) else None,
            "llm_calls": calls,
        }
        rows.append(row)
        with out_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
        print(
            f"[{ticker}] trader={row['trader_action']} final={row['final_action']} calls={calls}",
            flush=True,
        )

    ok_rows = [r for r in rows if "error" not in r]
    dist = Counter(str(r["trader_action"]) for r in ok_rows)
    calls_total = sum(r["llm_calls"] or 0 for r in rows)
    print(f"\n=== 决策动作分布（{len(rows)} 标的）===")
    for action, n in dist.most_common():
        print(f"  {action:<8} {n}  ({n / len(ok_rows):.0%})" if ok_rows else f"  {action} {n}")
    if ok_rows:
        executable = sum(n for a, n in dist.items() if a in ("buy", "sell"))
        print(f"可执行（buy/sell）占比: {executable / len(ok_rows):.1%}")
    print(f"总 LLM 调用: {calls_total}（每标的均值 {calls_total / len(rows):.1f}）")
    print(f"产物 → {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
