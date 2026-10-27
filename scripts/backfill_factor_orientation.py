"""存量因子 orientation 回填（add-factor-direction-reversal-monitor Task 3）。

对 ``orientation IS NULL`` 的因子行按全史已记录 ``factor_ic`` 均值符号回填：
|均值| < 1e-12 或无样本 → 0。幂等可重跑（仅回填 NULL 行）。
用法: uv run python scripts/backfill_factor_orientation.py [--db PATH]
"""

from __future__ import annotations

import argparse

from finance_agent.factors.library import list_factors, list_ic_series, set_orientation

_ZERO_TOL = 1e-12


def _sign(value: float) -> int:
    if value > _ZERO_TOL:
        return 1
    if value < -_ZERO_TOL:
        return -1
    return 0


def backfill_orientation(*, db_path: str | None = None) -> dict[str, int]:
    backfilled = 0
    for row in list_factors(db_path=db_path):
        if row.get("orientation") is not None:
            continue
        values = [float(v) for _d, v in list_ic_series(int(row["id"]), db_path=db_path)]
        mean = sum(values) / len(values) if values else 0.0
        set_orientation(int(row["id"]), _sign(mean), db_path=db_path)
        backfilled += 1
    return {"backfilled": backfilled, "skipped": 0}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default=None)
    args = parser.parse_args()
    print(backfill_orientation(db_path=args.db))


if __name__ == "__main__":
    main()
