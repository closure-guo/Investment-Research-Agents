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
from datetime import datetime
from typing import Any

from finance_agent.charts import collect_chart_data, generate_all_charts
from finance_agent.llm.gateway import complete_text
from finance_agent.models import AnalystReport, DebateMessage, TradeDecision

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
        "你是投研报告编辑。根据用户关注点和各层分析产出，写一段 150-200 字的研究聚焦摘要，"
        "紧扣用户关注点组织语言，点出最关键的结论与数据。纯文本，不使用 emoji，不输出标题。"
        "内容仅基于所提供材料中的数据组织，不得引入材料外的数值或推测。"
    )
    focus_line = f"用户关注点: {focus}\n" if focus else "（用户未指定关注点，请综合各维度要点）\n"
    prompt = (
        f"股票: {stock_name}\n{focus_line}关注维度: {tags_desc}\n\n"
        f"各层分析产出:\n" + "\n".join(materials)
    )
    with contextlib.suppress(Exception):
        text, meta = complete_text(
            [{"role": "system", "content": system}, {"role": "user", "content": prompt}],
            purpose="quick",
            max_tokens=400,
            temperature=0.3,
            llm_config=_request_config_dict(state.get("llm_config"), api_key),
            trace={"name": "report", "metadata": {"agent": "report"}},
        )
        # legacy 行为保留：content 为空时回退 reasoning_content
        resp = text or meta.get("raw_reasoning") or ""
        resp = (resp or "").strip()
        if resp:
            return resp

    # 兜底：取首个分析师 summary 截断
    fallback = materials[0] if materials else ""
    return fallback[:200]


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
    date = datetime.now().strftime("%Y-%m-%d")
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
        ("chart_heatmap", "财报发布窗口期股价变化"),
        ("chart_dashboard", "财务指标综合仪表盘"),
        ("chart_market_share", "全球市场份额"),
    ]

    sections: list[str] = [
        f"# {stock_name}({stock_code}) 投资分析报告",
        f"\n*报告日期: {date}" + (f" · 研究聚焦: {focus}*\n" if has_focus else "*\n"),
    ]

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

    # ── 后续固定章节（编号自动顺延） ──
    conclusion = state.get("research_manager_conclusion")
    if conclusion:
        sections.append(f"{next_title('多空辩论结论')}\n{conclusion}\n")

    decision = state.get("final_trade_decision") or state.get("trader_plan")
    if decision:
        sections.append(f"{next_title('交易决策')}\n{_format_trade_decision(decision)}\n")

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
        # #111：审批理由随决策渲染（在场时）；缺失时保持仅标注（历史 state 兼容）
        fm_reasoning = (state.get("fund_manager_decision_reasoning") or "").strip()
        # D1：FM 操作定性（action/置信度）与裁决 action 并排展示——「批准的是什么
        # 方案」直接可见，方向相悖时矛盾自明；历史 state 无字段时保持旧行为
        qualifier = ""
        fm_action = state.get("fund_manager_action")
        if fm_action:
            qualifier = f"（操作定性 {fm_action}"
            fm_confidence = state.get("fund_manager_confidence")
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
        reasoning_block = f"\n\n{fm_reasoning}\n" if fm_reasoning else "\n"
        sections.append(
            f"{next_title('基金经理决策')}\n\n**{annotation}**{qualifier}{reasoning_block}"
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


def _format_trade_decision(decision: TradeDecision | dict) -> str:
    """格式化交易决策（report-render-operational-params：渲染完整操作参数）。

    buy/sell 渲染仓位+入场/止损/目标价（0/缺失「未提供」）；watch/hold 语义上
    无建仓参数，不渲染硬价格行，注明再评估触发条件见理由。
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

    lines = [f"- **方向**: {action}", f"- **置信度**: {confidence:.0%}"]
    if position:
        lines.append(f"- **仓位**: {position}")
    if action in ("buy", "sell"):
        lines.append(f"- **入场价**: {_fmt_price(entry)}")
        lines.append(f"- **止损价**: {_fmt_price(stop)}")
        lines.append(f"- **目标价**: {_fmt_price(target)}")
        derived = _fmt_derived_metrics(action, entry, stop, target)
        if derived:
            lines.append(derived)
    else:
        lines.append("- **再评估触发条件**: 见理由")
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
