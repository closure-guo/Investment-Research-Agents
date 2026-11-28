"""同窗随机基准（update-outcome-band-volatility-scaled 增补，2026-11-28 拍板）。

问题：战绩页只报「胜率 / 平均超额」，但没有对照——下跌市里随便一只股票都可能跑输
指数，"回避对了" 与 "市场本来就跌" 分不开（2026-11-28 审计实测：同窗随机股票在系统
「退出」观点的结算窗口里 66% 会「赢」，而系统 43%）。本模块给出**同样窗口、全市场
等权**的随机股票平均超额，作为每条聚合超额读数的对照基准。

口径
----
- 窗口：``[决策归属日, resolved_at]``——归属日与 ``settle_entry_price`` 同源
  （``judgment.derive_attribution_date``：收盘前产出→当日；收盘后/非交易日→次一
  交易日），与结算判定窗口一致，不复算第二种口径。
- 个股腿：``close_raw``（``market_snapshot.db``，不复权），与结算侧同源；
  基准腿：``index_closes``（000300.SH）。
- 逐窗口算「全市场等权平均超额」，再对窗口等权平均（每个窗口一票，避免长期窗口
  股票数多寡影响权重）。
- 缺行（个股/基准任一）→ 该窗口剔除并计入 ``skipped_windows``，不插值、不用前值。
"""

from __future__ import annotations

import statistics
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from finance_agent.outcome.track_record.judgment import derive_attribution_date

BENCHMARK_CODE = "000300.SH"
SETTLED_DIRECTIONAL_STATUSES = ("resolved_win", "resolved_loss", "resolved_neutral")


def window_baseline(
    closes_entry: Mapping[str, float],
    closes_exit: Mapping[str, float],
    bench_ret: float,
) -> dict[str, Any]:
    """单个窗口的全市场等权平均超额（纯函数）。

    ``bench_ret`` = 同窗口基准收益；个股超额 = ``close_exit/close_entry − 1 − bench_ret``。
    无可用个股（停牌/缺行全部剔除）→ ``n=0`` 且均值为 ``None``（不伪造 0）。
    """
    diffs: list[float] = []
    for code, p0 in closes_entry.items():
        p1 = closes_exit.get(code)
        if not p1 or not p0 or p0 <= 0:
            continue
        diffs.append(p1 / p0 - 1.0 - bench_ret)
    if not diffs:
        return {"n": 0, "mean_excess": None, "median_excess": None}
    return {
        "n": len(diffs),
        "mean_excess": round(statistics.fmean(diffs), 6),
        "median_excess": round(statistics.median(diffs), 6),
    }


def settled_windows(
    predictions: Sequence[Mapping[str, Any]], calendar: Sequence[str]
) -> list[tuple[str, str]]:
    """已结算方向性观点（long/short 三态）的 ``(归属日, resolved_at)`` 去重窗口列表。"""
    calendar_set = set(calendar)
    windows: set[tuple[str, str]] = set()
    for p in predictions:
        if p.get("status") not in SETTLED_DIRECTIONAL_STATUSES:
            continue
        if p.get("direction") not in ("long", "short"):
            continue
        exit_date = str(p.get("resolved_at") or "")[:10]
        if exit_date not in calendar_set:
            continue
        entry_date = derive_attribution_date(str(p.get("created_at") or ""), list(calendar))
        if entry_date not in calendar_set or entry_date >= exit_date:
            continue
        windows.add((entry_date, exit_date))
    return sorted(windows)


def build_same_window_baseline(
    predictions: Sequence[Mapping[str, Any]],
    calendar: Sequence[str],
    index_close: Mapping[str, float],
    close_getter: Callable[[list[str]], Mapping[str, Mapping[str, float]]],
) -> dict[str, Any]:
    """同窗随机基准聚合（纯函数，I/O 经 ``close_getter`` 注入以便测试）。

    返回 ``available`` / ``windows``（有效窗口数）/ ``skipped_windows`` /
    ``stocks_per_window``（窗口内股票数的中位数）/ ``mean_excess``（窗口间等权）/
    ``median_excess`` / ``caliber``（披露口径字符串）。无有效窗口 → ``available=False``
    + ``reason``（页面如实显示「基准不可用」，不折算 0）。
    """
    windows = settled_windows(predictions, calendar)
    if not windows:
        return {
            "available": False,
            "reason": "无可对齐的已结算窗口",
            "windows": 0,
            "skipped_windows": 0,
            "stocks_per_window": None,
            "mean_excess": None,
            "median_excess": None,
            "caliber": "同窗全市场等权（close_raw，基准 000300.SH）",
        }
    dates = sorted({d for window in windows for d in window})
    close_map = close_getter(dates)
    per_window: list[dict[str, Any]] = []
    skipped = 0
    for entry_date, exit_date in windows:
        close_entry = close_map.get(entry_date) or {}
        close_exit = close_map.get(exit_date) or {}
        b0, b1 = index_close.get(entry_date), index_close.get(exit_date)
        if not close_entry or not close_exit or not b0 or not b1:
            skipped += 1
            continue
        result = window_baseline(close_entry, close_exit, float(b1) / float(b0) - 1.0)
        if not result["n"]:
            skipped += 1
            continue
        per_window.append(result)
    if not per_window:
        return {
            "available": False,
            "reason": "快照库或指数收盘数据缺失",
            "windows": 0,
            "skipped_windows": skipped,
            "stocks_per_window": None,
            "mean_excess": None,
            "median_excess": None,
            "caliber": "同窗全市场等权（close_raw，基准 000300.SH）",
        }
    means = [float(r["mean_excess"]) for r in per_window]
    return {
        "available": True,
        "windows": len(per_window),
        "skipped_windows": skipped,
        "stocks_per_window": int(statistics.median([int(r["n"]) for r in per_window])),
        "mean_excess": round(statistics.fmean(means), 6),
        "median_excess": round(statistics.median(means), 6),
        "caliber": "同窗全市场等权（close_raw，基准 000300.SH）",
    }


def same_window_baseline(
    predictions: Sequence[Mapping[str, Any]] | None = None,
    *,
    db_path: Any | None = None,
    snapshot_db: Any | None = None,
) -> dict[str, Any]:
    """I/O 包装：读观点 + 指数收盘（track-record 库）+ 全市场收盘（快照库）。

    任何一步不可得（库缺失/空）→ ``available=False`` + ``reason``；**永不抛异常到
    页面**（对照组是观测增强，不阻断战绩页）。
    """
    from finance_agent.data.snapshot import read_close_map
    from finance_agent.outcome.track_record.model import list_index_closes, list_predictions

    try:
        if predictions is None:
            predictions = list_predictions(limit=100_000, db_path=db_path)
        index_rows = list_index_closes(BENCHMARK_CODE, db_path=db_path)
        calendar = [str(r["trade_date"])[:10] for r in index_rows]
        index_close = {str(r["trade_date"])[:10]: float(r["close"]) for r in index_rows}
        if not calendar:
            return {
                "available": False,
                "reason": "指数收盘序列不可得",
                "windows": 0,
                "skipped_windows": 0,
                "stocks_per_window": None,
                "mean_excess": None,
                "median_excess": None,
                "caliber": "同窗全市场等权（close_raw，基准 000300.SH）",
            }
        return build_same_window_baseline(
            predictions,
            calendar,
            index_close,
            lambda dates: read_close_map(dates, db_path=snapshot_db),
        )
    except Exception as e:  # noqa: BLE001 - 观测旁路：任何失败都降级披露
        return {
            "available": False,
            "reason": f"基准计算失败：{type(e).__name__}",
            "windows": 0,
            "skipped_windows": 0,
            "stocks_per_window": None,
            "mean_excess": None,
            "median_excess": None,
            "caliber": "同窗全市场等权（close_raw，基准 000300.SH）",
        }
