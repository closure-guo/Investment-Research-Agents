"""add-market-snapshot 实网对账补验（#325）——新浪基准版。

原对账脚本（2026-10-13-add-market-snapshot-reconcile.py）以 ``ak.stock_zh_a_hist``
（东财）为参考源；东财行情域 IP 封禁长期化（incident 033，2026-10-27 复测仍
``RemoteDisconnected``），参考源不可达。本脚本改用**新浪** ``ak.stock_zh_a_daily``
作独立参考源——库内回填数据原产于东财 hist，新浪是另一家数据商，构成跨源对账。

口径对照（小样本已验证）：
- ``open`` / ``close_raw``：与新浪 ``open`` / ``close``（不复权）逐位一致
- ``close_qfq``：与新浪 ``close``（adjust="qfq"）一致；新浪 qfq 可能两位小数，
  容差取 0.011
- ``volume``：库值单位=**手**，新浪单位=**股**，比较时库值 ×100

局限（如实披露）：只验证「回填写入对源 API 的解析/映射/存储保真度」及跨源价格
一致性；不覆盖北交所（新浪部分代码无日线）、不验证状态字段/prev_close 语义。

退出码：0=全部一致；1=存在不一致；2=库内区间无行；3=参考数据全部取数失败。
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

# 20 只跨板样本（与原脚本同清单；北交所不入样本——新浪无北交所日线）
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
_VOLUME_LOT_TO_SHARE = 100.0  # 库=手，新浪=股


def _sina_symbol(code: str) -> str:
    if code.startswith(("60", "68", "9")):
        return f"sh{code}"
    if code.startswith(("00", "30", "20")):
        return f"sz{code}"
    return f"bj{code}"


def _norm_date(value: object) -> str | None:
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
    try:
        out = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def _sina_daily(code: str, adjust: str, *, retries: int = 3) -> pd.DataFrame | None:
    import akshare as ak

    for attempt in range(1, retries + 1):
        try:
            return ak.stock_zh_a_daily(symbol=_sina_symbol(code), adjust=adjust)
        except Exception as exc:  # noqa: BLE001 - 取数失败按 None 处理并记录
            print(
                f"  [参考取数] {code} adjust={adjust!r} attempt {attempt}/{retries}: "
                f"{type(exc).__name__}: {str(exc)[:120]}"
            )
            if attempt < retries:
                time.sleep(1.5 * attempt)
    return None


def _reference_for_symbol(
    code: str, dates: set[str]
) -> dict[str, tuple[float | None, float | None, float | None, float | None]] | None:
    """参考帧 → ``{date: (open, close_raw, volume, close_qfq)}``；任一腿失败 → None。"""
    raw = _sina_daily(code, "")
    if not isinstance(raw, pd.DataFrame) or raw.empty:
        return None
    if not {"date", "open", "close", "volume"} <= set(raw.columns):
        return None
    qfq = _sina_daily(code, "qfq")
    if not isinstance(qfq, pd.DataFrame) or qfq.empty:
        return None
    if not {"date", "close"} <= set(qfq.columns):
        return None

    qfq_by_date: dict[str, float | None] = {}
    for _, row in qfq.iterrows():
        d = _norm_date(row["date"])
        if d and d in dates:
            qfq_by_date[d] = _fnum(row["close"])
    ref: dict[str, tuple[float | None, float | None, float | None, float | None]] = {}
    for _, row in raw.iterrows():
        d = _norm_date(row["date"])
        if not d or d not in dates:
            continue
        ref[d] = (
            _fnum(row["open"]),
            _fnum(row["close"]),
            _fnum(row["volume"]),
            qfq_by_date.get(d),
        )
    return ref or None


def _tolerance(field: str) -> float:
    # 价格逐位一致（实测）留 1e-6；新浪 qfq 常见两位小数 → 0.011；
    # volume 手×100 浮点路径 → 相对 1e-6
    if field == "close_qfq":
        return 0.011
    if field == "volume":
        return 1e-6
    return 1e-6


def _compare(db_value: float | None, ref_value: float | None, field: str) -> bool:
    if db_value is None or ref_value is None:
        return db_value is None and ref_value is None
    left, right = db_value, ref_value
    if field == "volume":
        left = db_value * _VOLUME_LOT_TO_SHARE
    tol = _tolerance(field)
    if field == "volume":
        scale = max(abs(left), abs(right), 1.0)
        return abs(left - right) / scale <= tol
    return abs(left - right) <= max(tol, tol * max(abs(right), 1.0))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--start", default="2024-10-01")
    ap.add_argument("--end", default="2025-12-31")
    ap.add_argument("--dates", type=int, default=20)
    ap.add_argument("--seed", type=int, default=20261027)
    args = ap.parse_args()

    con = sqlite3.connect(f"file:{Path(args.db)}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    cur = con.cursor()
    day_rows = cur.execute(
        "SELECT DISTINCT trade_date FROM market_snapshot "
        "WHERE trade_date BETWEEN ? AND ? ORDER BY trade_date",
        (args.start, args.end),
    ).fetchall()
    days = [r[0] for r in day_rows]
    if not days:
        print(f"库内区间 {args.start}..{args.end} 无行——无可对账（回填未做？）")
        return 2
    rng = random.Random(args.seed)  # noqa: S311 - 对账抽样可复现用，非密码学场景
    sample_days = sorted(rng.sample(days, min(args.dates, len(days))))
    print(f"抽样交易日（seed={args.seed}，命中 {len(sample_days)}/{len(days)}）:")
    print("  " + ", ".join(sample_days))

    mismatches: list[dict] = []
    ref_ok_symbols = 0
    db_rows: dict[str, dict[str, dict]] = {}
    for code in DEFAULT_SYMBOLS:
        placeholders = ",".join("?" * len(sample_days))
        rows = cur.execute(
            "SELECT trade_date, open, close_raw, close_qfq, volume FROM market_snapshot "  # noqa: S608 - 占位符为 ? 拼接,非用户输入
            f"WHERE code = ? AND trade_date IN ({placeholders})",
            (code, *sample_days),
        ).fetchall()
        if not rows:
            continue
        db_rows[code] = {r["trade_date"]: dict(r) for r in rows}

    for code in DEFAULT_SYMBOLS:
        per_code = db_rows.get(code) or {}
        if not per_code:
            print(f"  [{code}] 库内无抽样行，跳过")
            continue
        ref = _reference_for_symbol(code, set(per_code))
        if ref is None:
            print(f"  [{code}] 参考源取数失败/缺列，无法对账")
            continue
        ref_ok_symbols += 1
        checked = 0
        for d, db_row in sorted(per_code.items()):
            ref_row = ref.get(d)
            if ref_row is None:
                mismatches.append({"code": code, "date": d, "field": "all", "note": "参考源缺该日"})
                continue
            ref_vals = {
                "open": ref_row[0],
                "close_raw": ref_row[1],
                "volume": ref_row[2],
                "close_qfq": ref_row[3],
            }
            for field in _FIELDS:
                checked += 1
                if not _compare(db_row.get(field), ref_vals[field], field):
                    mismatches.append(
                        {
                            "code": code,
                            "date": d,
                            "field": field,
                            "db": db_row.get(field),
                            "ref": ref_vals[field],
                        }
                    )
        print(f"  [{code}] 对账 {len(per_code)} 日 × {len(_FIELDS)} 字段 = {checked} 格")

    total_cells = sum(len(v) for v in db_rows.values()) * len(_FIELDS)
    print()
    print(f"对账符号数: {ref_ok_symbols}/{len(db_rows)}；总格数: {total_cells}")
    if not ref_ok_symbols:
        print("参考数据全部取数失败（网络受阻）")
        return 3
    if mismatches:
        print(f"不一致 {len(mismatches)} 格（前 20 条）：")
        for item in mismatches[:20]:
            print(f"  {item}")
        return 1
    print("全部一致 ✓")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
