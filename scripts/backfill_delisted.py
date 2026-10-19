"""退市股历史补采（delta add-microcap-lane-guardrails T4，运维脚本）。

**薄封装**：复用 ``scripts/backfill_market_snapshot.py`` 的内部件——取数包装
``AKShareClient.fetch_symbol_history_raw_qfq``、行映射 ``_history_rows``、落库
``write_snapshot_rows_bulk``（每票一次事务）、宿主机直跑护栏 ``_host_write_guard``、
收尾完整性自检 ``verify_snapshot_integrity``。

口径（docs/evals/metrics.md §1.16，binding）：

- 标的池 = ``security_master`` 中 ``delist_date`` 非空（退市清单落库产物；
  随快照日批主表刷新写入）；
- 逐票窗口 = ``list_date..delist_date``（含两端，取自各自主表行）；
  ``list_date`` 缺失/不可解析 → **窗口不可得**：不取数、进摘要 ``window_missing``
  明细（如实标注，MUST NOT 用猜测起点取数）；
- 源无历史可得（取数返回 None / 空帧 / 日期全不可解析）→ 计数 + 代码明细
  ``history_missing``（如实标注，不伪造行）；
- **断点续跑 = 整窗覆盖判据**（不只是「窗口内有行」）：窗口内有行、字段
  （amount/high/low）齐备、且**最早行触及窗口起点锚**（日历中首个 ≥ ``list_date``
  的交易日；无日历则 ``list_date`` 自身）才跳过。只判头部——退市股末日行情可能早于
  ``delist_date``（退市整理期结束日 ≠ 退市日），中间停牌日本无行；仅尾部有行
  （如运行期日批行）→ 判为**部分覆盖**，进摘要 ``partial_overlap`` 并重取
  （``write_snapshot_rows_bulk`` 为 INSERT OR REPLACE，幂等）；
- 缺失明细（``history_missing``/``window_missing``）随非 dry-run 运行落
  ``snapshot_meta["delist_backfill_missing"]``（JSON，持久留痕供运维与后续变更读取）；
- **日批行覆盖守卫**（issue #404）：写前查窗口内 ``prev_close`` 非空的日批行，这些
  日期**跳过不覆盖**（保留日批状态位/前收盘）——``write_snapshot_rows_bulk`` 为
  INSERT OR REPLACE，不设守卫会把日批行冲成回填形状；命中明细进摘要
  ``daily_overlap`` 与 ``snapshot_meta``；
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
import json
import logging
import sqlite3
import sys
import time
from collections.abc import Callable
from datetime import date, datetime
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
    _connect,
    _default_db_path,
    _parse_date_param,
    get_all_meta,
    get_security_master,
    init_snapshot_db,
    set_meta,
    write_snapshot_rows_bulk,
)

# 既有内部件的模块级引用（测试经本模块名 monkeypatch 桩替换）
_BACKFILL_CAVEAT = backfill_module._BACKFILL_CAVEAT
_history_rows = backfill_module._history_rows
_host_write_guard = backfill_module._host_write_guard
verify_snapshot_integrity = backfill_module.verify_snapshot_integrity

logger = logging.getLogger(__name__)

# 本脚本自身的相对路径（护栏提示命令指向本脚本，不误报既有全量回填脚本）
_SCRIPT_PATH = "scripts/backfill_delisted.py"

# 缺失/覆盖明细的 snapshot_meta 键（持久留痕；规格：key 名固定供运维既有巡检复用）
_MISSING_META_KEY = "delist_backfill_missing"


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


def _snapshot_calendar(db_path: str | Path) -> list[str]:
    """快照 meta ``trade_calendar`` → 规范交易日升序列表；缺失/坏值 → ``[]``。

    与 ``screening.pipeline`` 的快照日历解析同纪律（非规范形态跳过，不参与比较）；
    日历不可得 → 覆盖锚点退化（见 ``_first_trading_day_on_or_after``）。
    """
    raw = get_all_meta(db_path).get("trade_calendar")
    if not raw:
        return []
    try:
        decoded = json.loads(raw)
    except ValueError:
        return []
    if not isinstance(decoded, list):
        return []
    days: list[str] = []
    for entry in decoded:
        text = str(entry)
        try:
            parsed = date.fromisoformat(text)
        except ValueError:
            continue
        if parsed.isoformat() == text:
            days.append(text)
    return sorted(set(days))


def _first_trading_day_on_or_after(calendar: list[str], start: str) -> str:
    """日历中 ``>= start`` 的首个交易日；日历不含该区间 → 退化为 ``start`` 自身。

    覆盖锚点：退市股首个行情行应落在其上市后的首个交易日（通常即 ``list_date``，
    为非交易日时顺延）。无日历时用 ``start`` 自身（保守：要求窗口头有行）。
    """
    for day in calendar:
        if day >= start:
            return day
    return start


def _window_coverage(db_path: str | Path, code: str, start: str, end: str) -> dict[str, Any] | None:
    """窗口内该票行覆盖概况：``{"rows","min","max","complete"}``；无行/库不可读 → None。

    ``complete`` = 全部行 ``amount``/``high``/``low`` 非空（与既有断点续跑判据
    ``_has_rows_in_window`` 的字段齐备口径一致：缺列行不构成覆盖，须重取 upsert 补齐）。
    """
    path = Path(db_path)
    if not path.exists():
        return None
    try:
        conn = _connect(path)
    except sqlite3.Error:
        return None
    try:
        row = conn.execute(
            "SELECT COUNT(*), MIN(trade_date), MAX(trade_date), "
            "COUNT(amount), COUNT(high), COUNT(low) FROM market_snapshot "
            "WHERE code = ? AND trade_date >= ? AND trade_date <= ?",
            (code, start, end),
        ).fetchone()
    except sqlite3.Error:  # 空文件/异库无该表 → 视为无行
        return None
    finally:
        conn.close()
    total = int(row[0] or 0) if row else 0
    if total == 0:
        return None
    return {
        "rows": total,
        "min": str(row[1]),
        "max": str(row[2]),
        "complete": int(row[3]) == total and int(row[4]) == total and int(row[5]) == total,
    }


def _window_is_fully_covered(coverage: dict[str, Any] | None, *, anchor: str) -> bool:
    """整窗覆盖判据（断点续跑）：窗口内有行、字段齐备、且**最早行触及起点锚**。

    - 只判**头部**：退市股末日行情可能早于 ``delist_date``（退市整理期结束日 ≠ 退市日），
      尾部不判；中间停牌日本无行，不做逐日缺口判定（否则停牌票永远重取）；
    - 仅 1 行日批行（落在窗口尾部）→ 头部未触及 → **不算覆盖**，必须重取
      （评审 F1：旧判据「窗口内有行且字段齐备」会让整票被跳过、谎报覆盖）。
    """
    if coverage is None or not coverage["complete"]:
        return False
    return str(coverage["min"]) <= anchor


def _daily_overlap_dates(db_path: str | Path, code: str, start: str, end: str) -> list[str]:
    """窗口内已有**日批行**（``prev_close`` 非空）的交易日，升序（#404 覆盖守卫）。

    ``prev_close`` 是日批链独有的字段：回填行显式置 NULL（见 ``_history_rows``），
    故非空即「该日已有正式日频数据」。库/表不可读 → ``[]``（按无日批行处理，取数
    方向安全；与其它覆盖查询同纪律）。
    """
    path = Path(db_path)
    if not path.exists():
        return []
    try:
        conn = _connect(path)
    except sqlite3.Error:
        return []
    try:
        rows = conn.execute(
            "SELECT trade_date FROM market_snapshot "
            "WHERE code = ? AND trade_date >= ? AND trade_date <= ? "
            "AND prev_close IS NOT NULL ORDER BY trade_date",
            (code, start, end),
        ).fetchall()
    except sqlite3.Error:  # 空文件/异库无该表 → 视为无日批行
        return []
    finally:
        conn.close()
    return [str(row[0]) for row in rows]


def _persist_missing_summary(db_path: str | Path, summary: dict[str, Any]) -> None:
    """缺失/覆盖明细落 ``snapshot_meta["delist_backfill_missing"]``（持久留痕）。

    stdout 摘要照旧；本键供运维巡检与后续变更读取（同键 upsert，重跑覆盖为最新）。
    落库失败只 WARN——留痕不构成主流程失败（数据/汇总已由 stdout 与摘要给出）。
    """
    payload = {
        "history_missing": list(summary["history_missing"]),
        "history_missing_count": summary["history_missing_count"],
        "window_missing": list(summary["window_missing"]),
        "partial_overlap": list(summary["partial_overlap"]),
        "daily_overlap": list(summary["daily_overlap"]),
        "symbols_daily_overlap": summary["symbols_daily_overlap"],
        "skipped_resumed": summary["skipped_resumed"],
        "updated_at": datetime.now().isoformat(),
    }
    try:
        set_meta(
            _MISSING_META_KEY,
            json.dumps(payload, ensure_ascii=False, sort_keys=True),
            db_path=db_path,
        )
    except Exception as e:  # noqa: BLE001 - 留痕失败不掩盖主流程结果
        logger.warning("缺失明细留痕落库失败（%s）: %s", _MISSING_META_KEY, e)


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
        # 部分覆盖（窗口内有行但未触及窗口头）→ 已重取（INSERT OR REPLACE 幂等）；
        # 与 ``skipped_resumed``（整窗覆盖）如实区分，不谎报覆盖
        "partial_overlap": [],
        # 日批行覆盖守卫（#404）：窗口内已有日批行（prev_close 非空）的日期被跳过，
        # 明细 = [{"code","dates","count"}]；symbols_daily_overlap = 命中票数
        "daily_overlap": [],
        "symbols_daily_overlap": 0,
        "dry_run": dry_run,
    }
    calendar = _snapshot_calendar(resolved_db)
    fetched_any = False
    total = len(windows)
    for index, (code, start, end) in enumerate(windows, start=1):
        coverage = _window_coverage(resolved_db, code, start, end)
        anchor = _first_trading_day_on_or_after(calendar, start)
        if _window_is_fully_covered(coverage, anchor=anchor):
            summary["skipped_resumed"] += 1
            print(
                f"[{index}/{total}] {code}: 跳过（整窗已覆盖，断点续跑；"
                f"{coverage['rows']} 行 {coverage['min']}..{coverage['max']}）"
            )
            continue
        if coverage is not None:
            summary["partial_overlap"].append(code)
            print(
                f"[{index}/{total}] {code}: 部分覆盖（{coverage['rows']} 行，"
                f"最早 {coverage['min']} 未触及 {anchor}）→ 重取"
            )
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
        # #404 日批行覆盖守卫：已有日批行的日期不覆盖（回填行会冲掉状态位/前收盘）
        overlap_dates = _daily_overlap_dates(resolved_db, code, start, end)
        if overlap_dates:
            overlap_set = set(overlap_dates)
            rows = [row for row in rows if row.get("trade_date") not in overlap_set]
            summary["daily_overlap"].append(
                {"code": code, "dates": overlap_dates, "count": len(overlap_dates)}
            )
            summary["symbols_daily_overlap"] += 1
            print(
                f"[{index}/{total}] {code}: 保留日批行 {len(overlap_dates)} 日"
                f"（{overlap_dates[0]}..{overlap_dates[-1]}）"
            )
        if not rows:
            print(f"[{index}/{total}] {code}: 全部为日批重叠日，跳过写入")
            continue

        written = len(rows) if dry_run else write_snapshot_rows_bulk(rows, db_path=resolved_db)
        summary["rows_written"] += written
        summary["symbols_ok"] += 1
        flag = "[dry-run] 可取" if dry_run else "写入"
        print(f"[{index}/{total}] {code}: {flag} {written} 行（窗口 {start}..{end}）")

    if not dry_run:
        _persist_missing_summary(resolved_db, summary)
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
        f"partial_overlap={len(summary['partial_overlap'])} "
        f"daily_overlap={len(summary['daily_overlap'])} "
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
