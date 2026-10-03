"""回填无风险利率历史 + as-of 重算指标快照（update-risk-free-rate-source）。

与日批 sync_risk_free_rates 共用同一幂等写入（INSERT OR REPLACE），重跑安全。
用法:
    uv run python scripts/backfill_risk_free_rates.py
    uv run python scripts/backfill_risk_free_rates.py --db-path data/sessions.db --no-recompute-metrics
"""

import argparse
import os

from finance_agent.outcome.track_record.model import init_track_record_tables
from finance_agent.outcome.track_record.risk_free import sync_risk_free_rates


def main() -> None:
    parser = argparse.ArgumentParser(description="回填中债1Y无风险利率历史")
    parser.add_argument("--db-path", default=os.getenv("SESSIONS_DB_PATH", "data/sessions.db"))
    parser.add_argument(
        "--no-recompute-metrics", action="store_true", help="只回填利率，不重算快照"
    )
    args = parser.parse_args()
    init_track_record_tables(args.db_path)
    result = sync_risk_free_rates(db_path=args.db_path)
    print(f"rf stored={result['stored']} range=[{result['start']}..{result['end']}]")
    if not args.no_recompute_metrics:
        from finance_agent.outcome.track_record.metrics import recompute_rf_metrics_history

        for row in recompute_rf_metrics_history(db_path=args.db_path):
            print(row)


if __name__ == "__main__":
    main()
