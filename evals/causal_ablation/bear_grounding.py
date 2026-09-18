"""无源增量 grounding 扫描（bg-v1，预登记 `2026-09-18-p2-b1-grounding-scan.md`）。

**口径**：不问「相对 16 条发现是否新增」，问「这条自标 kind=data 的第 1 轮空方论点，
其事实断言能否被辩手当时的**全部输入**（4 份分析师摘要）支撑」——标准 NLI 形态，
前提即辩手真实输入（`debate.py::_build_debate_context`：r1 只见摘要）。

**门控纪律**：judge 判定，未过校准（采样协议 v2 分层 + ≥0.80）一律 provisional。
"""

from __future__ import annotations

import random
from collections.abc import Sequence
from typing import Any

from evals.causal_ablation.family_b_judge import JudgeFn, _ask_json, default_judge_fn
from evals.causal_ablation.family_b_text import _dump

BG_RUBRIC = "bg-v1"

_TEMPLATE = """你是审计员。辩手在第 1 轮发言时，它的全部信息输入只有下面这几份分析师摘要（此外没有任何数据）。
判断这条被自标为「数据论据」（kind=data）的论点里的事实断言是否都能从摘要中得到支撑。

判定口径：
- 全部事实断言（数字/事件/状态）都能在摘要中找到依据（原文或直接换算）→ supported=true
- 存在摘要中没有任何依据的断言（无中生有的数字/资金流/事件，**或与摘要方向相反**）→ supported=false，
  并把该断言原文摘进 unsupported_part
- 合理推断不算无源：从摘要数字推出的结论有源即可；只有断言本身无源才判 false
- 方向相反同样无源（例：摘要说板块资金净流入，论点说机构资金持续撤离）

分析师摘要（辩手的全部输入）：
{summaries}

被审论点（第 1 轮，kind=data）：
{argument}

只输出 JSON: {{"supported": true|false, "unsupported_part": "<无源断言原文，无则空串>", "reason": "<一句话依据>", "confidence": "high"|"medium"|"low"}}"""


def grounding_units(ticker: str, state: dict) -> list[dict]:
    """宇宙 = 第 1 轮 ∧ kind=data 的空方论点（r2+ 可见辩论历史，判可支撑性会误伤）。"""
    summaries: list[str] = []
    for name, rep in (state.get("analyst_reports") or {}).items():
        data = rep if isinstance(rep, dict) else _dump(rep)
        text = str(data.get("summary") or "").strip()
        if text:
            summaries.append(f"[{name}] {text}")
    joined = "\n".join(summaries)
    out: list[dict] = []
    for message in state.get("debate_history") or []:
        data = message if isinstance(message, dict) else _dump(message)
        if str(data.get("role") or "") != "bear" or int(data.get("round") or 0) != 1:
            continue
        for argument in data.get("key_arguments") or []:
            arg = argument if isinstance(argument, dict) else _dump(argument)
            if str(arg.get("kind") or "") != "data":
                continue
            text = str(arg.get("text") or "").strip()
            if not text:
                continue
            out.append(
                {
                    "unit_id": f"{ticker}::bg::{len(out)}",
                    "ticker": ticker,
                    "round": 1,
                    "kind": "data",
                    "argument": text,
                    "anchors": list(arg.get("anchors") or []),
                    "summaries": joined,
                }
            )
    return out


def grounding_prompt(unit: dict) -> str:
    return _TEMPLATE.format(
        summaries=unit.get("summaries") or "（无）",
        argument=unit.get("argument") or "",
    )


def run_grounding(rows: Sequence[dict], *, llm_fn: JudgeFn | None = None) -> list[dict]:
    fn = llm_fn or default_judge_fn()
    out: list[dict] = []
    for row in rows:
        parsed = _ask_json(grounding_prompt(row), fn)
        out.append(
            {
                **row,
                "judge_label": parsed.get("supported") if isinstance(parsed, dict) else None,
                "unsupported_part": (parsed or {}).get("unsupported_part") or "",
                "judge_reason": (parsed or {}).get("reason"),
                "confidence": (parsed or {}).get("confidence"),
                "judge_parse_failed": parsed is None,
                "rubric": BG_RUBRIC,
            }
        )
    return out


def grounding_report(rows: Sequence[dict]) -> dict[str, Any]:
    labeled = [r for r in rows if r.get("judge_label") is not None]
    unsupported = [
        (str(r.get("unit_id")), str(r.get("unsupported_part") or ""), tuple(r.get("anchors") or []))
        for r in labeled
        if r.get("judge_label") is False
    ]
    return {
        "rows": len(rows),
        "labeled": len(labeled),
        "parse_failed": sum(1 for r in rows if r.get("judge_parse_failed")),
        "unsupported_count": len(unsupported),
        "rate": (len(unsupported) / len(labeled)) if labeled else None,
        "unsupported": unsupported,
    }


def calibration_sample(
    rows: Sequence[dict], *, supported_fraction: float = 0.1, seed: int = 7
) -> list[dict]:
    """采样协议 v2（2026-09-18 修正）：**正例全审** + 有源行随机抽 `supported_fraction`。

    依据 bg-v1 实测：判定模型自报把握 64/64 全 high（零区分度）——分层不能挂在自报把握上，
    挂在判定结果的正例上（判无源 = 待人工终裁的决定性行，全审；有源行抽 10% 防系统性漏报）。"""
    unsupported = [r for r in rows if r.get("judge_label") is False]
    supported = [r for r in rows if r.get("judge_label") is True]
    n = max(1, round(len(supported) * supported_fraction)) if supported else 0
    picked = random.Random(seed).sample(supported, n) if n else []  # noqa: S311 - 确定性抽样，非密码学用途
    ordered = {str(r.get("unit_id")): r for r in [*unsupported, *picked]}
    return [r for r in rows if str(r.get("unit_id")) in ordered]
