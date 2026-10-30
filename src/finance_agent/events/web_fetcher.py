"""L2: 受限 WebSearch 事件获取。"""

from __future__ import annotations

import json
import logging
from datetime import datetime

from finance_agent.events.config import (
    ALLOWED_DOMAINS,
    WEBSEARCH_QUERY_TEMPLATE,
)
from finance_agent.llm.gateway import complete_text

logger = logging.getLogger(__name__)

_EXTRACTION_PROMPT = """请从以下搜索摘要中提取关键非财务事件，返回 JSON 数组。

## 三级事件分类标准

### L1 — 战略级关键事件（优先提取）
对公司未来 3-5 年经营格局有持续性影响的非财务动作。
- 提价 / 降价策略变更
- 渠道体系重大变革（直销化、经销商洗牌、出海渠道搭建）
- 核心管理层变动（CEO / 董事长 / 事业部总经理级别）
- 重大并购、重组、资产注入 / 剥离
- 颠覆性产品发布（开辟新赛道或改变竞争格局）
- 重大政策影响（行业准入、监管规则变化、关税壁垒）
- 监管处罚（停产整顿、大额罚款、退市风险警示）

### L2 — 战役级前置信号（无 L1 时补充提取）
对未来 1-2 年经营有重要影响，是战略动作的明确前兆或组成部分，但自身不单独改变长期格局。
- 大额订单 / 重大合同公告（需标注客户名称、金额量级、交付周期）
- 产能扩张 / 产线改造 / 海外建厂计划（需标注产能增幅、投资规模、投产时间）
- 关键客户认证 / 供应商准入（如进入头部车企、英伟达供应链等）
- 核心技术突破的阶段性公告（实验室成果、中试成功、小规模量产）
- 重要战略合作 / 长期供货框架协议
- 股权激励 / 回购计划（金额显著、覆盖面广的）

### L3 — 边缘信息（必须排除）
- 日常股价波动、股价创新高/新低
- 研报评级变化（买入/卖出/目标价调整）
- 常规财报数据发布、业绩预告、分红方案
- 股东大会、董事会例会等常规公司治理事件
- 非实质性传闻、市场传言、分析师观点

## 提取规则

1. 优先识别 L1 级事件。若存在 L1 事件，只返回 L1（不混入 L2）。
2. 若无 L1 事件，可提取 L2 级前置信号，但必须在 `summary` 末尾追加标注：`【L2前瞻信号，影响周期1-2年，需跟踪后续兑现】`。
3. 绝对排除 L3 级边缘信息。
4. 每个事件必须包含：
   - date（YYYY-MM-DD，如日期不详用当年最早相关报道日期或留空）
   - type（事件类型，从下方枚举中选择）
   - title（简短，20字以内）
   - summary（1-2句，说明事件内容和潜在影响）
   - impact（positive / negative / neutral）
   - source（来源网站域名）
   - level（"L1" 或 "L2"）
5. 如果摘要中没有任何 L1 或 L2 级别事件，返回空数组 []。

## 事件类型枚举

L1 类型：提价、渠道变革、管理层变动、并购/重组、产品发布、政策影响、监管处罚
L2 类型：大额订单、产能扩张、客户认证、技术突破、战略合作、股权激励

## 搜索摘要

{content}

请直接返回 JSON 数组，不要任何额外文字。"""


def _domain_allowed(url: str) -> bool:
    """域名白名单子串匹配（纯函数化便于单测；语义与既有 any(d in url) 一致）。"""
    return any(d in url for d in ALLOWED_DOMAINS)


def _search_with_duckduckgo(query: str, max_results: int = 5) -> list[dict]:
    """使用 ddgs 库执行搜索。"""
    try:
        from ddgs import DDGS

        with DDGS() as ddgs:
            results = []
            for r in ddgs.text(query, max_results=max_results * 2):
                # 过滤域名
                url = r.get("href", "")
                if _domain_allowed(url):
                    results.append(
                        {
                            "source": url,
                            "title": r.get("title", ""),
                            "snippet": r.get("body", ""),
                        }
                    )
                if len(results) >= max_results:
                    break
            return results
    except Exception as e:
        logger.debug("ddgs search failed: %s", e)
        return []


def _search_fallback(query: str, max_results: int = 5) -> list[dict]:
    """当 ddgs 搜索无结果时，返回空结果，由 pipeline 降级到预构建库。"""
    logger.warning("Web search returned no results for query: %s", query[:50])
    return []


def fetch_events_from_web(stock_code: str, stock_name: str) -> list[dict] | None:
    """通过受限 WebSearch 获取事件。

    只在 EVENT_SOURCE=auto 时被调用。失败返回 None，由 pipeline 降级到 L3。
    """
    try:
        # 构造搜索查询
        current_year = datetime.now().year
        year_range = f"{current_year - 1} {current_year}"
        query = WEBSEARCH_QUERY_TEMPLATE.format(
            stock_name=stock_name,
            stock_code=stock_code,
            year_range=year_range,
        )

        # 执行搜索
        results = _search_with_duckduckgo(query, max_results=5)
        if not results:
            results = _search_fallback(query, max_results=5)
        if not results:
            return None

        # 拼接搜索结果
        content = "\n\n".join(
            f"[{r.get('source', 'unknown')}] {r.get('title', '')}\n{r.get('snippet', '')}"
            for r in results
        )

        # LLM 提取结构化事件
        text, meta = complete_text(
            [
                {
                    "role": "system",
                    "content": "你是财经信息提取专家。只输出 JSON 数组，不要 Markdown 代码块标记。",
                },
                {"role": "user", "content": _EXTRACTION_PROMPT.replace("{content}", content)},
            ],
            purpose="deep",
            temperature=0.1,
            llm_config=None,
            trace={"name": "web_fetcher", "metadata": {"agent": "web_fetcher"}},
        )
        # legacy 行为保留：content 为空时回退 reasoning_content
        raw = text or meta.get("raw_reasoning") or ""

        # 解析 JSON
        parsed = json.loads(raw)
        if isinstance(parsed, list):
            return parsed
        return None

    except Exception as e:
        logger.warning("Web fetch events failed for %s: %s", stock_code, e)
        return None
