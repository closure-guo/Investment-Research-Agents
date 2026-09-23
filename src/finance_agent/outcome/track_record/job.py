"""predictions 日批判定 job(add-track-record):遍历 open 观点 → 拉行情 → 判定 → 落状态。

与旧 settle_open_decisions(止损/目标/超期)不同:horizon 到点按区间超额判定 win/loss/neutral;
superseded(同标的新观点方向相反/目标价不同)立即按现价结算旧观点(neutral 不走 superseded;
与 horizon 路径共用 ±2% 中性带判定 win/loss/neutral);
连续无行情标 unresolvable。幂等:update_prediction_status 前不重复处理已非 open 的观点;
失败隔离:单观点异常跳过不中断整批。

delta update-decision-settlement-contract:两路径入场基准均为派生 settle_entry_price
(judgment.derive_entry,非存档参考价);neutral 判定结果写 avoidance_status、status 落终态
"avoidance"(不写 resolved_*,也不再留在 open 池);long/short 结算后向 Langfuse 上报
decision_hit/return/excess(superseded 提前结算同样上报,excess 不可得故只报两个;Score
失败仅 WARN)。unresolvable 无结算日,resolved_at 写 NULL。
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import pandas as pd

from finance_agent.data.akshare_client import SETTLEMENT_ADJUST
from finance_agent.outcome.track_record.judgment import (
    AVOIDANCE_STATUSES,
    DEFAULT_NEUTRAL_BAND,
    TERMINAL_AVOIDANCE_STATUS,
    Resolution,
    derive_entry,
    resolve_prediction,
    should_supersede,
)
from finance_agent.outcome.track_record.model import (
    list_predictions,
    update_prediction_status,
)

logger = logging.getLogger(__name__)

STALE_DAYS = int(__import__("os").getenv("DECISION_STALE_DAYS", "5"))
BENCHMARK_CODE = "000300"


def _report_scores(langfuse: Any, prediction: dict, resolution: Resolution) -> int:
    """long/short 结算后上报 3 个 Score;neutral 回避判定与缺 trace/langfuse 跳过。

    与旧链路 outcome.job.report_outcome_scores 同形:trace 不可查仅 WARN,不阻断结算。
    """
    trace_id = prediction.get("langfuse_trace_id")
    if not trace_id or langfuse is None or resolution.status in AVOIDANCE_STATUSES:
        return 0
    benchmark_return = (
        resolution.raw_return - resolution.excess_return
        if resolution.excess_return is not None
        else None
    )
    comment = (
        f"settle_price={resolution.exit_price} hold_days={resolution.hold_days} "
        f"benchmark_return={benchmark_return if benchmark_return is not None else 'n/a'}"
    )
    scores: list[tuple[str, float, str]] = [
        ("decision_hit", 1.0 if resolution.raw_return > 0 else 0.0, "BOOLEAN"),
        ("decision_return", resolution.raw_return, "NUMERIC"),
    ]
    if resolution.excess_return is not None:
        scores.append(("decision_excess", resolution.excess_return, "NUMERIC"))
    reported = 0
    for name, value, data_type in scores:
        try:
            langfuse.create_score(
                name=name, value=value, trace_id=trace_id, data_type=data_type, comment=comment
            )
            reported += 1
        except Exception as e:  # noqa: BLE001 - trace 不可查容错
            logger.warning("score 上报失败(trace 不可查?): %s %s", name, e)
    return reported


def _code(symbol: str) -> str:
    """'600519.SH' → '600519'。"""
    return symbol.split(".")[0]


def _normalize_dates(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["日期"] = df["日期"].astype(str).str[:10]
    return df


def _is_stale(kline: pd.DataFrame | None, benchmark: pd.DataFrame | None) -> bool:
    if kline is None or kline.empty or benchmark is None or benchmark.empty:
        return False
    ticker_last = str(kline.iloc[-1]["日期"])
    bench_dates = [str(d) for d in benchmark["日期"]]
    return len([d for d in bench_dates if d > ticker_last]) >= STALE_DAYS


def settle_open_predictions(
    *,
    client: Any = None,
    db_path: str | Path | None = None,
    kline_days: int = 280,
    langfuse: Any = None,
) -> dict[str, int]:
    """遍历 open predictions 日批判定。返回 {settled, superseded, unresolvable, skipped,
    scores_reported, errors}。"""
    if client is None:
        from finance_agent.data.akshare_client import AKShareClient

        client = AKShareClient()
    if langfuse is None:
        try:
            from finance_agent.langfuse_tracing import get_langfuse

            langfuse = get_langfuse()
        except Exception:  # noqa: BLE001 - 观测旁路,不阻断结算
            langfuse = None
    result = {
        "settled": 0,
        "superseded": 0,
        "unresolvable": 0,
        "skipped": 0,
        "scores_reported": 0,
        "errors": 0,
    }
    try:
        open_preds = list_predictions(status="open", db_path=db_path)
    except Exception as e:  # noqa: BLE001
        logger.error("读取 open 观点失败,本批终止: %s", e)
        result["errors"] += 1
        return result

    benchmark: pd.DataFrame | None
    try:
        benchmark = client.fetch_index_kline(BENCHMARK_CODE, days=kline_days)
        if benchmark is not None and not benchmark.empty:
            benchmark = _normalize_dates(benchmark)
    except Exception as e:  # noqa: BLE001
        logger.warning("基准行情拉取失败,本批按无基准降级: %s", e)
        benchmark = None

    # 按 symbol 分组,组内按 created_at 升序:同标的多条 open → 旧观点被新观点 supersede
    by_symbol: dict[str, list[dict]] = {}
    for p in open_preds:
        by_symbol.setdefault(p["symbol"], []).append(p)
    for sym, group in by_symbol.items():
        group.sort(key=lambda p: str(p["created_at"]))
        if len(group) > 1:
            for old in group[:-1]:
                if old["direction"] == "neutral":
                    continue  # spec:neutral 不走 superseded(回避判定以完整 horizon 窗口为准)
                new = group[-1]
                if should_supersede(old, new):
                    try:
                        kline = client.fetch_kline(
                            _code(sym), days=kline_days, adjust=SETTLEMENT_ADJUST
                        )
                        if kline is not None and not kline.empty:
                            kline = _normalize_dates(kline)
                        exit_price = (
                            float(kline.iloc[-1]["收盘"])
                            if kline is not None and not kline.empty
                            else None
                        )
                        if exit_price is None:
                            result["skipped"] += 1
                            continue
                        derived = derive_entry(old, kline)
                        if derived is None:
                            result["skipped"] += 1
                            continue
                        entry_date, entry = derived
                        if entry <= 0:
                            # 与 horizon 路径对齐:无有效入场基准 → 不判定(不写 0 价入场)
                            result["skipped"] += 1
                            continue
                        exit_date = str(kline.iloc[-1]["日期"])
                        sign = 1.0 if old["direction"] == "long" else -1.0
                        # 分类前 round 消浮点噪声；与 horizon 路径共用 ±2% 中性带
                        raw = round(sign * (exit_price / entry - 1.0), 6)
                        band = DEFAULT_NEUTRAL_BAND
                        sup_resolution = Resolution(
                            status=(
                                "resolved_win"
                                if raw > band
                                else ("resolved_loss" if raw < -band else "resolved_neutral")
                            ),
                            entry_date=entry_date,
                            entry_price=round(entry, 6),
                            exit_date=exit_date,
                            exit_price=exit_price,
                            raw_return=round(raw, 6),
                            # superseded 为提前结算:无完整同期基准区间,超额不可得
                            excess_return=None,
                            hold_days=int((kline["日期"] > entry_date).sum()),
                            resolution_rule="superseded",
                        )
                        update_prediction_status(
                            old["prediction_id"],
                            {
                                "status": sup_resolution.status,
                                "exit_price": sup_resolution.exit_price,
                                "raw_return": sup_resolution.raw_return,
                                "resolution_rule": sup_resolution.resolution_rule,
                                "resolved_at": sup_resolution.exit_date,
                                "settle_entry_price": sup_resolution.entry_price,
                            },
                            db_path,
                        )
                        result["superseded"] += 1
                        # spec decision-outcome「结算即上报 Score」:状态转 resolved_* 即上报
                        result["scores_reported"] += _report_scores(langfuse, old, sup_resolution)
                    except Exception as e:  # noqa: BLE001
                        logger.warning("superseded 判定失败 %s: %s", old["prediction_id"], e)
                        result["errors"] += 1

    # 剩余 open 观点:horizon 到点判定 / 长期无行情 unresolvable
    remaining = list_predictions(status="open", db_path=db_path)
    for p in remaining:
        try:
            kline = client.fetch_kline(
                _code(p["symbol"]), days=kline_days, adjust=SETTLEMENT_ADJUST
            )
            if kline is not None and not kline.empty:
                kline = _normalize_dates(kline)
            created = str(p["created_at"])[:10]
            has_new_rows = (
                kline is not None and not kline.empty and not kline[kline["日期"] > created].empty
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("行情拉取失败,本次跳过 %s: %s", p["prediction_id"], e)
            result["errors"] += 1
            continue

        if not has_new_rows:
            if _is_stale(kline, benchmark):
                update_prediction_status(
                    p["prediction_id"],
                    {
                        "status": "unresolvable",
                        "resolution_rule": "stale_no_market",
                        # 无结算日 → NULL(写 created_at 会让持仓天数分桶失真为 0 天)
                        "resolved_at": None,
                    },
                    db_path,
                )
                result["unresolvable"] += 1
            else:
                result["skipped"] += 1
            continue

        try:
            resolution = resolve_prediction(p, kline, benchmark)
        except Exception as e:  # noqa: BLE001
            logger.warning("判定评估异常,本次跳过 %s: %s", p["prediction_id"], e)
            result["errors"] += 1
            continue
        if resolution is None:
            result["skipped"] += 1
            continue
        payload: dict[str, Any] = {
            "exit_price": resolution.exit_price,
            "raw_return": resolution.raw_return,
            "excess_return": resolution.excess_return,
            "resolution_rule": resolution.resolution_rule,
            "resolved_at": resolution.exit_date,
            "settle_entry_price": resolution.entry_price,
        }
        if resolution.status in AVOIDANCE_STATUSES:
            # neutral 回避判定:结果只入 avoidance_status;status 落终态 avoidance 使其
            # 离开 open 池(保持 open 会被日批反复重结算,且不进胜率/不复用 resolved_*)
            payload["avoidance_status"] = resolution.status
            payload["status"] = TERMINAL_AVOIDANCE_STATUS
        else:
            payload["status"] = resolution.status
        try:
            update_prediction_status(p["prediction_id"], payload, db_path)
            result["settled"] += 1
            result["scores_reported"] += _report_scores(langfuse, p, resolution)
        except Exception as e:  # noqa: BLE001
            logger.warning("落库异常,本次跳过 %s: %s", p["prediction_id"], e)
            result["errors"] += 1

    return result
