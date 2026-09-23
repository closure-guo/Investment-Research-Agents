"""Cohort 读数导出（delta add-forward-paper-trading-cohort §「Cohort 读数导出」，Δ3 Task 6）。

按 ``universe_version`` + 时间窗（``since`` = ``trade_date`` 下界，含）导出 cohort 观点
清单（逐观点行）与批次级汇总（跑批成功率 / 观点落库率 / 结算状态分布 / failure 明细）。

**join 唯一键 = ``langfuse_trace_id``**：``predictions`` 表**无 ``session_id`` 列**
（DDL 与迁移已核实），故 ``cohort_runs.langfuse_trace_id`` ↔
``predictions.langfuse_trace_id`` 是唯一可行关联。success 行若无法关联到任何观点行
（trace 为 NULL/空，或 trace 有值但 predictions 无匹配行）→ 计入 ``unlinked``
（同时进 ``failure_reasons["unlinked"]``），**不得当作普通失败码、也不得静默丢弃**；
``unlinked_trace_missing`` 披露其中因 trace 缺失而无法 join 的子集。

**消费方 = Δ1 收口流程**（``docs/evals/metrics.md`` §1.8⑤「收口纪律」）：
健康检查 ``evals/outcome/health.py::collect_outcome_health`` 负责结算口径（结算成功率 /
不可判定率 / integrity / 记账完整率）；本导出补足**跑批侧**读数（跑批成功率 /
观点落库率 / 结算状态分布 / failure 明细 / 成本），两者在收口时并读。口径变更
SHALL 先改 ``docs/evals/metrics.md`` §1.8 再动本文件。

**只读**：仅 SELECT；不建表（不调用 ``init_cohort_runs``）、不写任何行。
DB 文件缺失抛 ``FileNotFoundError``（不静默建空库）；表缺失按空处理（零值）。

口径（一次说清，避免与健康检查混淆）：
- ``planned`` = 记账行总数；``skipped`` = 未执行的尝试（幂等重复 / 预算熔断）。
- ``run_success_rate`` = ``success / (success + failure)``（skipped 非执行尝试，不计分母；
  分母 0 → ``None``，**不报 0%**）。
- ``opinions_persisted`` = 成功关联到 ≥1 条观点行的 success 行数（观点落库率分子，
  与 ``unlinked`` 互补：``opinions_persisted + unlinked == success``）。
- ``rows`` = 关联到的**观点行**（一条 run 产出多条观点时逐行展开）。
- ``tokens_null_runs`` = 已执行行（success/failure）中 ``tokens_total`` 为 NULL 的行数
  （usage 真值缺失信号；skipped 行本就无 usage，不计）。
- ``failure_reasons`` = 非空 ``failure_reason``（failure/skipped 稳定码）+ ``unlinked``。

CLI：``python -m evals.outcome.cohort_readings --db ... --universe-version v1 --since 2026-09-24``
（stdout JSON，``ensure_ascii=False, indent=2``）。
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from pathlib import Path
from typing import Any

from finance_agent.outcome.cohort.model import list_cohort_runs

# 关联失败稳定码（success 行未关联到任何观点行；见模块 docstring）
UNLINKED_CODE = "unlinked"
_ATTEMPTED_STATUSES = ("success", "failure")

_ROW_COLUMNS = (
    "symbol",
    "created_at",
    "direction",
    "status",
    "avoidance_status",
    "raw_return",
    "excess_return",
    "entry_price",
    "settle_entry_price",
    "confidence",
    "langfuse_trace_id",
)


def _db_path(db_path: str | Path | None) -> Path:
    return Path(db_path) if db_path else Path(os.getenv("SESSIONS_DB_PATH", "data/sessions.db"))


def _has_table(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone()
    return row is not None


def _table_exists(path: Path, name: str) -> bool:
    """表是否存在（只读探测；表缺失按空处理，不建表）。"""
    conn = sqlite3.connect(path, timeout=15.0)
    try:
        return _has_table(conn, name)
    finally:
        conn.close()


def _fetch_predictions(path: Path, traces: set[str]) -> list[dict[str, Any]]:
    """按 trace id 集合取观点行（只读）；表缺失或无 trace → 空列表。"""
    if not traces:
        return []
    conn = sqlite3.connect(path, timeout=15.0)
    conn.row_factory = sqlite3.Row
    try:
        if not _has_table(conn, "predictions"):
            return []
        placeholders = ",".join("?" * len(traces))
        sql = (
            f"SELECT {', '.join(_ROW_COLUMNS)} FROM predictions"  # noqa: S608 - 列名/占位符固定
            f" WHERE langfuse_trace_id IN ({placeholders})"
        )
        return [dict(r) for r in conn.execute(sql, sorted(traces)).fetchall()]
    finally:
        conn.close()


def _row_view(prediction: dict[str, Any]) -> dict[str, Any]:
    return {
        "ticker": prediction["symbol"],
        "created_at": prediction["created_at"],
        "direction": prediction["direction"],
        "status": prediction["status"],
        "avoidance_status": prediction["avoidance_status"],
        "raw_return": prediction["raw_return"],
        "excess_return": prediction["excess_return"],
        "entry_price": prediction["entry_price"],
        "settle_entry_price": prediction["settle_entry_price"],
        "confidence": prediction["confidence"],
        "trace_id": prediction["langfuse_trace_id"],
    }


def collect_cohort_readings(
    db_path: str | Path | None = None,
    *,
    universe_version: str | None = None,
    since: str | None = None,
) -> dict[str, Any]:
    """导出 cohort 读数（只读）。

    ``universe_version`` 精确过滤记账行；``since`` 为 ``trade_date`` 下界（含），
    语义复用 ``list_cohort_runs``（不另造）。DB 文件缺失抛 ``FileNotFoundError``。
    """
    path = _db_path(db_path)
    if not path.exists():
        raise FileNotFoundError(f"DB 不存在：{path}")

    runs = (
        list_cohort_runs(universe_version=universe_version, since=since, db_path=path)
        if _table_exists(path, "cohort_runs")
        else []
    )

    status_counts: dict[str, int] = {}
    failure_reasons: dict[str, int] = {}
    versions: set[str] = set()
    trade_dates: set[str] = set()
    success_runs: list[dict[str, Any]] = []
    tokens_total_sum = 0
    tokens_null_runs = 0
    llm_calls_sum = 0
    for run in runs:
        status = run["status"]
        status_counts[status] = status_counts.get(status, 0) + 1
        versions.add(run["universe_version"])
        trade_dates.add(run["trade_date"])
        if run["failure_reason"]:
            code = run["failure_reason"]
            failure_reasons[code] = failure_reasons.get(code, 0) + 1
        if run["tokens_total"] is None:
            if status in _ATTEMPTED_STATUSES:
                tokens_null_runs += 1  # skipped 未执行 → 无 usage，不计
        else:
            tokens_total_sum += run["tokens_total"]
        if run["llm_calls"] is not None:
            llm_calls_sum += run["llm_calls"]
        if status == "success":
            success_runs.append(run)

    traces = {r["langfuse_trace_id"] for r in success_runs if r["langfuse_trace_id"]}
    predictions = _fetch_predictions(path, traces)
    by_trace: dict[str, int] = {}
    for prediction in predictions:
        trace = prediction["langfuse_trace_id"]
        by_trace[trace] = by_trace.get(trace, 0) + 1

    opinions_persisted = 0
    unlinked = 0
    unlinked_trace_missing = 0
    for run in success_runs:
        trace = run["langfuse_trace_id"]
        if trace and by_trace.get(trace):
            opinions_persisted += 1
        else:
            unlinked += 1
            if not trace:
                unlinked_trace_missing += 1
    if unlinked:
        failure_reasons[UNLINKED_CODE] = unlinked

    settlement_status_counts: dict[str, int] = {}
    for prediction in predictions:
        status = prediction["status"]
        settlement_status_counts[status] = settlement_status_counts.get(status, 0) + 1

    success = status_counts.get("success", 0)
    failure = status_counts.get("failure", 0)
    attempted = success + failure
    rows = sorted(
        (_row_view(p) for p in predictions),
        key=lambda r: (r["created_at"] or "", r["ticker"] or ""),
    )
    return {
        "batch": {
            "universe_version": universe_version,
            "universe_versions": sorted(versions),
            "since": since,
            "trade_dates": sorted(trade_dates),
            "planned": len(runs),
            "success": success,
            "failure": failure,
            "skipped": status_counts.get("skipped", 0),
            "run_success_rate": round(success / attempted, 4) if attempted else None,
            "opinions_persisted": opinions_persisted,
            "unlinked": unlinked,
            "unlinked_trace_missing": unlinked_trace_missing,
            "settlement_status_counts": settlement_status_counts,
            "failure_reasons": failure_reasons,
            "tokens_total_sum": tokens_total_sum,
            "tokens_null_runs": tokens_null_runs,
            "llm_calls_sum": llm_calls_sum,
        },
        "rows": rows,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="cohort 读数导出（只读；cohort_runs × predictions 经 langfuse_trace_id）"
    )
    ap.add_argument("--db", default=None)
    ap.add_argument("--universe-version", default=None)
    ap.add_argument("--since", default=None, help="trade_date 下界（含）")
    args = ap.parse_args(argv)
    try:
        readings = collect_cohort_readings(
            args.db, universe_version=args.universe_version, since=args.since
        )
    except FileNotFoundError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 2
    print(json.dumps(readings, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
