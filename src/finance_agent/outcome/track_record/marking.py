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
    """拉取并归一基准日 K；失败仅 WARN 返回 None（超额字段置空/日历降级）。"""
    try:
        benchmark = client.fetch_index_kline(BENCHMARK_CODE, days=kline_days)
        if benchmark is not None and not benchmark.empty:
            return _normalize_dates(benchmark)
    except Exception as e:  # noqa: BLE001
        logger.warning("基准行情拉取失败,超额收益字段置空: %s", e)
    return None


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
        first_close = float(rows.iloc[0]["收盘"])
        if not reference_price_ok(float(entry), first_close):
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

    metrics_date = persist_metrics_snapshot(
        db_path=db_path, client=client, kline_days=kline_days, benchmark=benchmark
    )
    return {**mark_result, "equity_points": len(points), "metrics_date": metrics_date}


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
