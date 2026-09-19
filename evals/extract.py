# evals/extract.py
"""state → judge 变量提取(spec consistency/decision_grounding 变量映射)。

所有值截断到 4096 字节(truncate_for_trace 头尾保留),控制 judge prompt 体积。
state 缺失键一律给空字符串,judge 输入永不 KeyError。
"""

from __future__ import annotations

import json
import re
from typing import cast

from finance_agent.langfuse_tracing import truncate_for_trace

# 用户决策（r2 三道关复盘）：截断预算一律放宽——800/4096 级中段截断让 judge 反复
# 抱怨「无法核对」并压低置信度，还切出残句与丢期间标签的假矛盾
_JUDGE_MAX_BYTES = 32768
# 结论性标题词表：兼容真实报告的编号式标题（「## 六、基金经理决策」）。
# 「多空辩论结论」是中间章节，标题命中时取最后一个（最终决策在报告末尾）。
_CONCLUSION_HEADERS = ("结论", "总结", "交易建议", "投资建议", "综合结论", "基金经理决策")
_CONCLUSION_TITLE_RE = re.compile(
    r"^#{1,4}\s*(?:[一二三四五六七八九十0-9]+[、.．]\s*)?"
    r"(?:结论|总结|交易建议|投资建议|综合结论|基金经理决策)[^\n]*\n"
    r"(.*?)(?=^#{1,4}\s|\Z)",
    re.MULTILINE | re.DOTALL,
)


def _trunc(text: str) -> str:
    return truncate_for_trace(text, _JUDGE_MAX_BYTES)


def _append_within_budget(head: str, tail: str, limit: int = _JUDGE_MAX_BYTES) -> str:
    """head（决策 JSON）保持完整，tail 只在剩余预算内追加。

    整体 _trunc 会把 head 的中段连闭合括号一起挖掉（r2 实证 consistency 材料
    【Risk Judge 裁决】9/9 成残缺 JSON，人读化失败）。
    """
    head = _trunc(head)
    if not tail:
        return head
    remaining = limit - len(head.encode("utf-8")) - 1
    if remaining < 200:
        return head
    return head + "\n" + truncate_for_trace(tail, remaining)


def extract_conclusion(report: str) -> str:
    """提取报告结论章节：最后命中的结论性标题（含编号式「六、基金经理决策」）起，
    到下一标题或文末；无标题时从尾部最近断句边界起（避免从句子中间切出半句话）。"""
    if not report:
        return ""
    matches = list(_CONCLUSION_TITLE_RE.finditer(report))
    if matches:
        return matches[-1].group(1).strip()
    tail = report[-800:]
    # 无结论性标题时 fallback：优先段落边界（\n\n / 句号+换行），其次断句边界，
    # 避免从句子中间切出半句话
    for anchor, skip in (("\n\n", 2), ("。\n", 2)):
        idx = tail.rfind(anchor)
        if idx >= 0:
            return tail[idx + skip : idx + skip + 500].strip()
    idx = max(tail.rfind("。"), tail.rfind("；"))
    if idx >= 0:
        return tail[idx + 1 : idx + 501].strip()
    return tail[-500:].strip()


def _as_dict(obj: object) -> dict:
    """state 元素可能是 dict 或 pydantic（LangGraph reducer 原样保留），统一转 dict。"""
    if isinstance(obj, dict):
        return obj
    if hasattr(obj, "model_dump"):
        return cast(dict, obj.model_dump())
    return {}


def _summarize_analyst_reports(reports: dict) -> str:
    # 每 agent 展示上限（字节）：4 分析师并列时保证每个结论方向都可见——
    # 整体 head/tail 截断会把中间 agent 整段切掉（fundamental 不可见，
    # judge 的 consistency 评分与标注材料都拿不到各层结论，2026-09-09 回归）。
    # 4×_AGENT_MAX_BYTES ≈ 3200 < _JUDGE_MAX_BYTES(4096)，外层 _trunc 兜底不再命中。
    _AGENT_MAX_BYTES = 6000

    parts: list[str] = []
    for name, rep in reports.items():
        rep = _as_dict(rep)
        if not rep:
            continue
        # add-agent-readable-conclusion：优先普通人可读结论（plain_conclusion），
        # 旧 trace 报告对象无该字段时回退 summary/conclusion（审计可读优先，黑话次要）
        text = rep.get("plain_conclusion") or rep.get("summary") or rep.get("conclusion") or ""
        if not text:
            text = json.dumps(rep, ensure_ascii=False)[:500]
        claims = _format_claims(rep.get("claims") or [])
        if claims:
            text = f"{text}\n论据: {claims}"
        parts.append(f"【{name}】{truncate_for_trace(text, _AGENT_MAX_BYTES)}")
    return "\n".join(parts)


def _format_claims(claims: list) -> str:
    """把报告 Claim 列表压缩成 judge 可核对的一行（论据 + 数值）。

    judge 需要具体数值核对 evidence_refs 的 claim（delta 根因 2：摘要
    抹掉数值导致「无中生有」误判）。interpretation 缺失时退回 stated_value。
    """
    items: list[str] = []
    for c in claims:
        c = _as_dict(c)
        if not c:
            continue
        interp = c.get("interpretation", "")
        value = c.get("stated_value", "")
        if interp and value not in ("", None):
            items.append(f"{interp}({value})")
        elif interp:
            items.append(interp)
        elif value not in ("", None):
            items.append(str(value))
    return "; ".join(items)


def _argument_text(arg: object) -> str:
    """论点文本：dict/pydantic DebateArgument 取 text，裸字符串原样（旧格式/夹具）。

    不得 str() 整个论点对象——pydantic 会渲染成 ``text=... kind=... anchors=...``
    或 dict-repr（``{'text': ...}``），把结构噪声灌进 judge 材料。
    """
    if isinstance(arg, str):
        return arg.strip()
    if isinstance(arg, dict):
        return str(arg.get("text") or "").strip()
    if hasattr(arg, "text"):
        return str(arg.text or "").strip()
    return "" if arg is None else str(arg).strip()


def _rebuttal_coverage(history: list) -> str | None:
    """交锋覆盖率（D1 1.12）：各角色论点被对方回应的比例——零 token 确定性指标。

    辩论图按轮扇出（route_to_debate_r1/r2 各派 bull/bear 并行）：同轮互不可见，
    rebuttal_to 按 prompt 契约指向「对方上一轮」的论点编号。因此按 (role, round)
    定位被回应方，而非「上一条发言」（扇出下历史顺序不确定，且同方连续发言会
    互相记账——r1/r2 实证 8 条 trace 全「4/8」）。

    分母只算「存在回应机会」的论点：对方出现过更后轮次，该轮论点才可被回应；
    末轮论点在并行结构下永远无人可回应，计入分母会把上限压到 50%。

    仅 bull/bear 两方辩论输出覆盖率；三方风险辩论对手不唯一、单轮辩论无人可
    回应 → None。round 字段缺失时按该角色第几次发言兜底。
    """
    messages: list[tuple[str, int, list[str], list]] = []
    occurrence: dict[str, int] = {}
    for raw in history:
        msg = _as_dict(raw)
        if not msg:
            continue
        role = str(msg.get("role", "?"))
        occurrence[role] = occurrence.get(role, 0) + 1
        try:
            rnd = int(msg.get("round") or occurrence[role])
        except (TypeError, ValueError):
            rnd = occurrence[role]
        # 论点文本按原位置取（不过滤空项）：rebuttal_to 的 1-based 编号对应对方
        # key_arguments 的位置，过滤会让编号错位
        args = [_argument_text(a) for a in (msg.get("key_arguments") or [])]
        messages.append((role, rnd, args, msg.get("rebuttal_to") or []))

    roles = {role for role, _, _, _ in messages}
    if roles != {"bull", "bear"}:
        return None
    opponent = {"bull": "bear", "bear": "bull"}
    args_by: dict[tuple[str, int], list[str]] = {}
    max_round: dict[str, int] = {}
    for role, rnd, args, _ in messages:
        args_by[(role, rnd)] = args
        max_round[role] = max(max_round.get(role, 0), rnd)

    covered: dict[str, set] = {}
    rebuttable: dict[str, int] = dict.fromkeys(roles, 0)
    for role, rnd, args, rebuttal in messages:
        opp = opponent[role]
        if max_round.get(opp, 0) > rnd:
            rebuttable[role] += len(args)
        if not rebuttal or rnd <= 1:
            continue
        target = args_by.get((opp, rnd - 1))
        if target is None:
            continue
        # 被回应的是对方（opp）上一轮的论点
        covered.setdefault(opp, set()).update(
            (rnd - 1, n) for n in rebuttal if isinstance(n, int) and 1 <= n <= len(target)
        )
    lines = []
    for role in ("bull", "bear"):
        if rebuttable[role] == 0:
            continue
        lines.append(f"{role} 论点被回应 {len(covered.get(role, set()))}/{rebuttable[role]}")
    return "；".join(lines) if lines else None


def _convergence_skeleton(history: list) -> str:
    """收敛信号骨架行（v8，确定性统计）：发言轮数/各方论点数/让步与坚持语计数。

    正则启发式有假阳/假阴，强制标注「程序统计，供参考」——judge 仍以原文为准；
    置于原始发言之前（spec：judge 不必从原始发言自行推断交锋收敛情况）。
    """
    if not history:
        return ""
    roles: dict[str, int] = {}
    args_count = 0
    concede = 0
    persist = 0
    for msg in history:
        msg = _as_dict(msg)
        if not msg:
            continue
        role = str(msg.get("role", "?"))
        roles[role] = roles.get(role, 0) + 1
        args_count += len(msg.get("key_arguments") or [])
        content = str(msg.get("content") or "")
        concede += len(re.findall(r"确实|(?<!不)同意|部分接受|有道理|合理性", content))
        persist += len(re.findall(r"不同意|反驳|恰恰相反|无法认同|不成立", content))
    role_line = "/".join(f"{role}×{n}" for role, n in roles.items())
    return (
        f"收敛信号(程序统计，供参考): 发言 {len(history)} 轮({role_line})；"
        f"论点共 {args_count} 条；让步语 {concede} 处、坚持/反驳语 {persist} 处"
    )


def _summarize_debate(history: list) -> str:
    # 每条发言上限（字节）：多轮【bull】【bear】交替时保证全部轮次可见——
    # 整体 head/tail 截断会把中间轮次连标签一起挖掉（judge 评「逐条交锋」时
    # 看不到交锋过程，2026-09-10 实测回归：2fd1ee6d 的【bear】标签被挖掉）。
    # 4 条发言 × 800 字节 ≈ 3200 < _JUDGE_MAX_BYTES(4096)，外层 _trunc 兜底不再命中。
    _MESSAGE_MAX_BYTES = 24000  # round7 审计：单条风险辩论消息达 20.9KB，6000 仍拦腰截断
    # 论点行上限：key_arguments 是每轮立场骨架（LLM 已结构化输出），截正文时骨架
    # 必须全数在场——judge 的「逐条回应对方论点」以论点行为对照锚点。
    _ARGUMENTS_MAX_BYTES = 2000

    parts: list[str] = []
    skeleton = _convergence_skeleton(history)
    if skeleton:
        parts.append(skeleton)
    roles: dict[str, int] = {}
    args_count = 0
    concede = 0
    persist = 0
    for msg in history:
        msg = _as_dict(msg)
        if not msg:
            continue
        role = str(msg.get("role", "?"))
        roles[role] = roles.get(role, 0) + 1
        args_count += len(msg.get("key_arguments") or [])
        content = str(msg.get("content") or "")
        concede += len(re.findall(r"确实|(?<!不)同意|部分接受|有道理|合理性", content))
        persist += len(re.findall(r"不同意|反驳|恰恰相反|无法认同|不成立", content))
    role_line = "/".join(f"{role}×{n}" for role, n in roles.items())
    return (
        f"收敛信号(程序统计，供参考): 发言 {len(history)} 轮({role_line})；"
        f"论点共 {args_count} 条；让步语 {concede} 处、坚持/反驳语 {persist} 处"
    )


def anchor_coverage(checks: list) -> dict | None:
    """论点锚点覆盖率（零 LLM）：anchored/total + 拆项；无论点返回 None。

    checks 来自 state 通道 ``debate_anchor_checks``（Task 2/3 落盘结构）。
    拆项桶名与 finance_agent.debate_anchors.anchor_stats（span 统计）一致——
    两处口径必须同源：none=推断无锚（允许）、missing=data/event 无锚（违规）、
    unresolved=锚未解析、unspecified=旧格式（无 kind 声明）。
    """
    valid = [c for c in checks if isinstance(c, dict)]
    if not valid:
        return None
    anchored = sum(1 for c in valid if c.get("anchored"))
    return {
        "value": round(anchored / len(valid), 4),
        "total": len(valid),
        "anchored": anchored,
        "unanchored_inference": sum(1 for c in valid if c.get("status") == "none"),
        "unresolved": sum(1 for c in valid if c.get("status") == "unresolved"),
        "missing_required": sum(1 for c in valid if c.get("status") == "missing"),
        "unspecified": sum(1 for c in valid if c.get("status") == "unspecified"),
    }


# 锚点状态 → 材料标记。必须按 status 取（unspecified 条目的 anchor_statuses
# 非权威，恒 unresolved），anchored 布尔只作覆盖计数。
_STATUS_MARK = {
    "resolved": "✓",
    "unresolved": "✗",
    "missing": "✗",
    "none": "○",
    "unspecified": "?",
}
# 骨架行分队：Layer II 多空各自一方，Layer IV 三方风险辩论者聚合为「风控」
# （delta spec: 「bull a/b｜bear c/d｜风控 e/f（含拆项）」）
_PARTY_ORDER = ("bull", "bear", "风控")
_PARTY_OF = {
    "bull": "bull",
    "bear": "bear",
    "aggressive": "风控",
    "conservative": "风控",
    "neutral": "风控",
}


def _party_coverage(checks: list) -> list[tuple[str, int, int]]:
    """各方 (party, anchored, total)；缺席方不出现，未登记角色（非辩手）忽略。"""
    totals: dict[str, tuple[int, int]] = {}
    for c in checks:
        party = _PARTY_OF.get(str(c.get("role")))
        if party is None:
            continue
        anchored, total = totals.get(party, (0, 0))
        totals[party] = (anchored + (1 if c.get("anchored") else 0), total + 1)
    return [(p, *totals[p]) for p in _PARTY_ORDER if p in totals]


def _summarize_debate(history: list, checks: list | None = None) -> str:
    # 每条发言上限（字节）：多轮【bull】【bear】交替时保证全部轮次可见——
    # 整体 head/tail 截断会把中间轮次连标签一起挖掉（judge 评「逐条交锋」时
    # 看不到交锋过程，2026-09-10 实测回归：2fd1ee6d 的【bear】标签被挖掉）。
    # 4 条发言 × 800 字节 ≈ 3200 < _JUDGE_MAX_BYTES(4096)，外层 _trunc 兜底不再命中。
    _MESSAGE_MAX_BYTES = 24000  # round7 审计：单条风险辩论消息达 20.9KB，6000 仍拦腰截断
    # 论点行上限：key_arguments 是每轮立场骨架（LLM 已结构化输出），截正文时骨架
    # 必须全数在场——judge 的「逐条回应对方论点」以论点行为对照锚点。
    _ARGUMENTS_MAX_BYTES = 2000

    checks = [c for c in (checks or []) if isinstance(c, dict)]
    by_key = {(c.get("role"), c.get("round"), c.get("index")): c for c in checks}
    cov = anchor_coverage(checks)

    parts: list[str] = []
    skeleton = _convergence_skeleton(history)
    if skeleton:
        parts.append(skeleton)
    # 锚点骨架行（确定性统计，数据来自 debate_anchor_checks 通道；旧 trace 无记录
    # 则不产出）：各方 anchored/total（缺席方省略）+ 全局拆项
    if cov:
        parties = _party_coverage(checks)
        if parties:
            parts.append(
                "【锚点覆盖】"
                + "｜".join(f"{party} {anchored}/{total}" for party, anchored, total in parties)
                + f"（推断无锚 {cov['unanchored_inference']}，未解析 {cov['unresolved']}，"
                f"data/event 无锚 {cov['missing_required']}，旧格式 {cov['unspecified']}）"
            )
    occurrence: dict[str, int] = {}
    for msg in history:
        msg = _as_dict(msg)
        if not msg:
            continue
        role = str(msg.get("role", "?"))
        occurrence[role] = occurrence.get(role, 0) + 1
        try:
            rnd = int(msg.get("round") or occurrence[role])
        except (TypeError, ValueError):
            rnd = occurrence[role]
        content = msg.get("content", "")
        raw_args = msg.get("key_arguments") or []
        # 论点行前缀 [kind mark]：按 (role, round, index) 命中检查记录后以 status
        # 取标记（index 用原始 1-based 位置，空文本项也占位）；无记录=旧路径仅文本
        items: list[str] = []
        for index, raw_arg in enumerate(raw_args, start=1):
            text = _argument_text(raw_arg)
            if not text:
                continue
            record = by_key.get((role, rnd, index))
            mark = _STATUS_MARK.get(str((record or {}).get("status"))) if record else None
            if record and mark:
                items.append(f"[{record.get('kind') or 'unspecified'} {mark}] {text}")
            else:
                items.append(text)
        arg_line = ""
        if items:
            joined = "; ".join(items)
            if len(joined.encode("utf-8")) > _ARGUMENTS_MAX_BYTES:
                joined = joined.encode("utf-8")[:_ARGUMENTS_MAX_BYTES].decode(
                    "utf-8", errors="ignore"
                )
            arg_line = f"论点: {joined}\n"
        parts.append(f"【{role}】{arg_line}{truncate_for_trace(content, _MESSAGE_MAX_BYTES)}")
    # 交锋覆盖率尾行（确定性指标，1.12）：有 rebuttal_to 数据时才追加
    coverage = _rebuttal_coverage(history)
    if coverage:
        parts.append(f"交锋覆盖: {coverage}")
    return "\n".join(parts)


def _structured_report_var(state: dict) -> str:
    """deep 报告的 judge 变量 `report`：结构化拼装，替代全文 head/tail 截断。

    实证（4f58faf7）：final_report 约 2 万字符且开头为图表节——4096 字节挖心后
    judge 仅见图表路径与审批章，从章节标题幻觉推断「全面覆盖」恒 5 分。结构化
    拼装（聚焦/分析师/RM/交易方案/FM）保证可读分析内容在场；图片路径（judge
    无法读取的纯噪声）天然不进入。拼装为空时回退 final_report 全文（兼容）。
    """
    parts: list[str] = []
    focus_summary = state.get("focus_summary") or ""
    if focus_summary:
        parts.append(f"【研究聚焦】{focus_summary}")
    analyst = _summarize_analyst_reports(state.get("analyst_reports") or {})
    if analyst:
        parts.append(f"【分析师结论】{analyst}")
    rm = state.get("research_manager_conclusion") or ""
    if rm:
        parts.append(f"【研究经理结论】{rm}")
    trade = _serialize_decision(state.get("final_trade_decision") or state.get("trader_plan"))
    if trade:
        parts.append(f"【交易方案】{trade}")
    fm_decision = state.get("fund_manager_decision") or ""
    if fm_decision:
        fm_action = state.get("fund_manager_action")
        fm_conf = state.get("fund_manager_confidence")
        qualifier = ""
        if fm_action:
            qualifier = f"（操作定性 {fm_action}"
            if fm_conf is not None:
                qualifier += f"，置信度 {fm_conf}"
            qualifier += "）"
        fm_reasoning = (state.get("fund_manager_decision_reasoning") or "").strip()
        fm_text = f"{fm_decision}{qualifier}"
        if fm_reasoning:
            fm_text += f"\n理由: {fm_reasoning}"
        parts.append(f"【基金经理决策】{fm_text}")
    return "\n".join(parts)


def extract_judge_vars(state: dict, query: str = "") -> dict[str, str]:
    """提取 judge 变量（11 个）,全字符串,缺失给 ""。"""
    raw_report = state.get("final_report") or ""
    report = _structured_report_var(state) or raw_report
    decision = state.get("final_trade_decision") or {}
    risk_debate = state.get("risk_debate_history") or []
    # 锚点检查记录（Task 3 通道）：多空 + 风控辩论的检查同通道累积；旧 trace 为空
    anchor_checks = state.get("debate_anchor_checks") or []
    risk_tail = _summarize_debate(risk_debate[-2:], anchor_checks) if risk_debate else ""
    decision_txt = _serialize_decision(decision)
    # D1：FM 操作定性随决策进 judge 变量——「approve 批准的是什么方向」直接可见
    fm_decision = state.get("fund_manager_decision") or ""
    fm_action = state.get("fund_manager_action")
    fm_conf = state.get("fund_manager_confidence")
    fm_parts: list[str] = []
    if fm_decision:
        qualifier = ""
        if fm_action:
            qualifier = f"（操作定性 {fm_action}"
            if fm_conf is not None:
                qualifier += f"，置信度 {fm_conf}"
            qualifier += "）"
        fm_parts.append(f"{fm_decision}{qualifier}")
    fm_reasoning = state.get("fund_manager_decision_reasoning")
    if fm_reasoning:
        fm_parts.append(f"理由: {fm_reasoning}")
    return {
        "query": query,
        "report": _trunc(report),
        # D3：报告结论取「研究聚焦」分析综合（focus_summary），回退全文标题提取
        # （旧行为=审批章复述，与 FM 节点逐字重复，consistency 无独立信号）
        "report_conclusion": _trunc(state.get("focus_summary") or extract_conclusion(raw_report)),
        "analyst_reports": _trunc(_summarize_analyst_reports(state.get("analyst_reports") or {})),
        "debate_history": _trunc(
            _summarize_debate(state.get("debate_history") or [], anchor_checks)
        ),
        "research_manager_decision": _trunc(state.get("research_manager_conclusion") or ""),
        # v8：Trader 原始方案（Layer III 输出）进 consistency 材料——
        # Trader 方案 → Risk Judge 裁决是否静默推翻此前无法核对（round8 代裁报告）
        "trader_plan": _trunc(_serialize_decision(state.get("trader_plan") or {})),
        # decision_grounding v6：被评的是 Risk Judge 裁决，其证据基础含风控指标与三方
        # 风险辩论——r1 复盘 8 条理由里 5 条抱怨风控数字无出处、3 条抱怨中性方论据无出处
        "risk_metrics": _format_risk_metrics(state.get("risk_metrics") or {}),
        "risk_debate_history": _trunc(_summarize_debate(risk_debate, anchor_checks)),
        "trade_decision": _trunc(decision_txt),
        "risk_judgment": _append_within_budget(decision_txt, risk_tail),
        # #111：FM 理由随决策进 judge 变量（consistency 维度可见否决依据）
        "fund_manager_decision": "\n".join(fm_parts),
    }


_RISK_METRIC_LABELS: tuple[tuple[str, str, bool], ...] = (
    # (state 键, 展示名, 是否百分比)
    ("max_drawdown", "最大回撤", True),
    ("volatility", "年化波动率", True),
    ("var_95", "VaR(95%)", True),
    ("sharpe_ratio", "夏普比率", False),
    ("beta", "beta", False),
)


def _format_risk_metrics(metrics: object) -> str:
    """风控指标 → 一行人读文本（已知键按中文名+百分比呈现，未知键原样附带）。"""
    if not isinstance(metrics, dict) or not metrics:
        return ""
    parts: list[str] = []
    seen: set[str] = set()
    for key, label, is_pct in _RISK_METRIC_LABELS:
        value = metrics.get(key)
        if value is None:
            continue
        seen.add(key)
        try:
            parts.append(
                f"{label} {float(value) * 100:.1f}%" if is_pct else f"{label} {float(value):.2f}"
            )
        except (TypeError, ValueError):
            parts.append(f"{label} {value}")
    for key, value in metrics.items():
        if key not in seen and value is not None:
            parts.append(f"{key} {value}")
    return "；".join(parts)


def _serialize_decision(decision: object) -> str:
    """把决策对象安全序列化为字符串。

    TradeDecision 为 pydantic 模型,json.dumps 直传会抛
    'Object of type TradeDecision is not JSON serializable'。
    pydantic 用 model_dump();dict 直传;其余 str() 兜底。
    长 reasoning 在对象内截断（而非序列化后由 _trunc 挖心）——r1 实证茅台裁决被
    挖掉 2349 字节后 JSON 残缺，材料人读化解析失败，标注人与 judge 看到原始残缺 JSON。
    """
    if not decision:
        return ""
    try:
        data = decision.model_dump() if hasattr(decision, "model_dump") else decision
        if isinstance(data, dict) and isinstance(data.get("reasoning"), str):
            data = {
                **data,
                "reasoning": truncate_for_trace(data["reasoning"], _DECISION_REASONING_MAX_BYTES),
            }
        return json.dumps(data, ensure_ascii=False)
    except (TypeError, ValueError):
        return str(decision)


# 裁决 reasoning 上限：与 evidence_refs（十余条 claim ≈ 1KB）合计留在 _JUDGE_MAX_BYTES 之内
_DECISION_REASONING_MAX_BYTES = 6000
