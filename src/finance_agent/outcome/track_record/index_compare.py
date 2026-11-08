"""指数区间收益对比(add-index-performance-compare)。

指数收盘落库与区间收益读数;win/loss 判定基准仍为 BENCHMARK_CODE(沪深300),
判定链路不消费本模块。口径细则见 openspec/changes/add-index-performance-compare
的 specs/index-comparison。
"""

from __future__ import annotations

import calendar
import logging
from datetime import date
from pathlib import Path
from typing import Any

from finance_agent.outcome.track_record.model import (
    count_directional_views,
    count_settled_in_window,
    list_equity_curve,
    list_index_closes,
    upsert_index_closes,
)

logger = logging.getLogger(__name__)

# 对比指数集(代码级常量,非配置项——见 design Non-Goals)
INDEX_COMPARE_UNIVERSE: list[dict[str, str]] = [
    {"code": "000001", "name": "上证指数"},
    {"code": "000300", "name": "沪深300"},
    {"code": "000905", "name": "中证500"},
    {"code": "000852", "name": "中证1000"},
    {"code": "399006", "name": "创业板指"},
]


def sync_index_closes(
    *,
    client: Any = None,
    db_path: str | Path | None = None,
    days: int = 280,
) -> dict[str, Any]:
    """拉取指数集 K 线并幂等落库;单指数失败仅 WARNING,不外抛。

    日批(daily_marking)与回填脚本共用:days 为拉取的交易日长度,
    280 交易日 ≈ 1.1 年,覆盖前端最长 1y 窗口。返回 {stored, failed}:
    failed 收集拉取抛异常或返回空行情的指数(空行情是 fetch_index_kline
    双源皆失败的生产故障形态,计入 failed);成功拉取并落库的计入 stored。
    """
    if client is None:
        from finance_agent.data.akshare_client import AKShareClient

        client = AKShareClient()
    stored = 0
    failed: list[str] = []
    for u in INDEX_COMPARE_UNIVERSE:
        code = u["code"]
        try:
            df = client.fetch_index_kline(code, days=days)
            if df is None or df.empty:
                failed.append(code)
                logger.warning("指数 %s(%s) 行情为空,本批跳过", code, u["name"])
                continue
            # str(d)[:10] 是必要的:新浪回退源日期为 datetime64,str() 带时间后缀会破坏
            # trade_date 字符串比较与 INSERT OR REPLACE 幂等
            rows = [
                (code, str(d)[:10], float(c)) for d, c in zip(df["日期"], df["收盘"], strict=False)
            ]
            stored += upsert_index_closes(rows, db_path=db_path)
        except Exception as e:  # noqa: BLE001 - 单指数失败隔离,展示层不得放大
            failed.append(code)
            logger.warning("指数 %s(%s) 收盘落库失败: %s", code, u["name"], e)
    return {"stored": stored, "failed": failed}


_SPAN_MONTHS = {"all": None, "3m": 3, "6m": 6, "1y": 12}


def _shift_months(d: date, months: int) -> date:
    """日历月平移;目标月无对应日(如 01-31 −1 月)钳制到当月最后一天。"""
    y = d.year + (d.month - 1 + months) // 12
    m = (d.month - 1 + months) % 12 + 1
    return date(y, m, min(d.day, calendar.monthrange(y, m)[1]))


def _build_caliber(
    *,
    nav_points: int,
    window_start: str | None,
    window_end: str | None,
    db_path: str | Path | None,
) -> dict[str, int]:
    """对比读数的组合构成披露(add-track-record-caliber-disclosure)。

    纯追加口径:告诉读者这条「组合」到底是什么、样本多大。窗口缺值时各计数
    归零(不报错、不影响既有读数)。
    """
    out = {
        "nav_points": int(nav_points),
        "directional_views": 0,
        "long_views": 0,
        "short_views": 0,
        "settled_count": 0,
    }
    if not window_start or not window_end:
        return out
    by_direction = count_directional_views(window_start, window_end, db_path=db_path)
    out["long_views"] = by_direction.get("long", 0)
    out["short_views"] = by_direction.get("short", 0)
    out["directional_views"] = out["long_views"] + out["short_views"]
    out["settled_count"] = count_settled_in_window(window_start, window_end, db_path=db_path)
    return out


def build_index_compare(
    span: str = "all",
    db_path: str | Path | None = None,
) -> dict[str, Any]:
    """跑赢指数对比读数(口径见 specs/index-comparison;as_of/disclaimer 由端点层附加)。"""
    curve = list_equity_curve(db_path=db_path)
    window_start = curve[0]["curve_date"] if curve else None
    window_end = curve[-1]["curve_date"] if curve else None
    months = _SPAN_MONTHS.get(span)
    if months and curve:
        cutoff = _shift_months(date.fromisoformat(str(window_end)), -months).isoformat()
        window_start = max(str(window_start), cutoff)

    agent_return: float | None = None
    if len(curve) >= 2:
        first = next(p for p in curve if p["curve_date"] >= window_start)
        window_start = str(first["curve_date"])  # span 窗口起点截断到实际覆盖
        if first["agent_nav"]:
            agent_return = round(float(curve[-1]["agent_nav"]) / float(first["agent_nav"]) - 1.0, 6)

    indices: list[dict[str, Any]] = []
    for u in INDEX_COMPARE_UNIVERSE:
        ret: float | None = None
        eff: str | None = None
        rows = list_index_closes(
            u["code"], end=str(window_end) if window_end else None, db_path=db_path
        )
        if rows and window_start is not None:
            base = None
            for r in rows:  # 行按日期升序;取 ≤ 窗口起点的最近可得日(向过去回退)
                if r["trade_date"] <= window_start:
                    base = r
                else:
                    break
            if base is None:  # 窗口起点前无数据 → 取窗口内最早可得日(如实披露)
                base = rows[0]
            if base["close"]:
                ret = round(float(rows[-1]["close"]) / float(base["close"]) - 1.0, 6)
                eff = str(base["trade_date"])
        beat = agent_return > ret if agent_return is not None and ret is not None else None
        indices.append(
            {
                "code": u["code"],
                "name": u["name"],
                "return": ret,
                "effective_start_date": eff,
                "beat": beat,
            }
        )

    return {
        "span": span,
        "window": {"start": window_start, "end": window_end},
        "agent_return": agent_return,
        "indices": indices,
        "caliber": _build_caliber(
            nav_points=len(curve),
            window_start=window_start,
            window_end=window_end,
            db_path=db_path,
        ),
    }
