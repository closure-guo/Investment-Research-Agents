"""Layer IV Risk Management — 风险辩论 Agent + Risk Judge。

3 个风险辩论者（aggressive/conservative/neutral）并行产出 DebateMessage，
Risk Judge 综合辩论给出最终 TradeDecision。
"""

from __future__ import annotations

import json
import logging
import math

from finance_agent.debate_anchors import anchor_stats, check_argument_anchors
from finance_agent.langfuse_tracing import update_current_span
from finance_agent.metrics.decision_price_check import check_decision_prices
from finance_agent.models import DebateMessage, TradeDecision
from finance_agent.nodes._llm_utils import call_llm_for_json, focus_hint, holdings_context_line
from finance_agent.nodes.validate import apply_payout_self_check as _apply_payout_self_check
from finance_agent.nodes.validate import final_price_missing, inaction_rationale_missing
from finance_agent.outcome.track_record.model import recent_directions
from finance_agent.prompts.loader import load_prompt_with_meta

logger = logging.getLogger(__name__)


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


# 增量事实词表（add-decision-hysteresis，首版从严窄词表）：新报告期披露 /
# 重大公告事件 / 技术形态破位确认三类——reasoning 含任一即视为申报了翻转或
# 均衡带执行所需的证据增量；漏放优于误拦（漏放走重申二次申报兜底）
_INCREMENTAL_MARKERS: tuple[str, ...] = (
    "披露",
    "季报",
    "中报",
    "年报",
    "财报",
    "业绩预告",
    "业绩快报",
    "公告",
    "中标",
    "回购",
    "减持",
    "增持",
    "质押",
    "解禁",
    "定增",
    "分红",
    "放量",
    "死叉",
    "金叉",
    "破位",
    "收复",
)

_ACTION_DIRECTION = {"buy": "long", "sell": "short", "hold": "neutral", "watch": "neutral"}
_DIRECTION_ACTION = {"long": "buy", "short": "sell", "neutral": "watch"}


def _has_incremental_claim(reasoning: object) -> bool:
    """reasoning 是否申报了证据增量（窄词表包含判定，首版语义）。"""
    if not isinstance(reasoning, str):
        return False
    return any(marker in reasoning for marker in _INCREMENTAL_MARKERS)


def _evaluate_hysteresis(state: dict, decision: TradeDecision) -> dict:
    """均衡带/滞回判定（纯判定，不改决策）：balanced_zone / prior / flip /
    incremental_claimed / needs_reaffirm。历史查询 fail-open（空序列即不翻转）。"""
    rating = str(state.get("research_manager_rating") or "")
    balanced_zone = "中性" in rating
    history = recent_directions(str(state.get("stock_code") or ""))
    prior = history[-1] if history else None
    cur_direction = _ACTION_DIRECTION.get(str(getattr(decision, "action", "")), "neutral")
    flip = prior is not None and str(prior["direction"]) != cur_direction
    incremental = _has_incremental_claim(getattr(decision, "reasoning", None))
    action_is_exec = str(getattr(decision, "action", "")) in ("buy", "sell")
    needs_reaffirm = (balanced_zone and action_is_exec and not incremental) or (
        flip and not incremental
    )
    return {
        "balanced_zone": balanced_zone,
        "prior_direction": str(prior["direction"]) if prior else None,
        "prior_date": str(prior["date"]) if prior else None,
        "flip": flip,
        "incremental_claimed": incremental,
        "needs_reaffirm": needs_reaffirm,
        "applied": "",
    }


def _build_hysteresis_context(state: dict) -> str:
    """近窗决策史 + 均衡带标志注入决策上下文（首次输出即知情，减少无谓翻转）；
    无历史且非均衡带时返回空串（零增量）。"""
    parts: list[str] = []
    if "中性" in str(state.get("research_manager_rating") or ""):
        parts.append(
            "证据均衡带：是（Research Manager 评级中性）——执行动作（buy/sell）"
            "须以证据增量（新报告期披露/重大公告/技术形态破位确认）为支撑"
        )
    history = recent_directions(str(state.get("stock_code") or ""))
    if history:
        seq = " → ".join(
            f"{x['date']} {x['direction']}({float(x['confidence']):.0%})" for x in history
        )
        parts.append(f"近窗决策史（旧→新）：{seq}——方向翻转须申报增量事实，未申报将被复核维持前判")
    return ("\n\n【决策滞回上下文】\n" + "\n".join(parts) + "\n") if parts else ""


def _reaffirm_decision_semantics(
    state: dict,
    decision: TradeDecision,
    context: str,
    system: str,
    api_key: str | None,
    llm_config: dict | None,
    pinfo,
) -> tuple[TradeDecision, dict]:
    """均衡带/滞回复核回路：无增量的执行动作或翻转 → 携反馈打回重申恰一次；
    重申仍无 → 降级观望（均衡带执行）/ 维持前判（翻转），如实标注落 hysteresis。"""
    hy = _evaluate_hysteresis(state, decision)
    if not hy["needs_reaffirm"]:
        return decision, hy
    why = "方向翻转未申报证据增量" if hy["flip"] else "证据均衡带内执行动作未申报证据增量"
    retry_context = (
        f"{context}\n\n【决策滞回复核打回】{why}。滞回约束要求：方向翻转或证据均衡带内"
        "执行动作（buy/sell）MUST 在 reasoning 中显式申报增量事实（新报告期披露、"
        "重大公告、技术形态破位确认之一或多）。若确有增量请改写 reasoning 显式申报后"
        "重新输出完整决策 JSON；若无增量请输出与近窗前向一致的观望决策。"
    )
    data = call_llm_for_json(
        retry_context,
        system=system,
        api_key=api_key,
        node_name="risk_judge",
        llm_config=llm_config,
        stock_code=state.get("stock_code"),
        prompt_name=pinfo.prompt_name,
        prompt_version=pinfo.prompt_version,
    )
    decision = TradeDecision.model_validate(data)
    hy = _evaluate_hysteresis(state, decision)
    if not hy["needs_reaffirm"]:
        hy["applied"] = ""
        hy["reaffirmed"] = True
        return decision, hy
    # 重申仍无增量 → 确定性处置
    hy["reaffirmed"] = True
    if hy["flip"]:
        prior_action = _DIRECTION_ACTION.get(str(hy["prior_direction"]), "watch")
        # 维持前判到执行动作时：反方向价位不可信清空（交既有价位完整性打回补报）；
        # sell_type 维持语义中性默认 short（清空会撞后续 sell_type 申报打回的级联）
        update: dict = {
            "action": prior_action,
            "position_size": None,
            "entry_price": None,
            "stop_loss": None,
            "target_price": None,
            "exit_schedule": None,
        }
        if prior_action == "sell":
            update["sell_type"] = "short"
        else:
            update["sell_type"] = None
        decision = decision.model_copy(update=update)
        if prior_action == "watch":
            decision = decision.model_copy(
                update={
                    "inaction_reason": "维持前判（无证据增量）：近窗前向为观望，本次翻转未申报增量事实"
                }
            )
        hy["applied"] = "维持前判（无证据增量）"
    else:
        decision = decision.model_copy(
            update={
                "action": "watch",
                "position_size": None,
                "entry_price": None,
                "stop_loss": None,
                "target_price": None,
                "sell_type": None,
                "exit_schedule": None,
                "inaction_reason": "证据均衡无增量，默认观望：均衡带内执行动作未申报证据增量",
            }
        )
        hy["applied"] = "证据均衡无增量，默认观望"
    return decision, hy


def _recheck_price_note_after_retry(
    decision: TradeDecision, final_price_check: dict, cause: str
) -> None:
    """重试使终稿换代后复核价位结论（终审 I-1 模式）：如实改注，不再打回。

    cause 为重试回路名（「理由重试」/「再评估重试」），用于 note 措辞溯源。
    终稿换代为非执行动作时不得声称「价位齐备」（对 watch/hold 是错话）——
    双翻转终态措辞精确化（评审 Minor 收口）。
    """
    _price_after = final_price_missing(decision)
    if _price_after:
        final_price_check["note"] = (
            f"{cause}后终稿价位缺失：{'、'.join(_price_after)}（未再次打回，如实标注）"
        )
    elif final_price_check["note"]:
        # 双翻转终态措辞精确化（评审 Minor 收口）：终稿为非执行动作时不得声称
        # 「价位齐备」（对 watch/hold 是错话）——如实标注改为非执行动作。
        if str(getattr(decision, "action", "")) in ("watch", "hold"):
            final_price_check["note"] = (
                f"价位结论已被{cause}覆盖（终稿改为非执行动作，价位不适用，未再次打回）"
            )
        else:
            final_price_check["note"] = f"价位结论已被{cause}覆盖（终稿换代后价位齐备，未再次打回）"


def risk_judge(state: dict) -> dict:
    """Layer IV Risk Judge — 最终交易决策。"""
    context = _build_risk_context(state)
    # 滞回上下文注入（add-decision-hysteresis）：均衡带标志 + 近窗决策史随
    # context 下发，首次输出即知情；空串零增量
    context += _build_hysteresis_context(state)
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
    # 证据均衡带与方向滞回复核（add-decision-hysteresis）：无增量的执行动作或
    # 翻转打回重申恰一次，重申仍无 → 降级观望/维持前判（决策语义最先定型，
    # 后续 sell_type/价位回路在其上验参数）
    decision, decision_hysteresis = _reaffirm_decision_semantics(
        state, decision, context, system, api_key, state.get("llm_config"), _pinfo
    )
    # sell 分型申报（update-sell-action-typing，#188）：sell 缺 sell_type → 打回申报
    # 一次；申报 exit → 价位豁免（final_price_missing 分型分支生效，免去无意义价位
    # 打回）；仍缺 → 默认 short（保守，与现行建仓模板语义一致）+ 如实标注。
    # 判定在价位完整性检查之前（design：打回预算不叠加，先定型再验参数）
    if (
        str(getattr(decision, "action", "")) == "sell"
        and getattr(decision, "sell_type", None) is None
    ):
        retry_context = (
            f"{context}\n\n【sell 分型申报打回】sell 决策必须申报 sell_type："
            "exit=持有者减仓/退出敞口（申报 exit_schedule 减仓节奏，无需 entry/stop/target，"
            "重新介入条件写入 reeval_triggers）；short=做空建仓（沿用 entry/stop/target "
            "必填模板）。请重新输出补全 sell_type 的完整决策 JSON。"
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
        if (
            str(getattr(decision, "action", "")) == "sell"
            and getattr(decision, "sell_type", None) is None
        ):
            decision = decision.model_copy(update={"sell_type": "short"})
            _sell_type_note = "sell_type 未申报，默认 short 模板（如实标注）"
        else:
            _sell_type_note = "打回后已申报 sell_type"
    else:
        _sell_type_note = ""
    # 终稿价位完整性（extend-payout-self-check-coverage，601888 实证：buy 价位全 None
    # 直通）：buy/sell 缺失首次打回重试一次；仍缺放行 + 如实标注（同 Trader 价检语义）
    final_price_check: dict = {"result": "pass", "note": _sell_type_note}
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
        _recheck_price_note_after_retry(decision, final_price_check, "理由重试")
    # 终稿执行动作再评估触发条件必填化（update-decision-integrity-gates Task 3，
    # 601818 实证：sell 终稿无任何再评估触发条件，保守/中性方两轮辩论均点名
    # 「reeval_triggers 为空是纪律性缺陷」，FM 仍以「执行安排完备」批准）：buy/sell
    # 清洗后 reeval_triggers 为空 → 打回一次（与 final_price_check 同款一次重试语义），
    # feedback 提示可从风险辩论共识结构化申报；仍空放行 + 如实标注，MUST NOT 虚构
    # 条目。Trader 侧 plan 的 buy/sell 维持现状直通（缺口在终稿层收口，避免双点位
    # 校验叠加打回循环）。
    final_reeval_check: dict = {"result": "pass", "note": ""}
    if str(getattr(decision, "action", "")) in ("buy", "sell") and not decision.reeval_triggers:
        retry_context = (
            f"{context}\n\n【再评估触发条件打回】buy/sell 终稿必须结构化申报再评估触发条件"
            "（退出后监控机制），当前 reeval_triggers 为空，缺失：reeval_triggers。"
            "可从风险辩论中保守/中性方已提出的触发条件结构化申报"
            "（1-3 条可观察、可判定的再评估触发条件），"
            "请重新输出补全 reeval_triggers 的完整决策 JSON。"
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
        if not decision.reeval_triggers:
            final_reeval_check["note"] = "已打回仍未申报再评估触发条件"
        else:
            final_reeval_check["note"] = "打回后已申报"
        # 重试使终稿换代，按既有「重试后复核前序结论」模式复核价位/理由结论
        # （不再触发新一轮打回，MUST NOT 死循环）
        _recheck_price_note_after_retry(decision, final_price_check, "再评估重试")
        _missing_inaction_after = inaction_rationale_missing(decision)
        if _missing_inaction_after:
            final_inaction_check["note"] = (
                "再评估重试后终稿改为非执行动作且理由缺失："
                f"{'、'.join(_missing_inaction_after)}（未再次打回，如实标注）"
            )
    # add-watch-trigger-tracking：watch 终稿双向触发位申报检查（同 final_reeval_check
    # 一次打回语义）。触发位是 watch 唯一的可执行承诺，必须结构化可追踪；两个均缺失
    # 打回一次，仍缺放行 + 如实标注（MUST NOT 死循环/虚构）。buy/sell/hold 不约束。
    final_trigger_check: dict = {"result": "pass", "note": ""}
    if str(getattr(decision, "action", "")) == "watch" and (
        getattr(decision, "trigger_high", None) is None
        and getattr(decision, "trigger_low", None) is None
    ):
        retry_context = (
            f"{context}\n\n【触发位申报打回】watch 终稿必须结构化申报双向触发位"
            "（trigger_high=上破触发价，须严格高于最新收盘价；trigger_low=下破触发价，"
            "须严格低于最新收盘价），当前两者均缺失。"
            "可从风险辩论与再评估触发条件中的价位线索申报（如「站上 X 重估」→ trigger_high=X）。"
            "请重新输出补全 trigger_high/trigger_low 的完整决策 JSON。"
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
        if str(getattr(decision, "action", "")) != "watch":
            # 打回使终稿换代翻转了动作（如 watch→buy）：触发位申报对非 watch 终稿
            # 不再适用（buy/sell/hold 不约束触发位），「已打回仍未申报触发位」即
            # 错话且含完整性标记词（仍未申报）会误渲染「审批对象结构不完整标注」。
            # 如实降级备注——措辞避开 _FM_INCOMPLETE_MARKERS（report.py 仍未申报/缺失）。
            final_trigger_check["note"] = "触发位重试后终稿改为非 watch 动作，触发位申报不再适用"
        elif (
            getattr(decision, "trigger_high", None) is None
            and getattr(decision, "trigger_low", None) is None
        ):
            final_trigger_check["note"] = "已打回仍未申报触发位"
        else:
            final_trigger_check["note"] = "打回后已申报"
        # 重试使终稿换代，复核前序结论（不再触发新一轮打回，MUST NOT 死循环）
        _recheck_price_note_after_retry(decision, final_price_check, "触发位重试")
        _missing_inaction_after = inaction_rationale_missing(decision)
        if _missing_inaction_after:
            final_inaction_check["note"] = (
                "触发位重试后终稿理由缺失："
                f"{'、'.join(_missing_inaction_after)}（未再次打回，如实标注）"
            )
        if not decision.reeval_triggers:
            final_reeval_check["note"] = (
                final_reeval_check["note"]
                or "触发位重试后 reeval_triggers 为空（未再次打回，如实标注）"
            )
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

    # 决策文本价位交叉校验门禁（update-decision-integrity-gates Task 1 →
    # update-decision-price-gate）：reeval_triggers/inaction_reason/reasoning 的
    # 自由文本价位 vs state 已验证技术指标。anomaly 非空 → 携明细定向打回重试一次 →
    # 重放 payout self-check → 复检；修正放行（note「打回后已修正」），仍异常 gate fail
    # （after_risk_judge 据此阻断，不进 FM 审批）。校验器自身异常 fail-open（放行 + 告警）。
    def _run_price_check(dec: TradeDecision) -> list[dict]:
        try:
            return check_decision_prices(
                dec,
                state.get("technical_indicators") or {},
                state.get("price_levels") or {},
                _latest_close_for_price_check(state),
            )
        except Exception:  # noqa: BLE001 -- fail-open：校验器自身异常不得阻断决策放行
            logger.warning("decision_price_check 执行失败（fail-open 放行）", exc_info=True)
            return []

    decision_price_anomalies = _run_price_check(decision)
    decision_price_gate: dict = {"result": "pass", "note": ""}
    if decision_price_anomalies:
        # 首次 anomaly 快照（update-decision-price-gate-admission）：重试后残留
        # 与此比较做恶化判据（见下方分层注释）
        first_anomalies: list[dict] = list(decision_price_anomalies)
        # 反馈四要素（delta spec）：原始文本片段（source_text）+ 指标名/已验证值/
        # 偏差幅度（message 携带）。单侧缺失退化为另一侧，两侧皆空不拼入；
        # 全部为空时兜底「（无明细）」，MUST NOT 拼出空反馈打回。
        feedback_parts: list[str] = []
        for anomaly in decision_price_anomalies:
            if not isinstance(anomaly, dict):
                continue
            sides = [str(anomaly.get("source_text") or ""), str(anomaly.get("message") or "")]
            detail = "：".join(part for part in sides if part)
            if detail:
                feedback_parts.append(detail)
        feedback = "；".join(feedback_parts) or "（无明细）"
        retry_context = (
            f"{context}\n\n【决策价位交叉校验打回】终稿自由文本价位与已验证技术指标冲突：{feedback}。"
            "请核对相关价位（以已验证指标值为准），修正后重新输出完整决策 JSON。"
            # 机械可执行规则（601066 10-07 阻断复盘，#254 后继）：无规则时修正 LLM
            # 补了 24.60 门槛却保留「站上 entry_ref 23.03」，改写文本成为新
            # source_text → 恶化判据阻断——规则必须说到「怎么改才算改干净」。
            "修正规则：触发条件中的上破类价位 MUST 严格高于最新收盘价、跌破类 MUST "
            "严格低于最新收盘价；等于收盘价的价位（含 entry_ref）MUST NOT 保留在"
            "触发条件中——需要现价语境时以陈述句写入 reasoning（如「现价 23.03」），"
            "不得写成触发条件；上破条件建议附加确认语义（放量突破/收盘站稳）以保留"
            "筛选力。"
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
        decision_price_gate["note"] = "打回后已修正"
        # 重试换代重放 payout self-check（重试可能再引入自算赔率），计数合并不清零
        _reasoning2, _payout_fixed2, _payout_skipped2 = _apply_payout_self_check(
            decision.reasoning,
            decision.action,
            decision.entry_price,
            decision.stop_loss,
            decision.target_price,
        )
        if _payout_fixed2:
            decision = decision.model_copy(update={"reasoning": _reasoning2})
        _payout_fixed = _payout_fixed or _payout_fixed2
        _payout_skipped += _payout_skipped2
        # 重试换代复核价位完整性结论（I-1 模式：如实改注，不再打回，MUST NOT 死循环）
        _recheck_price_note_after_retry(decision, final_price_check, "价位交叉校验重试")
        # 重试换代复核兄弟结论（比照再评估重试 I-1 链，评审 Minor 收口）：终稿换代后
        # 理由/触发条件结论可能失真——如实改注，不再打回（MUST NOT 死循环、不得虚报「已申报」）
        _missing_inaction_after = inaction_rationale_missing(decision)
        if _missing_inaction_after:
            final_inaction_check["note"] = (
                "价位交叉校验重试后终稿为非执行动作且理由缺失："
                f"{'、'.join(_missing_inaction_after)}（未再次打回，如实标注）"
            )
        if not decision.reeval_triggers:
            final_reeval_check["note"] = (
                "价位交叉校验重试后终稿未申报再评估触发条件（未再次打回，如实标注）"
            )
        # 重试换代复核触发位结论（add-watch-trigger-tracking 评审收口）：门禁重试可把
        # 终稿翻成 watch 而双向触发位缺失——主块此时已过，空注 pass 是假阳性。打回
        # 预算已被门禁用掉，不再打回（MUST NOT 死循环），如实标注放行
        if (
            str(getattr(decision, "action", "")) == "watch"
            and getattr(decision, "trigger_high", None) is None
            and getattr(decision, "trigger_low", None) is None
        ):
            final_trigger_check["note"] = (
                "价位交叉校验重试后终稿改为 watch 且触发位缺失（未再次打回，如实标注）"
            )
        decision_price_anomalies = _run_price_check(decision)
        if decision_price_anomalies:
            # 门禁准入恶化判据分层（update-decision-price-gate-admission，owner 终裁
            # 已批）：残留 ⊆ 首次（source_text 同源且条数不增）= LLM 坚持原文本的合理
            # 申辩形态（多为校验器语义盲区，incident 034 实证）→ 放行 + 如实标注待
            # 人工终裁；残留恶化（新增 source 或条数增加）= 重试引入新错误 → 阻断。
            # source_text 为校验器提取的原文片段：LLM 改写文本必然产生新片段，
            # 集合精确运算即可刻画「未采纳修正且未引入新错误」，模糊匹配不需要
            # （模糊地带按恶化阻断，保守方向正确）。
            first_sources = {
                str(a.get("source_text") or "") for a in first_anomalies if isinstance(a, dict)
            }
            resid_sources = {
                str(a.get("source_text") or "")
                for a in decision_price_anomalies
                if isinstance(a, dict)
            }
            worse = len(decision_price_anomalies) > len(first_anomalies) or any(
                s not in first_sources for s in resid_sources
            )
            if worse:
                decision_price_gate["result"] = "fail"
                decision_price_gate["note"] = (
                    f"已打回仍恶化（新增异常文本或条数增加）：{len(decision_price_anomalies)} 条残留"
                )
            else:
                decision_price_gate["note"] = (
                    f"打回后残留 {len(decision_price_anomalies)} 条未清零（未恶化，放行待人工终裁）"
                )

    return {
        "final_trade_decision": decision,
        "payout_ratio_corrected": _payout_fixed,
        "payout_ratio_conflict_skipped": _payout_skipped,
        "final_price_check": final_price_check,
        "final_inaction_check": final_inaction_check,
        "final_reeval_check": final_reeval_check,
        "final_trigger_check": final_trigger_check,
        "decision_price_anomalies": decision_price_anomalies,
        "decision_price_gate": decision_price_gate,
        "decision_hysteresis": decision_hysteresis,
    }


def _latest_close_for_price_check(state: dict) -> float | None:
    """最新收盘最稳取法：kline 尾行收盘（与 validate 同源）→ price_levels.entry_ref → None。

    形态噪声（空表/缺列/NaN/非数）一律回落下一级，取不到传 None——校验器据此
    跳过空洞检测、仅做偏差核对，MUST NOT 因行情缺位抛异常。
    """
    kline = state.get("kline")
    try:
        if kline is not None and len(kline) > 0:
            value = float(kline["收盘"].iloc[-1])
            if math.isfinite(value) and value > 0:
                return value
    except Exception:  # noqa: BLE001 -- 行情形态噪声回落 entry_ref，不阻断观测
        logger.debug(
            "latest_close 从 kline 尾行取值失败，回落 price_levels.entry_ref", exc_info=True
        )
    entry_ref = (state.get("price_levels") or {}).get("entry_ref")
    converted = float(entry_ref) if isinstance(entry_ref, (int, float)) else None
    if converted is not None and math.isfinite(converted) and converted > 0:
        return converted
    return None


def _build_risk_context(state: dict) -> str:
    """构建风险辩论的 LLM context。"""
    sections = []

    # 用户关注点（D5）：风险谱向用户期限/关注维度倾斜
    hint = focus_hint(state)
    if hint:
        sections.append(hint)

    # 受众持仓上下文（add-holdings-context）：三态均注入（同 Trader 层，两层不得漂移）
    sections.append(holdings_context_line(state))

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
