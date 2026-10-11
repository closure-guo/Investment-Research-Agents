"""5 层架构报告生成节点 - 汇总 Agent 输出为结构化 Markdown 报告。

从 state 读取 5 层 Agent 输出，组装为最终报告。
同时收集 chart_data 并生成 PNG 图表嵌入报告。

支持深度研究意图澄清环节收集的 focus：按用户关注点重排章节、
排序图表、折叠非重点章节，并在报告开头生成"研究聚焦"摘要。
"""

from __future__ import annotations

import contextlib
import os
import tempfile
from datetime import date, datetime
from typing import Any

from finance_agent.charts import collect_chart_data, generate_all_charts
from finance_agent.llm.gateway import complete_text
from finance_agent.llm.output_guard import validate_deliverable_text
from finance_agent.metric_vocab import render_date
from finance_agent.models import AnalystReport, DebateMessage, TradeDecision
from finance_agent.nodes.fund_manager import final_integrity_notes
from finance_agent.outcome.track_record.judgment import DEFAULT_HORIZON_DAYS

# ── focus -> 结构化标签（规则驱动，可测试） ──

_FOCUS_KEYWORDS: list[tuple[str, tuple[str, ...]]] = [
    ("valuation", ("估值", "pe", "pb", "贵", "便宜", "性价比", "合理")),
    ("growth", ("成长", "增速", "增长", "未来", "空间", "扩张")),
    ("technical", ("技术", "趋势", "均线", "量价", "突破", "支撑", "压力", "k线")),
    ("sentiment", ("舆情", "情绪", "新闻", "事件", "热度", "口碑", "负面")),
    ("macro", ("宏观", "政策", "行业", "景气", "周期", "利率")),
    ("risk", ("风险", "回撤", "波动", "安全", "下行", "隐患")),
    ("short_term", ("短期", "近期", "当下", "现在", "马上")),
    ("mid_long_term", ("中长期", "长期", "持有", "配置", "战略")),
]


def parse_focus_tags(focus: str) -> list[str]:
    """从自由文本 focus 解析出结构化标签。

    匹配规则：focus 文本（小写）包含任一关键词即命中该标签。
    返回去重后的标签列表，可能为空（表示通用/无明确侧重）。
    """
    if not focus:
        return []
    text = focus.lower()
    tags: list[str] = []
    for tag, keywords in _FOCUS_KEYWORDS:
        if any(kw in text for kw in keywords):
            tags.append(tag)
    return tags


def derive_focus_from_query(query: str) -> str:
    """focus 兜底（harden-decision-report-semantics D4）：从原始 query 提取命中的
    关注点中文关键词（去重保序，最多 4 个）合成弱 focus 文本。

    意图澄清未收集到 focus 时，保证用户角度仍以关键词形式进入各层 context；
    零命中返回空串（不硬造关注点）。原始 query 全文 MUST NOT 经此进入分析师层。
    """
    if not query:
        return ""
    low = query.lower()
    hits: list[str] = []
    for _tag, keywords in _FOCUS_KEYWORDS:
        for kw in keywords:
            if kw in low and kw not in hits:
                hits.append(kw)
    return "、".join(hits[:4])


# ── 图表 -> 关联标签（硬编码映射，可控可测） ──

_CHART_TAGS: dict[str, set[str]] = {
    "chart_revenue_profit": {"valuation", "growth"},
    "chart_growth": {"growth"},
    "chart_margin": {"valuation", "growth"},
    "chart_roe": {"valuation"},
    "chart_cashflow": {"valuation", "risk"},
    "chart_stock_price": {"technical", "short_term"},
    "chart_growth_vs_price": {"growth", "technical"},
    "chart_assets": {"valuation"},
    "chart_contract_liab": {"growth"},
    "chart_debt_ratio": {"risk"},
    "chart_heatmap": {"short_term", "technical"},
    "chart_dashboard": {"valuation", "growth"},
    "chart_market_share": {"growth", "macro"},
}


def _rank_charts(chart_names: list[str], focus_tags: list[str]) -> list[str]:
    """按 focus_tags 对图表名排序：命中的前置，未命中的后置，保持稳定。"""
    if not focus_tags:
        return chart_names
    ft = set(focus_tags)

    def score(name: str) -> int:
        return len(_CHART_TAGS.get(name, set()) & ft)

    return sorted(chart_names, key=lambda n: (-score(n), chart_names.index(n)))


# ── 分析师名称 -> 关联标签 ──

_ANALYST_TAGS: dict[str, set[str]] = {
    "technical": {"technical"},
    "macro": {"macro"},
    "fundamental": {"valuation", "growth"},
    "sentiment": {"sentiment"},
}

# 基金经理决策的中文标注（ADR-0011 Layer V 三种决策语义）
_FUND_MANAGER_ANNOTATIONS: dict[str, str] = {
    "approve": "审批通过",
    "reject": "未通过审批",
    "return": "已退回交易员重新评估",
}

# 置信度漂移披露阈值（update-decision-integrity-gates Task 4，spec「置信度漂移披露」）
_CONFIDENCE_DRIFT_THRESHOLD = 0.15
# 结构不完整标注的判定标记：note 含其一才算「不完整」——「打回后已申报」等复核性
# 标注属完整方案，零增量不渲染（spec Scenario「方案完整时不产生额外渲染」）
_FM_INCOMPLETE_MARKERS: tuple[str, ...] = ("仍未申报", "缺失")


def _fm_incomplete_integrity_block(state: dict) -> str:
    """审批对象结构不完整标注块（update-decision-integrity-gates Task 4）。

    从终稿五个完整性检查键（final_price_check/final_inaction_check/final_reeval_check/
    final_trigger_check 与 decision_price_gate 复核注，经 fund_manager.final_integrity_notes
    单源收集）中
    挑出标记「不完整」（_FM_INCOMPLETE_MARKERS 命中）的 note 原文逐项列出；
    无不完整标注返回空串（零增量，无空标注行——pass 复核注如「打回后已修正」不渲染）。
    """
    fragments = [
        f"{label}——{note}"
        for label, note in final_integrity_notes(state)
        if any(marker in note for marker in _FM_INCOMPLETE_MARKERS)
    ]
    if not fragments:
        return ""
    return f"> **审批对象结构不完整标注**：{'；'.join(fragments)}\n\n"


def _ruling_confidence(state: dict) -> float | None:
    """读终稿置信度（TradeDecision 对象或 dict 均可；缺失/噪声返回 None）。"""
    ruling = state.get("final_trade_decision")
    conf = (
        ruling.get("confidence")
        if isinstance(ruling, dict)
        else getattr(ruling, "confidence", None)
    )
    if isinstance(conf, bool) or not isinstance(conf, (int, float)):
        return None
    return float(conf)


# ── 财务口径披露（确定性渲染，不依赖 LLM 引用） ──


def _format_freshness_section(state: dict) -> str | None:
    """确定性渲染最新报告期快照 / 估值快照 / 健康度行业口径。

    spec industry-threshold-coverage「健康度评分行业口径披露」：报告渲染层 SHALL
    使读者可见评分所用口径。三轮 688072 实跑证明 LLM markdown 引用有方差
    （有数据不引用），故此处程序化渲染，不经过任何 LLM。
    三个数据源全缺时返回 None（不出现在报告中，零回归）。
    """
    lines: list[str] = []

    def _v(x) -> str:
        # 终审 M1：缺失字段不得渲染字面 None 进中文报告
        return "暂缺" if x is None else str(x)

    snap = state.get("latest_period_snapshot")
    if snap:
        missing = snap.get("missing") or []
        missing_note = f"；缺失项：{'、'.join(missing)}" if missing else ""

        def _item(label: str, val, unit: str = "") -> str:
            # clear-valuation-chain-debts D4：缺失渲染「<label> 暂缺」，
            # 单位后缀不跟在暂缺后面（「暂缺%」是文案病）
            return f"{label} 暂缺" if val is None else f"{label} {val}{unit}"

        lines.append(
            "- 最新报告期快照：{date}（{ptype}，利润表累计口径）— {gm}、{dr}、{inv}、{cl}，{rg}、{ng}{note}".format(
                date=_v(snap.get("报告日", "?")),
                ptype=_v(snap.get("期类型", "?")),
                gm=_item("毛利率", snap.get("毛利率(%)"), "%"),
                dr=_item("资产负债率", snap.get("资产负债率(%)"), "%"),
                inv=_item("存货", snap.get("存货"), " 亿"),
                cl=_item("合同负债", snap.get("合同负债"), " 亿"),
                rg=_item("营收同比", snap.get("营收同比(%)"), "%"),
                ng=_item("归母净利同比", snap.get("归母净利同比(%)"), "%"),
                note=missing_note,
            )
        )
        # add-banking-industry-calibration D6：信用减值损失（列存在即纳入，快照行
        # 内追加非新章节）——净利波动的真实驱动可溯源（银行主诉求：主动提储 vs
        # 资产质量恶化）；字段缺席时零变化
        imp = snap.get("信用减值损失")
        imp_yoy = snap.get("信用减值损失同比(%)")
        if imp is not None:
            imp_seg = f"信用减值损失 {imp} 亿"
            if imp_yoy is not None:
                imp_seg += f"（同比 {imp_yoy}%）"
            lines[-1] += f"、{imp_seg}"

    vsnap = state.get("valuation_snapshot")
    if vsnap:
        reasons = vsnap.get("missing_reasons") or []
        if reasons:
            lines.append(f"- 估值数据缺失（{'；'.join(reasons)}），无法判断贵贱")
        else:
            pe_caliber = vsnap.get("PE_caliber")
            caliber_note = {
                "static": "主源静态口径",
                "derived_ttm": "TTM 推导口径",
            }.get(pe_caliber, "口径未知")
            pe_disp = vsnap.get("PE") if pe_caliber == "static" else vsnap.get("PE_ttm")
            lines.append(
                f"- 估值快照：市值 {vsnap.get('market_cap')} 亿、"
                f"PE {pe_disp}（{caliber_note}）、PB {vsnap.get('PB')}"
            )

    health = state.get("health_score")
    if health:
        override = health.get("industry_override") or {}
        industry = override.get("industry")
        metrics = override.get("metrics") or []
        if industry and metrics:
            caliber_line = f"行业口径：{industry}（行业阈值覆盖：{'、'.join(metrics)}）"
        else:
            caliber_line = "通用口径（无行业阈值覆盖）"
        # add-banking-industry-calibration D2：行业剔除维度后满分 <100，
        # 不呈现满分会让读者按 100 分制误读（16.67/50 vs 16.67/100 语义完全不同）
        cap = health.get("score_cap")
        cap_note = f"，满分 {cap}" if isinstance(cap, (int, float)) and cap != 100 else ""
        lines.append(
            f"- 财务健康度：{health.get('total')} 分（{health.get('rating')}{cap_note}），评分采用 {caliber_line}"
        )

    # add-risk-metrics-disclosure（issue #242 断点 1）：风控链路确定性计算
    # （risk_metrics / price_levels）与决策数字同源，此处程序渲染使报告内可溯源。
    risk = state.get("risk_metrics")
    if risk:

        def _pct(label: str, key: str) -> str:
            v = risk.get(key)
            return f"{label} 暂缺" if v is None else f"{label} {v * 100:.2f}%"

        risk_line = "- 风险指标（基于日 K 线与基准历史计算）：{vol}、{mdd}、{var}".format(
            vol=_pct("年化波动率", "volatility"),
            mdd=_pct("最大回撤", "max_drawdown"),
            var=_pct("VaR95", "var_95"),
        )
        beta = risk.get("beta")
        if beta is not None:
            # beta 缺席 = 基准不可得（数据语义），省略该段而非渲染「暂缺」
            risk_line += f"、beta {beta}"
        lines.append(risk_line)

    levels = state.get("price_levels")
    if levels:
        if levels.get("available"):
            stop = levels.get("stop_band_long") or {}
            target = levels.get("target_band_long") or {}
            lines.append(
                "- 价位参考（基于近期 K 线结构计算）：入场参考 {entry}、"
                "止损带 {s_low}–{s_high}、目标带 {t_low}–{t_high}".format(
                    entry=_v(levels.get("entry_ref")),
                    s_low=_v(stop.get("low")),
                    s_high=_v(stop.get("high")),
                    t_low=_v(target.get("low")),
                    t_high=_v(target.get("high")),
                )
            )
        else:
            lines.append(f"- 价位参考不可用（{levels.get('reason') or 'unknown'}）")

    if not lines:
        return None
    body = "\n".join(lines)
    # clear-valuation-chain-debts D4：返回纯正文（无标题）——标题由 generate_report
    # 经 next_title 编号注入，避免裸「###」插在「##」编号章节之间破坏导出切章
    return f"以下为管线确定性计算的数据快照与评分口径（非 LLM 生成，供交叉核对）：\n\n{body}\n"


# ── 距上次报告增量摘要 / 多空辩论分歧卡（add-report-revision-view）──
# 全链路零 LLM 调用：增量摘要 = 一次回溯查询（入口注入 state
# `previous_report_snapshot`）+ 确定性 diff 渲染。纪律见 delta spec
# report-decision-rendering：全维度基于结构化持久化数据，MUST NOT 解析
# 上一报告 markdown 文本提取数字；缺失维度如实「未申报」。


def _diff_scalar(old: object, new: object) -> str:
    """标量维度 diff：任一侧缺失 → 「未申报」；相等 → 「旧 → 新（无变化）」。"""
    if old is None or old == "" or new is None or new == "":
        return "未申报"
    if str(old) == str(new):
        return f"{old} → {new}（无变化）"
    return f"{old} → {new}"


def _diff_number(old: object, new: object, *, price: bool = False) -> str:
    """数值维度 diff（现价/PE/置信度，统一 %.2f）：任一侧缺失/非法 → 「未申报」。

    price=True 时按 _fmt_price 同口径拒绝 ≤0（正常价位不可能 ≤0）；
    PE 允许负值（亏损股），仅 None/非数值为缺失。
    """
    try:
        old_v = float(old) if old is not None else None  # type: ignore[arg-type]
        new_v = float(new) if new is not None else None  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return "未申报"
    if old_v is None or new_v is None:
        return "未申报"
    if price and (old_v <= 0 or new_v <= 0):
        return "未申报"
    if round(old_v, 2) == round(new_v, 2):
        return f"{old_v:.2f} → {new_v:.2f}（无变化）"
    return f"{old_v:.2f} → {new_v:.2f}"


def _num_or_unreported(value: object) -> str:
    """复合维度组内单项：None/非法 → 「未申报」词形（对齐触发位行纪律）。"""
    try:
        v = float(value) if value is not None else None  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return "未申报"
    return "未申报" if v is None or v <= 0 else f"{v:.2f}"


def _level_repr(decision: dict) -> str | None:
    """触发位/价位复合维度的单侧表示：双向触发位优先（watch 申报参数），
    否则入场/止损/目标价三元组；两侧字段全缺返回 None（该侧「未申报」）。
    术语对齐交易决策节：上破=trigger_high、下破=trigger_low。"""
    tl, th = decision.get("trigger_low"), decision.get("trigger_high")
    if tl is not None or th is not None:
        return f"上破 {_num_or_unreported(th)} / 下破 {_num_or_unreported(tl)}"
    entry, stop, target = (
        decision.get("entry_price"),
        decision.get("stop_loss"),
        decision.get("target_price"),
    )
    if entry is not None or stop is not None or target is not None:
        return (
            f"入场 {_num_or_unreported(entry)} · 止损 {_num_or_unreported(stop)}"
            f" · 目标 {_num_or_unreported(target)}"
        )
    return None


def _diff_levels(old_dec: dict, new_dec: dict) -> str:
    """触发位/价位维度 diff：任一侧无任何价位字段 → 「未申报」；
    表示一致 → 「无变化」（复合维度不堆值，对齐 spec 场景）。"""
    old_repr = _level_repr(old_dec)
    new_repr = _level_repr(new_dec)
    if old_repr is None or new_repr is None:
        return "未申报"
    if old_repr == new_repr:
        return "无变化"
    return f"{old_repr} → {new_repr}"


def _decision_as_dict(decision: object) -> dict:
    """终稿/trader plan 归一为 dict：真实管线 state 中是 TradeDecision pydantic
    对象（非 dict），直接 .get 会 AttributeError（本地实跑 601066 实证）。"""
    if isinstance(decision, dict):
        return decision
    if hasattr(decision, "model_dump"):
        dumped: dict = decision.model_dump()
        return dumped
    return {}


def _format_revision_summary(state: dict, current_kpi: dict) -> str | None:
    """「距上次报告（YYYY-MM-DD）」增量摘要节（纯文本正文，标题自带）。

    previous_report_snapshot 由入口（api fast path / ReAct 工具路径）经
    session_store.get_previous_completed_session 注入；None/缺失 = 首份报告，
    整节不渲染。五个维度：决策方向/置信度/触发位（或价位）/现价/PE；
    全部维度均不可得时整节不渲染。
    """
    prev = state.get("previous_report_snapshot")
    if not isinstance(prev, dict):
        return None
    prev_date = str(prev.get("created_at") or "")[:10]
    if len(prev_date) != 10:
        return None  # 无可靠日期无法如实命名节标题，整节不渲染

    old_dec = _decision_as_dict(prev.get("final_trade_decision"))
    new_dec = _decision_as_dict(state.get("final_trade_decision") or state.get("trader_plan"))
    old_kpi = prev.get("kpi") or {}

    rows = [
        ("决策方向", _diff_scalar(old_dec.get("action"), new_dec.get("action"))),
        ("置信度", _diff_number(old_dec.get("confidence"), new_dec.get("confidence"))),
        ("触发位/价位", _diff_levels(old_dec, new_dec)),
        (
            "现价",
            _diff_number(
                old_kpi.get("current_price"), current_kpi.get("current_price"), price=True
            ),
        ),
        ("PE", _diff_number(old_kpi.get("pe"), current_kpi.get("pe"))),
    ]
    if all(value == "未申报" for _, value in rows):
        return None
    body = "\n".join(f"- {label}: {value}" for label, value in rows)
    return f"## 距上次报告（{prev_date}）\n\n{body}\n"


def _format_divergence_card(state: dict) -> str | None:
    """多空辩论结论分歧卡：评级 · 置信度 · 结论首句（第一个完整句）。

    评级/置信度取自研究经理结构化输出字段；缺失（历史会话/解析降级）时
    返回 None，该节按现状纯文本渲染，MUST NOT 编造评级。
    """
    rating = state.get("research_manager_rating")
    confidence = state.get("research_manager_confidence")
    if not rating or not isinstance(confidence, (int, float)) or isinstance(confidence, bool):
        return None
    conclusion = str(state.get("research_manager_conclusion") or "")
    # 结构化解析成功时 conclusion 首行为前置的「评级: …（置信度 …）」，首句取正文
    body = conclusion.split("\n", 1)[1] if conclusion.startswith("评级:") else conclusion
    body = body.strip()
    if not body:
        return None
    first_sentence = _truncate_at_sentence(body, limit=120)
    return f"评级: {rating} · 置信度 {confidence:.2f} · 分歧焦点: {first_sentence}"


# ── 研究聚焦摘要（LLM 生成，有兜底） ──


def _request_config_dict(llm_config: Any, api_key: str | None) -> dict | None:
    """请求级 llm_config（dict / LLMConfig）→ gateway 请求级 dict。

    复刻 legacy._request_config_dict 语义（5.1-B2 薄壳适配）：
    - 无 model → None（complete_text 经 env/preset 解析）
    - baseUrl 缺 → env LLM_BASE_URL；apiKey 缺 → cfg.apiKey → api_key 参数
      → LLM_API_KEY → DEEPSEEK_API_KEY（镜像 legacy _build_kwargs 回退链）
    - thinking 仅在显式设置时携带
    """
    if isinstance(llm_config, dict):
        model = llm_config.get("model")
        base_url = llm_config.get("baseUrl")
        key = llm_config.get("apiKey")
        thinking = llm_config.get("thinking")
        api_form = llm_config.get("apiForm")
        context_length = llm_config.get("contextLength")
    elif llm_config is not None:
        # LLMConfig dataclass（camelCase 字段）
        model = getattr(llm_config, "model", None)
        base_url = getattr(llm_config, "baseUrl", None)
        key = getattr(llm_config, "apiKey", None)
        thinking = getattr(llm_config, "thinking", None)
        api_form = getattr(llm_config, "apiForm", None)
        context_length = getattr(llm_config, "contextLength", None)
    else:
        return None
    if not model:
        return None
    cfg: dict = {"model": model}
    effective_base = base_url or os.environ.get("LLM_BASE_URL", "")
    if effective_base:
        cfg["baseUrl"] = effective_base
    effective_key = (
        key or api_key or os.environ.get("LLM_API_KEY") or os.environ.get("DEEPSEEK_API_KEY") or ""
    )
    if effective_key:
        cfg["apiKey"] = effective_key
    if thinking:
        cfg["thinking"] = thinking
    if api_form:
        cfg["apiForm"] = api_form
    if context_length is not None:
        cfg["contextLength"] = context_length
    return cfg


# 研究聚焦摘要字数约束（Task3-c 去重：基础 system 与重试强化指令同源引用）
_FOCUS_LEN_HINT = "150-200 字"


def _truncate_at_sentence(text: str, limit: int = 200) -> str:
    """句界截断（新增-2）：len ≤ limit 原样；否则在 text[:limit] 窗口内找最后
    一个「。」，找到则切到该句号（含），找不到才硬切 limit——兜底文本不在
    句中腰斩，读者不读到半句。"""
    if len(text) <= limit:
        return text
    window = text[:limit]
    cut = window.rfind("。")
    if cut == -1:
        return window
    return window[: cut + 1]


def _build_focus_summary(state: dict, focus: str, focus_tags: list[str]) -> str:
    """用 LLM 生成围绕用户关注点的开篇摘要，失败时回退到结构化拼接。"""
    api_key = state.get("api_key")
    stock_name = state.get("stock_name", "N/A")

    # 汇聚各层关键输出作为摘要素材
    materials: list[str] = []
    reports = state.get("analyst_reports") or {}
    for name, report in reports.items():
        summary = (
            report.summary if hasattr(report, "summary") else (report or {}).get("summary", "")
        )
        if summary:
            materials.append(f"[{name}] {summary}")
    conclusion = state.get("research_manager_conclusion")
    if conclusion:
        materials.append(f"[研究经理] {conclusion}")
    decision = state.get("final_trade_decision") or state.get("trader_plan")
    if decision:
        action = (
            decision.action if hasattr(decision, "action") else (decision or {}).get("action", "")
        )
        reasoning = (
            decision.reasoning
            if hasattr(decision, "reasoning")
            else (decision or {}).get("reasoning", "")
        )
        materials.append(f"[交易决策] {action}: {reasoning}")

    if not materials:
        return ""

    tags_desc = "、".join(t for t in focus_tags) or "综合"
    system = (
        f"你是投研报告编辑。根据用户关注点和各层分析产出，写一段 {_FOCUS_LEN_HINT}的研究聚焦摘要，"
        "紧扣用户关注点组织语言，点出最关键的结论与数据。纯文本，不使用 emoji，不输出标题。"
        "内容仅基于所提供材料中的数据组织，不得引入材料外的数值或推测。"
        "引用财务数据时使用材料中的最新披露期次；当最新期次与历史趋势方向相反时"
        "（如年报口径连续下滑而最新中报回升），MUST 并列呈现两期状态（如「年报连续下滑，"
        "但最新中报已回升至 X%」），MUST NOT 只保留单边半句。"
        "同一数值在年报/单季等多个期次并存时（如 Q1 单季毛利率恰等于上年年报毛利率），"
        "引用 MUST 显式标注期次，MUST NOT 裸引数值。"
    )
    focus_line = f"用户关注点: {focus}\n" if focus else "（用户未指定关注点，请综合各维度要点）\n"
    prompt = (
        f"股票: {stock_name}\n{focus_line}关注维度: {tags_desc}\n\n"
        f"各层分析产出:\n" + "\n".join(materials)
    )

    def _call(system_extra: str = "", trace_meta: dict | None = None) -> tuple[str, dict]:
        text, meta = complete_text(
            [
                {"role": "system", "content": system + system_extra},
                {"role": "user", "content": prompt},
            ],
            purpose="quick",
            max_tokens=400,
            temperature=0.3,
            llm_config=_request_config_dict(state.get("llm_config"), api_key),
            trace=trace_meta or {"name": "report", "metadata": {"agent": "report"}},
            output_guard={"target_lang": "zh"},
        )
        return (text or "").strip(), meta

    with contextlib.suppress(Exception):
        resp, meta = _call()
        # 调用侧判定（gateway 侧 output_guard 仅承担观测遥测；决策语义在本层）。
        # 判定路径统一（Task3-b）：空文本交由校验器判 empty，不再调用侧短路
        if validate_deliverable_text(resp or "", finish_reason=meta.get("finish_reason")).ok:
            return resp
        # 违约（泄露/截断/空）→ 定向重试 1 次（incident 036：glm-5.3 间歇性
        # 把任务独白写进正文；强化指令禁止思考输出）
        retry_resp, retry_meta = _call(
            f"\n\n重要：直接输出最终摘要文本。禁止输出任务理解、要点清单、"
            f"约束重述、Draft 标记或任何思考过程；只输出面向读者的 {_FOCUS_LEN_HINT}中文摘要。",
            {"name": "report", "metadata": {"agent": "report", "retry": 1}},
        )
        if validate_deliverable_text(
            retry_resp or "", finish_reason=retry_meta.get("finish_reason")
        ).ok:
            return retry_resp

    # 兜底：取首个分析师 summary 句界截断（新增-2）
    fallback = materials[0] if materials else ""
    return _truncate_at_sentence(fallback)


# ── 报告主函数 ──


def generate_report(state: dict) -> dict:
    """汇总 5 层 Agent 输出，生成最终 Markdown 报告 + 图表数据。

    当 state 含非空 focus 时，按用户关注点重排章节与图表：
    - 命中的分析师报告前置并标记 ★ 重点
    - 未命中的分析师报告折叠进 <details>
    - 图表按关联度排序
    - 报告开头追加"研究聚焦"摘要
    focus 为空时退化为固定结构，零回归。
    """
    stock_name = state.get("stock_name", "N/A")
    stock_code = state.get("stock_code", "N/A")
    today = datetime.now().date()  # add-watch-trigger-tracking：报告日（真空提示锚点）
    focus = (state.get("focus") or "").strip()
    focus_tags = parse_focus_tags(focus)
    has_focus = bool(focus_tags)

    # ── 收集图表数据 ──
    chart_data = collect_chart_data(state)

    # ── 生成 PNG 图表 ──
    stock_code_safe = "".join(c for c in stock_code if c.isalnum()) or "unknown"
    charts_dir = os.path.join(tempfile.gettempdir(), "finance_charts", stock_code_safe)
    chart_paths = generate_all_charts(chart_data, charts_dir)

    all_chart_titles = [
        ("chart_revenue_profit", "营业收入与归母净利润"),
        ("chart_growth", "同比增速"),
        ("chart_margin", "毛利率与净利率"),
        ("chart_roe", "ROE 变化趋势"),
        ("chart_cashflow", "经营现金流净额"),
        ("chart_stock_price", "股价趋势"),
        ("chart_growth_vs_price", "财务增速 vs 股价涨幅"),
        ("chart_assets", "总资产与归母权益"),
        ("chart_contract_liab", "合同负债"),
        ("chart_debt_ratio", "资产负债率趋势"),
        ("chart_heatmap", "年报报告期窗口股价变化"),
        ("chart_dashboard", "财务指标综合仪表盘"),
        ("chart_market_share", "全球市场份额"),
    ]

    sections: list[str] = [
        f"# {stock_name}({stock_code}) 投资分析报告",
        f"\n*报告日期: {today.isoformat()}" + (f" · 研究聚焦: {focus}*\n" if has_focus else "*\n"),
    ]

    # 数据新鲜度披露（update-report-data-disclosure）：头部标注行情最后交易日，
    # 读者可从成稿判断数据新鲜度（假期生成报告用的是节前数据）；kline 缺失时
    # 省略而非回退到报告生成日期（不伪造新鲜度）
    kline_df = state.get("kline")
    # add-watch-trigger-tracking：行情截止日（date 形态）随 kline 解析，供交易决策节
    # 数据真空提示使用；kline 缺失/解析失败时 None（与头部截止行同取值，不伪造新鲜度）
    data_cutoff: date | None = None
    if kline_df is not None and not kline_df.empty and "日期" in kline_df.columns:
        cutoff = render_date(kline_df["日期"].iloc[-1])
        data_cutoff = _parse_cutoff_date(cutoff)
        sections[1] = sections[1].replace("*\n", f" · 行情数据截止: {cutoff}*\n", 1)

    seq = 0  # 章节序号计数器，统一管理编号，避免硬编码错位

    def next_title(label: str) -> str:
        nonlocal seq
        seq += 1
        return f"## {_cn_num(seq)}、{label}\n"

    # ── 研究聚焦摘要（judge 变量 focus_summary 的数据源，D3）──
    # 渲染幂等（2026-09-19）：state 已有非空摘要时复用（重渲染不重烧；评估外科手术臂
    # 据此冻结导语），无预置值时照旧生成——首跑行为零变化
    summary = str(state.get("focus_summary") or "").strip() or _build_focus_summary(
        state, focus, focus_tags
    )
    if summary:
        sections.append(f"## 研究聚焦\n\n{summary}\n")
    # add-report-revision-view：报告头「距上次报告」增量摘要（研究聚焦之后，
    # 未编号 ## 与研究聚焦同为头部元素，不占章节序号）；首份报告/全维度缺失不渲染
    revision_section = _format_revision_summary(state, chart_data.get("kpi") or {})
    if revision_section:
        sections.append(revision_section)
    # ── 图表：按 focus 排序，分重点/完整两组 ──
    if chart_paths:
        ordered = _rank_charts([c for c, _ in all_chart_titles], focus_tags)
        title_map = dict(all_chart_titles)

        if has_focus:
            ft = set(focus_tags)
            primary = [c for c in ordered if _CHART_TAGS.get(c, set()) & ft and chart_paths.get(c)]
            rest = [
                c for c in ordered if not (_CHART_TAGS.get(c, set()) & ft) and chart_paths.get(c)
            ]

            if primary:
                sections.append(next_title("重点图表（围绕研究聚焦）"))
                for chart_name in primary:
                    title = title_map.get(chart_name, chart_name)
                    path = chart_paths.get(chart_name)
                    if path:
                        sections.append(f"### {title}\n")
                        sections.append(f"![{title}]({path})\n")
                sections.append("---\n")

            if rest:
                sections.append(next_title("其他图表"))
                sections.append("<details><summary>点击展开完整图表</summary>\n\n")
                for chart_name in rest:
                    title = title_map.get(chart_name, chart_name)
                    path = chart_paths.get(chart_name)
                    if path:
                        sections.append(f"### {title}\n")
                        sections.append(f"![{title}]({path})\n")
                sections.append("</details>\n\n---\n")
        else:
            sections.append(next_title("核心财务指标图表"))
            for chart_name in ordered:
                title = title_map.get(chart_name, chart_name)
                path = chart_paths.get(chart_name)
                if path:
                    sections.append(f"### {title}\n")
                    sections.append(f"![{title}]({path})\n")
            sections.append("---\n")

    # ── 分析师报告：按 focus 重排/折叠 ──
    reports = state.get("analyst_reports") or {}
    if reports:
        if has_focus:
            ft = set(focus_tags)
            primary_names = [n for n in reports if _ANALYST_TAGS.get(n, set()) & ft]
            rest_names = [n for n in reports if not (_ANALYST_TAGS.get(n, set()) & ft)]

            if primary_names:
                sections.append(next_title("重点分析（围绕研究聚焦）"))
                for name in primary_names:
                    sections.append(_format_analyst_report(name, reports[name], star=True))

            if rest_names:
                sections.append(next_title("其他分析维度"))
                sections.append("<details><summary>点击展开非重点分析师报告</summary>\n\n")
                for name in rest_names:
                    sections.append(_format_analyst_report(name, reports[name]))
                sections.append("</details>\n")
        else:
            sections.append(next_title("分析师团队报告"))
            for name, report in reports.items():
                sections.append(_format_analyst_report(name, report))

    # ── 同业对比（add-peer-comparison R4：确定性渲染，不依赖分析师 markdown 正文转述）──
    peer_section = _format_peer_section(state)
    if peer_section:
        sections.append(f"{next_title('同业对比')}\n{peer_section}\n")

    # ── 后续固定章节（编号自动顺延） ──
    freshness_section = _format_freshness_section(state)
    if freshness_section:
        # D4：披露节并入编号章节体系（export 按 level-2 切章，裸 ### 会破坏章节结构）
        sections.append(f"{next_title('财务数据口径披露')}\n{freshness_section}\n")

    conclusion = state.get("research_manager_conclusion")
    if conclusion:
        # add-report-revision-view：分歧卡（评级·置信度·结论首句）置于结论文本之前；
        # 结构化评级缺失时退化为纯文本现状渲染，不编造评级
        divergence_card = _format_divergence_card(state)
        conclusion_body = f"{divergence_card}\n\n{conclusion}" if divergence_card else conclusion
        sections.append(f"{next_title('多空辩论结论')}\n{conclusion_body}\n")

    decision = state.get("final_trade_decision") or state.get("trader_plan")
    if decision:
        # update-decision-price-gate：报警仅进 trace，渲染链不接收 anomalies
        # add-watch-trigger-tracking：行情截止日/入池声明（FM approve）/报告日进渲染
        # ——数据真空提示与入池跟踪声明的数据源
        decision_md = _format_trade_decision(
            decision,
            data_cutoff=data_cutoff,
            fund_approved=state.get("fund_manager_decision") == "approve",
            report_date=today,
        )
        sections.append(f"{next_title('交易决策')}\n{decision_md}\n")

    risk_history = state.get("risk_debate_history") or []
    if risk_history:
        sections.append(next_title("风控辩论"))
        for msg in risk_history:
            sections.append(_format_debate_message(msg))

    fm_decision = state.get("fund_manager_decision")
    if fm_decision:
        # 中文标注呈现（ADR-0011 Layer V）：reject 需明确标注「未通过审批」，
        # 而非仅显示原始英文枚举值。未命中时回退原始值，容忍加固前写入的历史非法值
        annotation = _FUND_MANAGER_ANNOTATIONS.get(fm_decision, fm_decision)
        # 审批对象结构不完整标注（update-decision-integrity-gates Task 4）：先渲染
        # 标注再渲染 FM 审批意见——「方案事实」与「FM 论断」的矛盾直接可见
        # （spec「报告并排渲染不完整标注与 FM 论断」）；方案完整时空串零增量
        integrity_block = _fm_incomplete_integrity_block(state)
        # #111：审批理由随决策渲染（在场时）；缺失时保持仅标注（历史 state 兼容）
        fm_reasoning = (state.get("fund_manager_decision_reasoning") or "").strip()
        # D1：FM 操作定性（action/置信度）与裁决 action 并排展示——「批准的是什么
        # 方案」直接可见，方向相悖时矛盾自明；历史 state 无字段时保持旧行为
        fm_confidence = state.get("fund_manager_confidence")
        qualifier = ""
        fm_action = state.get("fund_manager_action")
        if fm_action:
            qualifier = f"（操作定性 {fm_action}"
            if fm_confidence is not None:
                qualifier += f"，置信度 {fm_confidence}"
            qualifier += "）"
            ruling = state.get("final_trade_decision") or {}
            ruling_action = (
                ruling.get("action")
                if isinstance(ruling, dict)
                else getattr(ruling, "action", None)
            )
            if ruling_action:
                qualifier += f" · 裁决: {ruling_action}"
        # 置信度漂移披露（update-decision-integrity-gates Task 4，spec「置信度漂移
        # 披露」）：仅 approve 产漂移标注（reject/return 无操作定性可比），偏差
        # >0.15 时在操作定性旁渲染两值；MUST NOT 硬拦截，FM reasoning 全文本就
        # 随决策渲染供标注人判读
        drift = ""
        if (
            fm_decision == "approve"
            and isinstance(fm_confidence, (int, float))
            and not isinstance(fm_confidence, bool)
        ):
            ruling_conf = _ruling_confidence(state)
            if (
                ruling_conf is not None
                and round(abs(fm_confidence - ruling_conf), 6) > _CONFIDENCE_DRIFT_THRESHOLD
            ):
                drift = f" · 置信度漂移：FM {fm_confidence:g} / 终稿 {ruling_conf:g}"
        reasoning_block = f"\n\n{fm_reasoning}\n" if fm_reasoning else "\n"
        sections.append(
            f"{next_title('基金经理决策')}\n\n{integrity_block}**{annotation}**{qualifier}{drift}{reasoning_block}"
        )

    # ── 参考资料信源（Kimi 风格 URL 引用溯源）──
    # 信源列表在前端以卡片形式展示，报告 Markdown 中不再重复列出
    # web_sources 通过 state 传入前端 report_ready 事件的 web_sources 字段

    return {
        "final_report": "\n".join(sections),
        "focus_summary": summary,
        "chart_data": chart_data,
    }


_CN_NUMS = [
    "",
    "一",
    "二",
    "三",
    "四",
    "五",
    "六",
    "七",
    "八",
    "九",
    "十",
    "十一",
    "十二",
    "十三",
    "十四",
    "十五",
]


def _cn_num(n: int) -> str:
    """1-15 -> 中文数字。"""
    if 1 <= n < len(_CN_NUMS):
        return _CN_NUMS[n]
    return str(n)


_RELATIVE_CONCLUSION_ZH = {"undervalued": "偏低", "fair": "合理", "overvalued": "偏高"}


def _format_peer_section(state: dict) -> str:
    """同业对比段：确定性渲染 state.peer_comparison 对照材料（add-peer-comparison R4）。

    分析师 markdown 正文不进报告（摘要式渲染为既有设计），对照表由代码直出，
    「报告含同业对比段」不依赖 LLM 转述；材料自带口径标注与缺失标记（compute 层），
    缺失声明与相对估值结论在此追加。
    """
    peer_md = state.get("peer_comparison")
    lines: list[str] = []
    if isinstance(peer_md, str) and peer_md.strip():
        lines.append(peer_md.strip())
    elif state.get("peer_codes"):
        lines.append("同业数据不可用（抓取失败或未执行），无法提供同业对比；不作对比结论。")
    rval = state.get("relative_valuation")
    if isinstance(rval, dict):
        for metric in ("PE", "PB"):
            d = rval.get(metric)
            if not isinstance(d, dict) or d.get("conclusion") not in _RELATIVE_CONCLUSION_ZH:
                continue
            if d.get("target") is None or d.get("peer_avg") is None:
                continue
            lines.append(
                f"- 相对估值（{metric}）：主标的 {d['target']} vs 同业均值 {d['peer_avg']}"
                f" → 相对同业{_RELATIVE_CONCLUSION_ZH[d['conclusion']]}"
            )
    return "\n".join(lines)


def _format_analyst_report(name: str, report: AnalystReport | dict, star: bool = False) -> str:
    """格式化单个分析师报告。star=True 时在标题加 ★ 重点标记。"""
    if isinstance(report, AnalystReport):
        summary = report.summary
        findings = report.key_findings
    else:
        summary = report.get("summary", "")
        findings = report.get("key_findings", [])

    title = f"### {name}" + ("（★ 重点）" if star else "")
    lines = [f"{title}\n", f"{summary}\n"]
    if findings:
        lines.append("**关键发现：**\n")
        for f in findings:
            lines.append(f"- {f}")
        lines.append("")
    return "\n".join(lines)


def _fmt_price(value: object) -> str:
    """价位渲染：0/None/负值一律「未提供」（report-render-operational-params：
    不以占位数据冒充有效价位；正常价位不可能 ≤0，做空语义由 action 表达）。"""
    try:
        v = float(value) if value is not None else None  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return "未提供"
    if v is None or v <= 0:
        return "未提供"
    return f"{v:g}"


# add-watch-trigger-tracking：行情截止与报告日最大可接受间隔（自然日），超出即提示
# 数据真空（跳空缺口可能使触发条件失真）。间隔阈值 SHALL 为配置项——env 覆盖默认 3；
# 非法 env 值回退默认，不因配置错误炸渲染链（OUTCOME_DEFAULT_HORIZON_DAYS 同款先例）
try:
    _DATA_VACUUM_THRESHOLD_DAYS = int(os.getenv("REPORT_DATA_VACUUM_THRESHOLD_DAYS", "3"))
except (TypeError, ValueError):
    _DATA_VACUUM_THRESHOLD_DAYS = 3


def _fmt_trigger_level(value: object) -> str:
    """watch 触发位渲染：数值口径与入场/止损/目标价行一致（_fmt_price 的 :g），
    缺失/非法如实「未申报」——与建仓参数的「未提供」词形刻意区分
    （add-watch-trigger-tracking：触发位是 watch 决策专属的申报参数）。
    MUST NOT 从 reeval_triggers 文本解析回填。
    """
    rendered = _fmt_price(value)
    return "未申报" if rendered == "未提供" else rendered


def _parse_cutoff_date(value: str) -> date | None:
    """行情截止串（render_date 产物 YYYY-MM-DD）→ date；解析失败返回 None（不炸渲染）。"""
    try:
        return date.fromisoformat(value.strip())
    except (AttributeError, ValueError):
        return None


def _fmt_derived_metrics(action: str, entry: object, stop: object, target: object) -> str:
    """派生指标行（buy/sell）：止损距离与赔率由代码按参数原值计算。

    MUST NOT 采用 reasoning 中 LLM 自算数值（round7 校准：心算值无校验，
    代码计算是唯一真源）。任一参与数缺失/为 0/除零 → 对应指标省略。
    """
    try:
        e = float(entry) if entry is not None else None  # type: ignore[arg-type]
        s = float(stop) if stop is not None else None  # type: ignore[arg-type]
        t = float(target) if target is not None else None  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return ""
    if not e or not s or e == s:
        return ""
    stop_pct = abs(e - s) / e
    parts = [f"止损距离 {stop_pct:.1%}"]
    if t and t > 0:
        # buy：赚 t-e / 亏 e-s；sell：赚 e-t / 亏 s-e（合法价位关系下均为正）
        reward = (t - e) if action == "buy" else (e - t)
        risk = (e - s) if action == "buy" else (s - e)
        if risk > 0:
            parts.append(f"赔率 {abs(reward) / risk:.2f}:1")
    return "- **派生指标**（代码计算）: " + "、".join(parts)


_TRIGGER_MARKS = "①②③④⑤⑥⑦⑧⑨⑩"

# 仓位档位词表（update-decision-integrity-gates Task 2，spec
# report-decision-rendering「参数缺失时诚实标注」MODIFIED）
_POSITION_VOCAB = frozenset({"light", "moderate", "heavy"})


def _fmt_position_size(value: object) -> str:
    """仓位档位渲染：词表 light/moderate/heavy 大小写不敏感。

    None/空串/不在词表的非法字面量（600515 的 "none"、"null" 等）渲染前归一为缺失
    「未提供」，不以 LLM 原始字面量冒充有效值；合法档位（含大小写变体如 Light）按
    原值渲染。归一只作用于渲染，MUST NOT 回写决策对象（落库与 trace 保留原值）。
    """
    if isinstance(value, str) and value.strip().lower() in _POSITION_VOCAB:
        return value
    return "未提供"


def _fmt_reeval_triggers(triggers: object) -> str:
    """再评估触发条件渲染：编号条目；无有效条目 → 未申报（require-watch-hold-rationale）。

    update-decision-price-gate：报警仅进 trace，渲染链不接收 anomalies。
    """
    items: list[str] = []
    if isinstance(triggers, str):
        items = [triggers.strip()] if triggers.strip() else []
    elif isinstance(triggers, (list, tuple)):
        items = [t.strip() for t in triggers if isinstance(t, str) and t.strip()]
    if not items:
        return "未申报"
    parts = []
    for i, t in enumerate(items):
        mark = _TRIGGER_MARKS[i] if i < len(_TRIGGER_MARKS) else f"({i + 1})"
        parts.append(f"{mark} {t}")
    return "；".join(parts)


def _format_trade_decision(
    decision: TradeDecision | dict,
    *,
    data_cutoff: date | None = None,
    fund_approved: bool = False,
    report_date: date | None = None,
) -> str:
    """格式化交易决策（report-render-operational-params：渲染完整操作参数）。

    buy/sell 渲染仓位+入场/止损/目标价（0/缺失「未提供」）与「再评估触发条件」行
    （update-decision-integrity-gates Task 3，601818 实证：sell 终稿无触发条件时
    报告无任何可见缺口——缺失如实标注「未申报」，MUST NOT 整行省略或编造条目）；
    watch/hold 语义上无建仓参数，不渲染硬价格行，渲染结构化「不行动原因」与
    「再评估触发条件」（缺失如实标注「未申报」，require-watch-hold-rationale）。
    update-decision-price-gate：报警仅进 trace，渲染链不接收 anomalies。
    add-watch-trigger-tracking：watch 增渲染双向触发位行（缺失如实「未申报」，
    禁从 reeval_triggers 文本解析回填；buy/sell/hold 不渲染该两行）；fund_approved
    时渲染入池跟踪声明；data_cutoff 与 report_date 间隔 >_DATA_VACUUM_THRESHOLD_DAYS
    自然日时渲染数据真空提示（全 action）。新参数仅关键字传参（旧单参调用兼容不变）。
    """
    if isinstance(decision, TradeDecision):
        action = decision.action
        confidence = decision.confidence
        reasoning = decision.reasoning
        position = getattr(decision, "position_size", None)
        entry = getattr(decision, "entry_price", None)
        stop = getattr(decision, "stop_loss", None)
        target = getattr(decision, "target_price", None)
        corrected = getattr(decision, "price_level_corrected", False)
        correction_reason = getattr(decision, "price_level_correction_reason", "") or ""
        inaction = getattr(decision, "inaction_reason", None)
        triggers = getattr(decision, "reeval_triggers", []) or []
        sell_type = getattr(decision, "sell_type", None)
        exit_schedule = getattr(decision, "exit_schedule", None)
        trigger_high = getattr(decision, "trigger_high", None)
        trigger_low = getattr(decision, "trigger_low", None)
    else:
        action = decision.get("action", "N/A")
        confidence = decision.get("confidence", 0)
        reasoning = decision.get("reasoning", "")
        position = decision.get("position_size")
        entry = decision.get("entry_price")
        stop = decision.get("stop_loss")
        target = decision.get("target_price")
        corrected = decision.get("price_level_corrected", False)
        correction_reason = decision.get("price_level_correction_reason", "") or ""
        inaction = decision.get("inaction_reason")
        triggers = decision.get("reeval_triggers") or []
        sell_type = decision.get("sell_type")
        exit_schedule = decision.get("exit_schedule")
        trigger_high = decision.get("trigger_high")
        trigger_low = decision.get("trigger_low")

    lines = [f"- **方向**: {action}", f"- **置信度**: {confidence:.0%}"]
    # spec report-decision-rendering：仓位档位为必含字段——缺失/非法字面量如实
    # 「未提供」，不得整行省略（#140 终审 C-4；Task 2：词表外字面量渲染前归一，
    # 不回写决策对象）
    lines.append(f"- **仓位**: {_fmt_position_size(position)}")
    # sell 分型分模板（update-sell-action-typing，#188）：exit（持有者减仓）无新建仓
    # 参数——渲染减仓节奏、不渲染价位行与派生指标（与 watch/hold 无建仓参数语义
    # 同型，重新介入条件由 reeval_triggers 承载）；short 与未申报（None，默认
    # short）维持现行参数行（历史报告兼容）
    if action == "sell" and sell_type == "exit":
        if isinstance(exit_schedule, str) and exit_schedule.strip():
            lines.append(f"- **减仓节奏**: {exit_schedule}")
        else:
            lines.append("- **减仓节奏**: 未申报")
    elif action in ("buy", "sell"):
        lines.append(f"- **入场价**: {_fmt_price(entry)}")
        lines.append(f"- **止损价**: {_fmt_price(stop)}")
        lines.append(f"- **目标价**: {_fmt_price(target)}")
        derived = _fmt_derived_metrics(action, entry, stop, target)
        if derived:
            lines.append(derived)
    else:
        if isinstance(inaction, str) and inaction.strip():
            lines.append(f"- **不行动原因**: {inaction}")
        else:
            lines.append("- **不行动原因**: 未申报")
    # add-watch-trigger-tracking：watch 双向触发位行——申报值按价位行口径渲染，
    # 缺失如实「未申报」，MUST NOT 从 reeval_triggers 文本解析回填（未申报即未申报）；
    # 触发位是 watch 决策专属申报参数，buy/sell/hold 不渲染该两行（hold 的重新介入
    # 条件由 reeval_triggers 承载）
    if action == "watch":
        lines.append(f"- **上破触发位**: {_fmt_trigger_level(trigger_high)}")
        lines.append(f"- **下破触发位**: {_fmt_trigger_level(trigger_low)}")
    # add-watch-trigger-tracking：入池跟踪声明——FM approve 后决策入池按固定窗口
    # 结算，声明让读者可在战绩页对账
    if fund_approved:
        lines.append(
            f"- **跟踪**: 本决策已入池跟踪，按 {DEFAULT_HORIZON_DAYS} 交易日窗口结算，"
            "结算结果见战绩页"
        )
    # add-watch-trigger-tracking：数据真空提示（全 action）——报告日与行情截止日间隔
    # 超阈值时跳空缺口可能使价位触发条件失真；间隔 <=0（截止晚于报告日的异常态）
    # 不提示
    if data_cutoff is not None and report_date is not None:
        vacuum_days = (report_date - data_cutoff).days
        if vacuum_days > _DATA_VACUUM_THRESHOLD_DAYS:
            lines.append(
                f"- ⚠ 数据真空提示：触发价位锚定 {data_cutoff.isoformat()} 收盘价，"
                f"期间 {vacuum_days} 个自然日无行情，跳空缺口可能使触发条件失真"
            )
    # Task 3：reeval_triggers 为全部 action 的必渲染对象——buy/sell 清洗后为空时
    # 「未申报」行保留（601818 形态），MUST NOT 整行省略或编造条目
    lines.append(f"- **再评估触发条件**: {_fmt_reeval_triggers(triggers)}")
    lines.append(f"- **理由**: {reasoning}")
    if corrected:
        # toolize-price-levels：价位经工具参考带修正（可观测，不静默）
        lines.append(
            f"- **价位修正**: sanity 校验未通过，已按工具参考带修正（{correction_reason}）"
        )
    return "\n".join(lines)


def _format_debate_message(msg: DebateMessage | dict) -> str:
    """格式化辩论消息。"""
    if isinstance(msg, DebateMessage):
        role = msg.role
        content = msg.content
    else:
        role = msg.get("role", "?")
        content = msg.get("content", "")

    return f"**{role}**: {content}\n"
