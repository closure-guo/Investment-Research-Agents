"""Layer I 分析师 Agent — 4 个并行分析师节点。

每个分析师：
1. 从 state 读取 PREP 数据
2. 构建 prompt context
3. 调用 LLM
4. 解析 JSON 响应为 AnalystReport
5. 返回 state 更新
"""

from __future__ import annotations

import json
import logging
import re

from pydantic import ValidationError

from finance_agent.langfuse_tracing import truncate_for_trace, update_current_span
from finance_agent.metric_vocab import render_date
from finance_agent.models import AnalystReport
from finance_agent.nodes._llm_utils import call_llm_streaming, focus_hint, parse_json_response
from finance_agent.prompts.loader import load_prompt_with_meta

logger = logging.getLogger(__name__)

# add-agent-readable-conclusion：解析降级时的普通人可读结论占位（按 agent 中文名映射）。
# 措辞必须如实说「解析失败」——r1 实证旧文案「数据缺失」让 judge 与最终报告
# 都以为分析师没拿到数据，而实际是 JSON 解析挂了（数据齐全、辩手已引用）
_PLAIN_FALLBACK = {
    "technical": "技术面分析师输出解析失败，结论暂不可用",
    "macro": "宏观分析师输出解析失败，结论暂不可用",
    "fundamental": "基本面分析师输出解析失败，结论暂不可用",
    "sentiment": "舆情分析师输出解析失败，结论暂不可用",
}
_PLAIN_FALLBACK_DEFAULT = "分析师输出解析失败，结论暂不可用"

# 解析失败时从原始文本打捞已闭合的字符串字段（截断/后段损坏时前段字段通常完好）
_SALVAGE_RE = {
    field: re.compile(rf'"{field}"\s*:\s*"((?:[^"\\]|\\.)*)"')
    for field in ("summary", "plain_conclusion")
}


def _salvage_field(response: str, field: str) -> str:
    match = _SALVAGE_RE[field].search(response or "")
    if not match:
        return ""
    try:
        return str(json.loads(f'"{match.group(1)}"')).strip()
    except json.JSONDecodeError:
        return match.group(1).strip()


def _keep_valid_over_degraded(state: dict, agent: str, report: AnalystReport) -> AnalystReport:
    """引用重试重跑时，降级结果不覆盖既有正常报告（r1 中芯实证：第 3 代解析失败
    的兜底覆盖了前两代好报告，辩手与最终报告/judge 看到的版本不一致）。"""
    if not report.parse_degraded:
        return report
    existing = (state.get("analyst_reports") or {}).get(agent)
    if existing is None:
        return report
    if isinstance(existing, dict):
        try:
            existing = AnalystReport.model_validate(existing)
        except ValidationError:
            return report
    if getattr(existing, "parse_degraded", False):
        return report
    logger.warning("分析师 %s 重跑解析失败，保留既有正常报告不覆盖", agent)
    return existing


_VALID_CLAIM_TYPES = {
    "numerical",
    "temporal",
    "entity",
    "comparative",
    "regulatory",
    "computational",
}
_VALID_SOURCE_TYPES = {"data", "event", "llm_inference", "mixed"}


def _retry_feedback_section(state: dict, agent_name: str) -> str:
    """定向重试反馈段（harden-citation-semantic-coverage D3）：值级 FAIL 明细 +
    ground_truth 注入重试上下文——与旧「盲目重跑」的关键区别是给 LLM 改错信息。

    无反馈（首轮 / 非目标分析师 / feedback 缺该 agent 键）时返回 ""，使首轮
    与非目标分析师的 context 不受影响。"""
    feedback = (state.get("citation_retry_feedback") or {}).get(agent_name) or []
    if not feedback:
        return ""
    lines = ["## 上轮引用校验失败（必须修正以下数据引用，ground_truth 为真实值）"]
    for item in feedback:
        if item.get("kind") == "coverage_gap":
            # D6：覆盖率缺口打回——补建 claim 或删除正文数字
            lines.append(
                f"- 正文数字 {item['raw']} 未被任何 claim 认领：请补建对应 claim "
                f"（field_ref 指向 state 字段）或删除该正文数字"
            )
            continue
        lines.append(
            f"- field_ref={item['field_ref']}：你写的值 {item['stated_value']}，"
            f"真实值 {item['ground_truth']}（偏差 {item['delta']}）。"
            f"原表述：{item['interpretation']}"
        )
    return "\n".join(lines)


def _degradation_metadata(agent_name: str, round_no: int, kind: str, **extra: object) -> dict:
    """降级标记：legacy 键（degradation/agent，供既有看板过滤）+ 带 agent 与重试轮次的
    命名空间键。分析师节点没有自己的 span，所有标记落到同一父 span——只用 `degradation`
    单键会被后来的降级覆盖（r1 实证：中芯 ≥3 次降级只剩最后一个、agent=None）。"""
    md: dict = {
        "degradation": kind,
        "agent": agent_name,
        f"degradation.{agent_name}.r{round_no}": kind,
    }
    for key, value in extra.items():
        md[key] = value
        md[f"{key}.{agent_name}.r{round_no}"] = value
    return md


def _sanitize_claims(data: dict, agent_name: str = "", round_no: int = 0) -> dict:
    """修正 LLM 输出中非法的 claim 字段值。

    非法枚举值被强制改写为兜底值，并记录 WARNING —— 改写本身是有意的降级
    （保证管线不因单个 claim 失败中断），但需可观测，否则 prompt 与代码的
    枚举不一致会被系统性静默掩盖。
    """
    for claim in data.get("claims", []):
        claimType = claim.get("claim_type")
        if claimType not in _VALID_CLAIM_TYPES:
            logger.warning(
                "分析师 %s 的 claim_type 非法，已改写：%r -> 'entity'", agent_name, claimType
            )
            # 改写是刻意降级：保证管线不因单个 claim 失败中断，但需在 trace 可见
            update_current_span(
                metadata={
                    **_degradation_metadata(agent_name, round_no, "sanitize_claims"),
                    "field": "claim_type",
                    "raw": claimType,
                    "fixed": "entity",
                    f"degradation.{agent_name}.r{round_no}.sanitize.claim_type": f"{claimType}->entity",
                },
                level="WARNING",
            )
            claim["claim_type"] = "entity"
        sourceType = claim.get("source_type")
        if sourceType not in _VALID_SOURCE_TYPES:
            logger.warning(
                "分析师 %s 的 source_type 非法，已改写：%r -> 'data'", agent_name, sourceType
            )
            update_current_span(
                metadata={
                    **_degradation_metadata(agent_name, round_no, "sanitize_claims"),
                    "field": "source_type",
                    "raw": sourceType,
                    "fixed": "data",
                    f"degradation.{agent_name}.r{round_no}.sanitize.source_type": f"{sourceType}->data",
                },
                level="WARNING",
            )
            claim["source_type"] = "data"
        # 必填字符串字段的 None 值兜底为空串
        for field in ("field_ref", "stated_value", "interpretation"):
            if claim.get(field) is None:
                claim[field] = ""
        # metric_name/period 为可选申报字段：非 None 时统一转 str（LLM 偶发
        # 把 period 输出成 int 2024），缺省保持 None（None = 未申报，校验跳过）。
        for field in ("metric_name", "period"):
            if claim.get(field) is not None:
                claim[field] = str(claim[field])
    return data


def _parse_analyst_report(response: str, agent_name: str, round_no: int = 0) -> AnalystReport:
    """解析 LLM 响应为 AnalystReport，解析失败时降级为原始文本报告。

    降级保障单个分析师解析失败不拖垮整条管线，但会产出 claims=[]，
    而零 claim 使引用校验 all_passed=True（citation.py 的 failed == 0）。
    故降级 SHALL 记录 WARNING 并打标记，使问题可被发现而非静默通过。
    降级标记仅用于可观测性，不改变图的走向（不触发 citation retry，
    见 harden-llm-output-validation 决策 4 与 incidents/006）。
    round_no：引用重试轮次（state.iteration_count），进入降级标记键避免重跑互相覆盖。
    """
    try:
        data = _sanitize_claims(parse_json_response(response), agent_name, round_no)
        return AnalystReport.model_validate(data)
    except Exception as e:
        # #109 重新定性：JSON 可解析但漏尾字段 markdown（glm 常见，schema 尾字段
        # 易被省略）——不整体降级，用 summary/key_findings 合成 markdown、claims 保留
        if isinstance(e, ValidationError) and isinstance(data, dict) and "markdown" not in data:
            try:
                parts = [f"## 分析摘要\n{data.get('summary', '')}"]
                findings = data.get("key_findings") or []
                if findings:
                    parts.append("### 关键发现\n" + "\n".join(f"- {f}" for f in findings))
                synthesized = AnalystReport(
                    agent_name=data.get("agent_name", agent_name),
                    summary=str(data.get("summary", ""))[:200],
                    plain_conclusion=str(
                        data.get("plain_conclusion") or data.get("summary") or "分析完成"
                    )[:200],
                    key_findings=findings,
                    claims=data.get("claims") or [],
                    markdown="\n\n".join(parts),
                )
                logger.warning(
                    "分析师 %s 漏输出 markdown 字段，已用 summary/key_findings 合成（#109）",
                    agent_name,
                )
                update_current_span(
                    metadata=_degradation_metadata(agent_name, round_no, "markdown_synthesized"),
                    level="WARNING",
                )
                return synthesized
            except Exception as synth_err:  # noqa: BLE001 -- 合成失败走原降级
                logger.warning("markdown 合成失败，走原始文本降级: %s", synth_err)
        logger.warning(
            "分析师 %s 的 LLM 输出解析失败，降级为原始文本报告：%s: %s",
            agent_name,
            type(e).__name__,
            e,
        )
        # 降级须在 trace 可见（此前完全静默），raw_excerpt 截断避免大文本进 span
        update_current_span(
            metadata=_degradation_metadata(
                agent_name,
                round_no,
                "parse_degraded",
                raw_excerpt=truncate_for_trace(response[:500]),
            ),
            level="WARNING",
        )
        return AnalystReport(
            agent_name=agent_name,
            summary=_salvage_field(response, "summary")
            or (response[:200] if response else "分析完成"),
            plain_conclusion=_salvage_field(response, "plain_conclusion")
            or _PLAIN_FALLBACK.get(agent_name, _PLAIN_FALLBACK_DEFAULT),
            key_findings=[],
            claims=[],
            markdown=response or "## 分析\n（LLM 响应解析失败，显示原始文本）",
            parse_degraded=True,
        )


def technical_analyst(state: dict) -> dict:
    """Layer I 技术面分析师 Agent。"""
    context = _build_technical_context(state)
    feedback = _retry_feedback_section(state, "technical")
    if feedback:
        context = f"{context}\n\n{feedback}"
    _pinfo = load_prompt_with_meta("technical_analyst")
    system = _pinfo.template
    api_key = state.get("api_key")

    response = call_llm_streaming(
        context,
        system=system,
        api_key=api_key,
        node_name="technical_analyst",
        llm_config=state.get("llm_config"),
        stock_code=state.get("stock_code"),
        prompt_name=_pinfo.prompt_name,
        prompt_version=_pinfo.prompt_version,
    )
    report = _parse_analyst_report(
        response, "technical", round_no=int(state.get("iteration_count") or 0)
    )

    return {"analyst_reports": {"technical": _keep_valid_over_degraded(state, "technical", report)}}


# analyst-context-budget delta：技术指标 context 窗口。250 期全窗口指标 JSON
# 使 technical_analyst 单次 LLM 调用 11.5~14 分钟（601700 深研事故）；60 期
# 足够覆盖 MA60/趋势/背离分析（macro_analyst 同理只取近 3 期）。
_TECHNICAL_CONTEXT_WINDOW = 60


def _trim_series(values: list, window: int) -> list:
    """序列裁剪到最近 window 期；不超过 window 期时原样返回。"""
    return values[-window:] if isinstance(values, list) and len(values) > window else values


def _series_len(trimmed: dict) -> int:
    """裁剪结构内任一序列的长度（各序列等长；无序列返回 0）。"""
    for series in trimmed.values():
        if isinstance(series, dict):
            for values in series.values():
                if isinstance(values, list):
                    return len(values)
        elif isinstance(series, list):
            return len(series)
    return 0


def _series_semantic_header(direction: str, latest_label: str, count: int) -> str:
    """序列语义头（harden-citation-semantic-coverage D4）：机生声明排序方向 +
    最新期定位 + 期数。内容由代码依据 state 实际数据形态生成，LLM 所见语义
    与校验器解析语义一致（incident 022 期次错位疾病的主防线）。"""
    return f"# 序列语义: {direction}, {latest_label}, 共{count}期"


def _trim_technical_indicators(
    indicators: dict, window: int = _TECHNICAL_CONTEXT_WINDOW
) -> tuple[dict, bool]:
    """各指标序列裁剪为最近 window 期，返回（裁剪后结构, 是否发生裁剪）。

    结构为 {指标组: {序列名: list}} 两层（MA/MACD/RSI/BOLL/KDJ 均如此），
    非常规形态原样保留（防御）。
    """
    trimmed_any = False
    out: dict = {}
    for group, series in indicators.items():
        if isinstance(series, dict):
            trimmed_group: dict = {}
            for key, values in series.items():
                new = _trim_series(values, window)
                trimmed_any = trimmed_any or (new is not values)
                trimmed_group[key] = new
            out[group] = trimmed_group
        else:
            new = _trim_series(series, window)
            trimmed_any = trimmed_any or (new is not series)
            out[group] = new
    return out, trimmed_any


def _build_technical_context(state: dict) -> str:
    """构建技术面分析的 LLM context。"""
    sections = []

    stock_name = state.get("stock_name", "N/A")
    stock_code = state.get("stock_code", "N/A")
    sections.append(f"股票: {stock_name}({stock_code})")

    hint = focus_hint(state)
    if hint:
        sections.append(hint)

    indicators = state.get("technical_indicators") or {}
    if indicators:
        # analyst-context-budget（裁剪）+ fix-citation-contract-diseases（负索引）：
        # 序列裁剪到最近窗口期控制 token；负索引约定（-1=最新一期）使 LLM 引用与
        # 校验器解析按「长度无关」语义对齐，裁剪窗口此后怎么改都不影响校验。
        trimmed, did_trim = _trim_technical_indicators(indicators)
        # 机生语义头（incident 022）：方向 + 最新交易日（取自 kline 末行）+ 期数。
        # 技术序列与 kline 等长升序，裁剪后期数 = min(序列长, 窗口)。
        n_shown = _TECHNICAL_CONTEXT_WINDOW if did_trim else _series_len(trimmed)
        kline = state.get("kline")
        latest_date = ""
        try:
            latest_date = (
                render_date(kline["日期"].iloc[-1]) if kline is not None and len(kline) else ""
            )
        except (KeyError, IndexError, TypeError):
            latest_date = ""
        latest_label = (
            f"index -1 = 最新交易日({latest_date})" if latest_date else "index -1 = 最新一期"
        )
        header = _series_semantic_header("时间正序(旧→新)", latest_label, n_shown)
        # 数组方向声明（incident 022 第四类疾病）：序列为时间正序（旧→新），
        # 列表末尾为最新一期——LLM 按此读取 -1 语义，防止把展示首元素当最新。
        note = (
            f"各序列为最近 {_TECHNICAL_CONTEXT_WINDOW} 期，更早历史已省略；序列为时间正序（旧→新），列表末尾为最新一期；"
            if did_trim
            else "序列为时间正序（旧→新），列表末尾为最新一期；"
        )
        sections.append(
            f"{header}\n技术指标数据（state 键 technical_indicators；"
            f"{note}field_ref 引用序列值时用负索引：-1=最新一期）:\n"
            f"{json.dumps(trimmed, ensure_ascii=False, default=str)}"
        )

        # 派生值表（toolize-price-levels）：区间涨跌幅/距高低点回撤反弹由工具
        # 预生成，LLM 直接引用不心算；None 项如实标注数据不足
        derived = state.get("derived_series")
        if derived:
            derived_view = {k: (v if v is not None else "数据不足") for k, v in derived.items()}
            sections.append(
                # 前缀用规范根键名 derived_series.（与 state 键一致）；校验器同时接受
                # 简写 derived.（_ROOT_ALIASES 归一），此处以规范形态减少歧义
                "常用派生值（工具预生成，直接引用；field_ref 前缀 derived_series.，"
                "如 derived_series.chg_5d）:\n"
                f"{json.dumps(derived_view, ensure_ascii=False)}"
            )

    return "\n\n".join(sections)


def macro_analyst(state: dict) -> dict:
    """Layer I 宏观分析师 Agent。"""
    context = _build_macro_context(state)
    feedback = _retry_feedback_section(state, "macro")
    if feedback:
        context = f"{context}\n\n{feedback}"
    _pinfo = load_prompt_with_meta("macro_analyst")
    system = _pinfo.template
    api_key = state.get("api_key")

    response = call_llm_streaming(
        context,
        system=system,
        api_key=api_key,
        node_name="macro_analyst",
        llm_config=state.get("llm_config"),
        stock_code=state.get("stock_code"),
        prompt_name=_pinfo.prompt_name,
        prompt_version=_pinfo.prompt_version,
    )
    report = _parse_analyst_report(
        response, "macro", round_no=int(state.get("iteration_count") or 0)
    )

    return {"analyst_reports": {"macro": _keep_valid_over_degraded(state, "macro", report)}}


def _build_macro_context(state: dict) -> str:
    """构建宏观分析的 LLM context。"""
    sections = []

    stock_name = state.get("stock_name", "N/A")
    stock_code = state.get("stock_code", "N/A")
    industry = state.get("industry_info") or {}
    industry_name = industry.get("name", "N/A")
    sections.append(f"股票: {stock_name}({stock_code}), 所属行业: {industry_name}")

    hint = focus_hint(state)
    if hint:
        sections.append(hint)

    macro = state.get("macro_indicators") or {}
    if macro:
        # 只取最近 3 个月数据，减少 token 消耗；records 现挂在 "records" 键下（fetch 守卫结构）。
        trimmed = {}
        for key, value in macro.items():
            if isinstance(value, dict):
                recs = value.get("records") or []
                freshness = value.get("freshness")
                as_of = value.get("as_of_date")
                trimmed[key] = recs[:3]
                if freshness == "stale":
                    trimmed[f"{key} 数据滞后"] = (
                        f"最新至 {as_of or '未知日期'}，请按滞后数据处理并降级结论"
                    )
            else:
                trimmed[key] = value
        # 机生语义头：宏观序列降序（index 0 = 最新），期次取首个含 records 指标
        # 的最新月份与展示期数（records[:3] 截断后实际长度）
        latest_month, n_shown = "", 0
        for value in macro.values():
            if isinstance(value, dict):
                recs = value.get("records") or []
                if recs:
                    latest_month = str(recs[0].get("月份", ""))
                    n_shown = min(len(recs), 3)
                    break
        if latest_month:
            sections.append(
                _series_semantic_header(
                    "时间降序(新→旧)", f"index 0 = 最新一期({latest_month})", n_shown
                )
            )
        sections.append(
            f"宏观经济指标（state 键 macro_indicators，近3期）:\n"
            f"{json.dumps(trimmed, ensure_ascii=False, default=str)}"
        )
    else:
        sections.append("宏观经济指标（state 键 macro_indicators）: 数据暂不可用")

    return "\n\n".join(sections)


def fundamental_analyst(state: dict) -> dict:
    """Layer I 基本面分析师 Agent。"""
    context = _build_fundamental_context(state)
    feedback = _retry_feedback_section(state, "fundamental")
    if feedback:
        context = f"{context}\n\n{feedback}"
    _pinfo = load_prompt_with_meta("fundamental_analyst")
    system = _pinfo.template
    api_key = state.get("api_key")

    response = call_llm_streaming(
        context,
        system=system,
        api_key=api_key,
        node_name="fundamental_analyst",
        llm_config=state.get("llm_config"),
        stock_code=state.get("stock_code"),
        prompt_name=_pinfo.prompt_name,
        prompt_version=_pinfo.prompt_version,
    )
    report = _parse_analyst_report(
        response, "fundamental", round_no=int(state.get("iteration_count") or 0)
    )

    return {
        "analyst_reports": {"fundamental": _keep_valid_over_degraded(state, "fundamental", report)}
    }


def _build_fundamental_context(state: dict) -> str:
    """构建基本面分析的 LLM context。"""
    sections = []

    stock_name = state.get("stock_name", "N/A")
    stock_code = state.get("stock_code", "N/A")
    sections.append(f"股票: {stock_name}({stock_code})")

    hint = focus_hint(state)
    if hint:
        sections.append(hint)

    # 公告与研报（add-analyst-data-coverage）
    announcements = state.get("announcements") or []
    if announcements:
        trimmed_ann = [
            {
                "title": a.get("title", ""),
                "date": a.get("date", ""),
                "category": a.get("category", ""),
            }
            for a in announcements[:10]
        ]
        sections.append(
            f"公司公告（state 键 announcements，最近{len(trimmed_ann)}条）:\n"
            f"{json.dumps(trimmed_ann, ensure_ascii=False, default=str)}"
        )
    else:
        sections.append("公司公告（state 键 announcements）: 暂无数据")
    reports = state.get("research_reports") or []
    if reports:
        trimmed_rep = [
            {
                "title": r.get("title", ""),
                "org": r.get("org", ""),
                "rating": r.get("rating", ""),
                "target_price": r.get("target_price"),
                "date": r.get("date", ""),
            }
            for r in reports[:8]
        ]
        sections.append(
            f"券商研报（state 键 research_reports，最近{len(trimmed_rep)}条）:\n"
            f"{json.dumps(trimmed_rep, ensure_ascii=False, default=str)}"
            "\n注意：评级与目标价是卖方机构观点，存在立场偏差，不得直接作为你的结论依据，仅作市场预期参照。"
        )
    else:
        sections.append("券商研报（state 键 research_reports）: 暂无数据")

    # 三大报表（近 3 年，减少 token）——财报降序（最新在前），head = 最新 3 年
    for name, key in [
        ("资产负债表", "balance_sheet"),
        ("利润表", "income_statement"),
        ("现金流量表", "cash_flow_statement"),
    ]:
        df = state.get(key)
        if df is not None and not df.empty:
            recent = df.head(3) if len(df) > 3 else df
            # 报表段：降序声明 + 首行最新报告期（机生）
            latest_period = (
                render_date(recent["报告日"].iloc[0])
                if "报告日" in recent.columns and len(recent)
                else ""
            )
            period_label = f", 首行 = 最新报告期({latest_period})" if latest_period else ""
            sections.append(
                f"# 表格语义: 行按报告期降序(新→旧){period_label}, 共{len(recent)}期\n"
                f"{name}（state 键 {key}，近3年）:\n{recent.to_string(index=False)}"
            )

    # 预计算指标（由降序财报计算，继承降序；head = 最新 3 年）
    indicators = state.get("financial_indicators")
    if indicators is not None and not indicators.empty:
        recent = indicators.head(3) if len(indicators) > 3 else indicators
        sections.append(
            f"预计算财务指标（state 键 financial_indicators）:\n{recent.to_string(index=False)}"
        )

    # 四维度指标
    # 指标 dict 以年份为键：声明最新年（机生，取自任一有值指标的最大年键）
    latest_year = ""
    prof = state.get("profitability_metrics") or {}
    for metric_values in prof.values():
        if isinstance(metric_values, dict) and metric_values:
            latest_year = max(str(y) for y in metric_values)
            break
    year_note = f"，dict 以年份为键，最新年 = {latest_year}" if latest_year else ""
    for label, key in [
        ("盈利能力", "profitability_metrics"),
        ("偿债能力", "solvency_metrics"),
        ("运营效率", "efficiency_metrics"),
        ("现金流", "cashflow_metrics"),
    ]:
        val = state.get(key)
        if val:
            sections.append(
                f"{label}（state 键 {key}{year_note}）:\n{json.dumps(val, ensure_ascii=False, default=str)}"
            )

    # 杜邦分析
    dupont = state.get("dupont_tree")
    if dupont:
        sections.append(
            f"杜邦分析（state 键 dupont_tree）:\n{json.dumps(dupont, ensure_ascii=False, default=str)}"
        )

    # 增长率
    growth = state.get("growth_rates")
    if growth:
        sections.append(
            f"增长率（state 键 growth_rates）:\n{json.dumps(growth, ensure_ascii=False, default=str)}"
        )

    # 红黄绿灯 + 异常
    lights = state.get("traffic_lights")
    if lights:
        sections.append(
            f"红黄绿灯（state 键 traffic_lights）:\n{json.dumps(lights, ensure_ascii=False, default=str)}"
        )
    anomalies = state.get("anomalies")
    if anomalies:
        sections.append(
            f"异常检测（state 键 anomalies）:\n{json.dumps(anomalies, ensure_ascii=False, default=str)}"
        )

    # 健康度
    health = state.get("health_score")
    if health:
        sections.append(
            f"健康度评分（state 键 health_score）:\n{json.dumps(health, ensure_ascii=False, default=str)}"
        )

    # 同业对比
    peer = state.get("peer_comparison")
    if peer:
        sections.append(
            f"同业对比（state 键 peer_comparison）:\n{json.dumps(peer, ensure_ascii=False, default=str)}"
        )

    # 相对估值
    rval = state.get("relative_valuation")
    if rval:
        sections.append(
            f"相对估值（state 键 relative_valuation）:\n{json.dumps(rval, ensure_ascii=False, default=str)}"
        )

    # GARP
    garp = state.get("garp_result")
    if garp:
        sections.append(
            f"GARP估值（state 键 garp_result）:\n{json.dumps(garp, ensure_ascii=False, default=str)}"
        )

    # 季度趋势
    qtrend = state.get("quarterly_trend")
    if qtrend:
        quarters = qtrend.get("quarters") or []
        if quarters:
            sections.append(
                _series_semantic_header(
                    "时间降序(新→旧)", f"index 0 = 最新季度({quarters[0]})", len(quarters)
                )
            )
        sections.append(
            f"季度趋势（state 键 quarterly_trend）:\n{json.dumps(qtrend, ensure_ascii=False, default=str)}"
        )

    return "\n\n".join(sections)


def sentiment_analyst(state: dict) -> dict:
    """Layer I 舆情分析师 Agent。"""
    context = _build_sentiment_context(state)
    feedback = _retry_feedback_section(state, "sentiment")
    if feedback:
        context = f"{context}\n\n{feedback}"
    _pinfo = load_prompt_with_meta("sentiment_analyst")
    system = _pinfo.template
    api_key = state.get("api_key")

    response = call_llm_streaming(
        context,
        system=system,
        api_key=api_key,
        node_name="sentiment_analyst",
        llm_config=state.get("llm_config"),
        stock_code=state.get("stock_code"),
        prompt_name=_pinfo.prompt_name,
        prompt_version=_pinfo.prompt_version,
    )
    report = _parse_analyst_report(
        response, "sentiment", round_no=int(state.get("iteration_count") or 0)
    )

    return {"analyst_reports": {"sentiment": _keep_valid_over_degraded(state, "sentiment", report)}}


def _build_sentiment_context(state: dict) -> str:
    """构建舆情分析的 LLM context。"""
    sections = []

    stock_name = state.get("stock_name", "N/A")
    stock_code = state.get("stock_code", "N/A")
    sections.append(f"股票: {stock_name}({stock_code})")

    hint = focus_hint(state)
    if hint:
        sections.append(hint)

    # 新闻列表（取最近 15 条，减少 token）
    news = state.get("news_list") or []
    if news:
        recent_news = news[:15]
        # 只保留关键字段，减少 token
        trimmed = []
        for n in recent_news:
            trimmed.append(
                {
                    "title": n.get("title", ""),
                    "datetime": n.get("datetime", ""),
                    "source": n.get("source", ""),
                }
            )
        sections.append(
            f"新闻资讯（state 键 news_list，最近{len(trimmed)}条）:\n"
            f"{json.dumps(trimmed, ensure_ascii=False, default=str)}"
        )
    else:
        sections.append("新闻资讯（state 键 news_list）: 暂无数据")

    # 关键事件
    events = state.get("key_events") or []
    if events:
        sections.append(
            f"关键事件（state 键 key_events）:\n{json.dumps(events[:10], ensure_ascii=False, default=str)}"
        )

    # 解禁与大宗事件面（add-analyst-data-coverage）
    unlock = state.get("share_unlock") or []
    if unlock:
        sections.append(
            f"限售解禁排队（state 键 share_unlock）:\n{json.dumps(unlock[:8], ensure_ascii=False, default=str)}"
        )
    else:
        sections.append("限售解禁排队（state 键 share_unlock）: 暂无数据")
    blocks = state.get("block_trades") or []
    if blocks:
        sections.append(
            f"大宗交易明细（state 键 block_trades，近30天）:\n{json.dumps(blocks[:10], ensure_ascii=False, default=str)}"
        )
    else:
        sections.append("大宗交易明细（state 键 block_trades）: 暂无数据")

    return "\n\n".join(sections)
