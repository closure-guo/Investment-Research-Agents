"""Layer III Trader Agent — 基于分析师报告和辩论结论做出交易决策。"""

from __future__ import annotations

from finance_agent.models import TradeDecision
from finance_agent.nodes._llm_utils import call_llm_for_json, focus_hint
from finance_agent.nodes.validate import apply_payout_self_check as _apply_payout_self_check
from finance_agent.prompts.loader import load_prompt_with_meta


def trader(state: dict) -> dict:
    """Layer III Trader — 综合分析产出交易决策。"""
    context = _build_trader_context(state)
    _pinfo = load_prompt_with_meta("trader")
    system = _pinfo.template
    api_key = state.get("api_key")

    data = call_llm_for_json(
        context,
        system=system,
        api_key=api_key,
        node_name="trader",
        llm_config=state.get("llm_config"),
        stock_code=state.get("stock_code"),
        prompt_name=_pinfo.prompt_name,
        prompt_version=_pinfo.prompt_version,
    )
    # 赔率自检（eval-driven-contract-fixes 任务 6）：初稿 reasoning 自报赔率 vs 自身价位
    # 代码计算——冲突原位修正（确定性替换，无 LLM）
    data["reasoning"], _payout_fixed = _apply_payout_self_check(
        str(data.get("reasoning") or ""),
        data.get("action"),
        data.get("entry_price"),
        data.get("stop_loss"),
        data.get("target_price"),
    )
    decision = TradeDecision.model_validate(data)

    return {"trader_plan": decision, "payout_ratio_corrected": _payout_fixed}


def _build_trader_context(state: dict) -> str:
    """构建 Trader 的 LLM context。"""
    sections = []

    hint = focus_hint(state)
    if hint:
        sections.append(hint)

    # 分析师报告
    reports = state.get("analyst_reports") or {}
    for name, report in reports.items():
        if hasattr(report, "summary"):
            sections.append(f"[{name}] {report.summary}")
        elif isinstance(report, dict):
            sections.append(f"[{name}] {report.get('summary', '')}")

    # 研究经理结论
    conclusion = state.get("research_manager_conclusion")
    if conclusion:
        sections.append(f"研究经理结论: {conclusion}")

    # 基金经理退回意见（calibrate-fm-approval）：FM return 重跑时注入退回理由，
    # 使 Trader 能针对「风险缓释/仓位控制/止损安排」等要求改进方案；
    # 非 return 路径（approve/reject）或空理由不注入，避免误导。
    fm_reasoning = state.get("fund_manager_decision_reasoning")
    if state.get("fund_manager_decision") == "return" and fm_reasoning:
        sections.append(f"基金经理退回意见: {fm_reasoning}")

    # 价位参考（toolize-price-levels）：工具预算的价位带，供解读与偏离对照——
    # 数值由工具计算，LLM 不做价位心算
    import json as _json

    levels = state.get("price_levels")
    if levels and levels.get("available"):
        sections.append(
            f"价位参考（工具计算 price_levels，entry_ref=最新收盘；"
            f"long: stop_band_long 为止损参考带、target_band_long 为目标参考带）：\n"
            f"{_json.dumps(levels, ensure_ascii=False)}"
        )

    # 价位校验打回意见（sanity 校验 fail 后重出时携带）
    price_feedback = state.get("price_check_feedback")
    if price_feedback:
        sections.append(f"价位校验打回意见: {price_feedback}")

    # 辩论历史
    history = state.get("debate_history") or []
    if history:
        history_lines = []
        for msg in history:
            if hasattr(msg, "content"):
                history_lines.append(f"{msg.role}: {msg.content}")
            elif isinstance(msg, dict):
                history_lines.append(f"{msg.get('role', '?')}: {msg.get('content', '')}")
        sections.append("辩论记录:\n" + "\n".join(history_lines))

    return "\n\n".join(sections) if sections else "无可用数据"
