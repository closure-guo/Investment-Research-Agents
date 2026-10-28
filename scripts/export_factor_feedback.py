"""导出跨会话因子记忆桥（收割状态 → 生成侧可读清单）。

用法::

    uv run python scripts/export_factor_feedback.py
    uv run python scripts/export_factor_feedback.py --limit 60 --out tmp/explored.json

产物默认 ``data/factor_research/explored_factors.json``；distro 侧
``rdagent/scenarios/qlib/proposal/explored_feedback.py`` 经 ``FACTOR_FEEDBACK_PATH``
读取同一路径。脚本只读状态/候选文件，写出单个 JSON（原子替换），不写因子库。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from finance_agent.factors.feedback import (
    DEFAULT_LIMIT,
    DEFAULT_OUTPUT_PATH,
    PER_GATE_FLOOR,
    UNTRANSLATABLE_QUOTA,
    export_feedback,
)

DEFAULT_STATE = "data/factor_research/harvest_state.json"
DEFAULT_CANDIDATES = "data/factor_research/candidates.json"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="导出跨会话因子记忆桥（explored_factors.json）")
    parser.add_argument("--state", default=DEFAULT_STATE, help="harvest_state.json 路径")
    parser.add_argument("--candidates", default=DEFAULT_CANDIDATES, help="candidates.json 路径")
    parser.add_argument("--out", default=DEFAULT_OUTPUT_PATH, help="输出 JSON 路径")
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT, help="清单条目上限")
    parser.add_argument(
        "--untranslatable-quota",
        type=int,
        default=UNTRANSLATABLE_QUOTA,
        help="untranslatable 条目保底配额（不足时空位归还 rejected）",
    )
    parser.add_argument(
        "--gate-floor",
        type=int,
        default=PER_GATE_FLOOR,
        help="rejected 档每闸保底出场条数（累计不足则全量；余量按占比）",
    )
    parser.add_argument(
        "--library-db",
        default=None,
        help="因子库路径（风格缺口定向块数据源；缺省走 FACTOR_LIBRARY_DB_PATH 环境变量）",
    )
    args = parser.parse_args(argv)

    try:
        payload = export_feedback(
            state_path=Path(args.state),
            candidates_path=Path(args.candidates),
            output_path=Path(args.out),
            limit=args.limit,
            untranslatable_quota=args.untranslatable_quota,
            gate_floor=args.gate_floor,
            library_db_path=args.library_db,
        )
    except (OSError, ValueError) as exc:
        print(f"export failed: {exc}", file=sys.stderr)
        return 1

    counts = payload["counts"]
    print(
        f"wrote {args.out}: explored={payload['total_explored']} returned={payload['returned']} "
        f"admitted={counts.get('admitted', 0)} rejected={counts.get('rejected', 0)} "
        f"untranslatable={counts.get('untranslatable', 0)} "
        f"contract={'yes' if payload.get('contract') else 'no'} "
        f"style_gap={'yes' if payload.get('style_gap') else 'no'}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
