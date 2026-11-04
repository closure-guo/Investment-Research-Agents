"""非破坏性重算 derived 净值读数（update-track-record-symbol-aggregation）。

与 `track_record_rebuild.py` 的区别：**不清空 daily_marks**。口径变更只影响
「如何把 marks 聚合成净值」，marks 本身（含已结算观点的历史盯市）不动，因此
采用「原地重算 + 覆盖写」而非 wipe + 重建。

做两件事：

1. `equity_curve`：用当前口径从全部 marks 重算净值点，逐日 upsert（日期集合不变，
   只更新 agent_nav / benchmark_nav）；
2. `agent_metrics_daily`：对每个历史 metric_date 做 **as-of 重算**（只用
   mark_date ≤ 该日的数据），覆盖组合派生列（年化/波动/夏普/回撤/风险分/β/α），
   不动 sample_size / settled / win_rate / avg_excess（那是观点统计，与净值口径无关）。

用法：

  uv run python scripts/track_record_recompute_caliber.py --db-path /tmp/x.db --dry-run
  uv run python scripts/track_record_recompute_caliber.py --db-path /tmp/x.db --yes
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from finance_agent.data.hotdb_guard import host_write_guard
from finance_agent.outcome.track_record.metrics import (
    compute_metrics_from_marks,
    daily_portfolio_returns,
)
from finance_agent.outcome.track_record.model import (
    duplicate_prediction_ids,
    list_daily_marks,
    list_metric_dates,
    prediction_ids_by_direction,
    prediction_symbols,
    update_metrics_daily_columns,
    upsert_equity_point,
)

_REPO_ROOT = Path(__file__).resolve().parents[1]
_PORTFOLIO_COLUMNS = (
    "annual_return",
    "volatility",
    "sharpe",
    "max_drawdown",
    "risk_score",
    "risk_label",
    "beta",
    "jensen_alpha",
)


def _resolve_db(args_db: str | None) -> str:
    if args_db:
        return args_db
    return os.getenv("SESSIONS_DB_PATH", "data/sessions.db")


def recompute(db: str, *, dry_run: bool, metrics_scope: str = "latest") -> dict:
    """重算净值与历史指标行；dry_run 只打印不写库。

    日历与基准必须显式传入（与日批 `run_daily_marking` 同源）：缺省 calendar 会
    退化为 marks-only 口径，把「有观点但无盯市」的空仓日整段丢掉——净值点从 20
    掉到 17，且旧行不会被覆盖，形成新旧混排。
    """
    from finance_agent.outcome.track_record.model import list_index_closes
    from finance_agent.outcome.track_record.risk_free import risk_free_series

    marks = list_daily_marks(db_path=db)
    excl = prediction_ids_by_direction("neutral", db_path=db) | duplicate_prediction_ids(db_path=db)
    symbols = prediction_symbols(db_path=db)

    bench_by_date = {
        str(r["trade_date"]): float(r["close"])
        for r in list_index_closes("000300", db_path=db)
        if r["close"] is not None
    }
    directional_dates = sorted(
        {str(m["mark_date"]) for m in marks if str(m["prediction_id"]) not in excl}
    )
    first_mark = directional_dates[0] if directional_dates else None
    calendar = sorted(d for d in bench_by_date if first_mark is not None and d >= first_mark)

    def _metrics(as_of_marks: list[dict[str, Any]], upto: str | None = None):
        bench = {d: c for d, c in bench_by_date.items() if upto is None or d <= upto}
        cal = [d for d in calendar if upto is None or d <= upto]
        rf = risk_free_series(sorted({str(m["mark_date"]) for m in as_of_marks}), db_path=db)
        return compute_metrics_from_marks(
            as_of_marks,
            rf_series=rf,
            calendar_dates=cal or None,
            benchmark_by_date=bench or None,
            exclude_prediction_ids=excl,
            symbol_by_prediction=symbols,
        )

    new_curve = _metrics(marks).nav_points

    old_rets = daily_portfolio_returns(marks, exclude_prediction_ids=excl)
    new_rets = daily_portfolio_returns(
        marks, exclude_prediction_ids=excl, symbol_by_prediction=symbols
    )

    updated_points = 0
    if not dry_run:
        for pt in new_curve:
            upsert_equity_point(
                str(pt["date"]),
                agent_nav=float(pt["agent_nav"]),
                benchmark_nav=float(pt["benchmark_nav"])
                if pt["benchmark_nav"] is not None
                else None,
                db_path=db,
            )
            updated_points += 1

    rows: list[dict] = []
    skipped: list[str] = []
    metric_dates = list_metric_dates(db_path=db)
    if metrics_scope == "none":
        metric_dates = []
    elif metrics_scope == "latest":
        metric_dates = metric_dates[-1:]
    for md in metric_dates:
        as_of = [m for m in marks if str(m["mark_date"]) <= md]
        # 空仓期(当日无任何方向性盯市)不覆盖:算不出组合指标时保留原行,避免写入噪声 None
        if not any(str(m["prediction_id"]) not in excl for m in as_of if m["mark_date"] == md):
            skipped.append(md)
            continue
        pm = _metrics(as_of, upto=md)
        values = {c: getattr(pm, c) for c in _PORTFOLIO_COLUMNS}
        if dry_run:
            rows.append({"metric_date": md, "new": values})
        else:
            rows.append(update_metrics_daily_columns(md, values, db_path=db))

    return {
        "nav_points": len(new_curve),
        "written_points": updated_points,
        "old_arith_cum": round(sum(old_rets.values()), 6),
        "new_arith_cum": round(sum(new_rets.values()), 6),
        "old_last_nav": round(new_curve[-1]["agent_nav"], 6) if new_curve else None,
        "metrics_rows": rows,
        "skipped_rows": skipped,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="非破坏性重算 equity_curve + agent_metrics_daily")
    ap.add_argument("--db-path", default=None)
    ap.add_argument("--yes", action="store_true", help="确认写库（缺省为 dry-run）")
    ap.add_argument(
        "--metrics-scope",
        choices=("latest", "all", "none"),
        default="latest",
        help="agent_metrics_daily 覆盖范围：latest=仅最新一行(缺省，口径切点后保持历史不动)",
    )
    ap.add_argument("--skip-backup", action="store_true")
    ap.add_argument("--allow-host-write", action="store_true")
    args = ap.parse_args()
    db = _resolve_db(args.db_path)

    guard_reason = host_write_guard(
        db_path=db,
        allow_host_write=args.allow_host_write,
        dry_run=not args.yes,
        repo_root=_REPO_ROOT,
        script_name="scripts/track_record_recompute_caliber.py",
        argv=sys.argv[1:],
    )
    if guard_reason:
        print(guard_reason, file=sys.stderr)
        raise SystemExit(2)

    if args.yes and not args.skip_backup:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        shutil.copy2(db, f"{db}.bak-caliber-{stamp}")
        print(f"backup: {db}.bak-caliber-{stamp}")

    result = recompute(db, dry_run=not args.yes, metrics_scope=args.metrics_scope)
    print(f"nav_points={result['nav_points']} written={result['written_points']}")
    print(f"arith cum: old={result['old_arith_cum']:+.4f} new={result['new_arith_cum']:+.4f}")
    print(f"last nav: {result['old_last_nav']}")
    for row in result["metrics_rows"][:3]:
        print(" ", row)
    print(f"... {len(result['metrics_rows'])} metric rows")


if __name__ == "__main__":
    main()
