"""TDD tests for data/kline_freshness.py — K 线新鲜度分类（issue #623）。

背景：``fetch_kline`` 东财→新浪→腾讯回退链上任一源成功即视为成功，无「末根
bar 是否覆盖交易日」校验。逐股出 bar 有先后时，晚启动的会话可能整段分析锚在
昨收（2026-11-24 688297 实证：technical 全部 period=11-23，决策写昨收 32.71
而当日收盘 33.25）。

分类口径（纯函数，now 注入；calendar 为升序交易日 ISO 列表）：
- ``ok``         末根 bar ≥ 期望最新交易日（≤ 今天的最大日历日）
- ``stale_soft`` 缺的正好是期望最新一日，且已过收盘缓冲（交易日 15:10 后）或
                 期望日本身已过（非交易日运行）——盘中缺当日 bar 属正常
- ``stale_hard`` 末根 bar 更旧（缺多于最新一日）——多源静默缺失
- ``None``       calendar 不可得 / last_bar 不可解析：无法判断不告警
"""

from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd

from finance_agent.data.kline_freshness import (
    classify_kline_freshness,
    extract_last_bar_date,
)

# 日历含 11-24/11-25 两个交易日，11-22/11-23 为周末
_CAL = [
    "2026-11-18",
    "2026-11-19",
    "2026-11-20",
    "2026-11-23",
    "2026-11-24",
    "2026-11-25",
]


def _now(hour: int, minute: int = 0, *, day: int = 25) -> datetime:
    return datetime(2026, 11, day, hour, minute, tzinfo=ZoneInfo("Asia/Shanghai"))


class TestClassifyKlineFreshness:
    def test_last_bar_is_expected_day_is_ok(self):
        assert classify_kline_freshness("2026-11-25", now=_now(16, 0), calendar=_CAL) == "ok"

    def test_intraday_missing_today_bar_is_ok(self):
        """交易日盘中（15:10 前）末根为昨日：正常，不告警。"""
        assert classify_kline_freshness("2026-11-24", now=_now(10, 0), calendar=_CAL) == "ok"

    def test_close_buffer_boundary(self):
        """15:10 收盘缓冲：前一刻 ok，到点转 soft。"""
        assert classify_kline_freshness("2026-11-24", now=_now(15, 9), calendar=_CAL) == "ok"
        assert (
            classify_kline_freshness("2026-11-24", now=_now(15, 10), calendar=_CAL) == "stale_soft"
        )

    def test_missing_latest_day_on_non_trading_day_is_soft(self):
        """非交易日运行（期望日=11-25 已过）缺 11-25 bar：soft。"""
        assert (
            classify_kline_freshness("2026-11-24", now=_now(10, 0, day=29), calendar=_CAL)
            == "stale_soft"
        )

    def test_missing_more_than_latest_day_is_hard(self):
        """末根停在 11-21：缺 11-24 与 11-25 两日 → hard。"""
        assert (
            classify_kline_freshness("2026-11-21", now=_now(16, 0), calendar=_CAL) == "stale_hard"
        )

    def test_missing_more_than_latest_day_intraday_is_still_hard(self):
        """多日缺失在盘中也不可能是「正常等待当日 bar」：hard。"""
        assert (
            classify_kline_freshness("2026-11-21", now=_now(10, 0), calendar=_CAL) == "stale_hard"
        )

    def test_empty_calendar_returns_none(self):
        assert classify_kline_freshness("2026-11-20", now=_now(16, 0), calendar=[]) is None

    def test_unparseable_last_bar_returns_none(self):
        assert classify_kline_freshness(None, now=_now(16, 0), calendar=_CAL) is None


class TestExtractLastBarDate:
    def test_prefers_attrs_over_column(self):
        frame = pd.DataFrame({"日期": ["2026-11-20", "2026-11-21"]})
        frame.attrs["last_bar_date"] = "2026-11-21"
        assert extract_last_bar_date(frame) == "2026-11-21"

    def test_falls_back_to_date_column_and_normalizes(self):
        frame = pd.DataFrame({"日期": pd.to_datetime(["2026-11-20", "2026-11-24"])})
        assert extract_last_bar_date(frame) == "2026-11-24"

    def test_missing_date_column_returns_none(self):
        assert extract_last_bar_date(pd.DataFrame({"收盘": [1.0]})) is None

    def test_empty_frame_returns_none(self):
        assert extract_last_bar_date(pd.DataFrame({"日期": []})) is None
