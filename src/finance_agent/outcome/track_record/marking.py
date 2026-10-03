"""add-track-record-stage-b：每日盯市 + 净值/指标快照（APScheduler 日批）。

mark_open_predictions：对全部 open 观点按交易日盯市（mark_price/cum_return/
cum_excess，基准同步期收益）；缺数据容错（停牌/接口失败仅跳过该观点）。
run_daily_marking：盯市 → 净值曲线入库 → 指标快照入库（幂等，同日覆盖）。

与 settle（16:00）的时序：marking 建议在 settle 之后运行（16:30），先结算
到期观点再对剩余 open 观点盯市，避免对已结算观点重复盯市。

复权口径（delta add-backtest-leakage-controls）：**盯市口径 = 参考价口径（前复权 qfq，近似）**。
盯市分子必须与存储的参考价（`entry_price`，实时 quote 原值或管线 qfq 收盘）同尺度，
故 `fetch_kline` 不传 `adjust`（用默认 qfq）；**判定口径 = 后复权 hfq（精确，
`settle_entry_price` 由行情派生）**。两者不得混用——若盯市改传 hfq，`cum_return =
hfq(d)/raw(entry) - 1` 仅在 hfq 基准日成立，且经 `daily_marks` → 净值曲线/指标快照传导失真。
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import pandas as pd

from finance_agent.outcome.track_record.model import (
    init_track_record_tables,
    insert_daily_mark,
    list_predictions,
    upsert_equity_point,
    upsert_metrics_daily,
)
from finance_agent.outcome.track_record.reference_price import reference_price_ok

logger = logging.getLogger(__name__)

BENCHMARK_CODE = "000300"


def _code(symbol: str) -> str:
    return symbol.split(".")[0]


def _normalize_dates(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["日期"] = df["日期"].astype(str).str[:10]
    return df


def _bench_by_date(benchmark: pd.DataFrame | None) -> dict[str, float]:
    if benchmark is None or benchmark.empty:
        return {}
    return {str(d): float(c) for d, c in zip(benchmark["日期"], benchmark["收盘"], strict=False)}


def _bench_base(bench_by_date: dict[str, float], entry_date: str) -> float | None:
    """entry 日（及之后第一个交易日）的基准收盘价，作为超额收益基期。"""
    for d in sorted(bench_by_date):
        if d >= entry_date:
            return bench_by_date[d]
    return None


def _fetch_benchmark(client: Any, kline_days: int) -> pd.DataFrame | None:
    """拉取并归一基准日 K；失败/空结果仅 WARN 返回 None（超额置空/日历降级）。"""
    try:
        benchmark = client.fetch_index_kline(BENCHMARK_CODE, days=kline_days)
        if benchmark is not None and not benchmark.empty:
            return _normalize_dates(benchmark)
        logger.warning("基准行情返回空,按无基准降级")
    except Exception as e:  # noqa: BLE001
        logger.warning("基准行情拉取失败,超额收益字段置空: %s", e)
    return None


# 参考价防护仅对「created 后首个交易日」在近邻窗口内的观点生效（日历日）
_GUARD_WINDOW_DAYS = 7


def _within_guard_window(created: str, first_mark: str) -> bool:
    """首盯日距 created 超窗（280 根 K 线窗口滑动的老龄 open 观点）→ 比对无意义。"""
    try:
        from datetime import date

        delta = abs((date.fromisoformat(first_mark) - date.fromisoformat(created)).days)
    except ValueError:
        return False
    return delta <= _GUARD_WINDOW_DAYS


def mark_open_predictions(
    *,
    client: Any = None,
    db_path: str | Path | None = None,
    kline_days: int = 280,
    benchmark: pd.DataFrame | None = None,
) -> dict[str, int]:
    """盯市全部 open 观点。返回 {marked, skipped, errors}。

    容错：单个观点行情失败仅跳过（errors+1），本批继续；无入场价/信号
    缺要素的观点 skipped（不入盯市）。幂等：同 (prediction_id, mark_date)
    覆盖重写。benchmark 可由 run_daily_marking 传入复用（同批仅拉一次）。
    """
    if client is None:
        from finance_agent.data.akshare_client import AKShareClient

        client = AKShareClient()
    result = {"marked": 0, "skipped": 0, "errors": 0}

    try:
        open_preds = list_predictions(status="open", db_path=db_path)
    except Exception as e:  # noqa: BLE001
        logger.error("读取 open 观点失败,盯市批终止: %s", e)
        result["errors"] += 1
        return result

    if benchmark is None:
        benchmark = _fetch_benchmark(client, kline_days)
    bench_by_date = _bench_by_date(benchmark)

    for p in open_preds:
        entry = p.get("entry_price")
        created = str(p.get("created_at") or "")[:10]
        if not entry or entry <= 0 or not created:
            result["skipped"] += 1
            continue
        try:
            # 盯市口径 = 参考价口径：不传 adjust（默认 qfq），与存储的 entry_price 同尺度。
            kline = client.fetch_kline(_code(p["symbol"]), days=kline_days)
            if kline is None or kline.empty:
                result["skipped"] += 1
                continue
            kline = _normalize_dates(kline)
            rows = kline[kline["日期"] > created]
        except Exception as e:  # noqa: BLE001
            logger.warning("行情拉取失败,本次跳过 %s: %s", p["prediction_id"], e)
            result["errors"] += 1
            continue

        # neutral 按 long 口径记录（与 judgment 回避判定同号；incident 032 根因 A：
        # 旧代码 else -1.0 把 hold/watch 当空头，产生幻影空头损益）
        sign = -1.0 if p["direction"] == "short" else 1.0
        benchmark_base = _bench_base(bench_by_date, created)
        if rows.empty:
            result["skipped"] += 1
            continue
        # 参考价失效防护（incident 032 根因 B 存量）：entry 与首盯市收盘偏离超阈值
        # 视参考价不可信，跳过不写 marks，供人工甄别（append-only 冻结语义不修 entry）。
        # 仅在首盯日临近 created 时比对——280 根窗口滑动的老龄观点首盯日远离
        # created，收盘差异巨大是持仓期行情，不是坏参考价，不适用防护。
        first_close = float(rows.iloc[0]["收盘"])
        if _within_guard_window(created, str(rows.iloc[0]["日期"])) and not reference_price_ok(
            float(entry), first_close
        ):
            logger.warning(
                "参考价失效（entry=%s vs 首盯市收盘=%s 偏离超阈值），跳过 %s 供人工甄别",
                entry,
                first_close,
                p["prediction_id"],
            )
            result["skipped"] += 1
            continue
        for _, r in rows.iterrows():
            d = str(r["日期"])
            price = float(r["收盘"])
            cum_return = sign * (price / float(entry) - 1.0)
            cum_excess = None
            bench_now = bench_by_date.get(d)
            if benchmark_base and bench_now:
                cum_excess = cum_return - (bench_now / benchmark_base - 1.0)
            insert_daily_mark(
                p["prediction_id"],
                d,
                mark_price=price,
                cum_return=round(cum_return, 6),
                cum_excess=round(cum_excess, 6) if cum_excess is not None else None,
                benchmark_price=bench_now,
                db_path=db_path,
            )
            result["marked"] += 1
    return result


def run_daily_marking(
    *,
    client: Any = None,
    db_path: str | Path | None = None,
    kline_days: int = 280,
) -> dict[str, Any]:
    """日批入口：盯市 → 净值曲线入库 → 指标快照入库。幂等，同日覆盖。

    基准日 K 同批只拉一次：既作盯市超额基价，也作净值/指标的交易日历骨架
    （incident 032 根因 C：空仓日补 0、年化 n=交易日数）；拉取失败降级
    marks-only 口径（净值仅覆盖有盯市日期）。
    """
    init_track_record_tables(db_path)  # 幂等建表
    if client is None:
        from finance_agent.data.akshare_client import AKShareClient

        client = AKShareClient()
    benchmark = _fetch_benchmark(client, kline_days)
    mark_result = mark_open_predictions(
        client=client, db_path=db_path, kline_days=kline_days, benchmark=benchmark
    )

    from finance_agent.outcome.track_record.metrics import build_equity_curve_points

    bench_map = _bench_by_date(benchmark)
    points = build_equity_curve_points(
        db_path=db_path,
        calendar_dates=sorted(bench_map) if bench_map else None,
        benchmark_by_date=bench_map or None,
    )
    for pt in points:
        upsert_equity_point(
            str(pt["date"]),
            agent_nav=float(pt["agent_nav"]),
            benchmark_nav=float(pt["benchmark_nav"])
            if pt.get("benchmark_nav") is not None
            else None,
            db_path=db_path,
        )

    # update-risk-free-rate-source：指标快照前同步无风险利率（快照当轮即用新值）。
    # 失败隔离：同步异常不阻断盯市/快照，快照侧走回退链（carry-forward/常数）。
    rf_summary: dict[str, Any] = {"rf_stored": 0, "rf_failed": True}
    try:
        from finance_agent.outcome.track_record.risk_free import sync_risk_free_rates

        rf_summary = {
            "rf_stored": sync_risk_free_rates(client=client, db_path=db_path)["stored"],
            "rf_failed": False,
        }
    except Exception as e:  # noqa: BLE001 - rf 缺失不得放大成盯市失败
        logger.warning("无风险利率同步失败(回退链兜底): %s", e)

    metrics_date = persist_metrics_snapshot(
        db_path=db_path, client=client, kline_days=kline_days, benchmark=benchmark
    )

    # add-index-performance-compare:指数集收盘顺带落库(展示层对比用)。
    # 失败隔离铁律:任何指数异常不得使盯市失败、不得计入 marked/skipped/errors;
    # 判定链仍读 daily_marks.benchmark_price,与 index_closes 互不影响。
    index_summary: dict[str, Any] = {"index_stored": 0, "index_failed": ["_sync_error"]}
    try:
        from finance_agent.outcome.track_record.index_compare import sync_index_closes

        sync_result = sync_index_closes(client=client, db_path=db_path, days=kline_days)
        # sync_index_closes 返回 {stored, failed};按接口契约改名为 index_stored/index_failed
        index_summary = {
            "index_stored": sync_result["stored"],
            "index_failed": sync_result["failed"],
        }
    except Exception as e:  # noqa: BLE001 - 展示层数据缺失不得放大成盯市失败
        logger.warning("指数收盘同步整体失败(仅展示层): %s", e)

    return {
        **mark_result,
        "equity_points": len(points),
        "metrics_date": metrics_date,
        **rf_summary,
        **index_summary,
    }


def persist_metrics_snapshot(
    *,
    db_path: str | Path | None = None,
    client: Any = None,
    kline_days: int = 280,
    benchmark: pd.DataFrame | None = None,
) -> str:
    """重算并落库当日指标快照（metrics_snapshot 任务入口，幂等）。

    benchmark 缺省时自行拉取（独立 16:35 任务入口）；run_daily_marking 传入复用。
    """
    from finance_agent.outcome.track_record.metrics import compute_metrics_snapshot

    if benchmark is None:
        if client is None:
            from finance_agent.data.akshare_client import AKShareClient

            client = AKShareClient()
        benchmark = _fetch_benchmark(client, kline_days)
    bench_map = _bench_by_date(benchmark)
    snapshot = compute_metrics_snapshot(
        db_path=db_path,
        calendar_dates=sorted(bench_map) if bench_map else None,
        benchmark_by_date=bench_map or None,
    )
    metric_date = _today()
    upsert_metrics_daily(metric_date, snapshot, db_path=db_path)
    return metric_date


def _today() -> str:
    from datetime import datetime
    from zoneinfo import ZoneInfo

    return datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d")
