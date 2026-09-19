"""bear 提示词 A/B 验证（eval-driven-contract-fixes 任务 4 效果验证）。

**设计**： grounding 扫描（bg-v1）测得旧提示词无源率 8/64=0.125（owner 终裁一致率 1.000）。
提示词收紧（断言级锚定 + 反例判例）已发布 production——本脚本用**同一批输入**（缓存材料里
的 4 份分析师摘要）只重跑 bear_debater 单节点（新提示词），对比新论点的无源率与 data 论点量。

**对照有效性**：r1 bear 的输入恰为且仅为 4 份摘要（debate_history 清空）；两侧唯一差异 =
提示词版本。成本 ≈ 20 次调用（每标的 1 次；按标的断点续跑，含零 data 论点标的）。

用法：
    uv run python tests/scripts/p2_bear_prompt_ab.py           # 跑 A/B（20 次调用）
    uv run python tests/scripts/p2_bear_prompt_ab.py --report  # 只读缓存
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from evals.causal_ablation import bear_grounding as bg  # noqa: E402
from evals.causal_ablation import family_b_materials as fb  # noqa: E402
from finance_agent.nodes.debate import bear_debater  # noqa: E402

MATERIALS_DIR = _ROOT / "reports/ablation/p2/materials"
OLD_JUDGED = _ROOT / "reports/ablation/p2/judged-bg-v1.jsonl"
NEW_CACHE = _ROOT / "reports/ablation/p2/judged-bgpab-v2.jsonl"  # 判定行
DONE_MARKERS = _ROOT / "reports/ablation/p2/judged-bgpab-v2.done.json"  # 已跑标的（含零论点）


def _load_jsonl(path: Path) -> list[dict]:
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--report", action="store_true", help="只读缓存，不调 LLM")
    args = ap.parse_args()

    judged: dict[str, dict] = {}
    if NEW_CACHE.exists():
        judged = {str(r["unit_id"]): r for r in _load_jsonl(NEW_CACHE)}
    done: set[str] = set()
    if DONE_MARKERS.exists():
        done = set(json.loads(DONE_MARKERS.read_text(encoding="utf-8")))

    fresh_calls = 0
    if not args.report:
        from dotenv import load_dotenv

        load_dotenv()
        for path in sorted(MATERIALS_DIR.glob("*.full.pkl")):
            ticker = path.name.split(".")[0]
            if ticker in done:
                continue
            state = fb.load_material(MATERIALS_DIR, ticker)["state"]
            # r1 bear 输入 = 且仅为 4 份摘要：清空辩论历史（对照条件与旧扫描一致）
            out = bear_debater({**state, "debate_history": []})
            msg = out["debate_history"][-1]
            units = bg.grounding_units(
                ticker,
                {"analyst_reports": state.get("analyst_reports"), "debate_history": [msg]},
            )
            new_rows = bg.run_grounding(units)
            with NEW_CACHE.open("a", encoding="utf-8") as fh:
                for r in new_rows:
                    fh.write(json.dumps(r, ensure_ascii=False) + chr(10))
            judged.update({str(r["unit_id"]): r for r in new_rows})
            done.add(ticker)
            DONE_MARKERS.write_text(json.dumps(sorted(done)), encoding="utf-8")
            fresh_calls += 1
            n_data = sum(1 for u in units if u["kind"] == "data")
            n_all = len(
                [a for m in [msg] for a in (m.get("key_arguments") if isinstance(m, dict) else [])]
            )
            print(
                f"[ab] {ticker}: bear r1 论点 {n_all}（data {n_data}），判 {len(new_rows)} 行",
                flush=True,
            )
    print(f"[ab] 本轮新跑 {fresh_calls} 标的（累计完成 {len(done)}/20）", flush=True)

    new_rows = list(judged.values())
    new_report = bg.grounding_report(new_rows)
    old_report = bg.grounding_report(_load_jsonl(OLD_JUDGED))
    summary = {
        "old_prompt": {
            "data_units": old_report["labeled"],
            "unsupported": old_report["unsupported_count"],
            "rate": old_report["rate"],
        },
        "new_prompt": {
            "data_units": new_report["labeled"],
            "unsupported": new_report["unsupported_count"],
            "rate": new_report["rate"],
            "parse_failed": new_report["parse_failed"],
            "tickers_covered": len(done),
        },
        "note": "两侧宇宙均为「该侧 bear r1 输出 ∧ kind=data」；旧侧 64 单元来自 20 标的全量",
    }
    print(json.dumps(summary, ensure_ascii=False, indent=1))
    for uid, part, anchors in new_report["unsupported"]:
        print(f"  新无源 {uid}: {part[:60]}｜锚点 {list(anchors)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
