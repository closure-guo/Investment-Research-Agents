"""退市股历史补采（delta add-microcap-lane-guardrails T4，运维脚本）。

**薄封装**：复用 ``scripts/backfill_market_snapshot.py`` 的内部件——取数包装
``AKShareClient.fetch_symbol_history_raw_qfq``、行映射 ``_history_rows``、落库
``write_snapshot_rows_bulk``（每票一次事务）、断点续跑判据 ``_has_rows_in_window``、
宿主机直跑护栏 ``_host_write_guard``、收尾完整性自检 ``verify_snapshot_integrity``。

口径（docs/evals/metrics.md §1.16，binding）：

- 标的池 = ``security_master`` 中 ``delist_date`` 非空（退市清单落库产物；
  随快照日批主表刷新写入）；
- 逐票窗口 = ``list_date..delist_date``（含两端，取自各自主表行）；
  ``list_date`` 缺失/不可解析 → **窗口不可得**：不取数、进摘要 ``window_missing``
  明细（如实标注，MUST NOT 用猜测起点取数）；
- 源无历史可得（取数返回 None / 空帧 / 日期全不可解析）→ 计数 + 代码明细
  ``history_missing``（如实标注，不伪造行）；
- 回填行不含状态位/前收盘/涨跌幅（同既有回填通道），**不得**接入
  ``compute_rebase_factors``/停牌合成链；正式日频数据以 ``market_snapshot_daily`` 为准。

用法::

    uv run python scripts/backfill_delisted.py --db data/market_snapshot.db --dry-run
    uv run python scripts/backfill_delisted.py --limit 20 --sleep 0.5

宿主机直跑护栏沿用（issue #344：跨 bind mount WAL 争用）：非容器内 + 库落在主检出
``data/`` + backend 容器在跑 → 拒绝并退 2（``--dry-run`` 零写入豁免，
``--allow-host-write`` 显式绕过）。退出码 0（正常）/ 1（收尾完整性自检未通过）/
2（环境护栏或主表无可处理标的）。
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

# 复用既有回填脚本的内部件（唯一实现源，不复制取数/落库/护栏逻辑）。
# 两种调用形态都要成立：包形态（tests 走 ``scripts.backfill_delisted``，仓库根在
# sys.path）与直接路径运行（``python scripts/backfill_delisted.py``——此时
# ``scripts/`` 无 __init__.py，脚本目录才是 sys.path 首位）。
try:  # pragma: no cover - 分支由调用形态决定
    from scripts import backfill_market_snapshot as backfill_module
except ModuleNotFoundError:  # pragma: no cover
    import backfill_market_snapshot as backfill_module

from finance_agent.data.akshare_client import AKShareClient
from finance_agent.data.snapshot import (
    SnapshotError,
    _default_db_path,
    _parse_date_param,
    get_security_master,
    init_snapshot_db,
    write_snapshot_rows_bulk,
)

# 既有内部件的模块级引用（测试经本模块名 monkeypatch 桩替换）
_BACKFILL_CAVEAT = backfill_module._BACKFILL_CAVEAT
_has_rows_in_window = backfill_module._has_rows_in_window
_history_rows = backfill_module._history_rows
_host_write_guard = backfill_module._host_write_guard
verify_snapshot_integrity = backfill_module.verify_snapshot_integrity

logger = logging.getLogger(__name__)

# 本脚本自身的相对路径（护栏提示命令指向本脚本，不误报既有全量回填脚本）
_SCRIPT_PATH = "scripts/backfill_delisted.py"


def _delisted_windows(
    master: dict[str, dict],
) -> tuple[list[tuple[str, str, str]], list[str]]:
    """主表 → 处理清单 ``[(code, start, end)]``（code 升序）与窗口不可得代码明细。

    只取 ``delist_date`` 非空行；``list_date`` 缺失/不可解析或窗口日期非规范 →
    进 ``window_missing``（不猜起点，如实标注）。``start > end`` 的坏窗口同样进明细。
    """
    windows: list[tuple[str, str, str]] = []
    window_missing: list[str] = []
    for code in sorted(master):
        row = master.get(code) or {}
        delist_raw = str(row.get("delist_date") or "").strip()
        if not delist_raw:
            continue
        list_raw = str(row.get("list_date") or "").strip()
        if not list_raw:
            window_missing.append(code)
            continue
        try:
            start = _parse_date_param(list_raw, param="list_date").isoformat()
            end = _parse_date_param(delist_raw, param="delist_date").isoformat()
        except SnapshotError:
            window_missing.append(code)
            continue
        if start > end:
            window_missing.append(code)
            continue
        windows.append((code, start, end))
    return windows, window_missing


def run_backfill_delisted(
    *,
    client: AKShareClient | None = None,
    db_path: str | Path | None = None,
    dry_run: bool = False,
    limit: int | None = None,
    sleep_seconds: float = 0.5,
    sleep_func: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    """跑一轮退市股补采，返回摘要（计数 + ``history_missing``/``window_missing`` 明细）。

    限速只在两次真实取数之间 sleep（跳过票不空等）；``--dry-run`` 不写行
    （``rows_written`` 为「本应写入」行数），也不做收尾自检。``limit`` 只截断
    可处理清单（``list_date..delist_date`` 可得者）；``window_missing`` 恒为全量明细
    （审计用，不受 ``limit`` 影响）。
    """
    client = client or AKShareClient()
    resolved_db: Path = Path(db_path) if db_path else _default_db_path()
    if not dry_run:
        resolved_db = init_snapshot_db(db_path)
    master = get_security_master(db_path=resolved_db) if resolved_db.exists() else {}
    windows, window_missing = _delisted_windows(master)
    if limit is not None:
        windows = windows[: max(limit, 0)]

    if not dry_run:
        print(_BACKFILL_CAVEAT)
    print(
        f"退市股历史补采：标的 {len(windows)} 只；库: {resolved_db}"
        + ("（dry-run 只取数不写库）" if dry_run else "")
    )
    if window_missing:
        print(
            "⚠ 窗口不可得（list_date 缺失/不可解析，未取数，如实标注）: "
            + ", ".join(window_missing)
        )

    summary: dict[str, Any] = {
        "symbols_total": len(windows),
        "symbols_ok": 0,
        "rows_written": 0,
        "skipped_resumed": 0,
        "history_missing": [],
        "history_missing_count": 0,
        "window_missing": window_missing,
        "dry_run": dry_run,
    }
    fetched_any = False
    total = len(windows)
    for index, (code, start, end) in enumerate(windows, start=1):
        if _has_rows_in_window(resolved_db, code, start, end):
            summary["skipped_resumed"] += 1
            print(f"[{index}/{total}] {code}: 跳过（窗口内已有齐备行，断点续跑）")
            continue
        if fetched_any and sleep_seconds > 0:  # 只在两次真实取数之间限速
            sleep_func(sleep_seconds)
        fetched_any = True
        frame = client.fetch_symbol_history_raw_qfq(
            code, start.replace("-", ""), end.replace("-", "")
        )
        if frame is None or getattr(frame, "empty", True):
            summary["history_missing"].append(code)
            summary["history_missing_count"] += 1
            logger.warning("退市股无历史可得（源无数据）: %s [%s..%s]", code, start, end)
            print(f"[{index}/{total}] {code}: 无历史可得（源无数据，如实标注）")
            continue
        name = str((master.get(code) or {}).get("name") or "")
        rows = _history_rows(frame, code, name)
        if not rows:
            summary["history_missing"].append(code)
            summary["history_missing_count"] += 1
            logger.warning("退市股无可用行（日期不可解析/空帧）: %s", code)
            print(f"[{index}/{total}] {code}: 无历史可得（日期不可解析/空帧，如实标注）")
            continue
        written = len(rows) if dry_run else write_snapshot_rows_bulk(rows, db_path=resolved_db)
        summary["rows_written"] += written
        summary["symbols_ok"] += 1
        flag = "[dry-run] 可取" if dry_run else "写入"
        print(f"[{index}/{total}] {code}: {flag} {written} 行（窗口 {start}..{end}）")

    if not dry_run:
        integrity = verify_snapshot_integrity(resolved_db)
        summary["integrity"] = integrity
        if integrity["ok"]:
            print(
                f"完整性自检通过: quick_check={integrity['quick_check']}"
                f" · duplicate_keys={integrity['duplicate_keys']}"
            )
        else:
            print(
                f"✗ 完整性自检未通过: quick_check={integrity['quick_check']}"
                f" · duplicate_keys={integrity['duplicate_keys']}；请人工排查（incident 043 族）"
            )
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="退市股历史补采（security_master delist_date 窗口；复用既有回填通道）"
    )
    parser.add_argument("--limit", type=int, default=None, help="最多处理 N 只退市标的")
    parser.add_argument("--sleep", type=float, default=0.5, help="逐票取数间隔秒数（默认 0.5）")
    parser.add_argument("--db", default=None, help="快照库路径（缺省 SNAPSHOT_DB_PATH/默认）")
    parser.add_argument("--dry-run", action="store_true", help="只取数计数，不写库")
    parser.add_argument(
        "--allow-host-write",
        action="store_true",
        help="显式绕过宿主机直跑护栏（仅限确认无容器读方的时段；见 issue #344）",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    # 宿主机直跑护栏（issue #344）：先于一切库操作（含只读打开）
    guard_reason = _host_write_guard(
        db_path=args.db,
        allow_host_write=args.allow_host_write,
        dry_run=args.dry_run,
        argv=sys.argv[1:] if argv is None else list(argv),
        script_name=_SCRIPT_PATH,
    )
    if guard_reason:
        print(guard_reason, file=sys.stderr)
        return 2

    master = get_security_master(db_path=args.db)
    windows, _ = _delisted_windows(master)
    if not windows:
        print(
            "错误：证券主表无带退市日（delist_date）的标的——退市清单尚未落库？"
            "先跑一次行情快照日批刷新主表（退市清单随主表刷新写入），"
            "或核对 --db 指向的库。",
            file=sys.stderr,
        )
        return 2

    summary = run_backfill_delisted(
        db_path=args.db,
        dry_run=args.dry_run,
        limit=args.limit,
        sleep_seconds=args.sleep,
    )
    print(
        "退市股补采完成: "
        f"symbols_ok={summary['symbols_ok']} rows_written={summary['rows_written']} "
        f"skipped_resumed={summary['skipped_resumed']} "
        f"history_missing={summary['history_missing_count']} "
        f"window_missing={len(summary['window_missing'])}" + (" [dry-run]" if args.dry_run else "")
    )
    integrity = summary.get("integrity")
    if integrity is not None and not integrity["ok"]:
        print("补采收尾完整性自检未通过——退出码 1（fail loud）", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
