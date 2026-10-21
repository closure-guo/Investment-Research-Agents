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
from finance_agent.nodes.validate import (
    exec_reeval_missing,
    final_price_missing,
    inaction_rationale_missing,
)
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
    # 终稿价位完整性（extend-payout-self-check-coverage，601888 实证：buy 价位全 None
    # 直通）：buy/sell 缺失首次打回重试一次；仍缺放行 + 如实标注（同 Trader 价检语义）
    final_price_check: dict = {"result": "pass", "note": ""}
    _missing = final_price_missing(decision)
    if _missing:
        retry_context = (
            f"{context}\n\n【价位完整性打回】{'buy' if decision.action == 'buy' else 'sell'}"
            f"决策必须结构化申报数值价位，缺失：{'、'.join(_missing)}。"
            "请重新输出补全 entry_price/stop_loss/target_price 的完整决策 JSON（继承或显式改写 Trader 价位均可，但必须以结构化字段申报）。"
        )
        data = call_llm_for_json(
            retry_context,
            system=system,
            api_key=api_key,
            node_name="risk_judge",
            llm_config=state.get("llm_config"),
            stock_code=state.get("stock_code"),
            prompt_name=_pinfo.prompt_name,
            prompt_version=_pinfo.prompt_version,
        )
        decision = TradeDecision.model_validate(data)
        _missing = final_price_missing(decision)
        if _missing:
            final_price_check["note"] = f"已打回仍未申报：{'、'.join(_missing)}"
        elif str(getattr(decision, "action", "")) in ("watch", "hold"):
            # 终审 I-1：重试输出非执行动作时「已申报」是错话——如实标注价位不适用
            final_price_check["note"] = "打回后改为非执行动作（价位不适用）"
        else:
            final_price_check["note"] = "打回后已申报"
    # 终稿非执行动作理由完整性（require-watch-hold-rationale）：watch/hold 缺理由
    # 首次打回重试一次；仍缺放行 + 如实标注（与 final_price_check 同款一次重试语义）
    final_inaction_check: dict = {"result": "pass", "note": ""}
    _missing_inaction = inaction_rationale_missing(decision)
    if _missing_inaction:
        retry_context = (
            f"{context}\n\n【非执行动作理由打回】watch/hold 终稿必须结构化申报不行动理由"
            f"与再评估触发条件，缺失：{'、'.join(_missing_inaction)}。"
            "请重新输出补全 inaction_reason（一句话，具体到当前不满足执行条件的点）与 "
            "reeval_triggers（1-3 条可观察、可判定的再评估触发条件）的完整决策 JSON。"
        )
        data = call_llm_for_json(
            retry_context,
            system=system,
            api_key=api_key,
            node_name="risk_judge",
            llm_config=state.get("llm_config"),
            stock_code=state.get("stock_code"),
            prompt_name=_pinfo.prompt_name,
            prompt_version=_pinfo.prompt_version,
        )
        decision = TradeDecision.model_validate(data)
        _missing_inaction = inaction_rationale_missing(decision)
        if _missing_inaction:
            final_inaction_check["note"] = f"已打回仍未申报：{'、'.join(_missing_inaction)}"
        else:
            final_inaction_check["note"] = "打回后已申报"
        # I-1（终审）：理由重试使终稿换代，价位块结论作废——复核并如实改注
        _price_after = final_price_missing(decision)
        if _price_after:
            final_price_check["note"] = (
                f"理由重试后终稿价位缺失：{'、'.join(_price_after)}（未再次打回，如实标注）"
            )
        elif final_price_check["note"]:
            # 双翻转终态措辞精确化（评审 Minor 收口）：终稿为非执行动作时不得声称
            # 「价位齐备」（对 watch/hold 是错话）——如实标注改为非执行动作。
            if str(getattr(decision, "action", "")) in ("watch", "hold"):
                final_price_check["note"] = (
                    "价位结论已被理由重试覆盖（终稿改为非执行动作，价位不适用，未再次打回）"
                )
            else:
                final_price_check["note"] = (
                    "价位结论已被理由重试覆盖（终稿换代后价位齐备，未再次打回）"
                )
    # 终稿执行动作再评估触发条件必填（agent-node-contracts 2026-09 扩展，601818 实证）：
    # buy/sell 缺 reeval 打回一次，feedback 引用风险辩论共识线索；仍缺放行 + 如实标注
    final_reeval_check: dict = {"result": "pass", "note": ""}
    _missing_reeval = exec_reeval_missing(decision)
    if _missing_reeval:
        retry_context = (
            f"{context}\n\n【再评估触发条件打回】buy/sell 终稿必须结构化申报 "
            "reeval_triggers（1-3 条可观察、可判定的退出/加仓再评估条件）。"
            "风险辩论中保守/中性方已提出过触发条件线索，请将其结构化申报为完整决策 JSON 的字段。"
        )
        data = call_llm_for_json(
            retry_context,
            system=system,
            api_key=api_key,
            node_name="risk_judge",
            llm_config=state.get("llm_config"),
            stock_code=state.get("stock_code"),
            prompt_name=_pinfo.prompt_name,
            prompt_version=_pinfo.prompt_version,
        )
        decision = TradeDecision.model_validate(data)
        _missing_reeval = exec_reeval_missing(decision)
        if _missing_reeval:
            final_reeval_check["result"] = "released"
            final_reeval_check["note"] = "已打回仍未申报：reeval_triggers"
        else:
            final_reeval_check["note"] = "打回后已申报"
    # 赔率自检（任务 6 + extend-payout-self-check-coverage）：终稿 reasoning 自报赔率
    # vs 自身价位代码计算——冲突原位修正；转述窗口跳过（计数上报）
    _reasoning, _payout_fixed, _payout_skipped = _apply_payout_self_check(
        decision.reasoning,
        decision.action,
        decision.entry_price,
        decision.stop_loss,
        decision.target_price,
    )
    if _payout_fixed:
        decision = decision.model_copy(update={"reasoning": _reasoning})

    return {
        "final_trade_decision": decision,
        "payout_ratio_corrected": _payout_fixed,
        "payout_ratio_conflict_skipped": _payout_skipped,
        "final_price_check": final_price_check,
        "final_inaction_check": final_inaction_check,
        "final_reeval_check": final_reeval_check,
    }


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
