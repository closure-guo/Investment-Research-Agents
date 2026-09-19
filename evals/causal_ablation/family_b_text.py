"""族 B 文本侧 code 腿：B1 风险点增量（差集）与 B2 交锋锚定（rebuttal_to）+ 判读材料导出。

**分工**（预登记 §1）：B1 = 差集代码提取 + NLI 判「被决策吸收」；B2 = judge 判「被修正」。
本模块只做 **code 侧**（不受校准门控）：风险点差集、锚定率、锚点可解析统计，
并把 NLI/judge 需要的材料逐行导出（含 20% 校准抽样）——**判定本身由门控后的腿执行**。

**B1 口径**：辩论侧风险点 = 空方（`bear`）msg 的 `key_arguments` 文本；
参照池 = 四位分析师的 `key_findings`。是否「新增」按**字符二元组 Jaccard < 阈值**判——
这是**字面**判据（不是语义等价），阈值取 0.35：与分析师发现重合三成以上即视为同一风险点。
语义等价/吸收与否留给 NLI 腿，本模块不越权。

**B2 口径**：交锋锚定率 = 第 2 轮及以后论点中 `rebuttal_to` 非空的比例（直接读结构字段，
不解析自由文本）；附 `debate_anchor_checks` 的 anchored/unresolved 计数（数据事实，非判定）。
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

# 「同一风险点」的字面判据：字符二元组 Jaccard ≥ 阈值即视为重合（口径写在模块 docstring）
JACCARD_SAME_POINT = 0.35
_DEBATE_HISTORY = "debate_history"


def _dump(obj: Any) -> dict:
    if hasattr(obj, "model_dump"):
        return dict(obj.model_dump())
    return dict(obj) if isinstance(obj, dict) else {}


def _bigrams(text: str) -> set[str]:
    compact = "".join((text or "").split())
    if len(compact) < 2:
        return {compact} if compact else set()
    return {compact[i : i + 2] for i in range(len(compact) - 1)}


def text_similarity(a: str, b: str) -> float:
    """字符二元组 Jaccard（中文短句字面重合度的确定性度量）。"""
    ga, gb = _bigrams(a), _bigrams(b)
    if not ga or not gb:
        return 0.0
    return len(ga & gb) / len(ga | gb)


def debate_risk_points(state: dict, *, history_key: str = _DEBATE_HISTORY) -> list[dict]:
    """辩论侧风险点：空方（bear）论点的文本 + 元信息（角色/轮次/类型/锚点）。"""
    out: list[dict] = []
    for message in state.get(history_key) or []:
        data = _dump(message)
        if str(data.get("role") or "") != "bear":
            continue
        for argument in data.get("key_arguments") or []:
            arg = _dump(argument)
            text = str(arg.get("text") or "").strip()
            if not text:
                continue
            out.append(
                {
                    "role": data.get("role"),
                    "round": data.get("round"),
                    "kind": arg.get("kind"),
                    "text": text,
                    "anchors": list(arg.get("anchors") or []),
                }
            )
    return out


def analyst_reference_points(state: dict) -> list[str]:
    """参照池：四位分析师的 key_findings 文本（辩论之前已有的发现）。"""
    texts: list[str] = []
    for report in (state.get("analyst_reports") or {}).values():
        data = _dump(report)
        for finding in data.get("key_findings") or []:
            text = str(finding or "").strip()
            if text:
                texts.append(text)
    return texts


# 阈值敏感性扫描点（B1 的「新增」判定对阈值敏感——单点阈值是隐藏的自由度，须显式披露）
THRESHOLD_SWEEP: tuple[float, ...] = (0.05, 0.10, 0.15, 0.20, 0.30, 0.35)


def b1_unit(state: dict) -> dict:
    """B1 code 侧：辩论新增风险点（字面判据）的占比 + **阈值敏感性** + 逐条明细。

    `threshold_sweep` 是必需的披露：字面判据下"新增"的定义完全落在阈值上，
    单报一个数会把阈值选择藏进口径里（本批实测 600519 在 0.35 下 12/12 全"新增"，
    而其 bear 论点与分析师的同一风险点字面相似度只有 0.06–0.20——改写型文本）。
    """
    points = debate_risk_points(state)
    reference = analyst_reference_points(state)
    scored: list[dict] = []
    for point in points:
        best = max((text_similarity(point["text"], ref) for ref in reference), default=0.0)
        scored.append(dict(point, max_similarity=round(best, 4)))
    new_points = [p for p in scored if p["max_similarity"] < JACCARD_SAME_POINT]
    return {
        "debate_points": len(points),
        "reference_points": len(reference),
        "new_points": len(new_points),
        "new_share": (len(new_points) / len(points)) if points else None,
        "threshold": JACCARD_SAME_POINT,
        "threshold_sweep": {
            f"{t:.2f}": (sum(1 for p in scored if p["max_similarity"] < t) / len(scored))
            if scored
            else None
            for t in THRESHOLD_SWEEP
        },
        "points": new_points,
        "all_points": scored,
    }


def b2_unit(state: dict) -> dict:
    """B2 code 侧：交锋锚定率 + 锚点检查统计（均读结构字段，不解析自由文本）。

    口径：`rebuttal_to` 挂在**消息**上（该轮消息针对上一轮哪几条论点的列表），
    故锚定率 = 第 2 轮及以后**消息**中 rebuttal_to 非空的比例（按论点算会出现 >1 的伪值）。
    """
    later_messages = with_rebuttal = targets = 0
    for message in state.get(_DEBATE_HISTORY) or []:
        data = _dump(message)
        try:
            round_no = int(data.get("round") or 1)
        except (TypeError, ValueError):
            round_no = 1
        if round_no < 2:
            continue
        later_messages += 1
        rebuttal = data.get("rebuttal_to") or []
        targets += len(rebuttal)
        if rebuttal:
            with_rebuttal += 1
    checks = [_dump(c) for c in state.get("debate_anchor_checks") or []]
    return {
        "later_messages": later_messages,
        "messages_with_rebuttal": with_rebuttal,
        "rebuttal_rate": (with_rebuttal / later_messages) if later_messages else None,
        "rebuttal_targets_total": targets,
        "anchor_checks_total": len(checks),
        "anchor_checks_anchored": sum(1 for c in checks if c.get("anchored")),
        "anchor_checks_unresolved": sum(1 for c in checks if c.get("status") == "unresolved"),
    }


def decision_context(state: dict) -> dict:
    """判读材料：被吸收/被修正的判定要看的下游产物（决策 + 依据 + FM 裁决）。"""
    decision = _dump(state.get("final_trade_decision")) or _dump(state.get("trader_plan"))
    refs = [
        {"claim": str(_dump(r).get("claim") or ""), "source": str(_dump(r).get("source") or "")}
        for r in (decision.get("evidence_refs") or [])
    ]
    return {
        "action": decision.get("action"),
        "reasoning": str(decision.get("reasoning") or ""),
        "evidence_refs": refs,
        "fund_manager_decision": state.get("fund_manager_decision"),
        "fund_manager_reasoning": str(state.get("fund_manager_decision_reasoning") or ""),
    }


def judge_material_rows(units: Sequence[dict]) -> list[dict]:
    """B1 吸收判定材料（对话式判读用）：每行 = 一条新增风险点 + 决策上下文。

    每行给出 `nli_target`（是否被决策吸收？是/否）留空待判——NLI/judge 判定须先过校准门控。
    """
    rows: list[dict] = []
    for unit in units:
        state = unit.get("_state") or {}
        context = decision_context(state)
        # 导出**全部**空方论点（不按阈值预筛）：阈值未标定前，预筛会把未校准的判据
        # 焊进判定集；带 max_similarity 一起导出，采样与「新增」重算都在下游做
        for index, point in enumerate((unit.get("b1") or {}).get("all_points") or []):
            rows.append(
                {
                    "unit_id": f"{unit.get('ticker')}::b1::{index}",
                    "ticker": unit.get("ticker"),
                    "round": point.get("round"),
                    "kind": point.get("kind"),
                    "risk_point": point.get("text"),
                    "max_similarity": point.get("max_similarity"),
                    "decision_action": context["action"],
                    "decision_reasoning": context["reasoning"],
                    "evidence_refs": context["evidence_refs"],
                    "fm_decision": context["fund_manager_decision"],
                    "fm_reasoning": context["fund_manager_reasoning"],
                    "nli_target(被吸收?是/否)": "",
                    "human_label(被吸收?是/否)": "",
                }
            )
    return rows


def b2_material_rows(units: Sequence[dict]) -> list[dict]:
    """B2 修正判定材料：第 2 轮论点 + 其 rebuttal_to 指向的上一轮论点（judge 判「是否被修正」）。"""
    rows: list[dict] = []
    for unit in units:
        state = unit.get("_state") or {}
        history = state.get("debate_history") or []
        earlier: dict[int, str] = {}
        for message in history:
            data = _dump(message)
            try:
                round_no = int(data.get("round") or 1)
            except (TypeError, ValueError):
                round_no = 1
            args = data.get("key_arguments") or []
            if round_no < 2:
                for i, arg in enumerate(args, start=1):
                    earlier[i] = str(_dump(arg).get("text") or "")
                continue
            for arg in args:
                arg = _dump(arg)
                for target in _dump(message).get("rebuttal_to") or []:
                    rows.append(
                        {
                            "unit_id": f"{unit.get('ticker')}::b2::{target}",
                            "ticker": unit.get("ticker"),
                            "rebutted_point": earlier.get(int(target), ""),
                            "rebuttal_text": str(arg.get("text") or ""),
                            "judge_target(被修正?是/否)": "",
                            "human_label(被修正?是/否)": "",
                        }
                    )
    return rows


def calibration_sample(rows: Sequence[dict], *, fraction: float = 0.20) -> list[dict]:
    """校准抽样：按行序等距取 fraction（确定性，不随重跑变化）。"""
    if not rows or fraction <= 0:
        return []
    step = max(1, round(1 / fraction))
    return [rows[i] for i in range(0, len(rows), step)]
