"""Layer II Bull/Bear 辩论 Agent。

每个辩论者：
1. 读取分析师报告和辩论历史
2. 调用 LLM 生成辩论消息
3. 返回 DebateMessage 列表（供 LangGraph reducer 追加）
"""

from __future__ import annotations

from finance_agent.debate_anchors import anchor_stats, check_argument_anchors
from finance_agent.langfuse_tracing import update_current_span
from finance_agent.models import DebateMessage
from finance_agent.nodes._llm_utils import call_llm_for_json, focus_hint
from finance_agent.prompts.loader import load_prompt_with_meta


def bull_debater(state: dict) -> dict:
    """Layer II Bull（看多）辩论者。"""
    context = _build_debate_context(state)
    _pinfo = load_prompt_with_meta("bull_debater")
    system = _pinfo.template
    api_key = state.get("api_key")

    data = call_llm_for_json(
        context,
        system=system,
        api_key=api_key,
        node_name="bull_debater",
        llm_config=state.get("llm_config"),
        stock_code=state.get("stock_code"),
        prompt_name=_pinfo.prompt_name,
        prompt_version=_pinfo.prompt_version,
    )
    msg = DebateMessage.model_validate(data)

    # 论点锚点校验（add-debate-argument-anchors）：只落通道与 span stats，
    # fail-open 不改路由；resolved 仅表示锚存在，不代表锚支持论点
    checks = check_argument_anchors(msg, state)
    update_current_span(metadata={"anchor_stats": anchor_stats(checks)})

    return {"debate_history": [msg], "debate_anchor_checks": checks}


def bear_debater(state: dict) -> dict:
    """Layer II Bear（看空）辩论者。"""
    context = _build_debate_context(state)
    _pinfo = load_prompt_with_meta("bear_debater")
    system = _pinfo.template
    api_key = state.get("api_key")

    data = call_llm_for_json(
        context,
        system=system,
        api_key=api_key,
        node_name="bear_debater",
        llm_config=state.get("llm_config"),
        stock_code=state.get("stock_code"),
        prompt_name=_pinfo.prompt_name,
        prompt_version=_pinfo.prompt_version,
    )
    msg = DebateMessage.model_validate(data)

    # 论点锚点校验（add-debate-argument-anchors）：只落通道与 span stats，
    # fail-open 不改路由；resolved 仅表示锚存在，不代表锚支持论点
    checks = check_argument_anchors(msg, state)
    update_current_span(metadata={"anchor_stats": anchor_stats(checks)})

    return {"debate_history": [msg], "debate_anchor_checks": checks}


_ARG_MARKS = "①②③④⑤⑥⑦⑧⑨⑩"


def debate_message_fields(msg: object) -> tuple[str, int, str, list] | None:
    """DebateMessage / dict → (role, round, content, key_arguments)；非法消息返回 None。"""
    if hasattr(msg, "role"):
        return msg.role, msg.round, msg.content, list(msg.key_arguments or [])
    if isinstance(msg, dict):
        return (
            msg.get("role", "?"),
            msg.get("round", 1),
            msg.get("content", ""),
            msg.get("key_arguments") or [],
        )
    return None


def format_debate_message(role: str, rnd: int, content: str, args: list | None) -> str:
    """单条辩论消息标准渲染：「R{n} 论点: ①…②…」编号行 + 「role(R{n}): content」。

    交锋结构化引用（1.10）与 add-rm-assumption-table 共用——论点获得稳定身份，
    辩论者按编号回应（rebuttal_to）、RM 假设表证据按「角色+轮次+论点序号」转引。
    args 为空时退化为无编号行的基础格式。
    """
    arg_line = ""
    if args:
        numbered = " ".join(
            f"{_ARG_MARKS[i] if i < 10 else i + 1}.{a.text if hasattr(a, 'text') else a}"
            for i, a in enumerate(args)
        )
        arg_line = f"R{rnd} 论点: {numbered}\n"
    return f"{arg_line}{role}(R{rnd}): {content}"


def _build_debate_context(state: dict) -> str:
    """构建辩论的 LLM context。"""
    sections = []

    # 用户关注点（D5）：辩题向用户角度收敛
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

    # 辩论历史（第 2 轮需要参考第 1 轮）
    # 交锋结构化引用（1.10）：历史发言以「R{n} 论点: ①…②…」编号行前置 key_arguments
    # ——对方的论点获得稳定身份，输出方可按编号回应（rebuttal_to）与被覆盖率核算
    history = state.get("debate_history") or []
    if history:
        history_lines = []
        for msg in history:
            fields = debate_message_fields(msg)
            if fields is None:
                continue
            history_lines.append(format_debate_message(*fields))
        sections.append("辩论历史:\n" + "\n".join(history_lines))

    return "\n\n".join(sections) if sections else "无可用数据"
