"""外部候选因子导入脚本（unify-quant-research-architecture P0-3，运维入口）。

用法::

    uv run python scripts/import_external_factors.py --file candidates.json
    uv run python scripts/import_external_factors.py --file c.json --db data/factor_library.db

候选文件 = JSON 数组，每条 ``{"expression": "...", "hypothesis": "...", "category": "..."}``
（``category`` 可选）。通道契约见 ``finance_agent.factors.external``：来源强制
``external``、与本地提案同一五道闸、外部结论字段（IC/收益等）不参与裁决；
面板深度不足 fail loud（先补历史回填）。

- 退出码 0 = 跑完（含全拒 / 空文件）；2 = 参数或文件错误（不静默）。
- 摘要 JSON（``{admitted, rejected, errors, rejections}``）打印到 stdout。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

from finance_agent.data.hotdb_guard import host_write_guard
from finance_agent.factors.external import run_factor_external_import

_REPO_ROOT = Path(__file__).resolve().parents[1]


def _load_candidates(path: Path) -> list[dict[str, Any]]:
    """读候选文件；非 JSON 数组 / 元素非对象 → ``ValueError``（调用方退 2）。"""
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list) or not all(isinstance(item, dict) for item in payload):
        raise ValueError("候选文件须为 JSON 数组（元素为对象）")
    return payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="外部候选因子导入（DSL 表达式 → 五道闸）")
    parser.add_argument("--file", required=True, help="候选 JSON 文件路径")
    parser.add_argument("--db", default=None, help="因子库路径（缺省 FACTOR_DB_PATH/默认）")
    parser.add_argument("--snapshot-db", default=None, help="快照库路径（缺省 env/默认）")
    parser.add_argument(
        "--allow-host-write",
        action="store_true",
        help="显式绕过宿主直跑护栏（#515 迁卷后：确知在操作过期回滚副本时使用）",
    )
    args = parser.parse_args(argv)

    # 双热库（因子库 + 快照库）逐一过护栏：宿主 data/ 是回滚副本，导入落旧库 = 白导
    for label, db in (
        ("因子库", args.db or os.getenv("FACTOR_LIBRARY_DB_PATH", "data/factor_library.db")),
        (
            "快照库",
            args.snapshot_db or os.getenv("SNAPSHOT_DB_PATH", "data/market_snapshot.db"),
        ),
    ):
        reason = host_write_guard(
            db_path=db,
            allow_host_write=args.allow_host_write,
            dry_run=False,
            repo_root=_REPO_ROOT,
            script_name="scripts/import_external_factors.py",
            argv=argv if argv is not None else sys.argv[1:],
        )
        if reason:
            print(f"[{label}] {reason}", file=sys.stderr)
            return 2

    path = Path(args.file)
    try:
        candidates = _load_candidates(path)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"错误：候选文件读取失败：{exc}", file=sys.stderr)
        return 2

    summary = run_factor_external_import(
        candidates, db_path=args.db, snapshot_db_path=args.snapshot_db
    )
    print(json.dumps(summary, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
