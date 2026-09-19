"""B5 上游冻结重跑（2026-09-18 owner 批准，≈120 次调用；BACKLOG「B5 上游冻结重跑」）。

**背景**：B5 首跑（through_trader 对 full）发现两臂上游非冻结——分析师报告各臂独立现跑，
4 对零字面重叠（Jaccard 0.00），19:1 含上游采样噪声、不得纯层归因（§19.7 混淆披露，
owner 追问定性为设计遗漏）。

**方案**：以 full 现有**分析师产物**为冻结上游（byte 级一致，且已过其 run 的引用校验），
仅重跑 through_trader 的下游链——bull/bear r1 → bull/bear r2 → research_manager → trader
→ generate_report（generate_report 零 LLM）。两臂共享同上游后，B5 差异收敛到目标层。

**对照语义**：辩论层自身的 LLM 随机性属于被测层的一部分（层即处理）；full 臂沿用其既有
产物。成本 = 6 次调用/标的 × 20 = 120 次（+判定 60 次另跑）。

产物：materials/*.{through_trader_frozen}.json/pkl（供判定驱动换臂）。按标的断点续跑。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from evals.causal_ablation import family_b_materials as fb  # noqa: E402
from finance_agent.nodes.debate import bear_debater, bull_debater  # noqa: E402
from finance_agent.nodes.report import generate_report  # noqa: E402
from finance_agent.nodes.research_manager import research_manager  # noqa: E402
from finance_agent.nodes.trader import trader  # noqa: E402

MATERIALS_DIR = _ROOT / "reports/ablation/p2/materials"
DONE_MARKERS = _ROOT / "reports/ablation/p2/b5-frozen-rerun.done.json"
FROZEN_VARIANT = "through_trader_frozen"

# full run 的下游残留：冻结重跑前必须清空（debate/FM/决策/报告全部重算）
_DOWNSTREAM_KEYS = (
    "debate_history",
    "debate_anchor_checks",
    "research_manager_conclusion",
    "research_manager_rating",
    "research_manager_confidence",
    "research_manager_parse_degraded",
    "trader_plan",
    "final_trade_decision",
    "final_report",
    "fund_manager_decision",
    "fund_manager_decision_reasoning",
    "risk_debate_history",
    "risk_judge_conclusion",
)


def _run_node(state: dict, node) -> dict:
    """手动串联：debate_history 语义是追加（图里走 reducer），其余键覆盖。"""
    out = node(state)
    history = out.pop("debate_history", None)
    if history:
        state["debate_history"] = [*(state.get("debate_history") or []), *history]
    state.update(out)
    return state


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--report", action="store_true", help="只读现状，不调 LLM")
    args = ap.parse_args()
    done: set[str] = set()
    if DONE_MARKERS.exists():
        done = set(json.loads(DONE_MARKERS.read_text(encoding="utf-8")))

    fresh = 0
    if not args.report:
        from dotenv import load_dotenv

        load_dotenv()
        for path in sorted(MATERIALS_DIR.glob("*.full.pkl")):
            ticker = path.name.split(".")[0]
            if ticker in done:
                continue
            full_mat = fb.load_material(MATERIALS_DIR, ticker)
            state = dict(full_mat["state"])
            for k in _DOWNSTREAM_KEYS:
                state.pop(k, None)
            # 冻结链：辩论两轮（各自读同一上游）→ 研究经理 → Trader → 渲染
            _run_node(state, bull_debater)
            _run_node(state, bear_debater)
            _run_node(state, bull_debater)
            _run_node(state, bear_debater)
            _run_node(state, research_manager)
            _run_node(state, trader)
            _run_node(state, generate_report)
            report = str(state.get("final_report") or "")
            if not report.strip():
                print(f"[frozen] {ticker}: final_report 为空，中止", flush=True)
                return 2
            fb.save_material(
                MATERIALS_DIR,
                ticker,
                state=state,
                llm_calls=6,  # bull×2/bear×2/RM/trader；generate_report 零 LLM
                snapshot_digest=full_mat["snapshot_digest"],
                variant=FROZEN_VARIANT,
            )
            done.add(ticker)
            DONE_MARKERS.write_text(json.dumps(sorted(done)), encoding="utf-8")
            fresh += 1
            print(
                f"[frozen] {ticker}: 报告 {len(report)} 字（上游=full 分析师产物，已冻结）",
                flush=True,
            )
    print(f"[frozen] 本轮 {fresh} 标的（累计 {len(done)}/20）", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
