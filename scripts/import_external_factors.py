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
import sys
from pathlib import Path
from typing import Any

from finance_agent.factors.external import run_factor_external_import


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
    args = parser.parse_args(argv)

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
