"""参考数据历史回填（extend-factor-library-coverage §9，运维脚本）。

**推荐以容器内执行**（热库在 named volume；宿主机直跑会被 ``hotdb_guard`` 拒绝，
``data/`` 只是回滚副本——见 incident 043 同族风险）::

    docker compose exec -T backend python scripts/backfill_factor_ref.py \\
        --start 2024-01-01 --end 2026-11-27

- 源：新浪不复权日线（``stock_zh_a_daily``，``adjust=""``）的 ``outstanding_share``
  与 ``turnover``；``float_cap = outstanding_share × 当日 market_snapshot.close_raw``
  （当日无快照行 → 跳过该日，不落半行），``turnover`` 为源值小数口径；
  ``source="sina_history"`` 留痕。快照无该票任何行的标的计 ``symbols_failed``
  且不发起取数。
- 只写 ``market_ref_daily``（幂等 upsert），**MUST NOT 修改 market_snapshot 行**。
- 源失败/空/缺 ``outstanding_share``/``turnover`` 列 → 该票计入 ``symbols_failed``，
  不落 NULL/0 值半口径行；逐行无效值跳过。
- 缺省标的池 = 证券主表（``security_master``）全量；主表为空且未显式指定
  ``--symbols`` → 报错退 2（禁止静默空跑）。
- ``--dry-run`` 只取数计数、不写库；``--allow-host-write`` 显式绕过宿主护栏
  （仅限确知在操作回滚副本）。

退出码：0 正常；2 参数 / 标的池 / 环境护栏错误。
"""

from __future__ import annotations

import argparse
import logging
import math
import sqlite3
import sys
import time
from pathlib import Path
from typing import Any

from finance_agent.data import hotdb_guard
from finance_agent.data.akshare_client import AKShareClient, _norm_date_iso
from finance_agent.data.snapshot import (
    _connect,
    _default_db_path,
    _norm_code,
    get_security_master,
    upsert_market_ref_rows,
)

logger = logging.getLogger(__name__)

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SOURCE = "sina_history"


def _finite(value: Any) -> float | None:
    """数值强转；None/NaN/inf/不可解析 → None（不编造数值）。"""
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _split_csv(value: str) -> list[str]:
    return [part.strip() for part in value.split(",") if part.strip()]


def _snapshot_closes(
    code: str, start: str, end: str, db_path: str | Path | None
) -> dict[str, float]:
    """该票在 ``[start, end]`` 的快照不复权收盘价 ``{trade_date: close_raw}``。

    快照库无表（尚未初始化）→ 空 dict（调用方按无覆盖处置，不抛）。
    """
    try:
        conn = _connect(db_path or _default_db_path())
    except sqlite3.Error as e:
        logger.warning("参考数据回填：快照库不可打开，跳过全场: %s", e)
        return {}
    try:
        rows = conn.execute(
            "SELECT trade_date, close_raw FROM market_snapshot "
            "WHERE code = ? AND trade_date >= ? AND trade_date <= ?",
            (code, start, end),
        ).fetchall()
    except sqlite3.Error as e:
        logger.warning("参考数据回填：快照查询失败（可能未建表），跳过全场: %s", e)
        return {}
    finally:
        conn.close()
    closes: dict[str, float] = {}
    for row in rows:
        day = str(row["trade_date"])
        close = _finite(row["close_raw"])
        if close is not None and close > 0:
            closes[day] = close
    return closes


def _existing_ref_dates(code: str, start: str, end: str, db_path: str | Path | None) -> set[str]:
    """该票在区间内已完整回填（``float_cap`` 与 ``turnover`` 均非空）的日期集合。

    断点续跑判据：需覆盖快照区间内全部日期才跳过该票；侧表不存在 → 空集。
    """
    try:
        conn = _connect(db_path or _default_db_path())
    except sqlite3.Error:
        return set()
    try:
        rows = conn.execute(
            "SELECT trade_date FROM market_ref_daily "
            "WHERE code = ? AND trade_date >= ? AND trade_date <= ? "
            "AND float_cap IS NOT NULL AND turnover IS NOT NULL",
            (code, start, end),
        ).fetchall()
    except sqlite3.Error:
        return set()
    finally:
        conn.close()
    return {str(row["trade_date"]) for row in rows}


def run_backfill(
    *,
    start: str,
    end: str,
    symbols: list[str] | None = None,
    db_path: str | Path | None = None,
    dry_run: bool = False,
    sleep_seconds: float = 0.0,
    client: Any = None,
) -> dict[str, Any]:
    """逐票回填 ``market_ref_daily``，返回摘要（口径见模块 docstring）。

    摘要：``start``/``end``（请求区间）/ ``covered_start``/``covered_end``（实际
    写入区间，无行为 None）/ ``symbols_ok`` / ``symbols_failed``（源失败、缺列或
    快照无覆盖）/ ``skipped_resumed``（断点续跑跳过票数）/ ``rows_written`` /
    ``skipped_rows``（无快照行或行内无效值跳过数）。
    """
    client = client or AKShareClient()
    if symbols is None:
        symbols = sorted(get_security_master(db_path=db_path))

    rows_written = 0
    symbols_ok = 0
    symbols_failed = 0
    skipped_resumed = 0
    skipped_rows = 0
    covered_start: str | None = None
    covered_end: str | None = None
    for index, raw_code in enumerate(symbols):
        if index and sleep_seconds > 0:
            time.sleep(sleep_seconds)
        code = _norm_code(raw_code)
        if not code:
            symbols_failed += 1
            continue
        closes = _snapshot_closes(code, start, end, db_path)
        if not closes:
            # 无当日不复权收盘价可乘（快照未覆盖该票）→ 不取数、不落任何行
            logger.warning("参考数据回填：快照无覆盖，跳过 %s", code)
            symbols_failed += 1
            continue
        if set(closes) <= _existing_ref_dates(code, start, end, db_path):
            skipped_resumed += 1
            continue
        frame = client.fetch_symbol_share_history(code, start, end)
        if frame is None:
            symbols_failed += 1
            continue
        rows: list[dict[str, Any]] = []
        for _, row in frame.iterrows():
            day = _norm_date_iso(row.get("date"))
            close = closes.get(day) if day else None
            shares = _finite(row.get("outstanding_share"))
            turnover = _finite(row.get("turnover"))
            if not day or close is None or shares is None or turnover is None:
                skipped_rows += 1
                continue
            if shares <= 0 or turnover < 0:
                skipped_rows += 1
                continue
            rows.append(
                {
                    "trade_date": day,
                    "code": code,
                    "float_cap": shares * close,
                    "turnover": turnover,
                    "source": _SOURCE,
                }
            )
            covered_start = day if covered_start is None or day < covered_start else covered_start
            covered_end = day if covered_end is None or day > covered_end else covered_end
        if not rows:
            symbols_failed += 1
            continue
        symbols_ok += 1
        if not dry_run:
            rows_written += upsert_market_ref_rows(rows, db_path=db_path)

    return {
        "start": start,
        "end": end,
        "covered_start": covered_start,
        "covered_end": covered_end,
        "symbols_ok": symbols_ok,
        "symbols_failed": symbols_failed,
        "skipped_resumed": skipped_resumed,
        "rows_written": rows_written,
        "skipped_rows": skipped_rows,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="参考数据历史回填（新浪流通股本/换手率 → market_ref_daily；运维脚本）"
    )
    parser.add_argument("--start", required=True, help="起始日期 YYYY-MM-DD（含）")
    parser.add_argument("--end", required=True, help="结束日期 YYYY-MM-DD（含）")
    parser.add_argument("--symbols", default=None, help="逗号分隔代码列表；缺省取证券主表全量")
    parser.add_argument("--sleep", type=float, default=0.5, help="逐票取数间隔秒数（默认 0.5）")
    parser.add_argument("--limit", type=int, default=None, help="最多处理 N 只（标的池截断）")
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
        script_name="scripts/backfill_factor_ref.py",
        argv=sys.argv[1:] if argv is None else list(argv),
    )
    if guard_reason:
        print(guard_reason, file=sys.stderr)
        return 2

    symbols: list[str] | None = None
    if args.symbols:
        symbols = _split_csv(args.symbols)
        if not symbols:
            print("错误：--symbols 解析后为空", file=sys.stderr)
            return 2
    else:
        symbols = sorted(get_security_master(db_path=args.db))
        if not symbols:
            print(
                "错误：证券主表为空（security_master 无记录）且未提供 --symbols；"
                "先跑一次 market_snapshot_daily 日批落主表，或显式指定标的。",
                file=sys.stderr,
            )
            return 2
    if args.limit is not None:
        symbols = symbols[: max(args.limit, 0)]
    if not symbols:
        print("错误：标的池为空（--limit 截断后）", file=sys.stderr)
        return 2

    summary = run_backfill(
        start=args.start,
        end=args.end,
        symbols=symbols,
        db_path=args.db,
        dry_run=args.dry_run,
        sleep_seconds=args.sleep,
    )
    print(
        "回填完成: "
        f"symbols_ok={summary['symbols_ok']} symbols_failed={summary['symbols_failed']} "
        f"skipped_resumed={summary['skipped_resumed']} "
        f"rows_written={summary['rows_written']} skipped_rows={summary['skipped_rows']} "
        f"covered={summary['covered_start']}..{summary['covered_end']}"
        + (" [dry-run]" if args.dry_run else "")
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
