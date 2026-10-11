"""Agent 工厂函数。

根据模式构建 ReAct Agent，配置工具集、max_iterations 和 system prompt。

三种模式：
- quick: 工具=[web_search], max_iterations=3
- deep: 工具=[search_stock, run_deep_analysis, web_search], max_iterations=10
- follow-up: 工具=[web_search], max_iterations=3, 注入 session 上下文
"""

# 项目规范使用 camelCase 变量名（如 _nodeTimelines），与 pep8-naming 冲突，统一豁免
# ruff: noqa: N806

from __future__ import annotations

import asyncio
import concurrent.futures
import contextlib
import functools
import inspect
import json
import logging
import os
import threading
from collections.abc import Callable
from typing import Any

from finance_agent.harness import Agent, PermissionMode
from finance_agent.harness.context import ContextBudget
from finance_agent.llm import LLMConfig
from finance_agent.outcome.track_record.ingest import persist_prediction_from_accumulated
from finance_agent.prompts.loader import PromptInfo, load_prompt_with_meta
from finance_agent.tool_registry import (
    TOOL_BATCH_WEB_SEARCH,
    TOOL_RUN_DEEP_ANALYSIS,
    TOOL_SEARCH_STOCK,
    TOOL_WEB_SEARCH,
)

logger = logging.getLogger("finance_agent.agent_factory")

# ── 事件有界入队（spec「发布积压可观测与背压」+ add-event-delivery-hardening） ──

# 非可丢弃事件的有界入队等待（消费者停滞时显式放弃的上限）
EVENT_PUT_TIMEOUT_SECONDS = 30.0
# 连续 undeliverable 熔断阈值：达到后非终态事件降级为丢弃+计数（issue #227.6），
# 避免消费端长期僵死时边界事件逐个等满 30s（~40 边界事件最坏 15-20 分钟收尾）
UNDELIVERABLE_BREAKER_THRESHOLD = 3
# 图完成后剩余积压的压缩保留上限（incident 039）：graph_done 置位且 chunk_queue
# 剩余条数 ≥ 此值时，final_report 之前的 thinking 积压立即压缩（仅留 ≤ 上限的
# 有界尾部走慢路径）。对齐 pipeline_runner.PENDING_TOKEN_MAX 的量级。
TAIL_KEEP_MAX = 512


class BoundedEventSink:
    """SSE 事件有界入队列（分级处置 + 连续 undeliverable 熔断降级）。

    - droppable（thinking_token 明细）：队列满即丢弃，计数 thinking 桶 + 节流日志；
    - 其余事件与流终结哨兵（None）：阻塞入队不丢弃，EVENT_PUT_TIMEOUT_SECONDS
      有界等待后放弃并计 undeliverable 桶——宁可显式放弃不可无限阻塞泄漏任务；
    - 熔断（issue #227.6）：连续 N 次 undeliverable 后，非终态事件降级为立即
      丢弃（breaker_degraded 桶），MUST NOT 再逐个有界等待；哨兵 MUST 保留
      有界等待路径（终态不因熔断被丢弃，spec 约束保持）；任一事件成功入队
      重置连续计数。

    timeout / breaker_threshold 可注入，满队列终态送达与熔断行为可单测
    （tests/test_bounded_event_sink.py）。
    """

    def __init__(
        self,
        queue: asyncio.Queue,
        session_id: str,
        record_drop: Callable[[str, str], None],
        *,
        timeout: float = EVENT_PUT_TIMEOUT_SECONDS,
        breaker_threshold: int = UNDELIVERABLE_BREAKER_THRESHOLD,
    ) -> None:
        self._queue = queue
        self._session_id = session_id
        self._record_drop = record_drop
        self._timeout = timeout
        self._breaker_threshold = breaker_threshold
        self._consecutive_undeliverable = 0
        self._thinking_dropped = 0

    @property
    def _breaker_tripped(self) -> bool:
        return self._breaker_threshold > 0 and (
            self._consecutive_undeliverable >= self._breaker_threshold
        )

    async def put(self, evt: Any, *, droppable: bool = False) -> str:
        """入队单事件，返回处置结果：enqueued / dropped_thinking /
        breaker_degraded / undeliverable。"""
        if droppable and self._queue.full():
            self._thinking_dropped += 1
            self._record_drop(self._session_id, "thinking")
            if self._thinking_dropped == 1 or self._thinking_dropped % 100 == 0:
                logger.warning(
                    "thinking 明细丢弃（队列满）session=%s 累计=%s",
                    self._session_id,
                    self._thinking_dropped,
                )
            return "dropped_thinking"
        # 熔断降级：仅非终态事件（哨兵 None 豁免——终态 MUST NOT 被熔断丢弃）
        if self._breaker_tripped and evt is not None:
            self._record_drop(self._session_id, "breaker_degraded")
            logger.warning(
                "连续 undeliverable 熔断降级 session=%s type=%s 连续=%s",
                self._session_id,
                getattr(evt, "event_type", None)
                or (evt.get("type") if isinstance(evt, dict) else evt),
                self._consecutive_undeliverable,
            )
            return "breaker_degraded"
        try:
            await asyncio.wait_for(self._queue.put(evt), timeout=self._timeout)
        except TimeoutError:
            self._consecutive_undeliverable += 1
            self._record_drop(self._session_id, "undeliverable")
            logger.error(
                "事件入队超时（消费者停滞）session=%s type=%s 连续=%s",
                self._session_id,
                evt,
                self._consecutive_undeliverable,
            )
            return "undeliverable"
        self._consecutive_undeliverable = 0
        return "enqueued"


# ───────────────────────────────────────────────
# System Prompts（ADR-0016：从 prompts/*.md 加载，Langfuse 优先 + 本地兜底）
# ───────────────────────────────────────────────


def _quick_mode_prompt() -> PromptInfo:
    """快速模式 prompt + 元数据；template 已 strip 便于 .format 渲染。"""
    _info = load_prompt_with_meta("quick_mode")
    return PromptInfo(
        template=_info.template.strip(),
        prompt_name=_info.prompt_name,
        prompt_version=_info.prompt_version,
    )


def _deep_mode_prompt() -> PromptInfo:
    """深度模式 prompt + 元数据；template 已 strip 便于 .format 渲染。"""
    _info = load_prompt_with_meta("deep_mode")
    return PromptInfo(
        template=_info.template.strip(),
        prompt_name=_info.prompt_name,
        prompt_version=_info.prompt_version,
    )


def _follow_up_mode_prompt() -> PromptInfo:
    """追问模式 prompt + 元数据；template 已 strip 便于 .format 渲染。"""
    _info = load_prompt_with_meta("follow_up_mode")
    return PromptInfo(
        template=_info.template.strip(),
        prompt_name=_info.prompt_name,
        prompt_version=_info.prompt_version,
    )


# ───────────────────────────────────────────────
# 工具定义
# ───────────────────────────────────────────────


async def _web_search(query: str) -> str:
    """搜索网页获取实时信息

    Args:
        query: 搜索关键词
    """
    from finance_agent.web_search import format_search_for_llm, has_tavily_key, tavily_search

    if not has_tavily_key():
        return "[错误] 未配置 TAVILY_API_KEY，无法执行搜索。"
    # 用 to_thread 避免同步调用阻塞事件循环，30 秒超时保护
    response = await asyncio.wait_for(asyncio.to_thread(tavily_search, query), timeout=30.0)
    return format_search_for_llm(response)


async def _stub_web_search(query: str) -> str:
    """TESTING=1 专用的 stub web_search 工具。

    返回带固定标记的、可被 parse_search_output 解析的搜索结果，
    不调用真实 Tavily API，使 E2E 能确定性验证"思考->web search->思考"序列
    （agent-turn-box-display delta）。

    Args:
        query: 搜索关键词
    """
    # 加延迟，让 E2E 能确定性捕获"正在搜索"中间态
    # （否则 search_start 与 search_result 几乎同时，中间态一闪而过；
    #  需足够长以覆盖 Playwright 轮询间隔与首轮渲染时机的不确定性）
    await asyncio.sleep(5.0)
    return (
        "[1] STUB 搜索结果：茅台最新消息\n"
        "https://stub.example.com/maotai-news\n"
        f"针对查询“{query}”的固定 stub 搜索结果，用于 E2E 确定性测试。\n"
        "\n"
        "[2] STUB 搜索结果：茅台提价公告\n"
        "https://stub.example.com/maotai-price\n"
        "茅台年内第二次提价的固定 stub 内容。\n"
    )


MAX_WEB_SOURCES = 20
MAX_SOURCE_CONTENT_LEN = 500

_web_search_cache: dict[str, str] = {}


def _make_web_search_with_collector(collector: list[dict]):
    """创建 web_search 工具，同时将搜索结果收集到 collector 用于引用溯源。"""

    async def web_search(query: str) -> str:
        """搜索单个关键词，获取实时网页信息。适用于明确、单一的事实型查询。

        新闻、舆情、热点等时间敏感的多面话题，应改用 batch_web_search
        一次从 2-3 个不同角度发问（信息面实测约 3 倍）。个股行情/报价/资金
        流向类查询的搜索结果多为行情页，信息量低，慎用搜索。

        Args:
            query: 搜索关键词
        """
        from finance_agent.web_search import format_search_for_llm, has_tavily_key, tavily_search

        if not has_tavily_key():
            return "[错误] 未配置 TAVILY_API_KEY，无法执行搜索。"
        if query in _web_search_cache:
            return _web_search_cache[query]
        # 用 to_thread 避免同步调用阻塞事件循环，30 秒超时保护
        response = await asyncio.wait_for(asyncio.to_thread(tavily_search, query), timeout=30.0)
        result_text = format_search_for_llm(response)
        _web_search_cache[query] = result_text
        if len(collector) < MAX_WEB_SOURCES:
            for r in response.results:
                entry = {
                    "query": query,
                    "title": r.title,
                    "url": r.url,
                    "content": r.content[:MAX_SOURCE_CONTENT_LEN],
                }
                if entry not in collector:
                    collector.append(entry)
                    if len(collector) >= MAX_WEB_SOURCES:
                        break
        return result_text

    return web_search


def _make_batch_web_search(collector: list[dict]):
    """创建 batch_web_search 工具，并行搜索多个关键词并收集信源。"""

    async def batch_web_search(queries: list[str]) -> str:
        """并行批量搜索多个关键词，获取更全面的信息

        适用于新闻、舆情等多面话题：一次从 2-3 个不同角度发问
        （如事件本身 / 业绩与财务 / 资金与股价动向），信息面实测约为
        单 query 的 3 倍。角度选择比数量重要——个股行情/报价类关键词
        召回的多为行情页，不建议作为搜索角度。

        Args:
            queries: 搜索关键词列表，建议 2-3 个不同角度的查询
        """
        from finance_agent.web_search import (
            batch_tavily_search,
            format_batch_for_llm,
            has_tavily_key,
        )

        if not has_tavily_key():
            return "[错误] 未配置 TAVILY_API_KEY，无法执行搜索。"
        if not queries:
            return "[错误] 查询列表为空"
        uncached = [q for q in queries if q not in _web_search_cache]
        cached_responses_text = [_web_search_cache[q] for q in queries if q in _web_search_cache]
        if uncached:
            # 用 to_thread 避免同步调用阻塞事件循环，60 秒超时保护（批量搜索更慢）
            responses = await asyncio.wait_for(
                asyncio.to_thread(batch_tavily_search, uncached), timeout=60.0
            )
            new_text = format_batch_for_llm(responses)
            for resp in responses:
                _web_search_cache[resp.query] = format_batch_for_llm([resp])
                if len(collector) < MAX_WEB_SOURCES:
                    for r in resp.results:
                        entry = {
                            "query": resp.query,
                            "title": r.title,
                            "url": r.url,
                            "content": r.content[:MAX_SOURCE_CONTENT_LEN],
                        }
                        if entry not in collector:
                            collector.append(entry)
                            if len(collector) >= MAX_WEB_SOURCES:
                                break
        else:
            new_text = ""
        combined = "\n".join(t for t in [*cached_responses_text, new_text] if t)
        return combined or "[未找到相关信息]"

    return batch_web_search


def _fetch_quote_snapshot(stock_code: str) -> dict | None:
    """拉取现价/涨跌幅快照（工具数据）。失败返回 None（调用方如实标注缺失）。"""
    try:
        from finance_agent.data.akshare_client import AKShareClient

        quote = AKShareClient().fetch_stock_quote(stock_code)
        price = quote.get("price")
        pct = quote.get("pct_change")
        if price is None:
            return None
        return {
            "price": float(price),
            "pct_change": round(float(pct), 2) if pct is not None else 0.0,
        }
    except Exception:  # noqa: BLE001 - 快照是增强信息，失败不阻断查股
        return None


def _make_search_stock(api_key: str | None = None):
    """创建 search_stock 工具，注入 api_key 闭包。"""

    async def search_stock(query: str = "") -> str:
        """根据自然语言解析 A 股股票代码

        Args:
            query: 股票名称、代码或描述，如 "茅台"、"600519"、"贵州茅台"
        """
        if not query or not query.strip():
            return "[错误] 查询为空，请提供股票名称或代码"

        from finance_agent.react_agent import search_stock_tool

        # 用 to_thread 包装同步调用：search_stock_tool 内部含 AKShare 同步重试
        # （每次 3 次重试，可能阻塞 10+ 秒），直接调用会阻塞事件循环，
        # 导致 /api/sessions 等所有 API 请求被挂起，前端表现为切换会话卡顿、
        # 刷新后历史会话清空（实际数据未丢，仅加载请求超时）。
        result = await asyncio.to_thread(search_stock_tool, query, api_key)
        return _format_stock_result(result)

    return search_stock


def _format_stock_result(result: dict) -> str:
    """将 search_stock_tool 的 dict 结果格式化为 LLM 友好的字符串。"""
    if not result.get("found"):
        return result.get("message", "未找到匹配的股票")

    candidates = result.get("candidates", [])
    if not candidates:
        return result.get("message", "未找到匹配的股票")

    def _get(c: dict, *keys: str) -> str:
        """兼容 stock_code/code 和 stock_name/name 两种键名。"""
        for k in keys:
            v = c.get(k)
            if v:
                return str(v)
        return ""

    if len(candidates) == 1:
        c = candidates[0]
        code = _get(c, "stock_code", "code")
        name = _get(c, "stock_name", "name")
        text = f"找到股票：{name}({code})"
        # 行情快照（toolize-price-levels）：现价/涨跌幅由工具提供，LLM 引用不心算；
        # 快照缺失如实标注（不伪造数字）
        snapshot = _fetch_quote_snapshot(code)
        if snapshot:
            sign = "+" if (snapshot["pct_change"] or 0) >= 0 else ""
            text += f"\n行情快照：现价 {snapshot['price']} 元（{sign}{snapshot['pct_change']}%）"
        else:
            text += "\n行情快照缺失（当前无法获取现价，请勿臆测价格数值）"
        return text

    lines = ["找到多个候选股票，请确认："]
    for i, c in enumerate(candidates, 1):
        code = _get(c, "stock_code", "code")
        name = _get(c, "stock_name", "name")
        lines.append(f"{i}. {name}({code})")
    return "\n".join(lines)


def _parse_stock_result_text(text: str) -> dict | None:
    """从 _format_stock_result 的输出文本中解析股票代码和名称。

    输入示例：
      "找到股票：中际旭创(300308)"
    返回：
      {"code": "300308", "name": "中际旭创"}
    """
    import re

    m = re.search(r"找到股票：(.+?)\((\d{6})\)", text)
    if m:
        return {"name": m.group(1), "code": m.group(2)}
    return None


# 后台管线任务注册表：session_id -> asyncio.Task，用于测试等待和调试
_background_tasks: dict[str, asyncio.Task] = {}

# ReAct 管线专用 executor：_run_graph（含 matplotlib 图表生成等长任务）在此运行，
# 与事件循环默认 executor 隔离。此前用 run_in_executor(None,...) 共享默认池，
# 管线的长任务（generate_all_charts 的 findfont 全盘扫描）占满默认池后，
# 事件循环上 to_thread 的 SQLite 等快速 IO（/api/sessions、/api/health）排队超时，
# 前端表现为「刷新后会话列表为空」。独立 executor 使管线长任务不再饿死快速 IO。
_pipeline_executor = concurrent.futures.ThreadPoolExecutor(
    max_workers=max(4, min(32, (os.cpu_count() or 4) + 4)),
    thread_name_prefix="pipeline",
)


def _persist_decision_from_tool(
    accumulated: dict, session_id: str, stock_code: str, stock_name: str
) -> None:
    """ReAct 深模式完成时落库观点(旁路:函数内部已吞异常,此处仅兜底日志)。

    经共享入口(ingest.persist_prediction_from_accumulated)与旧 /api/analyze
    路径同语义;同步 SQLite 写,由调用方 to_thread 移出事件循环。
    """
    try:
        persist_prediction_from_accumulated(accumulated, session_id, stock_code, stock_name)
    except Exception as e:  # noqa: BLE001 - 旁路铁律
        logger.warning("ReAct 深模式落库兜底失败(不阻断): %s", e)


# 单字段截断上限（结构化结论摘要各字段长度保护；工具结果总预算见
# harness/context.py tool_result_budget=50000，此处字段级约束先行）
_SUMMARY_FIELD_LIMIT = 600


def _clip(text: str, limit: int = _SUMMARY_FIELD_LIMIT) -> str:
    """截断长文本并附省略号，防单字段撑爆工具输出。"""
    text = str(text).strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def _format_decision_line(decision: Any) -> str:
    """将 TradeDecision（pydantic 或 dict）压成一行关键信息。"""
    if hasattr(decision, "model_dump"):
        data = decision.model_dump()
    elif isinstance(decision, dict):
        data = decision
    else:
        return _clip(str(decision))
    action = data.get("action", "N/A")
    confidence = data.get("confidence")
    conf = f"{confidence:.0%}" if isinstance(confidence, (int, float)) else str(confidence)
    reasoning = _clip(data.get("reasoning", ""))
    return f"{action}（置信度 {conf}）：{reasoning}"


def _build_summary_brief(accumulated: dict, report_md: str) -> str:
    """构造 run_deep_analysis 工具返回给摘要 LLM 的结构化结论摘要。

    修复根因（add-deep-summary-brief）：此前仅截 report_md[:2000]，报告前段
    几乎全是图表列表与分析师章节开头，多空辩论/交易/风控/基金经理章节在
    截断线之后 → 摘要 LLM 看不到结论章节，只能按 deep_mode 指令写
    「本次分析未覆盖」。此处从 accumulated 提取各层结论拼装为固定格式块，
    使摘要 LLM 具备填全全部摘要字段所需的上游材料；缺失字段跳过不造占位。
    """
    blocks: list[str] = []

    # Layer I 分析师一句话（按管线展示顺序 technical/macro/fundamental/sentiment）
    reports = accumulated.get("analyst_reports") or {}
    analyst_lines = []
    for name in ("technical", "macro", "fundamental", "sentiment"):
        report = reports.get(name)
        if not report:
            continue
        summary = (
            report.summary
            if hasattr(report, "summary")
            else (report.get("summary", "") if isinstance(report, dict) else "")
        )
        if summary:
            analyst_lines.append(f"- {name}: {_clip(summary)}")
    if analyst_lines:
        blocks.append("【分析师结论】\n" + "\n".join(analyst_lines))

    # Layer II 多空辩论结论
    conclusion = accumulated.get("research_manager_conclusion")
    if conclusion:
        blocks.append(f"【多空辩论结论】\n{_clip(conclusion)}")

    # Layer III Trader 交易建议
    plan = accumulated.get("trader_plan")
    if plan:
        blocks.append(f"【交易建议】\n{_format_decision_line(plan)}")

    # Layer IV 风控裁决（Risk Judge 的最终交易决策）
    risk_decision = accumulated.get("final_trade_decision")
    if risk_decision:
        blocks.append(f"【风控裁决】\n{_format_decision_line(risk_decision)}")

    # Layer V 基金经理审批
    fm_decision = accumulated.get("fund_manager_decision")
    if fm_decision:
        fm_line = f"【基金经理审批】\n{fm_decision}"
        # #111：审批理由随决策呈现（在场时），缺失则仅展示决策值
        fm_reasoning = accumulated.get("fund_manager_decision_reasoning")
        if fm_reasoning:
            fm_line += f"\n理由：{_clip(fm_reasoning)}"
        blocks.append(fm_line)

    brief = "\n\n".join(blocks)
    if not brief:
        return ""
    return f"报告结论摘要：\n{brief}\n\n"


def _resolve_peer_codes(
    llm_codes: list | None,
    closure_codes: list | None,
    exclude: str | None = None,
) -> list[str] | None:
    """对标股代码归一化（add-peer-comparison）。

    LLM 显式传参优先于请求级闭包注入；归一化=字符串容错（schema string
    fallback 下 LLM 可能传逗号串或 JSON 数组串，JSON 数组直接解析、
    否则按中英文逗号/顿号/空白切分）/strip/剔非 6 位数字/去重/剔除主标的
    自身/上限 3 只。全无效返回 None（不阻断主标的分析）。
    """
    raw = llm_codes if llm_codes else closure_codes
    # 字符串容错：harness build_schema_from_function 对 list[str] | None 注解
    # fallback 为 JSON "string"（llm_client.py 类型内省只认裸类型名），LLM 端
    # 实际可能发 "000858,600519" 逗号串——直接迭代会逐字符校验、静默丢光；
    # 也可能把列表整体 JSON 序列化进 string 槽位（'["000858","600519"]'），
    # 先试 json.loads 解析，失败回落逗号/顿号/空白切分。
    if isinstance(raw, str):
        stripped = raw.strip()
        parsed: object = None
        if stripped.startswith("["):
            try:
                parsed = json.loads(stripped)
            except ValueError:
                parsed = None
        if isinstance(parsed, list):
            raw = parsed
        else:
            raw = stripped.replace("，", ",").replace("、", ",").replace(",", " ").split()
    if not raw:
        return None
    seen: list[str] = []
    for c in raw:
        s = str(c).strip()
        if len(s) == 6 and s.isdigit() and s != exclude and s not in seen:
            seen.append(s)
    return seen[:3] or None


def _make_run_deep_analysis(
    api_key: str | None = None,
    analysis_type: str = "comprehensive",
    peer_codes: list | None = None,
    enable_web_search: bool = False,
    session_id: str | None = None,
    web_sources: list[dict] | None = None,
    llm_config: LLMConfig | None = None,
):
    """创建 run_deep_analysis 流式工具，注入配置闭包。

    LLM 看到 stock_code / stock_name / peer_codes（显式传参优先于闭包注入
    的 peer_codes），其余参数通过闭包注入。
    返回一个异步生成器，yield StreamEvent（PROGRESS + TOOL_RESULT）。
    session_id 用于 Langfuse session 聚合与 trace 属性（ADR-0015）。
    """

    # LLM 工具参数与闭包参数同名：闭包值另存，内层 peer_codes 为 LLM 显式传参
    _closure_peer_codes = peer_codes

    async def run_deep_analysis(
        stock_code: str, stock_name: str = "", peer_codes: list[str] | None = None
    ):
        """运行 5 层深度分析管线

        Args:
            stock_code: A 股股票代码，如 "600519"
            stock_name: 股票名称，如 "贵州茅台"
            peer_codes: 对比请求时的对标股代码列表（1-3 个 6 位代码，须先经
                search_stock 解析确认）；非对比请求留空。同业对比会注入主标的报告，
                不要为对比发起多次调用
        """
        # ReAct 主链路快照与状态兜底（design.md §8 第 1 层）：
        # 工具自带 executor 线程不经 PipelineRunner，故在工具内维护
        # pipeline_snapshot 与会话 status，使「切换会话恢复」对真实主链路生效。
        # 事件流（StreamEvent yield 序列、metadata 结构、chunk_queue）保持不变。
        # 管线起始计时：用于报告落库的 duration_ms（ReAct 路径与 fast path 同语义）
        import time as _time_module

        from finance_agent import session_store as _session_store
        from finance_agent.api import (
            _ALL_NODES,
            LAYER_STEPS,
            _extract_output,
            _merge_update,
        )
        from finance_agent.harness import ActionType, StreamEvent, ToolResult
        from finance_agent.pipeline_runner import (
            PIPELINE_TIMEOUT_DEFAULT_SECONDS,
            _current_node,
            _progress,
            apply_node_event,
            build_layer_tree,
        )
        from finance_agent.stream_registry import registry
        from finance_agent.timeline_builder import (
            NodeTimelineAccumulator,
            timeline_persist_interval,
        )

        _pipeline_start_time = _time_module.time()

        # session_id 非空时才写快照/状态（理论空路径保持现状行为）
        _track_snapshot = bool(session_id)
        # 管线全局超时（环境变量可配置，默认 2400s = 40 分钟；
        # raise-pipeline-timeout-default delta：600s 与 LLM 端点耗时方差不匹配）
        pipeline_timeout = float(
            os.environ.get("PIPELINE_TIMEOUT_SECONDS", str(PIPELINE_TIMEOUT_DEFAULT_SECONDS))
        )
        _tree: list[dict] = build_layer_tree() if _track_snapshot else []
        # 管线节点时序（persist-full-session-timeline）：thinking chunk 按 node 分组
        # 持久化到 sessions.pipeline_timelines。
        # fix-timeline-write-amplification：可变累加器 O(1) 摊销（纯函数逐 token
        # 调用是 O(n²)——全 dict 复制 + content 整串拼接，incident 039 放大器）；
        # 语义等价性由 tests/test_timeline_accumulator.py 对照纯函数钉死。
        _timelineAcc = NodeTimelineAccumulator()

        def _dump_and_persist_timelines(sid: str, timelines: dict) -> int:
            # 序列化一次并写库，返回字节数（自适应间隔输入，design D2）
            payload = json.dumps(timelines, ensure_ascii=False)
            _session_store.update_pipeline_timelines_json(sid, payload)
            return len(payload.encode("utf-8"))

        def _now_ms() -> int:
            import time as _time

            return int(_time.time() * 1000)

        def _persist_snapshot(tree: list[dict], now_ms: int) -> None:
            """把当前 layerTree 组装成快照并落库（与 PipelineRunner._run 同契约）。"""
            _session_store.update_pipeline_snapshot(
                session_id,
                {
                    # layerTree 序列化为内嵌 JSON 字符串，对齐前端 deserializeLayerTree 契约
                    "layerTree": json.dumps(tree, ensure_ascii=False),
                    "currentNodeId": _current_node(tree),
                    "progress": _progress(tree),
                    "updatedAt": now_ms,
                    # 管线启动时间戳（毫秒）：前端刷新重建 running 管线时用作「已用时」
                    # 计时起点，避免用前端本地 Date.now() 导致刷新归零。
                    "pipeline_start_ts": int(_pipeline_start_time * 1000),
                },
            )

        initial_state = {
            "stock_code": stock_code,
            "stock_name": stock_name or stock_code,
            "analysis_type": analysis_type,
            "peer_codes": _resolve_peer_codes(peer_codes, _closure_peer_codes, exclude=stock_code),
            "enable_web_search": enable_web_search,
            "api_key": api_key,
            "web_sources": web_sources or [],
            # 请求级 LLM 配置透传到管线节点（call_llm_streaming / call_llm）
            "llm_config": llm_config,
            # add-report-revision-view：回溯同标的上一份 completed 报告注入 state
            # （与 fast path 同语义）；None = 首份报告；session_id 排除追问自身
            "previous_report_snapshot": _session_store.get_previous_completed_session(
                stock_code, session_id
            ),
        }

        accumulated: dict = dict(initial_state)
        completed: set[str] = set()
        started_nodes: set[str] = set()  # 已发 node_start 的节点（去重）

        # 状态兜底：工具入口置 running（管线本体已在 executor 线程运行）
        if _track_snapshot:
            # _track_snapshot = bool(session_id)，类型收窄供 mypy
            assert session_id is not None  # noqa: S101
            _session_store.update_session_status(session_id, "running")
            # 持久化管线触发锚点：ReAct 路径下锚点 = chat_history 中
            # 最后一条 user 消息索引 + 1，供前端历史重建定位报告插入位置
            _session_store.set_pipeline_anchor(session_id)

        # 在线程中运行同步 graph.stream，避免阻塞事件循环
        # ADR-0015：CallbackHandler 通过 langchain callback 机制工作（不依赖 OTel
        # context 跨线程传播），直接在子线程跑即可。
        loop = asyncio.get_event_loop()
        chunk_queue: asyncio.Queue = asyncio.Queue()

        # 超时/异常后协作式终止生产端（spec pipeline-events：超时 SHALL 终止管线
        # 执行）。图跑在 executor 线程无法强杀；消费端停止后生产端若继续拉流，
        # 会孤儿式烧完剩余 LLM 调用（601700 复盘：超时后 R2 分析师又跑了 3 分钟）。
        graph_cancel = threading.Event()
        # 图完成信号（spec pipeline-events「管线超时与中断检测」MODIFIED）：
        # _run_graph 在 finally 中先于哨兵置位——消费者见 graph_done 即知哨兵
        # 必然到达，排空阶段的 get() 无需超时；看门狗亦据此区分「图未完成
        # 真超时」与「图已完成仅排空滞后」（2026-10-04 拓荆案例：思考洪峰把
        # 消费链压出 ~19 分钟滞后，管线本体完成却被误判超时置 failed）。
        graph_done = threading.Event()

        def _run_graph():
            gen = _stream_graph(initial_state, session_id=session_id)
            try:
                for mode, chunk in gen:
                    if graph_cancel.is_set():
                        break
                    asyncio.run_coroutine_threadsafe(chunk_queue.put((mode, chunk)), loop)
            except Exception as e:
                asyncio.run_coroutine_threadsafe(chunk_queue.put(e), loop)
            finally:
                with contextlib.suppress(BaseException):
                    gen.close()  # GeneratorExit 传播关闭底层 graph.stream，阻断后续节点
                # 先置位图完成信号再投哨兵：保证消费者看到 graph_done 后哨兵
                # 必然到达（排空 get() 无需超时的正确性前提）。注意 graph_done
                # 只证明生产者发完流——chunk_queue 中可能仍缓冲未消费 chunk
                # （含报告前的合法 thinking），消费者侧压缩以 report_seen 或
                # 剩余积压超界（≥ TAIL_KEEP_MAX，incident 039）为准。
                graph_done.set()
                asyncio.run_coroutine_threadsafe(chunk_queue.put(None), loop)

        loop.run_in_executor(_pipeline_executor, _run_graph)

        # event_queue：后台 Task -> SSE 转发层，maxsize 防止消费者断开后无限增长
        event_queue: asyncio.Queue = asyncio.Queue(maxsize=100)

        async def _background_consume():
            nonlocal _tree
            # 节点真实生命周期时间戳（custom 流的 node_start/node_end），
            # 用于给 updates 流的 node_complete 附加 server_*（修复快速节点计时恒 0）。
            node_lifecycle: dict[str, dict] = {}
            # thinking chunk 高频写库节流：上次 update_pipeline_timelines 的时间戳。
            # 修复「事件循环被高频同步 SQLite 写冻结」：写操作 to_thread 移出事件循环，
            # 且按自适应间隔节流（fix-timeline-write-amplification：间隔随上次序列化
            # 字节数伸缩，写带宽 ≤256KB/s 有界——固定 0.5s 窗对 MB 级全量写是 O(n²)
            # 写放大，incident 039 泵限速 ~1.3 事件/s 的根因）。
            last_timeline_persist = 0.0
            last_persist_payload = 0  # 上次实际序列化字节数；0 → 下限 0.5s（现状冷启动）
            # thinking 明细丢弃累计（droppable 背压）：由 sink 内部计数与节流
            # 报告产出标记：updates 分支见到含 final_report 键的 update dict
            # （真实图最后一个 updates chunk 的标记）即置位。tail_thinking 压缩
            # 门槛（incident 039）：graph_done 且（report_seen 或剩余积压
            # ≥ TAIL_KEEP_MAX）——仅 report_seen 时前置积压场景永不压缩。
            report_seen = False
            # incident 039：graph_done 首次观测时的一次性积压快照。-1=未观测；
            # 观测后 = max(0, 当时积压 - TAIL_KEEP_MAX) = 可压缩的 thinking 预算。
            # 只能快照一次：压缩路径无 await，泵一压缩就追平生产者使 qsize 回落，
            # 运行时反复读 qsize 会振荡（永远到不了阈值），而观测时刻生产者已停，
            # qsize 即精确剩余积压，此后不再增长。
            tail_compress_budget = -1
            # 有界入队 + 熔断降级逻辑收敛到 BoundedEventSink（可单测），
            # 闭包薄委托保持既有调用点形态
            sink = BoundedEventSink(event_queue, session_id or "", registry.record_drop)

            async def _put_event(evt, *, droppable: bool = False) -> None:
                """事件入队（spec「发布积压可观测与背压」分级处置），见 BoundedEventSink。"""
                await sink.put(evt, droppable=droppable)

            try:
                while True:
                    # 墙钟超时（spec pipeline-events「管线超时与中断检测」MODIFIED：
                    # 预算耗尽须先核对图完成信号——图已完成时排空延迟不构成超时，
                    # 2026-10-04 拓荆案例）。图未完成时维持自管线启动起算的全局
                    # 预算硬约束：原单次空闲超时在 thinking token 持续流动时永不
                    # 触发（601700 复盘：线上深研管线跑了 71 分钟无人拦截），剩余
                    # 预算耗尽即抛 TimeoutError，走下方既有 failed + failure_reason
                    # 分支。
                    if not graph_done.is_set():
                        _remaining = pipeline_timeout - (_time_module.time() - _pipeline_start_time)
                        if _remaining <= 0:
                            raise TimeoutError(f"管线执行超过 {pipeline_timeout}s 全局预算")
                        item = await asyncio.wait_for(chunk_queue.get(), timeout=_remaining)
                    else:
                        # 图已完成：排空剩余缓冲。thinking 明细在下方压缩丢弃，
                        # 纯 CPU 速度排空（无逐条写库/入队等待），终态有界送达
                        item = await chunk_queue.get()
                    if item is None:
                        break
                    if isinstance(item, Exception):
                        raise item

                    mode, chunk = item

                    # Custom mode: forward thinking tokens + 节点生命周期时间戳
                    # 局限说明（persist-full-session-timeline）：管线节点的 search/tool
                    # 事件不会出现在本工具的 custom/updates 流内——custom 流仅含
                    # thinking/node_start/node_end（nodes/_llm_utils.py 与 nodes/_timing.py
                    # 的 writer），updates 流仅含节点状态 dict；search_start/tool_call 等
                    # 事件是 Agent 层工具（web_search 等）经 stream_agent_to_sse 发出的
                    # 对话流事件，与管线节点无映射。故 ReAct 路径下管线 search/tool 归属
                    # 不可达，仅 fast path（PipelineRunner._run）生效；此处不维护 currentNode。
                    if mode == "custom":
                        if isinstance(chunk, dict):
                            ctype = chunk.get("type")
                            if ctype == "thinking":
                                if graph_done.is_set():
                                    if tail_compress_budget < 0:
                                        tail_compress_budget = max(
                                            0, chunk_queue.qsize() - TAIL_KEEP_MAX
                                        )
                                    if report_seen or tail_compress_budget > 0:
                                        # spec「图完成后工具消费端压缩缓冲明细」：
                                        # 压缩丢弃事件转发（不入 event_queue）使
                                        # 终态 TOOL_RESULT 有界送达。
                                        # incident 039（2026-10-08 茅台）：真实语序下
                                        # thinking 洪峰在 final_report 之前——仅以
                                        # report_seen 为门槛时，消费者要先排完数万条
                                        # 积压才置位，压缩在本场景永不生效，终态被
                                        # 推迟数十分钟-数小时（会话滞留 running 直到
                                        # 重启被 reconcile 补打 interrupted）。故
                                        # graph_done 即按一次性积压预算压缩，仅保留
                                        # ≤ TAIL_KEEP_MAX 的有界尾部（最靠近报告
                                        # chunk 的合法 timeline 内容）走慢路径；
                                        # report_seen 后（报告已产出）的尾部明细
                                        # 无条件压缩。
                                        # 压缩仅作用于事件转发——时间线累积照常
                                        # （fix-timeline-write-amplification 后累积为
                                        # O(1) parts 追加、持久化由自适应间隔限流，
                                        # spec session-persistence「结束 flush 全量」
                                        # 不因消费滞后丢内容；此前与压缩耦合是
                                        # O(n²) 时代的历史包袱，滞后场景会把积压
                                        # thinking 从落库时序里静默丢掉）。
                                        if _track_snapshot:
                                            _timelineAcc.add_thinking_token(
                                                chunk.get("node", ""), chunk.get("token", "")
                                            )
                                        registry.record_drop(session_id or "", "tail_thinking")
                                        if tail_compress_budget > 0:
                                            tail_compress_budget -= 1
                                        continue
                                # 透传管线节点名（此前丢弃 chunk["node"]，导致前端所有管线思考
                                # 归入 nodeTimelines['']，按 agent 分组不可达——真实 bug 修复）
                                node = chunk.get("node", "")
                                # 管线时序持久化：thinking chunk 按 node 累积（仅跟踪快照时）
                                if _track_snapshot:
                                    _timelineAcc.add_thinking_token(node, chunk.get("token", ""))
                                    # 高频写节流 + to_thread：避免每个 thinking chunk 都在
                                    # 事件循环线程同步写 SQLite 冻结事件循环（会话列表超时根因）
                                    now_p = _time_module.time()
                                    if now_p - last_timeline_persist >= timeline_persist_interval(
                                        last_persist_payload
                                    ):
                                        last_timeline_persist = now_p
                                        last_persist_payload = await asyncio.to_thread(
                                            _dump_and_persist_timelines,
                                            session_id,
                                            _timelineAcc.materialize(),
                                        )
                                await _put_event(
                                    StreamEvent.think(
                                        chunk.get("token", ""),
                                        metadata={"node": node} if node else None,
                                    ),
                                    droppable=True,
                                )
                            elif ctype == "node_start":
                                # 节点真实入口时间戳（timed_node 装饰器发出）
                                node_lifecycle.setdefault(chunk["node"], {})["start_ts"] = chunk[
                                    "ts"
                                ]
                            elif ctype == "node_end":
                                # 节点真实出口时间戳与耗时；记录并立即下发 node_timing 事件
                                # （node_complete 由 updates chunk 驱动、可能已先于 node_end 发出，
                                #  故真实耗时经独立 node_timing 下发，前端据此覆盖近似值）。
                                node_name = chunk["node"]
                                lc = node_lifecycle.setdefault(node_name, {})
                                lc["end_ts"] = chunk["ts"]
                                lc["duration_ms"] = chunk.get("duration_ms")
                                if _track_snapshot:
                                    _now = _now_ms()
                                    _tree = apply_node_event(
                                        _tree,
                                        {
                                            "type": "node_timing",
                                            "node_id": node_name,
                                            "server_start_ts": lc.get("start_ts"),
                                            "server_end_ts": lc.get("end_ts"),
                                            "server_duration_ms": lc.get("duration_ms"),
                                        },
                                        _now,
                                    )
                                    # to_thread：快照写移出事件循环
                                    await asyncio.to_thread(_persist_snapshot, _tree, _now)
                                await _put_event(
                                    StreamEvent.progress(
                                        content=f"{node_name} timing",
                                        metadata={
                                            "node": node_name,
                                            "sse_type": "node_timing",
                                            "node_id": node_name,
                                            "server_start_ts": lc.get("start_ts"),
                                            "server_end_ts": lc.get("end_ts"),
                                            "server_duration_ms": lc.get("duration_ms"),
                                        },
                                    )
                                )
                        continue

                    # Updates mode: existing node progress logic
                    for node_name, update in chunk.items():
                        # 报告产出标记（对齐真实图行为：generate_report 的
                        # final_report updates chunk 是最后一个 updates chunk，
                        # 此后到达的 thinking 均为报告后的缓冲明细）
                        if isinstance(update, dict) and "final_report" in update:
                            report_seen = True
                        if node_name not in _ALL_NODES:
                            continue

                        if isinstance(update, dict) and update:
                            _merge_update(accumulated, node_name, update)

                        idx = _ALL_NODES.index(node_name)
                        for i in range(idx + 1):
                            completed.add(_ALL_NODES[i])

                        step_info = {s["node"]: s for s in LAYER_STEPS}.get(node_name, {})

                        # 节点首次出现：先发 node_start（与 fast path 事件序列对齐）
                        if node_name not in started_nodes:
                            started_nodes.add(node_name)
                            start_meta = {
                                "node": node_name,
                                "sse_type": "node_start",
                                "node_id": node_name,
                                "layer": step_info.get("layer", ""),
                                "desc": step_info.get("desc", node_name),
                            }
                            # 附加后端真实入口时间戳（custom 流 node_start 已先行到达）
                            _lc = node_lifecycle.get(node_name, {})
                            if "start_ts" in _lc:
                                start_meta["server_start_ts"] = _lc["start_ts"]
                            if _track_snapshot:
                                _now = _now_ms()
                                _tree = apply_node_event(
                                    _tree,
                                    {
                                        "type": "node_start",
                                        "node_id": node_name,
                                        **(
                                            {"server_start_ts": start_meta["server_start_ts"]}
                                            if "server_start_ts" in start_meta
                                            else {}
                                        ),
                                    },
                                    _now,
                                )
                                # to_thread：快照写移出事件循环
                                await asyncio.to_thread(_persist_snapshot, _tree, _now)
                            await _put_event(
                                StreamEvent.progress(
                                    content=f"{step_info.get('layer', '')}: {step_info.get('desc', node_name)}...",
                                    metadata=start_meta,
                                )
                            )

                        output = _extract_output(
                            node_name, update if isinstance(update, dict) else {}, accumulated
                        )

                        # yield PROGRESS with detailed metadata -> stream_agent_to_sse 映射为 node_complete
                        # 注：真实耗时经独立 node_timing 事件下发（node_end 到达时），此处不附加，
                        # 因 node_end 时序上晚于本 updates chunk，duration 此刻尚不可得。
                        if _track_snapshot:
                            _now = _now_ms()
                            _tree = apply_node_event(
                                _tree,
                                {"type": "node_complete", "node_id": node_name, "output": output},
                                _now,
                            )
                            # to_thread：快照写移出事件循环
                            await asyncio.to_thread(_persist_snapshot, _tree, _now)
                            # 管线时序收口：node_complete 将该节点末尾未完成 thinking 置 done
                            _timelineAcc.apply_node_complete(node_name)
                            # 节点边界即时冲刷（spec「与 pipeline_snapshot 同节奏」锚点，
                            # 不经自适应间隔）；to_thread：同步 SQLite 写移出事件循环
                            await asyncio.to_thread(
                                _dump_and_persist_timelines,
                                session_id,
                                _timelineAcc.materialize(),
                            )
                        await _put_event(
                            StreamEvent.progress(
                                content=f"{step_info.get('layer', '')}: {step_info.get('desc', node_name)} ✓",
                                metadata={
                                    "node": node_name,
                                    "sse_type": "node_complete",
                                    "node_id": node_name,
                                    "layer": step_info.get("layer", ""),
                                    "desc": step_info.get("desc", node_name),
                                    "completed": sorted(completed),
                                    "progress": len(completed) / len(LAYER_STEPS),
                                    "output": output,
                                },
                            )
                        )
            except TimeoutError:
                # 管线全局超时：置 failed + failure_reason，并协作式终止生产端线程
                graph_cancel.set()
                if _track_snapshot:
                    _session_store.update_session_status(
                        session_id, "failed", failure_reason="管线执行超时"
                    )
                await _put_event(
                    StreamEvent.progress(
                        content="分析失败：管线执行超时",
                    )
                )
                # 超时也必须给 Agent 主循环一个 TOOL_RESULT：空结果会让模型误判
                # 「临时故障」盲目重试，且用户看不到失败原因（601700 复盘）。
                # spec「管线异常终止的工具结果显式错误语义」：is_error=True +
                # 机器可读 pipeline_timeout 标志（摘要 LLM 可区分「管线异常终止」
                # 与「正常无报告」，harness 据此置位 analysis_completed 防自动重跑）。
                _timeout_note = (
                    f"深度分析管线执行超时：全局预算 {pipeline_timeout:.0f}s 已耗尽，"
                    "管线已终止且会话标记为失败。如需继续可将环境变量 "
                    "PIPELINE_TIMEOUT_SECONDS 调大后重新发起分析。"
                )
                await _put_event(
                    StreamEvent(
                        event_type=ActionType.TOOL_RESULT,
                        content=_timeout_note,
                        tool_result=ToolResult(
                            tool_call_id="",
                            name=TOOL_RUN_DEEP_ANALYSIS,
                            output=_timeout_note,
                            is_error=True,
                            metadata={"pipeline_timeout": True},
                        ),
                    )
                )
            except Exception as e:
                # 异常兜底：置 failed + failure_reason，并协作式终止生产端线程
                graph_cancel.set()
                if _track_snapshot:
                    await asyncio.to_thread(
                        _session_store.update_session_status,
                        session_id,
                        "failed",
                        failure_reason=f"{type(e).__name__}: {e}",
                    )
                # 下发错误事件，使 SSE 转发层能感知异常（不 re-raise，仅通知）
                _error_note = f"深度分析管线异常终止：{type(e).__name__}: {e}"
                await _put_event(
                    StreamEvent.progress(
                        content=f"分析失败：{type(e).__name__}: {e}",
                    )
                )
                # 与超时同因：异常路径也必须发 TOOL_RESULT，Agent 才能向用户
                # 转述失败原因而非拿到空结果；is_error=True + pipeline_error
                # 机器标志（spec 显式错误语义，同超时分支）
                await _put_event(
                    StreamEvent(
                        event_type=ActionType.TOOL_RESULT,
                        content=_error_note,
                        tool_result=ToolResult(
                            tool_call_id="",
                            name=TOOL_RUN_DEEP_ANALYSIS,
                            output=_error_note,
                            is_error=True,
                            metadata={"pipeline_error": True},
                        ),
                    )
                )
            else:
                # 正常结束：写最终快照并置 completed（组装 report metadata 前）
                if _track_snapshot:
                    await asyncio.to_thread(_persist_snapshot, _tree, _now_ms())
                    # flush：thinking 高频写按节流可能跳过末尾 chunk，结束时补写完整时序
                    await asyncio.to_thread(
                        _dump_and_persist_timelines, session_id, _timelineAcc.materialize()
                    )

                report_md = accumulated.get("final_report", "")

                if not report_md:
                    # 管线阻断终态可见化（update-decision-price-gate，spec
                    # pipeline-events「管线阻断终态可见化」SHALL 路径无关）：
                    # 图流正常耗尽但未产出报告（决策价位门禁 fail / 勾稽 FAIL）
                    # 时不得标 completed 空报告。与 fast path
                    # _run_graph_streaming 收尾同语义：置 failed + 归因
                    # failure_reason；未经审批的决策 MUST NOT 落战绩，故跳过
                    # update_session_report 与 _persist_decision_from_tool；
                    # 并复用超时/异常分支的通知形态下发阻断 TOOL_RESULT，
                    # 使 Agent 能向用户转述失败而非拿到空报告（601700 复盘）。
                    from finance_agent.api import _blocked_failure_reason

                    reason, _blocked_node = _blocked_failure_reason(accumulated)
                    if _track_snapshot:
                        # _track_snapshot = bool(session_id)，类型收窄供 mypy（同工具入口先例）
                        assert session_id is not None  # noqa: S101
                        await asyncio.to_thread(
                            _session_store.update_session_status,
                            session_id,
                            "failed",
                            failure_reason=reason,
                        )
                    _blocked_note = f"深度分析未产出报告，会话已标记为失败：{reason}"
                    await _put_event(
                        StreamEvent(
                            event_type=ActionType.TOOL_RESULT,
                            content=_blocked_note,
                            tool_result=ToolResult(
                                tool_call_id="",
                                name=TOOL_RUN_DEEP_ANALYSIS,
                                output=_blocked_note,
                                is_error=True,
                                metadata={
                                    "pipeline_blocked": True,
                                    "failure_reason": reason,
                                },
                            ),
                        )
                    )
                else:
                    # 报告数据落库（与 fast path _run_graph_streaming 同语义）：
                    # ReAct 路径此前仅置 status，不落 report_markdown/chart_data/duration_ms，
                    # 导致刷新重建时报告正文/图表丢失、耗时显示"未知"。
                    if session_id:
                        await asyncio.to_thread(
                            _session_store.update_session_report,
                            session_id,
                            report_markdown=report_md,
                            chart_data=accumulated.get("chart_data") or {},
                            analyst_reports=accumulated.get("analyst_reports") or {},
                            agent_process=accumulated.get("agent_process") or {},
                            analyst_summaries=accumulated.get("analyst_summaries") or {},
                            duration_ms=int((_time_module.time() - _pipeline_start_time) * 1000),
                            file_paths=accumulated.get("file_paths") or {},
                            status="completed",
                            # add-report-revision-view：终稿决策随报告落库（与 fast path 同语义），
                            # 供同标的下份报告增量摘要回溯；无终稿落 NULL
                            final_trade_decision=accumulated.get("final_trade_decision"),
                        )
                        # add-track-record:ReAct 深模式完成即落库观点(全量记录,含 reject)。
                        # 挂点曾只存在于旧 /api/analyze 路径,深模式经工具路径无挂点 →
                        # predictions 恒为 0(真实事故);现经共享入口与旧路径同语义。
                        await asyncio.to_thread(
                            _persist_decision_from_tool,
                            accumulated,
                            session_id,
                            stock_code,
                            stock_name,
                        )
                        # add-user-feedback:把本次深分析运行的 Langfuse trace 关联到
                        # session(反馈端点按 session 解析最近一次运行落 score)
                        _tid = accumulated.get("langfuse_trace_id")
                        if not _tid:
                            try:
                                from finance_agent.langfuse_tracing import (
                                    get_langfuse as _lf_get,
                                )

                                _lfc = _lf_get()
                                if _lfc is not None:
                                    _tid = _lfc.get_current_trace_id()
                            except Exception:  # noqa: BLE001 - 取不到则降级(前端本地 toggle)
                                _tid = None
                        if _tid:
                            await asyncio.to_thread(
                                _session_store.set_session_trace_id, session_id, _tid
                            )

                    metadata = {
                        "chart_data": accumulated.get("chart_data") or {},
                        "analyst_reports": accumulated.get("analyst_reports") or {},
                        "stock_code": stock_code,
                        "stock_name": accumulated.get("stock_name") or stock_name or stock_code,
                        "report_markdown": report_md,
                        "web_sources": web_sources or [],
                        "file_paths": accumulated.get("file_paths") or {},
                        "sse_type": "report_ready",
                    }

                    # LLM 上下文放结构化结论摘要 + 报告正文节选（修复 add-deep-summary-brief：
                    # 仅截 report_md[:2000] 时辩论/交易/风控/基金经理章节不可见 → 「本次分析未覆盖」）
                    llm_output = f"深度分析完成。股票：{metadata['stock_name']}({stock_code})。\n"
                    llm_output += f"报告已生成，共 {len(report_md)} 字符。\n"
                    brief = _build_summary_brief(accumulated, report_md)
                    if brief:
                        llm_output += f"\n{brief}"
                    if len(report_md) > 2000:
                        llm_output += f"报告正文（节选）：\n{report_md[:2000]}...\n"
                    else:
                        llm_output += f"报告内容：\n{report_md}"

                    await _put_event(
                        StreamEvent(
                            event_type=ActionType.TOOL_RESULT,
                            content=llm_output,
                            tool_result=ToolResult(
                                tool_call_id="",
                                name=TOOL_RUN_DEEP_ANALYSIS,
                                output=llm_output,
                                metadata=metadata,
                            ),
                        )
                    )
            finally:
                await _put_event(None)
                if session_id:
                    _background_tasks.pop(session_id, None)
                    # drop 计数随运行终结清理（issue #227.1：慢性泄漏）；哨兵
                    # 自身若 undeliverable 已计数并日志，此处清除不影响审计
                    registry.clear_drop_counts(session_id)

        bg_task = asyncio.create_task(_background_consume())
        if session_id:
            _background_tasks[session_id] = bg_task

        # SSE 转发层：从 event_queue 读取并 yield
        while True:
            item = await event_queue.get()
            if item is None:
                break
            yield item

    return run_deep_analysis


def _build_trace_output(accumulated: dict) -> dict:
    """从管线 accumulated 状态构建根 span output 摘要（会话内容可见）。

    只放摘要级内容防 trace 体积膨胀：各 agent 产出摘要 + 最终报告前 500 字符。
    """
    out: dict = {}
    for key in ("stock_code", "stock_name", "analysis_type"):
        if accumulated.get(key):
            out[key] = accumulated[key]
    final_report = accumulated.get("final_report")
    if final_report:
        out["final_report_summary"] = final_report[:500] + ("…" if len(final_report) > 500 else "")
    reports = accumulated.get("analyst_reports") or {}
    if reports:
        out["analyst_reports"] = {
            k: (v.get("summary", "")[:200] if isinstance(v, dict) else str(v)[:200])
            for k, v in reports.items()
        }
    for key in ("trader_plan", "final_trade_decision", "fund_manager_decision"):
        v = accumulated.get(key)
        if v:
            out[key] = v if isinstance(v, dict) else str(v)[:300]
    return out


def _stream_graph(
    initial_state: dict,
    config: dict | None = None,
    session_id: str | None = None,
):
    """执行 5 层管线同步流式迭代。

    ADR-0015：手动建 root span + propagate_attributes(session) 包裹 graph.stream，
    使 5 层管线节点 + call_llm generation + 数据源 span 经 OTel contextvars 挂到
    root span 下（仿 quick 模式 react_loop）。v4 CallbackHandler 在 LangGraph
    graph.stream 不建主 trace，必须手动建 root，否则内部 span 各自成孤立 trace。
    CallbackHandler 仍作 callbacks 注入（增益项，若 v4 后续支持 LangGraph 节点 span）。
    """
    from finance_agent.graph import build_5layer_graph

    if config is None:
        config = {"recursion_limit": 100}

    from finance_agent.langfuse_tracing import (
        build_eval_metadata,
        eval_analysis_query,
        get_callback_handler,
        get_langfuse,
    )

    _handler = get_callback_handler()
    _lf = get_langfuse()
    if _handler is not None:
        config = {**config, "callbacks": [*config.get("callbacks", []), _handler]}

    # 手动 root span + session 聚合（仿 quick react_loop，agent_factory.py:1066）
    _root_cm: contextlib.AbstractContextManager[Any] = contextlib.nullcontext()
    _propagate_cm: contextlib.AbstractContextManager[Any] = contextlib.nullcontext()
    if _lf is not None:
        _stock = initial_state.get("stock_name") or initial_state.get("stock_code") or "unknown"
        _root_cm = _lf.start_as_current_observation(
            as_type="span",
            name=f"deep_analysis:{_stock}",
            input={
                "stock_code": initial_state.get("stock_code"),
                "query": eval_analysis_query(
                    initial_state.get("query"),
                    initial_state.get("stock_name") or "",
                    initial_state.get("stock_code") or "",
                ),
            },
        )
        if session_id:
            try:
                from langfuse import propagate_attributes

                _propagate_cm = propagate_attributes(session_id=session_id)
            except Exception:  # noqa: S110
                pass
    # 本地累计摘要字段（同线程）：进入 root span 后从 initial_state 与 updates
    # chunk 累计 agent 产出，finally 内、根 span 退出前写 output——
    # 修复 #67「跨线程 post-exit update 被 Langfuse v4 丢弃 → output=null」。
    _local_acc: dict = {}
    for _k in ("stock_code", "stock_name", "analysis_type"):
        if initial_state.get(_k):
            _local_acc[_k] = initial_state[_k]
    _root_obs = _root_cm.__enter__()
    _propagate_cm.__enter__()

    graph = build_5layer_graph()

    try:
        for _mode, _chunk in graph.stream(  # type: ignore[call-overload]
            initial_state,
            config=config,
            stream_mode=["updates", "custom"],
        ):
            if _mode == "updates" and isinstance(_chunk, dict):
                for _node_name, _update in _chunk.items():
                    if isinstance(_update, dict):
                        for _key in (
                            "analyst_reports",
                            "final_report",
                            "trader_plan",
                            "final_trade_decision",
                            "fund_manager_decision",
                            "debate_history",
                            "research_manager_conclusion",
                        ):
                            if _key not in _update:
                                continue
                            # analyst_reports 跨分析师节点 dict 合并（对齐
                            # _merge_update 语义，保证 trace output 含全部分析师）
                            if _key == "analyst_reports" and isinstance(_update[_key], dict):
                                _local_acc.setdefault("analyst_reports", {})
                                _local_acc["analyst_reports"].update(_update[_key])
                            # debate_history 每次 update 是单条消息（reducer 追加语义）
                            elif _key == "debate_history" and isinstance(_update[_key], list):
                                _local_acc.setdefault("debate_history", []).extend(_update[_key])
                            else:
                                _local_acc[_key] = _update[_key]
            yield _mode, _chunk
    finally:
        # 在 root span 退出前写入 output + 评估数据 metadata（保证不被 Langfuse 丢弃；
        # build_eval_metadata 供 hosted evaluator 取完整报告/分析师/辩论/裁决数据）
        if _root_obs is not None:
            with contextlib.suppress(Exception):
                _root_obs.update(
                    output=_build_trace_output(_local_acc),
                    metadata=build_eval_metadata(_local_acc),
                )
        with contextlib.suppress(Exception):
            _propagate_cm.__exit__(None, None, None)
        with contextlib.suppress(Exception):
            _root_cm.__exit__(None, None, None)
        if _lf is not None:
            with contextlib.suppress(Exception):
                _lf.flush()


# ───────────────────────────────────────────────
# 工厂函数
# ───────────────────────────────────────────────


def _build_context_budget(
    model: str, api_key: str | None, base_url: str | None
) -> ContextBudget | None:
    """按解析出的 react profile capability 派生 ContextBudget（设计档案 §12）。

    与 harness/litellm_client.chat_stream 同语义：model/baseUrl/apiKey 三者
    齐备才作为请求级 llm_config 下发，否则交 resolver 用 env/preset。
    resolver 失败回落默认预算（ContextBudget()），绝不阻断 Agent 构建。
    """
    llm_config: dict[str, Any] | None = None
    if model and base_url and api_key:
        llm_config = {"model": model, "baseUrl": base_url, "apiKey": api_key}
    try:
        from finance_agent.llm.resolver import resolve_profile

        profile = resolve_profile(purpose="react", llm_config=llm_config)
        return ContextBudget.from_capability(profile.capability)
    except Exception:  # noqa: BLE001 -- 预算派生失败不阻断 agent 构建
        return ContextBudget()


def _trace_tool(name: str) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """工具调用埋点（add-toolcall-evaluation 取证依赖）：Langfuse span。

    纯可观测性：Langfuse 未配置（get_langfuse()=None）时原样直通零开销；
    span 记录 name=tool_call:<工具名>、入参、执行耗时、异常标记 metadata.tool_error。
    同步/异步工具双路支持（functools.wraps 保留原签名；行为/异常传播不变）。
    """

    def deco(fn: Callable[..., Any]) -> Callable[..., Any]:
        if inspect.iscoroutinefunction(fn):

            @functools.wraps(fn)
            async def wrapped(*args: Any, **kwargs: Any) -> Any:
                from finance_agent.langfuse_tracing import get_langfuse

                client = get_langfuse()
                if client is None:
                    return await fn(*args, **kwargs)
                call_arg = kwargs if kwargs else (args[0] if args else None)
                try:
                    with client.start_as_current_observation(
                        as_type="span", name=f"tool_call:{name}", input={"call": call_arg}
                    ) as span:
                        result = await fn(*args, **kwargs)
                        with contextlib.suppress(Exception):  # 埋点写 output 失败不影响业务
                            span.update(output=str(result)[:2000])
                        return result
                except Exception as e:  # noqa: BLE001 - 业务异常照常传播，埋点标记错误
                    with contextlib.suppress(Exception):
                        span.update(output={"error": str(e)}, metadata={"tool_error": str(e)})
                    raise

            return wrapped

        @functools.wraps(fn)
        def wrapped(*args: Any, **kwargs: Any) -> Any:
            from finance_agent.langfuse_tracing import get_langfuse

            client = get_langfuse()
            if client is None:
                return fn(*args, **kwargs)
            call_arg = kwargs if kwargs else (args[0] if args else None)
            try:
                with client.start_as_current_observation(
                    as_type="span", name=f"tool_call:{name}", input={"call": call_arg}
                ) as span:
                    result = fn(*args, **kwargs)
                    with contextlib.suppress(Exception):  # 埋点写 output 失败不影响业务
                        span.update(output=str(result)[:2000])
                    return result
            except Exception as e:  # noqa: BLE001
                with contextlib.suppress(Exception):
                    span.update(output={"error": str(e)}, metadata={"tool_error": str(e)})
                raise

        return wrapped

    return deco


def build_agent(
    mode: str = "quick",
    api_key: str | None = None,
    analysis_type: str = "comprehensive",
    peer_codes: list | None = None,
    enable_web_search: bool = False,
    llm_config: LLMConfig | None = None,
    **kwargs: Any,
) -> Agent:
    """构建指定模式的 ReAct Agent。

    Args:
        mode: 模式 ("quick" | "deep" | "follow-up")
        api_key: LLM API 密钥
        analysis_type: 分析类型（深度模式，闭包注入 run_deep_analysis）
        peer_codes: 对标股代码（深度模式，闭包注入）
        enable_web_search: 是否启用搜索（深度模式，闭包注入）
        llm_config: 请求级 LLM 配置（model/baseUrl/apiKey/thinking），覆盖环境变量
        **kwargs: 额外参数（session_id 等）

    Returns:
        配置好的 Agent 实例
    """
    # model 解析优先级：llm_config.model → 环境变量 LLM_MODEL → 默认值
    model = (
        (llm_config.model if llm_config and llm_config.model else None)
        or os.getenv("LLM_MODEL")
        or "deepseek/deepseek-chat"
    )
    # api_key 解析优先级：llm_config.apiKey → 顶层 api_key → 环境变量（在 LiteLLMClient 内回退）
    effectiveApiKey = (llm_config.apiKey if llm_config and llm_config.apiKey else None) or api_key
    # base_url / thinking 从 llm_config 解析，None 时由 LiteLLMClient 回退环境变量/默认值
    effectiveBaseUrl = llm_config.baseUrl if llm_config and llm_config.baseUrl else None
    effectiveThinking = llm_config.thinking if llm_config and llm_config.thinking else None

    # 提前解析 mode prompt 元数据（ADR-0015 Task 4）：prompt_name/prompt_version
    # 注入 LLM client 实例字段，使 ReAct 链路每次 chat_stream 都挂到 generation metadata。
    if mode == "quick":
        _mode_pinfo = _quick_mode_prompt()
    elif mode == "deep":
        _mode_pinfo = _deep_mode_prompt()
    elif mode == "follow-up":
        _mode_pinfo = _follow_up_mode_prompt()
    else:
        _mode_pinfo = None

    llm_client = _make_llm_client(
        model,
        api_key=effectiveApiKey,
        base_url=effectiveBaseUrl,
        thinking=effectiveThinking,
        prompt_name=_mode_pinfo.prompt_name if _mode_pinfo else None,
        prompt_version=_mode_pinfo.prompt_version if _mode_pinfo else None,
    )

    # 预算按 capability 派生（设计档案 §12）：Agent 上下文预算跟随解析 profile
    context_budget = _build_context_budget(model, effectiveApiKey, effectiveBaseUrl)

    if mode == "quick":
        prompt = _mode_pinfo.template.format(now=_now()) if _mode_pinfo else ""
        agent = Agent(
            model=model,
            api_key=api_key,
            system_prompt=prompt,
            permission_mode=PermissionMode.YOLO,
            max_iterations=3,
            llm=llm_client,
            context_budget=context_budget,
        )
        # TESTING=1 时注册 stub web_search（固定结果，不调真实 Tavily），
        # 与 StubLLMClient 的 tool_call 场景配合，确定性复现"思考->web search->思考"。
        from finance_agent.api import TESTING

        agent.tools.register(
            _trace_tool(TOOL_WEB_SEARCH)(_stub_web_search if TESTING else _web_search),
            name=TOOL_WEB_SEARCH,
        )
        session_id = kwargs.get("session_id")
        if session_id:
            _inject_chat_history(agent, session_id)
        return agent

    if mode == "deep":
        prompt = _mode_pinfo.template.format(now=_now()) if _mode_pinfo else ""
        agent = Agent(
            model=model,
            api_key=api_key,
            system_prompt=prompt,
            permission_mode=PermissionMode.YOLO,
            max_iterations=10,
            llm=llm_client,
            context_budget=context_budget,
        )
        web_sources_collector: list[dict] = []
        agent.tools.register(
            _trace_tool(TOOL_SEARCH_STOCK)(_make_search_stock(api_key)), name=TOOL_SEARCH_STOCK
        )
        agent.tools.register(
            _trace_tool(TOOL_RUN_DEEP_ANALYSIS)(
                _make_run_deep_analysis(
                    api_key=api_key,
                    analysis_type=analysis_type,
                    peer_codes=peer_codes,
                    enable_web_search=enable_web_search,
                    session_id=kwargs.get("session_id"),
                    web_sources=web_sources_collector,
                    llm_config=llm_config,
                )
            ),
            name=TOOL_RUN_DEEP_ANALYSIS,
        )
        # TESTING=1 时注册 stub web_search（固定结果，不调真实 Tavily），
        # 与 quick 分支同一 stub 逻辑，使深度模式澄清阶段也能确定性复现
        # "思考->web search->思考"时间序列（agent-turn-box-display delta task 5.4）。
        from finance_agent.api import TESTING

        agent.tools.register(
            _trace_tool(TOOL_WEB_SEARCH)(
                _stub_web_search
                if TESTING
                else _make_web_search_with_collector(web_sources_collector)
            ),
            name=TOOL_WEB_SEARCH,
        )
        agent.tools.register(
            _trace_tool(TOOL_BATCH_WEB_SEARCH)(_make_batch_web_search(web_sources_collector)),
            name=TOOL_BATCH_WEB_SEARCH,
        )
        session_id = kwargs.get("session_id")
        if session_id:
            _inject_chat_history(agent, session_id)
        return agent

    if mode == "follow-up":
        session_id = kwargs.get("session_id")
        if not session_id:
            raise ValueError("follow-up 模式需要 session_id")

        session = _load_session(session_id)
        system_prompt = _build_follow_up_prompt(
            session, template=_mode_pinfo.template if _mode_pinfo else None
        )

        agent = Agent(
            model=model,
            api_key=api_key,
            system_prompt=system_prompt,
            permission_mode=PermissionMode.YOLO,
            max_iterations=3,
            llm=llm_client,
            context_budget=context_budget,
        )
        agent.tools.register(_trace_tool(TOOL_WEB_SEARCH)(_web_search), name=TOOL_WEB_SEARCH)
        return agent

    raise ValueError(f"未知模式: {mode}")


def _now() -> str:
    from datetime import datetime
    from zoneinfo import ZoneInfo

    return datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d %H:%M")


def _make_llm_client(
    model: str,
    api_key: str | None = None,
    base_url: str | None = None,
    thinking: str | None = None,
    prompt_name: str | None = None,
    prompt_version: str | int | None = None,
):
    """创建 litellm 适配的 LLM 客户端。

    TESTING=1 时返回 StubLLMClient（按固定节奏吐文本 delta），不连真 LLM。
    见 docs/superpowers/plans/2026-07-26-f3a-e2e-core-specs.md Task 1。

    prompt_name / prompt_version（ADR-0015 Task 4）：透传给 LiteLLMClient 实例字段，
    使 ReAct 链路 chat_stream 的 generation metadata 含 prompt 元数据。
    """
    from finance_agent.api import TESTING

    if TESTING:
        # F3a: 返回可控 stub LLM 客户端（按固定节奏吐文本 delta）
        # STUB_SCENARIO=tool_call 时启用"思考1->tool_call(web_search)->思考2->回答"场景，
        # 用于 E2E 确定性验证思考-搜索-思考时间序列（agent-turn-box-display delta）。
        from finance_agent.harness.stub_llm_client import StubLLMClient

        return StubLLMClient(scenario=os.getenv("STUB_SCENARIO"))

    from finance_agent.harness.litellm_client import LiteLLMClient

    return LiteLLMClient(
        model=model,
        api_key=api_key,
        base_url=base_url,
        thinking=thinking,
        prompt_name=prompt_name,
        prompt_version=prompt_version,
        # agent 标签（Task 4）：harness ReAct 链路的 generation observation 用
        # react_agent 命名，使 trace 可按 agent 归属/过滤。
        agent="react_agent",
    )


def _inject_chat_history(agent: Agent, session_id: str) -> None:
    """将 session 中的历史对话以标准 user/assistant 消息轮次注入 agent 上下文。

    用标准消息角色（而非 system prompt 纯文本）注入历史，
    使 LLM 能正确理解多轮对话的指代关系。
    """
    session = _load_session(session_id)
    chat_history_raw = session.get("chat_history", [])
    if isinstance(chat_history_raw, str):
        try:
            chat_history = json.loads(chat_history_raw)
        except json.JSONDecodeError:
            chat_history = []
    else:
        chat_history = chat_history_raw

    for h in chat_history:
        role = h.get("role", "user")
        content = h.get("content", "")
        if role == "user":
            agent.context.append_user(content)
        else:
            agent.context.append_assistant(content)


def _load_session(session_id: str) -> dict:
    """从 session_store 加载 session。"""
    from finance_agent.session_store import get_session

    session = get_session(session_id)
    if not session:
        raise ValueError(f"Session not found: {session_id}")
    return session


def _build_follow_up_prompt(session: dict, template: str | None = None) -> str:
    """构建追问模式的 system prompt，注入报告上下文。

    Args:
        session: session 数据
        template: 可选 prompt 模板文本。显式传入避免重复拉取 Langfuse
            （caller build_agent 已持有 PromptInfo）。None 时内部拉取。
    """
    import json

    report_md = session.get("report_markdown", "")
    report_excerpt = report_md[:6000]

    analyst_summaries_raw = session.get("analyst_summaries", "{}")
    if isinstance(analyst_summaries_raw, str):
        try:
            analyst_summaries = json.loads(analyst_summaries_raw)
        except json.JSONDecodeError:
            analyst_summaries = {}
    else:
        analyst_summaries = analyst_summaries_raw

    chat_history_raw = session.get("chat_history", "[]")
    if isinstance(chat_history_raw, str):
        try:
            chat_history = json.loads(chat_history_raw)
        except json.JSONDecodeError:
            chat_history = []
    else:
        chat_history = chat_history_raw

    # 格式化聊天历史
    history_lines = []
    for msg in chat_history:
        role = msg.get("role", "unknown")
        content = msg.get("content", "")
        label = "用户" if role == "user" else "助手"
        history_lines.append(f"{label}: {content}")
    history_text = "\n".join(history_lines) if history_lines else "（无历史对话）"

    tmpl = template if template is not None else _follow_up_mode_prompt().template
    return tmpl.format(
        now=_now(),
        report_excerpt=report_excerpt,
        analyst_summaries=json.dumps(analyst_summaries, ensure_ascii=False),
        chat_history=history_text,
        created_at=session.get("created_at", "未知"),
    )


# ───────────────────────────────────────────────
# SSE 事件流映射
# ───────────────────────────────────────────────


async def stream_agent_to_sse(
    agent: Agent,
    user_input: str,
    on_metadata: Callable[[dict], None] | None = None,
    on_resolved: Callable[[str, str], None] | None = None,
    extra_events: dict | None = None,
    force_tool: bool = False,
    session_id: str | None = None,
    user_id: str | None = None,
    heartbeat_interval: float = 10.0,
):
    """运行 Agent 并将 StreamEvent 映射为前端 SSE 格式。

    Args:
        agent: ReAct Agent 实例
        user_input: 用户输入
        on_metadata: TOOL_METADATA 事件的回调（用于 session 创建等）
        on_resolved: 股票代码解析完成回调 (stock_code, stock_name)
        extra_events: 额外的事件上下文（如 session_id 用于 session_created）
        session_id: Langfuse session 聚合 ID（ADR-0015）。设置后用 react_loop span
            包裹 ReAct 执行并 propagate_attributes(session_id)。
        user_id: Langfuse user 聚合 ID（ADR-0015）。设置后 propagate_attributes(user_id)。
        heartbeat_interval: SSE 空闲心跳间隔（秒），默认 10s，与设计文档"5~10 秒"对齐。

    Yields:
        SSE 格式的字符串: "data: {...}\\n\\n"
    """
    from finance_agent.harness import ActionType

    last_web_search_query = ""  # 追踪 web_search 的查询参数
    _final_answer_parts: list[str] = []  # ANSWER 事件内容累积（退出 react_loop 时写入 span output）

    # ADR-0015: react_loop span 包裹 ReAct 执行，使顶层 trace 结构为
    # [react_loop] -> [search_stock] / [run_deep_analysis span -> 5 层]。
    # 手动管理上下文以避免重构大段事件映射循环；异常路径下 span 由 OTel
    # span processor 周期性导出兜底。
    from contextlib import nullcontext as _nullcontext

    from finance_agent.langfuse_tracing import get_langfuse as _get_langfuse

    _lf = _get_langfuse()
    _react_cm: contextlib.AbstractContextManager[Any] = _nullcontext()
    _propagate_cm: contextlib.AbstractContextManager[Any] = _nullcontext()
    if _lf is not None:
        _react_cm = _lf.start_as_current_observation(
            as_type="span", name="react_loop", input={"query": user_input}
        )
        if session_id or user_id:
            try:
                from langfuse import propagate_attributes

                _propagate_cm = propagate_attributes(session_id=session_id, user_id=user_id)
            except Exception:  # noqa: S110
                pass
    _react_obs = _react_cm.__enter__()
    _propagate_cm.__enter__()
    agentGen = agent.run(user_input, force_tool=force_tool)
    nextTask = asyncio.create_task(agentGen.__anext__())
    try:
        while True:
            done, _ = await asyncio.wait({nextTask}, timeout=heartbeat_interval)
            if not done:
                # 空闲超时：发送心跳注释，不取消正在等待的 nextTask
                yield ": heartbeat\n\n"
                continue
            try:
                event = nextTask.result()
            except StopAsyncIteration:
                break
            ts = _now()

            if event.event_type == ActionType.ANSWER:
                _final_answer_parts.append(event.content)
                yield _sse({"type": "chat_token", "token": event.content, "timestamp": ts})

            elif event.event_type == ActionType.THINK:
                # 原生思考增量（DeepSeek reasoning_content），与回答（chat_token）分离。
                # 管线运行期间的思考带 node metadata（管线节点名），透传给前端按 agent 阶段分组。
                meta = event.metadata or {}
                payload: dict = {"type": "thinking_token", "token": event.content, "timestamp": ts}
                if meta.get("node"):
                    payload["node"] = meta["node"]
                yield _sse(payload)

            elif event.event_type == ActionType.TOOL_CALL:
                tc = event.tool_call
                # permission_required 事件（tool_call 为 None）不下发 SSE，但必须继续
                # 走完循环末尾的 nextTask 重建——不得用 continue，否则下一轮 wait 等待
                # 已完成的旧 task，无限空转死锁（快速模式"卡在搜索中"的根因）
                if tc:
                    name = tc.name or ""
                    args = tc.arguments if tc else {}
                    # web_search / batch_web_search 都发 search_start，驱动前端搜索横幅。
                    # batch_web_search 改为默认搜索工具后必须同样覆盖，否则前端把该
                    # tool_call 当搜索类丢弃（等 search_* 驱动），结果搜索无任何渲染。
                    if name == "web_search":
                        last_web_search_query = str(args.get("query", ""))
                        yield _sse(
                            {
                                "type": "search_start",
                                "query": last_web_search_query,
                                "timestamp": ts,
                            }
                        )
                    elif name == "batch_web_search":
                        queries = args.get("queries") or []
                        last_web_search_query = (
                            "；".join(str(q) for q in queries) if queries else "批量搜索"
                        )
                        yield _sse(
                            {
                                "type": "search_start",
                                "query": last_web_search_query,
                                "timestamp": ts,
                            }
                        )
                    yield _sse(
                        {
                            "type": "tool_call",
                            "name": name,
                            "args": args,
                            "timestamp": ts,
                        }
                    )

            elif event.event_type == ActionType.PROGRESS:
                # 映射为前端期望的 node_start / node_complete 事件
                meta = event.metadata or {}
                sse_type = meta.get("sse_type", "node_complete")
                if sse_type == "node_timing":
                    # 节点真实耗时（node_end 到达时下发），前端据此覆盖 updates 近似值
                    yield _sse(
                        {
                            "type": "node_timing",
                            "node_id": meta.get("node_id", ""),
                            "server_start_ts": meta.get("server_start_ts"),
                            "server_end_ts": meta.get("server_end_ts"),
                            "server_duration_ms": meta.get("server_duration_ms"),
                            "timestamp": ts,
                        }
                    )
                elif sse_type == "node_start":
                    payload = {
                        "type": "node_start",
                        "node_id": meta.get("node_id", ""),
                        "layer": meta.get("layer", ""),
                        "desc": meta.get("desc", ""),
                        "timestamp": ts,
                    }
                    # 透传后端真实入口时间戳（当前运行节点实时已运行时长基于此）
                    if "server_start_ts" in meta:
                        payload["server_start_ts"] = meta["server_start_ts"]
                    yield _sse(payload)
                elif sse_type == "node_complete":
                    yield _sse(
                        {
                            "type": "node_complete",
                            "node_id": meta.get("node_id", ""),
                            "layer": meta.get("layer", ""),
                            "desc": meta.get("desc", ""),
                            "completed": meta.get("completed", []),
                            "progress": meta.get("progress", 0),
                            "output": meta.get("output", {}),
                            "timestamp": ts,
                        }
                    )
                else:
                    yield _sse(
                        {
                            "type": "pipeline_progress",
                            "content": event.content,
                            "metadata": meta,
                            "timestamp": ts,
                        }
                    )

            elif event.event_type == ActionType.TOOL_METADATA:
                if on_metadata and event.metadata:
                    on_metadata(event.metadata)
                yield _sse(
                    {
                        "type": "pipeline_update",
                        "metadata": event.metadata or {},
                        "timestamp": ts,
                    }
                )

            elif event.event_type == ActionType.TOOL_RESULT:
                tr = event.tool_result
                if tr:
                    # 处理 metadata（chart_data、analyst_reports 等）
                    if tr.metadata:
                        if on_metadata:
                            on_metadata(tr.metadata)

                        sse_type = tr.metadata.get("sse_type", "")

                        # run_deep_analysis 的最终结果：发送 report_ready
                        if sse_type == "report_ready" or tr.name == "run_deep_analysis":
                            stock_code = tr.metadata.get("stock_code", "")
                            stock_name = tr.metadata.get("stock_name", "")
                            if on_resolved:
                                on_resolved(stock_code, stock_name)
                            yield _sse(
                                {
                                    "type": "resolved",
                                    "stock_code": stock_code,
                                    "stock_name": stock_name,
                                    "timestamp": ts,
                                }
                            )
                            yield _sse(
                                {
                                    "type": "report_ready",
                                    "report_markdown": tr.metadata.get("report_markdown", ""),
                                    "chart_data": tr.metadata.get("chart_data", {}),
                                    "file_paths": tr.metadata.get("file_paths", {}),
                                    "stock_code": stock_code,
                                    "stock_name": stock_name,
                                    "web_sources": tr.metadata.get("web_sources", []),
                                    "timestamp": ts,
                                }
                            )
                        else:
                            yield _sse(
                                {
                                    "type": "pipeline_update",
                                    "metadata": tr.metadata,
                                    "timestamp": ts,
                                }
                            )
                    else:
                        # 普通工具结果
                        result_data = tr.output
                        with contextlib.suppress(json.JSONDecodeError, TypeError):
                            result_data = json.loads(tr.output)

                        # web_search / batch_web_search: 解析纯文本结果，
                        # 发送结构化搜索来源（驱动前端搜索横幅转「已搜索 N 个网页」）
                        if tr.name in ("web_search", "batch_web_search") and isinstance(
                            result_data, str
                        ):
                            from finance_agent.web_search import parse_search_output

                            search_results = parse_search_output(result_data)
                            if search_results:
                                yield _sse(
                                    {
                                        "type": "search_result",
                                        "query": last_web_search_query,
                                        "results": [
                                            {"title": r.title, "url": r.url, "content": r.content}
                                            for r in search_results
                                        ],
                                        "count": len(search_results),
                                        "timestamp": ts,
                                    }
                                )

                        # search_stock: 解析纯文本结果，发送结构化股票信息
                        if tr.name == "search_stock" and isinstance(result_data, str):
                            stock_info = _parse_stock_result_text(result_data)
                            if stock_info:
                                # 通过 on_metadata 回调通知调用方（用于 fallback）
                                if on_metadata:
                                    on_metadata(
                                        {
                                            "search_stock_code": stock_info["code"],
                                            "search_stock_name": stock_info["name"],
                                        }
                                    )
                                yield _sse(
                                    {
                                        "type": "stock_resolved",
                                        "stock_code": stock_info["code"],
                                        "stock_name": stock_info["name"],
                                        "timestamp": ts,
                                    }
                                )

                        yield _sse(
                            {
                                "type": "tool_result",
                                "name": tr.name,
                                "result": result_data,
                                "timestamp": ts,
                            }
                        )

            elif event.event_type == ActionType.ERROR:
                yield _sse({"type": "error", "message": event.content, "timestamp": ts})

            # 准备下一次迭代
            nextTask = asyncio.create_task(agentGen.__anext__())

        # ADR-0015: 退出 react_loop span 前记录 agent 最终回复（会话内容可见）
        if _react_obs is not None and _final_answer_parts:
            with contextlib.suppress(Exception):
                _react_obs.update(output={"answer": "".join(_final_answer_parts)})

        # ADR-0015: 退出 react_loop span 与 session 聚合上下文
        _propagate_cm.__exit__(None, None, None)
        _react_cm.__exit__(None, None, None)

        yield _sse({"type": "chat_done", "timestamp": _now()})
        # NOTE: done 事件由调用方发送（确保 session_created 等事件先发完）

    finally:
        # GeneratorExit 清理：取消 pending task，关闭 agentGen，退出 Langfuse 上下文
        if not nextTask.done():
            nextTask.cancel()
        with contextlib.suppress(Exception):
            await agentGen.aclose()
        with contextlib.suppress(Exception):
            _propagate_cm.__exit__(None, None, None)
            _react_cm.__exit__(None, None, None)


def _sse(data: dict) -> str:
    """格式化 SSE 事件。"""
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"
