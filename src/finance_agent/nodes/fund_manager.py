"""Layer V Fund Manager Agent — 最终审批交易决策。"""

from __future__ import annotations

import json
import logging

from finance_agent.langfuse_tracing import get_langfuse
from finance_agent.models import FundManagerDecision
from finance_agent.nodes._llm_utils import call_llm_for_json, focus_hint
from finance_agent.prompts.loader import load_prompt_with_meta

logger = logging.getLogger("finance_agent.fund_manager")

# 终稿完整性检查的 state 键 → 中文标签（update-decision-integrity-gates Task 4；
# decision_price_gate 为 update-decision-price-gate 追加、final_trigger_check 为
# add-watch-trigger-tracking 追加）。
# report.py 渲染「审批对象结构不完整标注」复用同一收集器，键序/标签单源
FINAL_CHECK_LABELS: tuple[tuple[str, str], ...] = (
    ("final_price_check", "价位"),
    ("final_inaction_check", "非执行动作理由"),
    ("final_reeval_check", "再评估触发条件"),
    # watch 终稿触发位申报检查（「已打回仍未申报触发位」/「打回后已申报」/
    # 门禁翻转注「…触发位缺失…」随完整性标注一并进 FM 上下文）
    ("final_trigger_check", "触发位申报"),
    # 决策价位门禁复核注（「打回后已修正」/「已打回仍未通过：N 条残留」）随完整性
    # 标注一并进 FM 上下文（report 侧经 _FM_INCOMPLETE_MARKERS 过滤，不渲染）
    ("decision_price_gate", "决策价位校验"),
)


def final_integrity_notes(state: dict) -> list[tuple[str, str]]:
    """收集终稿完整性检查的非空 note（键序稳定，返回 [(标签, note 原文), ...]）。

    note 非空即收集——含「打回后已申报」等复核性标注（FM 可见性优先，全量如实进
    上下文；报告侧只挑「结构不完整」标注，见 report._fm_incomplete_integrity_block）。
    噪声形态（check 非 dict / note 非字符串 / 纯空白）静默跳过，MUST NOT 中断构建。
    """
    notes: list[tuple[str, str]] = []
    for key, label in FINAL_CHECK_LABELS:
        check = state.get(key)
        note = check.get("note") if isinstance(check, dict) else None
        if isinstance(note, str) and note.strip():
            notes.append((label, note.strip()))
    return notes


def fund_manager(state: dict) -> dict:
    """Layer V Fund Manager — 审批/拒绝/退回。"""
    context = _build_fund_manager_context(state)
    _pinfo = load_prompt_with_meta("fund_manager")
    system = _pinfo.template
    api_key = state.get("api_key")

    data = call_llm_for_json(
        context,
        system=system,
        api_key=api_key,
        node_name="fund_manager",
        llm_config=state.get("llm_config"),
        stock_code=state.get("stock_code"),
        prompt_name=_pinfo.prompt_name,
        prompt_version=_pinfo.prompt_version,
        # approve 缺 action/confidence 带错误摘要重试一次；仍缺向上抛（重试 ≠ 降级）
        validate=FundManagerDecision.model_validate,
    )
    # 枚举强校验：非法值/缺键抛 ValidationError 中断管线，不静默降级为 approve
    # （加固前为 data["decision"] 裸取键，非法值经 routing 的 else 分支被当作批准放行）
    parsed = FundManagerDecision.model_validate(data)
    decision = parsed.decision

    # #111：审批理由不得丢弃——reasoning 落 state，报告渲染 + judge 可见
    result: dict = {
        "fund_manager_decision": decision,
        "fund_manager_decision_reasoning": parsed.reasoning,
        "fund_manager_action": parsed.action,
        "fund_manager_confidence": parsed.confidence,
    }
    if decision == "return":
        result["return_count"] = state.get("return_count", 0) + 1
    if decision == "approve":
        # 决策落库需要 trace 关联:节点内 OTel 上下文可用(citation_node 同款 get_langfuse 模式)
        try:
            client = get_langfuse()
            trace_id = client.get_current_trace_id() if client else None
            if trace_id:
                result["langfuse_trace_id"] = trace_id
        except Exception:
            logger.warning("trace_id 捕获失败,decision_log 将无 trace 关联", exc_info=True)

    return result


def _anchor_warning_summary(checks: list[dict]) -> str | None:
    """辩论锚点告警汇总（add-fm-grounding-surface）。

    消费 add-anchor-value-grounding 的确定性信号：value_mismatch（文本数字
    不可溯源）、unresolved/missing（锚点未解析，全 kind）、echo_only（field
    形态锚仅回声命中）。逐条上限 5 条，超出计数汇总；全零不出段。
    fail-open：告警不改变路由，仲裁权在 FM。
    """
    offenders: list[str] = []
    for c in checks:
        reasons: list[str] = []
        if c.get("status") == "value_mismatch":
            reasons.append("value_mismatch")
        if c.get("status") in ("unresolved", "missing"):
            reasons.append(str(c.get("status")))
        if c.get("echo_only_field_refs"):
            reasons.append("echo_only")
        if not reasons:
            continue
        offenders.append(
            f"{c.get('role')} R{c.get('round')} #{c.get('index')}：{'+'.join(reasons)}"
            f"（锚点：{'、'.join(c.get('anchors') or [])}）"
        )
    if not offenders:
        return None
    shown = offenders[:5]
    extra = f"\n- 另 {len(offenders) - 5} 条未列示" if len(offenders) > 5 else ""
    return "辩论锚点告警（确定性校验，fail-open）：\n" + "\n".join(f"- {o}" for o in shown) + extra


def _build_fund_manager_context(state: dict) -> str:
    """构建 Fund Manager 的 LLM context。"""
    sections = []

    # 用户关注点（来自深度研究意图澄清环节）
    hint = focus_hint(state)
    if hint:
        sections.append(hint)

    # 最终交易决策（审批对象）。risk_judge 与 trader 同惯例：TradeDecision 对象原样
    # 进 state，此处必须接受对象——曾用 isinstance(dict) 守卫致对象被静默跳过，
    # FM 在看不到方案的情况下审批
    decision = state.get("final_trade_decision")
    if decision is not None and hasattr(decision, "model_dump"):
        decision = decision.model_dump()
    if isinstance(decision, dict) and decision:
        sections.append(f"交易决策: {json.dumps(decision, ensure_ascii=False)}")

    # 终稿完整性标注（update-decision-integrity-gates Task 4；final_trigger_check 为
    # add-watch-trigger-tracking 追加）：终稿完整性检查的 note 非空时如实进上下文——
    # FM 审批前能看到审批对象的结构完整性状态
    # （spec「FM 上下文携带完整性标注」；仲裁权保留：标注不禁止 approve）
    integrity = final_integrity_notes(state)
    if integrity:
        detail = "；".join(f"{label}——{note}" for label, note in integrity)
        sections.append(f"终稿完整性标注：{detail}")

    # add-fm-grounding-surface：三类 grounding 输入（非空才出现，fail-open——
    # 不改路由，仲裁权保留；spec「FM 上下文携带估值完整性标注/辩论锚点告警/数据口径披露原文」）
    vsnap = state.get("valuation_snapshot")
    missing_reasons = (vsnap or {}).get("missing_reasons") or []
    if missing_reasons:
        sections.append(
            "估值完整性标注："
            + "；".join(str(r) for r in missing_reasons)
            + "（估值数据缺席，估值相关论断缺乏确定性数据支撑）"
        )

    anchor_section = _anchor_warning_summary(state.get("debate_anchor_checks") or [])
    if anchor_section:
        sections.append(anchor_section)

    # 函数内导入：report.py 模块级导入本模块的 final_integrity_notes（渲染复用同一
    # 收集器），此处模块级导入会成环；披露节渲染单一实现复用（勿复制）
    from finance_agent.nodes.report import _format_freshness_section

    disclosure = _format_freshness_section(state)
    if disclosure:
        sections.append(f"数据口径披露（确定性渲染，供交叉核对）：\n{disclosure}")

    # 风控指标
    risk = state.get("risk_metrics") or {}
    if risk:
        sections.append(f"风控指标: {json.dumps(risk, ensure_ascii=False)}")

    # 退回次数
    return_count = state.get("return_count", 0)
    sections.append(f"已退回次数: {return_count}（上限 1 次）")

    return "\n\n".join(sections)
