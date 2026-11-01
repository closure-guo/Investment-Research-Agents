"""Layer II Research Manager — 总结 Bull/Bear 辩论，给出研究结论。"""

from __future__ import annotations

import logging

from finance_agent.nodes._llm_utils import (
    call_llm_streaming_with_fallback,
    focus_hint,
    parse_json_response,
)
from finance_agent.nodes.debate import debate_message_fields, format_debate_message
from finance_agent.prompts.loader import load_prompt_with_meta

logger = logging.getLogger("finance_agent.research_manager")

_RATING_RE = ("看多", "看空", "中性")


def research_manager(state: dict) -> dict:
    """Layer II Research Manager - 输出结构化评级（JSON）+ 评级前置的人读结论。

    D1 同族（harden-decision-report-semantics）：LLM 按 JSON 输出
    reasoning（先推理）/rating/confidence（后结论），消费端由本节点把
    评级前置拼装为「评级: …（置信度 …）」首行——展示倒金字塔，生成保推理序。
    解析失败降级：原文作 conclusion、rating/confidence=None、parse_degraded=True，
    不中断管线（对齐分析师降级语义）。
    """
    context = _build_research_context(state)
    _pinfo = load_prompt_with_meta("research_manager")
    system = _pinfo.template
    api_key = state.get("api_key")

    raw = call_llm_streaming_with_fallback(
        context,
        system=system,
        api_key=api_key,
        node_name="research_manager",
        llm_config=state.get("llm_config"),
        stock_code=state.get("stock_code"),
        prompt_name=_pinfo.prompt_name,
        prompt_version=_pinfo.prompt_version,
    )

    result: dict = {
        "research_manager_conclusion": raw,
        "research_manager_rating": None,
        "research_manager_confidence": None,
    }
    try:
        data = parse_json_response(raw)
        rating = str(data.get("rating", "")).strip()
        if rating not in _RATING_RE:
            raise ValueError(f"rating 非法: {rating!r}")
        confidence = float(data["confidence"])
        if not 0 <= confidence <= 1:
            raise ValueError(f"confidence 越界: {confidence}")
        reasoning = str(data.get("reasoning", "")).strip()
        if not reasoning:
            raise ValueError("reasoning 为空")
        result["research_manager_conclusion"] = (
            f"评级: {rating}（置信度 {confidence:.2f}）\n{reasoning}"
        )
        result["research_manager_rating"] = rating
        result["research_manager_confidence"] = confidence
    except Exception as exc:  # noqa: BLE001 — 降级为自由文本，不中断管线
        logger.warning("RM 结构化输出解析失败，降级为自由文本: %s", exc)
        result["parse_degraded"] = True
    return result


def _build_research_context(state: dict) -> str:
    sections = []

    hint = focus_hint(state)
    if hint:
        sections.append(hint)

    # 分析师报告摘要
    reports = state.get("analyst_reports") or {}
    for name, report in reports.items():
        if hasattr(report, "summary"):
            sections.append(f"[{name}] {report.summary}")
        elif isinstance(report, dict):
            sections.append(f"[{name}] {report.get('summary', '')}")

    # 辩论历史：编号行渲染与辩论层共用（add-rm-assumption-table）——
    # 假设表证据按「角色+轮次+论点序号」稳定引用；无 key_arguments 退化基础格式
    history = state.get("debate_history") or []
    if history:
        history_lines = []
        for msg in history:
            fields = debate_message_fields(msg)
            if fields is None:
                continue
            history_lines.append(format_debate_message(*fields))
        sections.append("辩论记录:\n" + "\n".join(history_lines))

    return "\n\n".join(sections) if sections else "无可用数据"
