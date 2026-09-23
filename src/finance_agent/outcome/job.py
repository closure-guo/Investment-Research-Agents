"""日批结算 job(design 决策 2/6/7):遍历 open 决策 → 拉行情 → 结算 → 落库 → 上报 Score。

幂等:mark_settled 前再查 settled_at IS NULL(spec「幂等结算」)。
失败隔离:单决策拉取/评估异常仅跳过该决策(errors 计数),不中断整批
(spec「行情缺失重试」、旁路铁律——job 不崩)。
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import pandas as pd

from finance_agent.data.akshare_client import SETTLEMENT_ADJUST
from finance_agent.outcome import store
from finance_agent.outcome.settle import (
    BENCHMARK_CODE,
    MAX_HOLD_DAYS,
    STALE_DAYS,
    Settlement,
    evaluate_decision,
)

logger = logging.getLogger(__name__)


def report_outcome_scores(langfuse: Any, decision: dict[str, Any], settlement: Settlement) -> int:
    """按 langfuse_trace_id 后置上报 3 个 Score,返回成功数。

    trace 不存在/已过期 → WARN 不阻断(spec「trace 不可查容错」);
    trace_id 为 None 或 langfuse 为 None → 直接跳过;
    excess 为 None(基准缺失)→ 不上报 excess;
    hold/watch 无方向语义 → 不上报(delta update-decision-settlement-contract)。
    """
    if str(decision.get("action") or "") in ("hold", "watch"):
        return 0
    trace_id = decision.get("langfuse_trace_id")
    if not trace_id or langfuse is None:
        return 0
    comment = (
        f"settle_price={settlement.settle_price} hold_days={settlement.hold_days} "
        f"benchmark_return={settlement.benchmark_return if settlement.benchmark_return is not None else 'n/a'}"
    )
    scores: list[tuple[str, float, str]] = [
        ("decision_hit", 1.0 if settlement.decision_hit else 0.0, "BOOLEAN"),
        ("decision_return", settlement.decision_return, "NUMERIC"),
    ]
    if settlement.decision_excess is not None:
        scores.append(("decision_excess", settlement.decision_excess, "NUMERIC"))
    reported = 0
    for name, value, data_type in scores:
        try:
            langfuse.create_score(
                name=name,
                value=value,
                trace_id=trace_id,
                data_type=data_type,
                comment=comment,
            )
            reported += 1
        except Exception as e:
            logger.warning("score 上报失败(trace 不可查?): %s %s", name, e)
    return reported


def _normalize_dates(df: pd.DataFrame) -> pd.DataFrame:
    """日期列归一化为 'YYYY-MM-DD' 字符串(与 settle._normalize_dates 同语义)。

    akshare 的日期列是 datetime.date 对象,与 str 比较会 TypeError;job 层的
    has_new_rows / _is_stale 判定先归一,不改动调用方 DataFrame。
    """
    df = df.copy()
    df["日期"] = df["日期"].astype(str).str[:10]
    return df


def _is_stale(kline: pd.DataFrame | None, benchmark: pd.DataFrame | None) -> bool:
    """ticker 最新行落后基准最新行 ≥ STALE_DAYS 个交易日(按基准行数,无状态判定)。"""
    if kline is None or kline.empty or benchmark is None or benchmark.empty:
        return False
    ticker_last = str(kline.iloc[-1]["日期"])
    bench_dates = [str(d) for d in benchmark["日期"]]
    later = [d for d in bench_dates if d > ticker_last]
    return len(later) >= STALE_DAYS


def settle_open_decisions(
    *,
    client: Any = None,
    db_path: str | Path | None = None,
    langfuse: Any = None,
    kline_days: int | None = None,
) -> dict[str, int]:
    """遍历 open 决策日批结算。返回 {settled, skipped, stale, scores_reported, errors}。"""
    if client is None:
        from finance_agent.data.akshare_client import AKShareClient

        client = AKShareClient()
    days = kline_days or MAX_HOLD_DAYS + 15
    result = {"settled": 0, "skipped": 0, "stale": 0, "scores_reported": 0, "errors": 0}

    try:
        open_decisions = store.get_open_decisions(db_path)
    except Exception as e:
        # DB 全挂:记一条干净 ERROR 返回,不让 traceback 逃逸(旁路铁律)
        logger.error("读取 open 决策失败,本批终止: %s", e)
        result["errors"] += 1
        return result

    # 基准全批只拉一次(N 决策 N+1 次网络调用,而非 2N);失败 → None 降级:
    # evaluate_decision 对 benchmark=None 跳过 excess,report 同步跳过 excess score。
    benchmark: pd.DataFrame | None
    try:
        benchmark = client.fetch_index_kline(BENCHMARK_CODE, days=days)
        if benchmark is not None and not benchmark.empty:
            benchmark = _normalize_dates(benchmark)
    except Exception as e:
        logger.warning("基准行情拉取失败,本批按无基准降级: %s", e)
        benchmark = None

    for decision in open_decisions:
        try:
            kline = client.fetch_kline(decision["ticker"], days=days, adjust=SETTLEMENT_ADJUST)
            decision_date = str(decision["timestamp"])[:10]
            if kline is not None and not kline.empty:
                kline = _normalize_dates(kline)
            has_new_rows = (
                kline is not None
                and not kline.empty
                and not kline[kline["日期"] > decision_date].empty
            )
        except Exception as e:
            logger.warning("行情拉取失败,本次跳过 %s: %s", decision["decision_id"], e)
            result["errors"] += 1
            continue

        if not has_new_rows:
            if _is_stale(kline, benchmark):
                logger.warning(
                    "data_stale: %s(%s)K 线落后基准 ≥ %d 个交易日",
                    decision["decision_id"],
                    decision["ticker"],
                    STALE_DAYS,
                )
                result["stale"] += 1
            result["skipped"] += 1
            continue

        try:
            settlement = evaluate_decision(decision, kline, benchmark)
        except Exception as e:
            # 防御性包裹(旁路铁律):evaluate 有数值守卫返回 None,仍防意外异常
            logger.warning("结算评估异常,本次跳过 %s: %s", decision["decision_id"], e)
            result["errors"] += 1
            continue
        if settlement is None:
            result["skipped"] += 1
            continue

        try:
            # 幂等:落库前再查 settled_at(并发/重复执行防御)
            current = [
                d
                for d in store.get_open_decisions(db_path)
                if d["decision_id"] == decision["decision_id"]
            ]
            if not current:
                result["skipped"] += 1
                continue
            store.mark_settled(
                decision["decision_id"],
                {
                    "status": settlement.status,
                    "settled_at": settlement.settle_date,
                    "settle_price": settlement.settle_price,
                    "hold_days": settlement.hold_days,
                    "decision_return": settlement.decision_return,
                    "benchmark_return": settlement.benchmark_return,
                    "decision_excess": settlement.decision_excess,
                },
                db_path,
            )
            result["settled"] += 1
            result["scores_reported"] += report_outcome_scores(langfuse, decision, settlement)
        except Exception as e:
            # sqlite 瞬时写失败(disk full/busy_timeout)仅跳过该决策,不中断整批
            logger.warning("落库/上报异常,本次跳过 %s: %s", decision["decision_id"], e)
            result["errors"] += 1
            continue

    return result
