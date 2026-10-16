"""历史回填：market_snapshot 逐票双复权口径落库（add-market-snapshot T11，运维脚本）。

用法::

    uv run python scripts/backfill_market_snapshot.py \\
        --start 2024-01-01 --end 2025-12-31 --symbols 600519,000001 --sleep 0.5

- 缺省标的池 = 证券主表（``security_master``）全量；主表为空且未显式指定
  ``--symbols``/``--symbols-file`` → 报错并退 2（禁止静默空跑）。
- 逐票 ``fetch_symbol_history_raw_qfq``（raw+qfq 双口径 + 成交额/最高/最低），按
  ``--sleep`` 限速；断点续跑：该票在 ``[start, end]`` 内已有**字段齐备**
  （amount/high/low 非空）的行则跳过（缺任一列的既有行重取 upsert 补齐；
  extend-backfill-high-low 后旧六列产物自动重取）。
- 落库经 ``write_snapshot_rows_bulk``——一票一次事务整批 upsert（~190 日/票；
  逐日提交在 16 进程并行下提交放大严重，unify-quant-research-architecture P0-1
  运维修复）；进程中断在票间。
- ⚠ 回填行不含停牌合成/前收盘/状态位（``is_suspended`` 等显式为 0，
  ``prev_close``/涨跌幅为 NULL）：**不得**把回填区间接入
  ``compute_rebase_factors`` 链（停牌合成依赖状态链，会把缺行误判）；正式日频
  数据以 ``market_snapshot_daily`` 日批为准。
- ``--dry-run`` 只取数并计数、不写库；退出码 0（正常）/ 2（参数或标的池错误）。
"""

from __future__ import annotations

import argparse
import logging
import sqlite3
import sys
import time
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

import pandas as pd

from finance_agent.data.akshare_client import AKShareClient, _norm_date_iso
from finance_agent.data.snapshot import (
    SnapshotError,
    _connect,
    _default_db_path,
    _norm_code,
    _num,
    _parse_date_param,
    get_security_master,
    init_snapshot_db,
    write_snapshot_rows_bulk,
)

logger = logging.getLogger(__name__)

# 非 dry-run 起跑打印一次的警示（回填行不完整，不得入 rebase/停牌合成链）
_BACKFILL_CAVEAT = (
    "⚠ 回填行不含状态位/前收盘（is_suspended 等显式 0，prev_close/涨跌幅为 NULL）："
    "不得把回填区间接入 compute_rebase_factors 链（停牌合成需要状态链）；"
    "正式日频数据以 market_snapshot_daily 日批为准。"
)


def _split_csv(value: str) -> list[str]:
    """逗号分隔串 → 去空白后的非空片段列表（顺序保持）。"""
    return [part.strip() for part in value.split(",") if part.strip()]


def _dedupe(codes: Iterable[str]) -> list[str]:
    """代码归一（6 位）去重，保持首次出现顺序；空/缺失值跳过。"""
    seen: set[str] = set()
    out: list[str] = []
    for raw in codes:
        code = _norm_code(raw)
        if not code or code in seen:
            continue
        seen.add(code)
        out.append(code)
    return out


def _parse_symbols_file(path: Path) -> list[str]:
    """symbols-file → 代码列表：每行一个（行内逗号分隔容忍）；空行与 ``#`` 注释跳过。"""
    codes: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        text = line.strip()
        if not text or text.startswith("#"):
            continue
        codes.extend(_split_csv(text))
    return codes


def _history_rows(frame: pd.DataFrame, code: str, name: str) -> list[dict[str, Any]]:
    """T5 双口径帧 → market_snapshot 行（无 IO 的纯映射，字段逐字对齐表列）。

    状态位显式置 0、``prev_close``/``pct_chg``/``list_date``/``listed_days``
    置 NULL：回填不编造日批才有的信息（见模块 docstring 警示）。
    ``amount`` 由八列契约的「成交额」映射（unify-quant-research-architecture）；
    ``high``/``low`` 由「最高/最低」映射（raw 口径，extend-backfill-high-low——
    面板 high/low 由 raw×复权因子派生，与日批快照语义一致）；源缺列在取数层即
    判失败，不产半口径帧。日期不可解析的行丢弃（无法作主键）。
    """
    rows: list[dict[str, Any]] = []
    for _, raw in frame.iterrows():
        trade_date = _norm_date_iso(raw.get("日期"))
        if not trade_date:
            continue
        rows.append(
            {
                "trade_date": trade_date,
                "code": code,
                "name": name,
                "open": _num(raw.get("开盘")),
                "high": _num(raw.get("最高")),
                "low": _num(raw.get("最低")),
                "close_raw": _num(raw.get("收盘_raw")),
                "close_qfq": _num(raw.get("收盘_qfq")),
                "volume": _num(raw.get("成交量")),
                "amount": _num(raw.get("成交额")),
                "pct_chg": None,
                "prev_close": None,
                "is_st": 0,
                "is_suspended": 0,
                "limit_up_sealed": 0,
                "limit_down_sealed": 0,
                "listed_days": None,
                "list_date": None,
            }
        )
    return rows


def _has_rows_in_window(db_path: str | Path, code: str, start: str, end: str) -> bool:
    """该票在 ``[start, end]`` 内是否已有**字段齐备**的行（断点续跑判据）。

    齐备 = 有行且全部行 ``amount``/``high``/``low`` 非空（unify-quant-research-
    architecture + extend-backfill-high-low：缺任一列的既有行不构成覆盖 → 重取
    upsert 补齐；旧六列契约的回填产物由此自动重取）。库/表不存在 → False（按未
    回填处理，取数方向安全）；查询异常同样按 False。
    """
    path = Path(db_path)
    if not path.exists():
        return False
    try:
        conn = _connect(path)
    except sqlite3.Error:
        return False
    try:
        row = conn.execute(
            "SELECT COUNT(*), COUNT(amount), COUNT(high), COUNT(low) FROM market_snapshot "
            "WHERE code = ? AND trade_date >= ? AND trade_date <= ?",
            (code, start, end),
        ).fetchone()
    except sqlite3.Error:  # 空文件/异库无该表 → 视为无行
        return False
    finally:
        conn.close()
    total = int(row[0]) if row else 0
    with_amount = int(row[1]) if row else 0
    with_high = int(row[2]) if row else 0
    with_low = int(row[3]) if row else 0
    return total > 0 and with_amount == total and with_high == total and with_low == total


def run_backfill(
    *,
    start: str,
    end: str,
    symbols: list[str],
    client: AKShareClient | None = None,
    db_path: str | Path | None = None,
    dry_run: bool = False,
    sleep_seconds: float = 0.5,
    sleep_func: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    """跑一轮回填，返回摘要（``symbols_ok``/``symbols_failed``/``rows_written``/
    ``skipped_resumed`` + 区间与 dry_run 标记）。

    限速只在两次真实取数之间 sleep（跳过票不空等）；``--dry-run`` 不建库不写行，
    ``rows_written`` 为「本应写入」行数。
    """
    start = _parse_date_param(start, param="start").isoformat()
    end = _parse_date_param(end, param="end").isoformat()
    if start > end:
        raise SnapshotError(f"start={start} 晚于 end={end}")
    client = client or AKShareClient()
    if dry_run:
        resolved_db: Path = Path(db_path) if db_path else _default_db_path()
    else:
        resolved_db = init_snapshot_db(db_path)

    if not dry_run:
        print(_BACKFILL_CAVEAT)
    print(
        f"回填区间 {start} ~ {end}；标的 {len(symbols)} 只；库: {resolved_db}"
        + ("（dry-run 只取数不写库）" if dry_run else "")
    )
    master = get_security_master(db_path=resolved_db) if Path(resolved_db).exists() else {}

    start_compact = start.replace("-", "")
    end_compact = end.replace("-", "")
    summary: dict[str, Any] = {
        "start": start,
        "end": end,
        "symbols_total": len(symbols),
        "symbols_ok": 0,
        "symbols_failed": 0,
        "rows_written": 0,
        "skipped_resumed": 0,
        "dry_run": dry_run,
    }
    fetched_any = False
    total = len(symbols)
    for index, code in enumerate(symbols, start=1):
        if _has_rows_in_window(resolved_db, code, start, end):
            summary["skipped_resumed"] += 1
            print(f"[{index}/{total}] {code}: 跳过（区间内已有行，断点续跑）")
            continue
        if fetched_any and sleep_seconds > 0:  # 只在两次真实取数之间限速
            sleep_func(sleep_seconds)
        fetched_any = True
        frame = client.fetch_symbol_history_raw_qfq(code, start_compact, end_compact)
        if frame is None:
            summary["symbols_failed"] += 1
            logger.warning("回填取数失败（None）: %s [%s..%s]", code, start, end)
            print(f"[{index}/{total}] {code}: 失败（取数返回 None）")
            continue
        name = str((master.get(code) or {}).get("name") or "")
        rows = _history_rows(frame, code, name)
        if not rows:
            summary["symbols_failed"] += 1
            logger.warning("回填无可用行（日期不可解析/空帧）: %s", code)
            print(f"[{index}/{total}] {code}: 失败（无可用行）")
            continue

        # 每票单事务整批 upsert（提交放大治理；行内 trade_date 逐行取用）
        written = len(rows) if dry_run else write_snapshot_rows_bulk(rows, db_path=resolved_db)
        summary["rows_written"] += written
        summary["symbols_ok"] += 1
        flag = "[dry-run] 可取" if dry_run else "写入"
        print(f"[{index}/{total}] {code}: {flag} {written} 行")

    return summary


def _resolve_symbols(args: argparse.Namespace) -> list[str]:
    """标的池：``--symbols``/``--symbols-file``（可同给，合并去重）缺省取主表全量。"""
    codes: list[str] = []
    if args.symbols:
        codes.extend(_split_csv(args.symbols))
    if args.symbols_file:
        codes.extend(_parse_symbols_file(Path(args.symbols_file)))
    if codes:
        return _dedupe(codes)
    return sorted(get_security_master(db_path=args.db))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="历史回填 market_snapshot（双复权口径，限速 + 断点续跑；运维脚本）"
    )
    parser.add_argument("--start", required=True, help="起始日期 YYYY-MM-DD（含）")
    parser.add_argument("--end", required=True, help="结束日期 YYYY-MM-DD（含）")
    parser.add_argument("--symbols", default=None, help="逗号分隔代码列表；缺省取证券主表全量")
    parser.add_argument(
        "--symbols-file", default=None, help="每行一个代码的文件（# 注释/空行跳过）"
    )
    parser.add_argument("--sleep", type=float, default=0.5, help="逐票取数间隔秒数（默认 0.5）")
    parser.add_argument("--limit", type=int, default=None, help="最多处理 N 只（标的池截断）")
    parser.add_argument("--db", default=None, help="快照库路径（缺省 SNAPSHOT_DB_PATH/默认）")
    parser.add_argument("--dry-run", action="store_true", help="只取数计数，不写库")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    try:
        start = _parse_date_param(args.start, param="--start").isoformat()
        end = _parse_date_param(args.end, param="--end").isoformat()
    except SnapshotError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 2
    if start > end:
        print(f"错误：--start({start}) 晚于 --end({end})", file=sys.stderr)
        return 2

    try:
        symbols = _resolve_symbols(args)
    except OSError as exc:  # symbols-file 不可读
        print(f"错误：--symbols-file 读取失败：{exc}", file=sys.stderr)
        return 2
    if args.limit is not None:
        symbols = symbols[: max(args.limit, 0)]

    if not symbols:
        if args.symbols or args.symbols_file:
            print(
                "错误：--symbols/--symbols-file 解析后标的为空（检查文件内容与注释格式）。",
                file=sys.stderr,
            )
        else:
            print(
                "错误：证券主表为空（security_master 无记录）且未提供 --symbols/--symbols-file；"
                "先跑一次 market_snapshot_daily 日批落主表，或显式指定标的。",
                file=sys.stderr,
            )
        return 2

    summary = run_backfill(
        start=start,
        end=end,
        symbols=symbols,
        db_path=args.db,
        dry_run=args.dry_run,
        sleep_seconds=args.sleep,
    )
    print(
        "回填完成: "
        f"symbols_ok={summary['symbols_ok']} symbols_failed={summary['symbols_failed']} "
        f"rows_written={summary['rows_written']} skipped_resumed={summary['skipped_resumed']}"
        + (" [dry-run]" if args.dry_run else "")
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
