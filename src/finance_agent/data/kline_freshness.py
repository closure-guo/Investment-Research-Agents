"""K 线末根新鲜度分类（issue #623 回退源静默陈旧守卫）。

背景
----
``AKShareClient.fetch_kline`` 的东财 → 新浪 → 腾讯回退链上任一源成功即视为
成功，**没有「末根 bar 是否覆盖交易日」的校验**。逐股出 bar 有先后时，晚启动
的会话可能整段分析锚在昨收上——2026-11-24 688297 实证：technical 节点全部
指标 ``period=2026-11-23``，决策把昨收 32.71 当作「现价」（当日收盘 33.25），
参考带/反抽档位/破位线全部错位。

分类口径（纯函数，``now`` 注入以便测试；``calendar`` 为升序交易日 ISO 列表）
-------------------------------------------------------------------------
- ``ok``         末根 bar ≥ 期望最新交易日（≤ 今天的最大日历日）
- ``stale_soft`` 缺的正好是期望最新一日，且已过收盘缓冲（交易日 15:10 后）
                 或期望日本身已过（非交易日运行）——盘中缺当日 bar 属正常
- ``stale_hard`` 末根 bar 更旧（缺多于最新一日）——多源静默缺失，不可当新鲜
                 数据用（调用方应走降级而不是带着旧价算价位）
- ``None``       calendar 不可得 / 末根 bar 不可解析：无法判断，不告警不阻断

消费方：``nodes/fetch.py`` 的 ``data_source:akshare:kline`` 收集处——
hard 抛 ``KlineStaleError`` 进既有 optional 降级路径；soft 记 warning 并在
state 注记 ``kline_as_of`` 供下游可见。
"""

from __future__ import annotations

import json
import logging
from collections.abc import Sequence
from datetime import date, datetime, time
from typing import Any
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

_BEIJING_TZ = ZoneInfo("Asia/Shanghai")

#: 收盘后多久仍缺当日 bar 才判软陈旧——A 股 15:00 收盘，数据源推 bar 有分钟级延迟
CLOSE_BUFFER = time(15, 10)


class KlineStaleError(RuntimeError):
    """末根 bar 早于上一交易日（硬陈旧）：回退源静默缺失，拒绝当新鲜数据用。"""


def now_beijing() -> datetime:
    """当前北京时间（测试经 monkeypatch 注入固定时刻）。"""
    return datetime.now(_BEIJING_TZ)


def classify_kline_freshness(
    last_bar_date: str | None,
    *,
    now: datetime,
    calendar: Sequence[str],
) -> str | None:
    """末根 bar 相对交易日历的新鲜度分类（语义见模块 docstring）。"""
    if not last_bar_date or not calendar:
        return None
    today = now.date().isoformat()
    up_to_today = [day for day in calendar if day <= today]
    if not up_to_today:
        return None
    expected = up_to_today[-1]
    if last_bar_date >= expected:
        return "ok"
    earlier = [day for day in calendar if day < expected]
    previous = earlier[-1] if earlier else None
    if previous is not None and last_bar_date >= previous:
        # 缺的正好是期望最新一日：盘中属正常；过收盘缓冲或期望日已过 → 软陈旧
        if expected == today and now.time() < CLOSE_BUFFER:
            return "ok"
        return "stale_soft"
    return "stale_hard"


def extract_last_bar_date(frame: Any) -> str | None:
    """从 K 线 DataFrame 提取末根 bar 日期（规范 ``YYYY-MM-DD``）。

    优先读 ``fetch_kline`` 写入的 ``attrs["last_bar_date"]``（列名归一化之前
    就打好标记，rename/排序不受影响）；缺失则回退末行 ``日期`` 列。帧无日期列
    （历史桩形态）/空帧/不可解析 → ``None``，调用方据此零行为变化。
    """
    try:
        attrs = getattr(frame, "attrs", None) or {}
        attr_value = attrs.get("last_bar_date")
        if attr_value:
            text = str(attr_value)[:10]
            date.fromisoformat(text)
            return text
        series = frame.get("日期")
        if series is None or len(series) == 0:
            return None
        text = str(series.iloc[-1])[:10]
        date.fromisoformat(text)
        return text
    except (AttributeError, TypeError, ValueError):
        return None


def load_trade_calendar_days() -> list[str]:
    """快照 meta ``trade_calendar`` 的容错读取（口径同 screening 容错版）。

    缺失/不可解析/库不可达 → ``[]``（无法判断新鲜度时守卫不告警、不阻断——
    缺历猜测比不判断更危险）。
    """
    from finance_agent.data.snapshot import get_all_meta  # 局部导入：避免数据层互引

    try:
        raw = get_all_meta().get("trade_calendar")
    except Exception:  # noqa: BLE001 - 日历只作守卫输入，任何读取失败都降级为空
        return []
    if not raw:
        return []
    try:
        decoded = json.loads(raw)
    except ValueError:
        return []
    if not isinstance(decoded, list):
        return []
    days: list[str] = []
    for entry in decoded:
        text = str(entry)
        try:
            parsed = date.fromisoformat(text)
        except ValueError:
            continue
        if parsed.isoformat() == text:
            days.append(text)
    return sorted(set(days))
