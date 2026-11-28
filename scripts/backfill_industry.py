"""行业门类历史回填（extend-factor-library-coverage §9，运维脚本）。

**推荐以容器内执行**（热库在 named volume；宿主机直跑会被 ``hotdb_guard`` 拒绝）::

    # 探针：先摸 50 票成功率（< 阈值即中止、不写库）
    docker compose exec -T backend python scripts/backfill_industry.py --probe 50
    # 全量：探针通过后同命令跑全量
    docker compose exec -T backend python scripts/backfill_industry.py

- 缺省标的池 = 证券主表（``security_master``）中 ``industry_l1`` 为空的在册代码；
  ``--symbols`` 显式指定时按给定清单处理。主表为空且未显式指定 → 退 2。
- 逐只 ``fetch_industry``（东财个股信息 → cninfo 降级，客户端内建），只写**非空**
  行业；写回经 ``update_security_master_industry``（幂等，空值不覆盖既有值）。
- 探针门禁：``--probe N`` 时先跑前 N 只并算成功率，低于 ``--min-success-rate``
  （默认 0.9）即整批中止（退出码 1、不写库）。
- 单只取数异常只计数（``failed``）不中断；``--dry-run`` 只取数不写库。
- ``--allow-host-write`` 显式绕过宿主护栏（仅限确知在操作回滚副本）。

退出码：0 正常；1 探针门禁未过；2 参数 / 标的池 / 环境护栏错误。
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path
from typing import Any

from finance_agent.data import hotdb_guard
from finance_agent.data.akshare_client import AKShareClient
from finance_agent.data.snapshot import (
    _default_db_path,
    _norm_code,
    get_security_master,
    update_security_master_industry,
)

logger = logging.getLogger(__name__)

_REPO_ROOT = Path(__file__).resolve().parents[1]


def _industry_of(info: Any) -> str | None:
    """``fetch_industry`` 返回 → 非空行业字符串；缺失/空 → None。"""
    industry = info.get("industry") if isinstance(info, dict) else None
    if isinstance(industry, str) and industry.strip():
        return industry.strip()
    return None


def _missing_industry_codes(db_path: str | Path | None) -> list[str]:
    """缺省回填池：证券主表中 ``industry_l1`` 为空的在册代码（升序）。"""
    return sorted(
        code
        for code, row in get_security_master(db_path=db_path).items()
        if not str(row.get("industry_l1") or "").strip()
    )


def run_backfill(
    *,
    symbols: list[str] | None = None,
    db_path: str | Path | None = None,
    dry_run: bool = False,
    sleep_seconds: float = 0.0,
    probe: int | None = None,
    min_success_rate: float = 0.9,
    client: Any = None,
) -> dict[str, Any]:
    """逐票回填 ``security_master.industry_l1``，返回摘要。

    摘要：``attempted`` / ``found``（取到非空行业数）/ ``failed``（取数异常数）/
    ``written``（写回行数）/ ``probe_success_rate``（未开探针 → None）/
    ``aborted``（探针门禁未过 → true，此时不写库）。
    """
    client = client or AKShareClient()
    if symbols is None:
        candidates = _missing_industry_codes(db_path)
    else:
        candidates = [code for raw in symbols if (code := _norm_code(raw))]

    mapping: dict[str, str] = {}
    attempted = 0
    failed = 0
    probe_rate: float | None = None
    aborted = False
    probe_at = min(probe, len(candidates)) if probe and probe > 0 else None
    for index, code in enumerate(candidates):
        if index and sleep_seconds > 0:
            time.sleep(sleep_seconds)
        attempted += 1
        try:
            info = client.fetch_industry(code) or {}
        except Exception as e:  # noqa: BLE001 - 单只失败不中断批量
            failed += 1
            logger.warning("行业回填取数失败(code=%s)：跳过: %s", code, e)
            info = {}
        industry = _industry_of(info)
        if industry:
            mapping[code] = industry
        if probe_at is not None and attempted == probe_at:
            probe_rate = len(mapping) / attempted
            if probe_rate < min_success_rate:
                aborted = True
                break

    if aborted:
        logger.error(
            "行业回填探针未过：成功率 %.1f%% < %.1f%%（%d/%d），整批中止、未写库",
            (probe_rate or 0.0) * 100,
            min_success_rate * 100,
            len(mapping),
            attempted,
        )
        return {
            "attempted": attempted,
            "found": len(mapping),
            "failed": failed,
            "written": 0,
            "probe_success_rate": probe_rate,
            "aborted": True,
        }

    written = 0 if dry_run else update_security_master_industry(mapping, db_path=db_path)
    return {
        "attempted": attempted,
        "found": len(mapping),
        "failed": failed,
        "written": written,
        "probe_success_rate": probe_rate,
        "aborted": False,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="行业门类历史回填（security_master.industry_l1；运维脚本）"
    )
    parser.add_argument("--symbols", default=None, help="逗号分隔代码列表；缺省取缺行业代码")
    parser.add_argument("--limit", type=int, default=None, help="最多处理 N 只（标的池截断）")
    parser.add_argument("--sleep", type=float, default=0.5, help="逐票取数间隔秒数（默认 0.5）")
    parser.add_argument("--probe", type=int, default=50, help="探针票数（默认 50；0 关闭门禁）")
    parser.add_argument(
        "--min-success-rate",
        type=float,
        default=0.9,
        help="探针成功率门禁（默认 0.9；低于即中止不写库）",
    )
    parser.add_argument("--db", default=None, help="快照库路径（缺省 SNAPSHOT_DB_PATH/默认）")
    parser.add_argument("--dry-run", action="store_true", help="只取数计数，不写库")
    parser.add_argument(
        "--allow-host-write",
        action="store_true",
        help="显式绕过宿主机直跑护栏（#515 迁卷后：确认在操作过期回滚副本时使用）",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    guard_reason = hotdb_guard.host_write_guard(
        db_path=args.db or _default_db_path(),
        allow_host_write=args.allow_host_write,
        dry_run=args.dry_run,
        repo_root=_REPO_ROOT,
        script_name="scripts/backfill_industry.py",
        argv=sys.argv[1:] if argv is None else list(argv),
    )
    if guard_reason:
        print(guard_reason, file=sys.stderr)
        return 2

    symbols: list[str] | None = None
    if args.symbols:
        symbols = [part.strip() for part in args.symbols.split(",") if part.strip()]
        if not symbols:
            print("错误：--symbols 解析后为空", file=sys.stderr)
            return 2
    else:
        master = get_security_master(db_path=args.db)
        if not master:
            print(
                "错误：证券主表为空（security_master 无记录）且未提供 --symbols；"
                "先跑一次 market_snapshot_daily 日批落主表，或显式指定标的。",
                file=sys.stderr,
            )
            return 2
        # 显式物化缺省池 → --limit 可截断（探针/小批试跑）
        symbols = _missing_industry_codes(args.db)
    if args.limit is not None:
        symbols = symbols[: max(args.limit, 0)]
        if not symbols:
            print("错误：标的池为空（--limit 截断后）", file=sys.stderr)
            return 2

    summary = run_backfill(
        symbols=symbols,
        db_path=args.db,
        dry_run=args.dry_run,
        sleep_seconds=args.sleep,
        probe=args.probe if args.probe > 0 else None,
        min_success_rate=args.min_success_rate,
    )
    rate = summary["probe_success_rate"]
    print(
        "行业回填完成: "
        f"attempted={summary['attempted']} found={summary['found']} "
        f"failed={summary['failed']} written={summary['written']}"
        f" probe_success_rate={'n/a' if rate is None else f'{rate:.1%}'}"
        + (" [dry-run]" if args.dry_run else "")
    )
    if summary["aborted"]:
        print("探针成功率未达门禁——整批中止、未写库（退出码 1）", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
