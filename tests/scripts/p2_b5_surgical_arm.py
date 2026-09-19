"""B5 外科手术对照臂（零 LLM）：同一次 full run 的产物，去掉风控辩论+FM 后重新渲染。

**为什么存在**（2026-09-19，owner 对冻结重跑版的追问）：冻结重跑只冻结了分析师层，
多空辩论/研究经理/Trader 仍各臂独立采样——共享层应全部冻结（owner 原则第二次应用）。
本脚本取 full 材料 state，pop 掉风控/FM 族键后调 generate_report 重新渲染：

- 共享层（分析师 + 多空辩论 + 研究经理 + trader_plan）byte 级一致——由构造保证，另程序自检
- 差异 = 风控辩论章节 + 基金经理章节的消失，及决策章节从 FM 裁决版回退 trader 原方案
  （渲染器回退语义：final_trade_decision or trader_plan，report.py:361）
- 对照语义 = 「同一次 run，风控层在场 vs 不在场」的严格反事实，纯层增量、零采样噪声

产物：materials/*.{full_no_riskfm}.json/pkl（判定驱动换臂用）。零 LLM，幂等可重跑。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from evals.causal_ablation import family_b_materials as fb  # noqa: E402
from finance_agent.nodes.report import generate_report  # noqa: E402

MATERIALS_DIR = _ROOT / "reports/ablation/p2/materials"
VARIANT = "full_no_riskfm"

# 风控/FM 族：pop 后渲染即为 through_trader 语义的决策段（trader_plan 回退）
_RISK_FM_KEYS = (
    "risk_debate_history",
    "risk_judge_conclusion",
    "fund_manager_decision",
    "fund_manager_decision_reasoning",
    "fund_manager_action",
    "fund_manager_confidence",
    "final_trade_decision",
)


def build_no_riskfm_state(full_state: dict) -> dict:
    state = {k: v for k, v in full_state.items() if k not in _RISK_FM_KEYS}
    # 冻结导语：focus_summary 是渲染期 LLM 产物（quick），复用语义（渲染幂等）下
    # 预置 full 的值 → 两臂导语 byte 一致，共享层彻底闭合
    if str(full_state.get("focus_summary") or "").strip():
        state["focus_summary"] = full_state["focus_summary"]
    return {**state, **generate_report(state)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--verify", action="store_true", help="只跑第四关自检，不写产物")
    args = ap.parse_args()
    shared_fail: list[str] = []
    n = 0
    for path in sorted(MATERIALS_DIR.glob("*.full.pkl")):
        ticker = path.name.split(".")[0]
        full_mat = fb.load_material(MATERIALS_DIR, ticker)
        full_state = full_mat["state"]
        new_state = build_no_riskfm_state(full_state)
        # 第四关自检：共享层 byte 级一致——浅拷贝构造下非豁免键应为同一对象
        # （DataFrame 不能用 != 比较；同一体即逐字一致）
        leaked = {
            k
            for k in full_state
            if k not in _RISK_FM_KEYS
            and k not in ("final_report", "chart_data")  # 渲染产物豁免（导语已冻结，不再豁免）
            and full_state.get(k) is not new_state.get(k)
        }
        if leaked:
            shared_fail.append(f"{ticker}: {sorted(leaked)}")
            continue
        report = str(new_state.get("final_report") or "")
        if not report.strip():
            print(f"[surgical] {ticker}: 渲染为空，中止", flush=True)
            return 2
        if not args.verify:
            fb.save_material(
                MATERIALS_DIR,
                ticker,
                state=new_state,
                llm_calls=0,
                snapshot_digest=full_mat["snapshot_digest"],
                variant=VARIANT,
            )
        n += 1
        print(f"[surgical] {ticker}: {len(report)} 字（共享层一致，风控/FM 已摘除）", flush=True)
    if shared_fail:
        print(f"[第四关] 共享层不一致: {shared_fail}", flush=True)
        return 3
    print(f"[surgical] {n}/20 完成｜第四关（共享层一致性）全部通过", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
