"""add-market-snapshot（T11 / 计划任务 4.2）两年区间随机抽样对账脚本。

用一次性脚本对 ``market_snapshot`` 库内回填区间的行与 live 参考源逐字段比对：

- 抽样：从库内 ``[--start, --end]`` 实际存在的交易日中，用固定随机种子
  ``--seed`` 抽 ``--dates``（默认 20）个交易日；种子与命中日期全部打印（可复现）。
- 参考源：live ``ak.stock_zh_a_hist`` 两次调用（``adjust=""`` 与 ``adjust="qfq"``），
  按日期内连接；逐票**全窗口取一次**（20 票 × 2 调用），按日期索引后与库内
  抽样行比对——不逐格打网络。
- 比对字段（与回填写入列对应）：``close_raw`` / ``close_qfq`` / ``open`` /
  ``volume``（open/volume 取 raw 腿口径，即回填脚本的写入口径）。
- 容差：绝对 ``--tolerance``（默认 1e-6，浮点存储路径两侧同源，理应逐位一致）。

本脚本**证明**：回填路径（akshare 取数 → ``_history_rows`` 映射 → 落库）对源 API
的解析/映射/存储保真度。**不证明**：状态字段（回填显式为 0、prev_close 为 NULL）、
日批 spot 路径的 close_prices 语义（仅收盘后与 hist 可比）。

退出码：0=全部一致；1=存在不一致；2=库内区间无行（无法对账，网络/回填受阻）；
3=参考数据全部取数失败（网络受阻）。

用法::

    uv run python tests/validation/2026-10-13-add-market-snapshot-reconcile.py \
        --db tmp/val-snapshot.db --seed 20261013
"""

from __future__ import annotations

import argparse
import math
import random
import sqlite3
import time
from datetime import date, datetime
from pathlib import Path

import pandas as pd

# 20 只跨板块标的（与验证任务给定清单一致）
DEFAULT_SYMBOLS: tuple[str, ...] = (
    "600519",
    "601398",
    "600036",
    "600030",
    "601818",
    "000001",
    "000002",
    "000651",
    "000333",
    "002415",
    "002594",
    "300750",
    "300059",
    "300124",
    "688111",
    "688981",
    "600845",
    "601066",
    "600026",
    "600515",
)

_FIELDS: tuple[str, ...] = ("close_raw", "close_qfq", "open", "volume")


def _norm_date(value: object) -> str | None:
    """任意 akshare 日期值（str/date/datetime/Timestamp）→ 规范 ``YYYY-MM-DD``。"""
    if value is None:
        return None
    if isinstance(value, pd.Timestamp):
        value = value.date()
    if isinstance(value, datetime):
        value = value.date()
    if isinstance(value, date):
        return value.isoformat()
    text = str(value).strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%d", "%Y%m%d", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def _fnum(value: object) -> float | None:
    """数值归一：None/NaN/inf/不可解析 → None（与落库侧 ``_num`` 同规则）。

    关键：NaN 参与比较时任何 ``>`` 均为 False，会让坏值静默「通过」——必须先归一。
    """
    try:
        out = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def _ak_hist(symbol: str, start: str, end: str, adjust: str, *, retries: int = 3):
    """live ``stock_zh_a_hist`` 带重试包装；全部失败返回 None（不抛）。"""
    import akshare as ak

    for attempt in range(1, retries + 1):
        try:
            return ak.stock_zh_a_hist(
                symbol=symbol,
                period="daily",
                start_date=start.replace("-", ""),
                end_date=end.replace("-", ""),
                adjust=adjust,
            )
        except Exception as exc:  # noqa: BLE001 - 取数失败按 None 处理并记录
            print(
                f"  [参考取数] {symbol} adjust={adjust!r} attempt {attempt}/{retries}: "
                f"{type(exc).__name__}: {str(exc)[:120]}"
            )
            if attempt < retries:
                time.sleep(1.5 * attempt)
    return None


def _reference_for_symbol(
    symbol: str, start: str, end: str
) -> dict[str, tuple[float | None, float | None, float | None, float | None]] | None:
    """参考帧 → ``{date: (open, close_raw, volume, close_qfq)}``；任一腿失败 → None。"""
    raw = _ak_hist(symbol, start, end, "")
    if not isinstance(raw, pd.DataFrame) or raw.empty:
        return None
    if not {"日期", "开盘", "收盘", "成交量"} <= set(raw.columns):
        return None
    qfq = _ak_hist(symbol, start, end, "qfq")
    if not isinstance(qfq, pd.DataFrame) or qfq.empty:
        return None
    if not {"日期", "收盘"} <= set(qfq.columns):
        return None

    ref: dict[str, tuple[float | None, float | None, float | None, float | None]] = {}
    qfq_by_date: dict[str, float | None] = {}
    for _, row in qfq.iterrows():
        d = _norm_date(row["日期"])
        if d:
            qfq_by_date[d] = _fnum(row["收盘"])
    for _, row in raw.iterrows():
        d = _norm_date(row["日期"])
        if not d:
            continue
        ref[d] = (
            _fnum(row["开盘"]),
            _fnum(row["收盘"]),
            _fnum(row["成交量"]),
            qfq_by_date.get(d),
        )
    return ref


def _read_db_rows(db_path: Path, symbols: list[str], start: str, end: str) -> list[sqlite3.Row]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        placeholders = ", ".join("?" * len(symbols))
        return conn.execute(
            f"SELECT code, trade_date, open, close_raw, close_qfq, volume "  # noqa: S608
            f"FROM market_snapshot WHERE code IN ({placeholders}) "
            "AND trade_date >= ? AND trade_date <= ? ORDER BY trade_date, code",
            [*symbols, start, end],
        ).fetchall()
    finally:
        conn.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="market_snapshot 回填对账（抽样 × live 参考）")
    parser.add_argument("--db", default="tmp/val-snapshot.db", help="快照库路径")
    parser.add_argument("--start", default="2024-01-01")
    parser.add_argument("--end", default="2025-12-31")
    parser.add_argument("--seed", type=int, default=20261013, help="抽样随机种子（复现用）")
    parser.add_argument("--dates", type=int, default=20, help="抽样交易日数")
    parser.add_argument("--symbols", default=",".join(DEFAULT_SYMBOLS))
    parser.add_argument("--tolerance", type=float, default=1e-6, help="绝对容差")
    parser.add_argument("--example-cap", type=int, default=10)
    args = parser.parse_args(argv)

    db_path = Path(args.db)
    symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]
    if not db_path.exists():
        print(f"BLOCKED: 快照库不存在: {db_path}")
        return 2

    rows = _read_db_rows(db_path, symbols, args.start, args.end)
    if not rows:
        print(
            f"BLOCKED: 库 {db_path} 在 [{args.start}, {args.end}] × {len(symbols)} 只标的"
            "范围内无任何回填行——回填未产出数据（网络受阻时取数全失败），无法对账。"
        )
        return 2

    all_dates = sorted({row["trade_date"] for row in rows})
    sample_n = min(args.dates, len(all_dates))
    # 复现性抽样（非密码学用途）：固定种子 + Random 实例，S311 不适用
    sampled_dates = sorted(random.Random(args.seed).sample(all_dates, sample_n))  # noqa: S311
    print(f"抽样种子 seed={args.seed}；库内交易日总数={len(all_dates)}；抽样 {sample_n} 天：")
    print("  " + ", ".join(sampled_dates))

    ref_ok, ref_failed = {}, []
    for i, code in enumerate(symbols, start=1):
        ref = _reference_for_symbol(code, args.start, args.end)
        if ref is None:
            ref_failed.append(code)
            print(f"[{i}/{len(symbols)}] {code}: 参考取数失败")
        else:
            ref_ok[code] = ref
            print(f"[{i}/{len(symbols)}] {code}: 参考 {len(ref)} 天")
    if not ref_ok:
        print("BLOCKED: 参考源全部取数失败（网络受阻），无法对账。")
        return 3

    cells_compared = 0
    mismatches: list[str] = []
    missing_ref = 0
    db_dates_by_code = {(r["code"], r["trade_date"]): r for r in rows}
    for code, ref in ref_ok.items():
        for d in sampled_dates:
            row = db_dates_by_code.get((code, d))
            if row is None:
                continue  # 当日库内该票无行（未回填到/非交易日）：不计入比对
            if d not in ref:
                missing_ref += 1
                mismatches.append(f"{code} {d} <missing_ref> 参考源无该日行")
                continue
            r_open, r_close_raw, r_volume, r_close_qfq = ref[d]
            pairs = (
                ("open", row["open"], r_open),
                ("close_raw", row["close_raw"], r_close_raw),
                ("close_qfq", row["close_qfq"], r_close_qfq),
                ("volume", row["volume"], r_volume),
            )
            for field, actual, expected in pairs:
                if expected is None and actual is None:
                    cells_compared += 1
                    continue
                cells_compared += 1
                if actual is None or expected is None:
                    mismatches.append(f"{code} {d} {field}: db={actual!r} ref={expected!r}")
                    continue
                if abs(actual - expected) > args.tolerance:
                    mismatches.append(f"{code} {d} {field}: db={actual!r} ref={expected!r}")

    # 参考有而库无（抽样日 × 参考已取到的票）：提示性统计，单独计数
    ref_only = 0
    for code, ref in ref_ok.items():
        for d in sampled_dates:
            if d in ref and (code, d) not in db_dates_by_code:
                ref_only += 1

    print()
    print(f"参考取数成功 {len(ref_ok)}/{len(symbols)} 只；失败: {', '.join(ref_failed) or '无'}")
    print(f"比对单元 = {cells_compared}（{len(sampled_dates)} 天 × 命中票 × {len(_FIELDS)} 字段）")
    print(f"不一致 = {len(mismatches)}（其中参考缺行 {missing_ref}）；参考有而库无 = {ref_only}")
    for line in mismatches[: args.example_cap]:
        print(f"  ✗ {line}")
    if len(mismatches) > args.example_cap:
        print(f"  …（另有 {len(mismatches) - args.example_cap} 条省略）")

    if cells_compared == 0:
        print("BLOCKED: 抽样集合与库内行无交集（数据不足以对账）。")
        return 2
    return 1 if mismatches else 0


if __name__ == "__main__":
    raise SystemExit(main())
