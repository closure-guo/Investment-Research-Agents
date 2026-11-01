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
import sys
from pathlib import Path

from finance_agent.data.hotdb_guard import host_write_guard
from finance_agent.factors.reject_backfill import backfill_rejected_from_state

_REPO_ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    parser = argparse.ArgumentParser(description="存量拒绝候选回填进因子库拒绝池")
    parser.add_argument("--state", required=True, help="收割状态文件路径（processed 映射）")
    parser.add_argument("--db", required=True, help="因子库 SQLite 路径")
    parser.add_argument(
        "--allow-host-write",
        action="store_true",
        help="显式绕过宿主直跑护栏（#515 迁卷后：确知在操作过期回滚副本时使用）",
    )
    parser.add_argument(
        "--candidates",
        default=None,
        help="收割候选文件路径（可选；命中 name 时重建 hypothesis/category）",
    )
    args = parser.parse_args()
    guard_reason = host_write_guard(
        db_path=args.db,
        allow_host_write=args.allow_host_write,
        dry_run=False,
        repo_root=_REPO_ROOT,
        script_name="scripts/backfill_rejected_factors.py",
        argv=sys.argv[1:],
    )
    if guard_reason:
        print(guard_reason, file=sys.stderr)
        raise SystemExit(2)
    summary = backfill_rejected_from_state(
        args.state, db_path=args.db, candidates_path=args.candidates
    )
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    main()
