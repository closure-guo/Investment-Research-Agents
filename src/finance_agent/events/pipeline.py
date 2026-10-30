"""事件获取流水线：当日缓存 → L2 WebSearch → L1 预构建库 → L3 兜底。"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import cast

from finance_agent.data.cache import get_shared_cache
from finance_agent.events.config import EVENT_CUTOFF_DAYS
from finance_agent.events.fallback import fallback_annotation
from finance_agent.events.preset_loader import load_preset_events

logger = logging.getLogger(__name__)

# 与深析 fetch 节点 {code}:key_events 同 key 同 TTL（1 天）——两侧同值幂等写
_CACHE_TTL_SECONDS = 86_400


def _cache_key(stock_code: str) -> str:
    return f"{stock_code}:key_events"


def fetch_key_events(
    stock_code: str, stock_name: str = "", use_web_search: bool = True
) -> list[dict]:
    """获取关键非财务事件。

    默认流程（use_web_search=True）：
    0. 当日缓存命中（含「验证过的空」）→ 直接返回（零网络）
    1. L2: WebSearch 实时搜索
    2. L1: 预构建库 key_events.json（WebSearch 失败或提取为空时，语义不变）
    3. L3: 兜底提示（两者都拿不到时）

    缓存语义（add-key-events-cache）：「验证过的空」——搜索与提取正常完成但无
    L1/L2 级事件——返回并缓存空列表；失败路径（搜索/提取异常 → web_events=None）
    返回兜底卡且不缓存，下次调用自动重试。use_web_search=False 不读不写缓存。

    返回事件列表；失败兜底时非空（兜底卡），验证过的空返回 []。
    """
    cache = get_shared_cache() if use_web_search else None
    key = _cache_key(stock_code)
    if cache is not None:
        cached = cast(list[dict], cache.get(key))
        if cached is not None:
            return cached

    web_ran_ok = False
    if use_web_search:
        try:
            from finance_agent.events.web_fetcher import fetch_events_from_web

            web_events = fetch_events_from_web(stock_code, stock_name)
        except Exception as e:  # web_fetcher 内部已兜异常；双保险不让缓存层放大故障
            logger.warning("WebSearch crashed for %s: %s", stock_code, e)
            web_events = None
        web_ran_ok = web_events is not None
        if web_events:
            events = _filter_events(web_events)
            if cache is not None:
                cache.set(key, events, ttl_seconds=_CACHE_TTL_SECONDS)
            return events

    preset = load_preset_events(stock_code)
    if preset is not None:
        # 结构性空（[]）或有数据（list）
        events = _filter_events(preset)
        if cache is not None and web_ran_ok:
            # 仅当取数链完整走完才缓存（L1 事件或空）；失败降级不缓存
            cache.set(key, events, ttl_seconds=_CACHE_TTL_SECONDS)
        return events

    if web_ran_ok:
        # 验证过的空：搜索与提取均正常、L1 亦无 → 空列表即当日确定答案
        if cache is not None:
            cache.set(key, [], ttl_seconds=_CACHE_TTL_SECONDS)
        return []

    return fallback_annotation()


def _filter_events(events: list[dict]) -> list[dict]:
    """过滤事件：只保留最近 N 天，ongoing=True 除外。"""
    if not events:
        return []

    cutoff = datetime.now() - timedelta(days=EVENT_CUTOFF_DAYS)
    result: list[dict] = []

    for e in events:
        # ongoing 事件始终保留
        if e.get("ongoing"):
            result.append(e)
            continue

        # 检查日期
        date_str = e.get("date", "")
        if not date_str:
            continue
        try:
            event_date = datetime.strptime(date_str, "%Y-%m-%d")
            if event_date >= cutoff:
                result.append(e)
        except ValueError:
            # 日期格式异常，保留（避免误删）
            result.append(e)

    return result
