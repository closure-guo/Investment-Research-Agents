"""add-signal-portfolio-risk-gate Task 5 对照验收脚本：20 个边界场景手算核对门禁。

用法::

    uv run python tests/validation/2026-10-13-add-signal-portfolio-risk-gate-acceptance.py
    uv run python tests/validation/2026-10-13-add-signal-portfolio-risk-gate-acceptance.py --out <路径.json>

**验收目标（对被测实现零依赖的独立算术对照）**：每个用例的期望原因码先在脚本内
手算——逐例手写算术注记（``hand_calc``）+ 脚本自带的独立判据链
（``_independent_reason``，手写规则、不调用被测 ``check_order``），两条独立路径
须互相一致，再与被测门禁结论对照。脚本自带常量副本（0.2/0.8/100，逐字核 spec），
末尾与实现侧 ``screening/constraints.py`` 的单一来源常量勾稽。另附门禁源码 AST
静态扫描（无 llm/gateway/harness import，spec 场景）。

**场景覆盖**（delta spec trade-execution「确定性风控门禁」）：合法单 / ST / 停牌 /
涨跌停买 / 跌停卖（含反向负例）/ 超单票 / 超总仓 / 满仓加仓与腾挪 / T+1 / 零股
（含卖零股全卖例外）/ 多因同单的三层优先级（①ST→…→⑦整手）。

退出码 0 当且仅当：20/20 逐例一致 + 常量勾稽一致 + 静态扫描通过。
"""

from __future__ import annotations

import argparse
import ast
import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

WORKTREE_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(WORKTREE_ROOT / "src"))

from finance_agent.execution import risk_gate  # noqa: E402
from finance_agent.execution.matching import MarketBar, PositionContext  # noqa: E402
from finance_agent.execution.risk_gate import OrderIntent, check_order  # noqa: E402
from finance_agent.screening import constraints  # noqa: E402

# 脚本自带常量副本（故意不 import 被测常量：对照要独立；数值逐字核 spec 0.2/0.8/100）
CAP_SINGLE = 0.2
CAP_TOTAL = 0.8
LOT = 100
# 上限判定容差（与实现同口径：浮点尘埃 ≤1e-9 不拒；spec 勾稽容差 1e-9）
TOL = 1e-9


@dataclass(frozen=True)
class Case:
    case_id: str
    scenario: str
    expected: str | None
    hand_calc: str
    side: str = "buy"
    qty: int = 100
    target_weight: float = 0.1
    current_weight: float = 0.0
    projected_total_weight: float = 0.2
    sellable_qty: int | None = None  # None → position 缺省（可卖 0）
    total_qty: int = 0
    is_st: bool = False
    is_suspended: bool = False
    limit_up_sealed: bool = False
    limit_down_sealed: bool = False


# 20 个边界场景（期望值逐例手算；优先级链 ①ST→②停牌→③涨跌停→④单票→⑤总仓→⑥T+1→⑦整手）
CASES: tuple[Case, ...] = (
    Case(
        "01_legal_buy",
        "合法买单（全检查通过）",
        None,
        "ST=False / 停牌=False / 无板 → 0.15 ≤ 0.20 → 0.55 ≤ 0.80 → 买单免 T+1 → 100 % 100 == 0 → 通过",
        target_weight=0.15,
        projected_total_weight=0.55,
    ),
    Case(
        "02_legal_sell",
        "合法卖单（可卖足额）",
        None,
        "0.05 ≤ 0.20 → 0.35 ≤ 0.80 → 200 ≤ 200（可卖）→ 200 % 100 == 0 → 通过",
        side="sell",
        qty=200,
        target_weight=0.05,
        projected_total_weight=0.35,
        sellable_qty=200,
        total_qty=200,
    ),
    Case(
        "03_st_flag",
        "ST 状态拒单",
        "ST_FLAG",
        "is_st=True → ① 命中（spec 场景：X 当日 ST）",
        is_st=True,
    ),
    Case(
        "04_st_first_multi",
        "多因同单：ST 优先（五因）",
        "ST_FLAG",
        "同单命中 ①ST + ②停牌 + ③涨停 + ④0.90>0.20 + ⑦150%100≠0 → 取 ①",
        qty=150,
        target_weight=0.9,
        projected_total_weight=0.95,
        is_st=True,
        is_suspended=True,
        limit_up_sealed=True,
    ),
    Case(
        "05_suspended",
        "停牌拒单",
        "SUSPENDED",
        "is_suspended=True（非 ST）→ ② 命中",
        is_suspended=True,
    ),
    Case(
        "06_suspended_before_t1_odd",
        "多因同单：停牌优先于 T+1/整手",
        "SUSPENDED",
        "同单 ②停牌 + ⑥350>100 + ⑦350%100=50≠0 → 取 ②",
        side="sell",
        qty=350,
        target_weight=0.1,
        projected_total_weight=0.2,
        sellable_qty=100,
        total_qty=1000,
        is_suspended=True,
    ),
    Case(
        "07_limit_up_buy",
        "一字涨停买不进",
        "LIMIT_UP_UNFILLABLE",
        "bar.limit_up_sealed=True 且 side=buy → ③ 命中",
        limit_up_sealed=True,
    ),
    Case(
        "08_limit_up_sell_ok",
        "一字涨停卖单可成交（负例）",
        None,
        "③ 仅拦买：涨停卖单不拒 → 0.10 ≤ 0.20 → 0.20 ≤ 0.80 → 100 ≤ 100 → 100%100==0 通过",
        side="sell",
        target_weight=0.1,
        projected_total_weight=0.2,
        sellable_qty=100,
        total_qty=100,
        limit_up_sealed=True,
    ),
    Case(
        "09_limit_down_sell_first",
        "一字跌停卖不出（且 T+1 同单）",
        "LIMIT_DOWN_UNFILLABLE",
        "同单 ③跌停 + ⑥100>50 → ③ 先于 ⑥",
        side="sell",
        target_weight=0.1,
        projected_total_weight=0.15,
        sellable_qty=50,
        total_qty=50,
        limit_down_sealed=True,
    ),
    Case(
        "10_limit_down_buy_ok",
        "一字跌停买单不受阻（负例）",
        None,
        "③ 仅拦卖：跌停买单不拒 → 0.10 ≤ 0.20 → 0.20 ≤ 0.80 → 买单免 T+1 → 整手通过",
        limit_down_sealed=True,
    ),
    Case(
        "11_single_cap",
        "单票超限 0.21 > 0.20",
        "SINGLE_POSITION_CAP",
        "0.21 > 0.20 + 1e-9 → ④ 命中（③ 无板已过）",
        target_weight=0.21,
    ),
    Case(
        "12_single_cap_boundary",
        "单票恰 0.20 边界不拒",
        None,
        "0.20 ≤ 0.20 + 1e-9（严格大于才拒）→ 通过",
        target_weight=0.2,
    ),
    Case(
        "13_total_cap_full_add",
        "满仓加仓超总仓 0.85 > 0.80",
        "TOTAL_POSITION_CAP",
        "current=0.80（满仓）加 0.05 → projected=0.85 > 0.80 + 1e-9 → ⑤ 命中",
        target_weight=0.1,
        current_weight=0.8,
        projected_total_weight=0.85,
    ),
    Case(
        "14_total_cap_boundary",
        "总仓恰 0.80 边界不拒",
        None,
        "0.80 ≤ 0.80 + 1e-9 → 通过",
        target_weight=0.1,
        current_weight=0.75,
        projected_total_weight=0.8,
    ),
    Case(
        "15_full_position_sell_frees",
        "满仓卖出腾挪（降总仓）",
        None,
        "current=0.80 减至 0.05 → projected=0.75 ≤ 0.80 → 100 ≤ 100 → 整手通过",
        side="sell",
        target_weight=0.05,
        current_weight=0.8,
        projected_total_weight=0.75,
        sellable_qty=100,
        total_qty=100,
    ),
    Case(
        "16_t1_sell_blocked",
        "T+1：卖出超可卖",
        "T1_SELL_BLOCKED",
        "600 股持仓仅 100 可卖（当日买入 500 未解禁）→ 200 > 100 → ⑥ 命中",
        side="sell",
        qty=200,
        target_weight=0.1,
        projected_total_weight=0.35,
        sellable_qty=100,
        total_qty=600,
    ),
    Case(
        "17_t1_no_position",
        "T+1：无持仓上下文（可卖视为 0）",
        "T1_SELL_BLOCKED",
        "position 缺省 → 可卖 0 → 100 > 0 → ⑥ 命中（保守拒绝无持仓卖出）",
        side="sell",
        target_weight=0.05,
        projected_total_weight=0.1,
        sellable_qty=None,
    ),
    Case(
        "18_odd_lot_buy",
        "零股买单 150 股",
        "ODD_LOT",
        "150 % 100 = 50 ≠ 0 → ⑦ 命中（买单无零股全卖例外）",
        qty=150,
    ),
    Case(
        "19_odd_lot_full_sell_ok",
        "卖零股全卖例外（350 股全平）",
        None,
        "350%100=50≠0 但 qty==total==350 且 350%100≠0 → 例外放行；350 ≤ 350 可卖",
        side="sell",
        qty=350,
        target_weight=0.05,
        projected_total_weight=0.05,
        sellable_qty=350,
        total_qty=350,
    ),
    Case(
        "20_multi_t1_before_odd",
        "多因同单：T+1 优先于整手",
        "T1_SELL_BLOCKED",
        "同单 ⑥150>100 + ⑦150%100=50≠0 → 取 ⑥（本门禁顺序，异于 matching M3）",
        side="sell",
        qty=150,
        target_weight=0.1,
        projected_total_weight=0.35,
        sellable_qty=100,
        total_qty=1000,
    ),
)


def _independent_reason(case: Case) -> str | None:
    """脚本内独立判据链（手写规则，不调用被测实现；与各例 hand_calc 互相印证）。"""
    if case.is_st:
        return "ST_FLAG"
    if case.is_suspended:
        return "SUSPENDED"
    if case.side == "buy" and case.limit_up_sealed:
        return "LIMIT_UP_UNFILLABLE"
    if case.side == "sell" and case.limit_down_sealed:
        return "LIMIT_DOWN_UNFILLABLE"
    if case.target_weight > CAP_SINGLE + TOL:
        return "SINGLE_POSITION_CAP"
    if case.projected_total_weight > CAP_TOTAL + TOL:
        return "TOTAL_POSITION_CAP"
    if case.side == "sell":
        sellable = 0 if case.sellable_qty is None else case.sellable_qty
        if case.qty > sellable:
            return "T1_SELL_BLOCKED"
    if case.qty % LOT != 0:
        full_odd_lot_sell = (
            case.side == "sell"
            and case.sellable_qty is not None
            and case.qty == case.total_qty
            and case.total_qty % LOT != 0
        )
        if not full_odd_lot_sell:
            return "ODD_LOT"
    return None


def _run_case(case: Case) -> dict[str, Any]:
    order = OrderIntent(
        decision_id=f"dec-{case.case_id}",
        signal_id="600519.SH:2026-10-13",
        symbol="600519.SH",
        side=case.side,  # type: ignore[arg-type]  # 用例取值受表约束
        qty=case.qty,
        target_weight=case.target_weight,
    )
    position = (
        None
        if case.sellable_qty is None
        else PositionContext(sellable_qty=case.sellable_qty, total_qty=case.total_qty)
    )
    bar = MarketBar(
        trade_date="2026-10-13",
        open=100.0,
        high=100.0,
        low=100.0,
        close=100.0,
        prev_close=100.0,
        amount=500.0,
        limit_up_sealed=case.limit_up_sealed,
        limit_down_sealed=case.limit_down_sealed,
    )
    decision = check_order(
        order,
        current_weight=case.current_weight,
        projected_total_weight=case.projected_total_weight,
        bar=bar,
        position=position,
        is_st=case.is_st,
        is_suspended=case.is_suspended,
    )
    independent = _independent_reason(case)
    return {
        "case_id": case.case_id,
        "scenario": case.scenario,
        "inputs": {
            k: v
            for k, v in asdict(case).items()
            if k not in ("case_id", "scenario", "expected", "hand_calc")
        },
        "expected_reason": case.expected,
        "hand_calc": case.hand_calc,
        "independent_reason": independent,
        "independent_matches_expected": independent == case.expected,
        "actual_reason": decision.reason,
        "approved": decision.approved,
        "matched": decision.reason == case.expected,
        "check_trace": [
            {"check": c["check"], "passed": c["passed"], "reason": c.get("reason")}
            for c in decision.detail["checks"]
        ],
    }


def _scan_no_llm_imports(path: Path) -> list[str]:
    """门禁源码静态扫描：返回违规 import 全名列表（期望空）。"""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.extend(f"{node.module}.{alias.name}" for alias in node.names)
    return [n for n in names if any(t in n.lower() for t in ("llm", "gateway", "harness"))]


def main() -> int:
    parser = argparse.ArgumentParser(description="20 边界场景手算核对风控门禁")
    parser.add_argument(
        "--out",
        default=str(Path(__file__).with_suffix(".json")),
        help="JSON artifact 输出路径",
    )
    args = parser.parse_args()

    constants_impl = {
        "MAX_SINGLE_POSITION": constraints.MAX_SINGLE_POSITION,
        "MAX_TOTAL_POSITION": constraints.MAX_TOTAL_POSITION,
        "LOT_SIZE": constraints.LOT_SIZE,
    }
    constants_match = (
        constants_impl["MAX_SINGLE_POSITION"] == CAP_SINGLE
        and constants_impl["MAX_TOTAL_POSITION"] == CAP_TOTAL
        and constants_impl["LOT_SIZE"] == LOT
    )
    forbidden_imports = _scan_no_llm_imports(Path(risk_gate.__file__))

    results = [_run_case(case) for case in CASES]
    matched = sum(1 for r in results if r["matched"])
    self_consistent = all(r["independent_matches_expected"] for r in results)

    print("[常量勾稽] 脚本副本 0.2/0.8/100 vs screening/constraints:", constants_impl)
    print(f"[常量勾稽] {'一致' if constants_match else '不一致（FAIL）'}")
    print(f"[静态扫描] risk_gate.py LLM/gateway/harness import 违规: {forbidden_imports or '无'}")
    print("[逐例对照] case / 场景 / 手算期望 / 独立判据 / 实测 / 结论")
    for r in results:
        mark = "OK " if r["matched"] else "FAIL"
        print(
            f"  {mark} {r['case_id']:<28} {r['scenario']:<24} "
            f"期望={str(r['expected_reason']):<24} 实测={str(r['actual_reason'])}"
        )
    totals = {
        "total": len(results),
        "matched": matched,
        "independent_self_consistent": self_consistent,
        "constants_match": constants_match,
        "forbidden_imports": forbidden_imports,
        "passed": bool(
            matched == len(results)
            and self_consistent
            and constants_match
            and not forbidden_imports
        ),
    }
    print(f"[verdict] {totals}")

    artifact = {
        "change": "add-signal-portfolio-risk-gate",
        "date": "2026-10-13",
        "spec": "delta trade-execution「确定性风控门禁」+ plan G4（检查顺序 ①–⑦）",
        "constants": {
            "script_copy": {
                "MAX_SINGLE_POSITION": CAP_SINGLE,
                "MAX_TOTAL_POSITION": CAP_TOTAL,
                "LOT_SIZE": LOT,
            },
            "implementation": constants_impl,
        },
        "constants_match": constants_match,
        "forbidden_imports": forbidden_imports,
        "cases": results,
        "verdict": totals,
    }
    out_path = Path(args.out)
    out_path.write_text(json.dumps(artifact, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"[artifact] {out_path}")
    return 0 if totals["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
