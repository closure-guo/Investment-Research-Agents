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

# add-rm-assumption-table：固定 4 行假设表契约（维度/状态/时间标签枚举与视觉标记）
_ASSUMPTION_DIMENSIONS = {
    "H1": "竞争格局",
    "H2": "成长驱动",
    "H3": "盈利质量",
    "H4": "估值与预期",
}
_ASSUMPTION_STATUSES = ("成立", "注意", "不成立")
_ASSUMPTION_TIME_TAGS = ("short", "long", "short+long")
_STATUS_MARKS = {"成立": "✅", "注意": "⚠️", "不成立": "❌"}


def validate_assumption_table(table: object) -> tuple[list[dict] | None, str]:
    """RM 假设表合法性校验：合法 → (table, "")；非法 → (None, 丢弃原因)。

    表是增强不是门禁：任何非法都整表丢弃，由调用方告警，不触碰评级解析语义。
    """
    if not isinstance(table, list):
        return None, f"assumption_table 非列表: {type(table).__name__}"
    if len(table) != 4:
        present = {str(r.get("code", "")).strip() for r in table if isinstance(r, dict)}
        missing = [c for c in _ASSUMPTION_DIMENSIONS if c not in present]
        detail = f"，缺 {'/'.join(missing)} 行" if missing else ""
        return None, f"行数非法: {len(table)}（应为 4）{detail}"
    seen: list[str] = []
    for row in table:
        if not isinstance(row, dict):
            return None, "存在非对象行"
        code = str(row.get("code", "")).strip()
        if code not in _ASSUMPTION_DIMENSIONS:
            return None, f"code 非法: {code!r}"
        seen.append(code)
        if row.get("status") not in _ASSUMPTION_STATUSES:
            return None, f"行 {code} status 非法: {row.get('status')!r}"
        if row.get("time_tag") not in _ASSUMPTION_TIME_TAGS:
            return None, f"行 {code} time_tag 非法: {row.get('time_tag')!r}"
        if not str(row.get("assumption") or "").strip():
            return None, f"行 {code} assumption 为空"
        if not str(row.get("inflection_signal") or "").strip():
            return None, f"行 {code} inflection_signal 为空"
        bull, bear = row.get("bull_evidence"), row.get("bear_evidence")
        if bull is not None and not isinstance(bull, list):
            return None, f"行 {code} bull_evidence 非列表"
        if bear is not None and not isinstance(bear, list):
            return None, f"行 {code} bear_evidence 非列表"
        if not (bull or bear):
            return None, f"行 {code} 多空证据双侧皆空"
    missing = [c for c in _ASSUMPTION_DIMENSIONS if c not in seen]
    if missing:
        return None, f"缺 {'/'.join(missing)} 行"
    return table, ""


def _cell(value: object) -> str:
    return str(value if value is not None else "").replace("|", "／").replace("\n", " ").strip()


def format_assumptions_text(table: list[dict]) -> str:
    """假设表 → 人读 markdown 表格（RM conclusion 拼装与报告节渲染共用同一格式）。"""
    lines = [
        "| 维度 | 核心假设 | 多方证据 | 空方证据 | 拐点信号 | 状态 | 周期 |",
        "|---|---|---|---|---|---|---|",
    ]
    for row in table:
        code = str(row.get("code", ""))
        dim = f"{code} {_ASSUMPTION_DIMENSIONS.get(code, '')}".strip()
        bull = "；".join(_cell(e) for e in (row.get("bull_evidence") or [])) or "—"
        bear = "；".join(_cell(e) for e in (row.get("bear_evidence") or [])) or "—"
        status = str(row.get("status", ""))
        mark = _STATUS_MARKS.get(status, "")
        lines.append(
            f"| {dim} | {_cell(row.get('assumption'))} | {bull} | {bear} | "
            f"{_cell(row.get('inflection_signal'))} | {f'{mark} {status}'.strip()} | "
            f"{_cell(row.get('time_tag'))} |"
        )
    return "\n".join(lines)


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
        "research_manager_assumptions": None,
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
        # 假设表是增强不是门禁：非法整表丢弃+告警，不触碰 parse_degraded
        table, reason = validate_assumption_table(data.get("assumption_table"))
        if table is not None:
            result["research_manager_assumptions"] = table
            result["research_manager_conclusion"] += "\n\n" + format_assumptions_text(table)
        else:
            logger.warning("RM 假设表丢弃（评级照常生效）: %s", reason)
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
