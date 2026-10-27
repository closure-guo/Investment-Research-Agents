"""存量拒绝候选回填 CLI（add-rejected-factor-pool T5）。

从收割状态文件把历史 gate-rejected（带 DSL 表达式）候选按原裁决原样导入因子库
拒绝池；幂等可重跑（同构命中 upsert，零新增）。``untranslatable`` /
``transcription_error``（无 DSL）不在导入面内。

用法（``--state`` / ``--db`` 显式必填，MUST NOT 依赖生产默认路径隐式生效）::

    uv run python scripts/backfill_rejected_factors.py \
        --state data/factor_harvest_state.json \
        --db data/factor_library.db \
        [--candidates data/factor_candidates.json]
"""

from __future__ import annotations

import argparse
import json

from finance_agent.factors.reject_backfill import backfill_rejected_from_state


def main() -> None:
    parser = argparse.ArgumentParser(description="存量拒绝候选回填进因子库拒绝池")
    parser.add_argument("--state", required=True, help="收割状态文件路径（processed 映射）")
    parser.add_argument("--db", required=True, help="因子库 SQLite 路径")
    parser.add_argument(
        "--candidates",
        default=None,
        help="收割候选文件路径（可选；命中 name 时重建 hypothesis/category）",
    )
    args = parser.parse_args()
    summary = backfill_rejected_from_state(
        args.state, db_path=args.db, candidates_path=args.candidates
    )
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    main()
