"""B1 判定 prompt 单变量迭代探针（LLM 花费：40 次调用/轮，限校准样本行）。

**背景**：v2（双问+通道分账+判例一起上）2026-09-18 校准样本验证未过门（吸收 0.825 /
新增 0.750），且双变量同动无法归因。本探针做**单变量**迭代：v2a = v1 吸收口径原封不动
+ 仅新增「是否新增」问题（口诀 + 终裁判例，无通道规则、无 Q1/Q2 交叉修正）。

**过拟合警告**：40 行人工标签同时被用于 v2 失败诊断与 v2a 验证——任何在本集上 ≥0.80 的
变体都只是**候选**，正式启用前须按采样协议 v2 在**新抽样本**上复审（登记于 BACKLOG）。

用法：
    uv run python tests/scripts/p2_b1_variant_probe.py            # 跑 v2a（缓存续跑）
    uv run python tests/scripts/p2_b1_variant_probe.py --report   # 只报一致率不调 LLM
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from evals.causal_ablation import family_b_judge as fj  # noqa: E402
from evals.causal_ablation import family_b_materials as fb  # noqa: E402
from evals.causal_ablation import family_b_text as ft  # noqa: E402

MATERIALS_DIR = _ROOT / "reports/ablation/p2/materials"
OUT_DIR = _ROOT / "reports/ablation/p2"
CAL_B1 = _ROOT / "tests/validation/2026-09-17-p2-calibration-b1.csv"
V2A_RUBRIC = "b1-v2a"

_V2A_TEMPLATE = """你是判定员。回答两个**相互独立**的问题。

问题一「是否新增」（new_risk_point）：这条风险点里，有没有分析师发现没有提供过的判断或事实？
判法（两问口诀）：把风险点里分析师已说过的事实全部删掉，看剩下什么——
- 剩下的是关于这家公司的、可对错的主张（因果归属 / 预测情景 / 跨源合成 / 新事实断言）→ true
- 只剩下风险化措辞、权重口径主张、或对对方观点的评价（该侧重哪个数 / 该怎么说 / 对方观点不行）→ false
判例（人工终裁确认）：
- true：「负增长是需求端被动收缩而非主动改革控货」（因果归属）；「收购整合后商誉减值压力将累积」（预测）；
  「技术强势与资金面背离」（跨源合成）；「机构资金持续撤离」（新事实断言，上游无此数据则无源）
- false：「利润改善缺乏营收端支撑」（分析师已把营收零增长与利润微增并置，只是换成风险口吻）；
  「边际动能才是定价核心」（权重主张）；「低胜率的左侧赌博」（评价对方观点）；「PMI 跌破荣枯线压制需求」（宏观分析师原话）
问题一与问题二相互独立，问题一的答案不得影响问题二。

风险点：{risk_point}

分析师发现（判「是否新增」的参照池，共 {n_references} 条）：{reference_points}

问题二「是否吸收」（absorbed，与 v1 口径完全一致）：
判定口径（宁严勿宽）：
- 吸收 = 决策的理由/依据里**明确回应**了该风险点（采纳、量化、或作为不行动的理由）；
- 只是同一主题被提到但未回应风险本身 → 未吸收；
- 决策方向与该风险相反但给出理由 → 算吸收（理由即回应）。

决策动作：{action}
决策理由：{reasoning}
决策依据引用：{evidence_refs}
基金经理裁决：{fm_decision}；理由：{fm_reasoning}

只输出 JSON: {{"new_risk_point": true|false, "absorbed": true|false, "reason": "<一句话依据>"}}"""


def b1_prompt_v2a(row: dict) -> str:
    refs = "；".join(
        f"{r.get('claim')}（来源 {r.get('source')}）" for r in row.get("evidence_refs") or []
    )
    points = [str(p) for p in row.get("reference_points") or []]
    return _V2A_TEMPLATE.format(
        risk_point=row.get("risk_point") or "",
        n_references=len(points),
        reference_points=" ｜ ".join(points) or "（无）",
        action=row.get("decision_action") or "",
        reasoning=row.get("decision_reasoning") or "",
        evidence_refs=refs or "（无）",
        fm_decision=row.get("fm_decision") or "（无）",
        fm_reasoning=row.get("fm_reasoning") or "（无）",
    )


def _rows() -> list[dict]:
    with CAL_B1.open(encoding="utf-8-sig", newline="") as fh:
        ids = {str(r.get("unit_id") or "") for r in csv.DictReader(fh)}
    out: list[dict] = []
    for path in sorted(MATERIALS_DIR.glob("*.full.pkl")):
        state = fb.load_material(MATERIALS_DIR, path.name.split(".")[0])["state"]
        refs = ft.analyst_reference_points(state)
        for row in ft.judge_material_rows(
            [{"ticker": path.name.split(".")[0], "_state": state, "b1": ft.b1_unit(state)}]
        ):
            if str(row.get("unit_id")) in ids:
                row["reference_points"] = refs
                out.append(row)
    return out


def _agreement(judged: list[dict]) -> dict:
    with CAL_B1.open(encoding="utf-8-sig", newline="") as fh:
        human = {
            str(r["unit_id"]): (r["人工判定(被吸收?是/否)"], r["人工判定(是否新增?是/否)"])
            for r in csv.DictReader(fh)
        }
    n = ag_ab = ag_nw = 0
    dis: list[str] = []
    for r in judged:
        uid = str(r.get("unit_id"))
        h = human.get(uid)
        if not h:
            continue
        n += 1
        ok_ab = bool(r.get("judge_label")) == (h[0] == "是")
        ok_nw = bool(r.get("judge_new_risk_point")) == (h[1] == "是")
        ag_ab += ok_ab
        ag_nw += ok_nw
        if not ok_ab or not ok_nw:
            dis.append(f"{uid}: 吸收{'√' if ok_ab else '×'} 新增{'√' if ok_nw else '×'}")
    return {"n": n, "absorbed": ag_ab / n, "newness": ag_nw / n, "disagreements": dis}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--report", action="store_true", help="只读缓存报一致率，不调 LLM")
    args = ap.parse_args()
    cache = OUT_DIR / f"judged-b1-{V2A_RUBRIC}.jsonl"
    done: dict[str, dict] = {}
    if cache.exists():
        done = {
            str(json.loads(x).get("unit_id")): json.loads(x)
            for x in cache.read_text(encoding="utf-8").splitlines()
            if x.strip()
        }
    rows = _rows()
    todo = [r for r in rows if str(r.get("unit_id")) not in done]
    print(f"[v2a] 样本 {len(rows)} 行｜缓存 {len(done)}｜待判 {len(todo)}", flush=True)
    if todo and not args.report:
        from dotenv import load_dotenv

        load_dotenv()
        fresh = fj.run_b1_judgments(todo, prompt_fn=b1_prompt_v2a, rubric=V2A_RUBRIC)
        with cache.open("a", encoding="utf-8") as fh:
            for r in fresh:
                fh.write(json.dumps(r, ensure_ascii=False) + chr(10))
        done.update({str(r["unit_id"]): r for r in fresh})
        print(f"[v2a] 新判 {len(fresh)} 行已落盘 {cache.name}", flush=True)
    result = _agreement(list(done.values()))
    print(
        f"[v2a vs 人工] n={result['n']}｜吸收一致 {result['absorbed']:.3f}｜新增一致 {result['newness']:.3f}"
    )
    for d in result["disagreements"]:
        print("  ", d)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
