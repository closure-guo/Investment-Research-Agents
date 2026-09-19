"""Layer IV Risk Management — 风险辩论 Agent + Risk Judge。

3 个风险辩论者（aggressive/conservative/neutral）并行产出 DebateMessage，
Risk Judge 综合辩论给出最终 TradeDecision。
"""

from __future__ import annotations

import json

from finance_agent.debate_anchors import anchor_stats, check_argument_anchors
from finance_agent.langfuse_tracing import update_current_span
from finance_agent.models import DebateMessage, TradeDecision
from finance_agent.nodes._llm_utils import call_llm_for_json, focus_hint
from finance_agent.nodes.validate import apply_payout_self_check as _apply_payout_self_check
from finance_agent.prompts.loader import load_prompt_with_meta


def _risk_debater(state: dict, role: str, prompt_name: str, node_name: str = "") -> dict:
    """风险辩论者通用逻辑。"""
    context = _build_risk_context(state)
    _pinfo = load_prompt_with_meta(prompt_name)
    system = _pinfo.template.replace("{role}", role)
    if role == "aggressive":
        system = system.replace("{perspective}", "激进收益")
    elif role == "conservative":
        system = system.replace("{perspective}", "保守风控")
    else:
        system = system.replace("{perspective}", "中性平衡")
    api_key = state.get("api_key")

    data = call_llm_for_json(
        context,
        system=system,
        api_key=api_key,
        node_name=node_name,
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

    return {"risk_debate_history": [msg], "debate_anchor_checks": checks}


def aggressive_debater(state: dict) -> dict:
    """Layer IV 激进型风险辩论者。"""
    return _risk_debater(state, "aggressive", "risk_debater", node_name="aggressive_debater")


def conservative_debater(state: dict) -> dict:
    """Layer IV 保守型风险辩论者。"""
    return _risk_debater(state, "conservative", "risk_debater", node_name="conservative_debater")


def neutral_debater(state: dict) -> dict:
    """Layer IV 中性型风险辩论者。"""
    return _risk_debater(state, "neutral", "risk_debater", node_name="neutral_debater")


def risk_judge(state: dict) -> dict:
    """Layer IV Risk Judge — 最终交易决策。"""
    context = _build_risk_context(state)
    _pinfo = load_prompt_with_meta("risk_judge")
    system = _pinfo.template
    api_key = state.get("api_key")

    data = call_llm_for_json(
        context,
        system=system,
        api_key=api_key,
        node_name="risk_judge",
        llm_config=state.get("llm_config"),
        stock_code=state.get("stock_code"),
        prompt_name=_pinfo.prompt_name,
        prompt_version=_pinfo.prompt_version,
    )
    decision = TradeDecision.model_validate(data)
    # 赔率自检（任务 6）：终稿 reasoning 自报赔率 vs 自身价位代码计算——冲突原位修正
    _reasoning, _payout_fixed = _apply_payout_self_check(
        decision.reasoning,
        decision.action,
        decision.entry_price,
        decision.stop_loss,
        decision.target_price,
    )
    if _payout_fixed:
        decision = decision.model_copy(update={"reasoning": _reasoning})

    return {"final_trade_decision": decision, "payout_ratio_corrected": _payout_fixed}


def _build_risk_context(state: dict) -> str:
    """构建风险辩论的 LLM context。"""
    sections = []

    # 用户关注点（D5）：风险谱向用户期限/关注维度倾斜
    hint = focus_hint(state)
    if hint:
        sections.append(hint)

    # Trader 方案
    plan = state.get("trader_plan") or {}
    if hasattr(plan, "model_dump"):
        plan = plan.model_dump()
    if isinstance(plan, dict) and plan:
        sections.append(f"交易方案: {json.dumps(plan, ensure_ascii=False)}")

    # 风控指标
    risk = state.get("risk_metrics") or {}
    if risk:
        sections.append(f"风控指标: {json.dumps(risk, ensure_ascii=False)}")

    # 派生指标（deterministic-derived-metrics）：validate 节点代码计算的
    # 止损距离/赔率随 context 下发，辩论方与裁决直接引用，不自行重算
    dm = state.get("derived_metrics") or {}
    if dm.get("stop_distance_pct") is not None and dm.get("risk_reward_ratio") is not None:
        sections.append(
            f"派生指标（代码计算）: 止损距离 {dm['stop_distance_pct']:.1%}、"
            f"赔率 {dm['risk_reward_ratio']:.2f}:1"
            "（算术已由代码完成，直接引用，MUST NOT 自行重算或改写）"
        )

    # 风险辩论历史（第 2 轮参考第 1 轮）
    history = state.get("risk_debate_history") or []
    if history:
        history_lines = []
        for msg in history:
            if hasattr(msg, "content"):
                history_lines.append(f"{msg.role}: {msg.content}")
            elif isinstance(msg, dict):
                history_lines.append(f"{msg.get('role', '?')}: {msg.get('content', '')}")
        sections.append("风险辩论记录:\n" + "\n".join(history_lines))

    return "\n\n".join(sections) if sections else "无可用数据"
