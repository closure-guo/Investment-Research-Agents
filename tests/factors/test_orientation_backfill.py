"""scripts/backfill_factor_orientation.py 单测：幂等回填。"""

from __future__ import annotations

import sqlite3

from scripts.backfill_factor_orientation import backfill_orientation

from finance_agent.factors.library import add_factor, record_ic


def test_backfill_sign_and_idempotent(tmp_path):
    db = str(tmp_path / "factor_library.db")
    fid_neg = add_factor(expression="ts_mean(close,5)", hypothesis="h", category="测试",
                         status="active", db_path=db)
    fid_zero = add_factor(expression="ts_mean(close,10)", hypothesis="h", category="测试",
                          status="active", db_path=db)
    for day, value in [("2026-01-05", -0.04), ("2026-01-06", -0.06), ("2026-01-07", -0.02)]:
        record_ic(fid_neg, day, value, db_path=db)
    # fid_zero 无 IC 样本
    result = backfill_orientation(db_path=db)
    assert result == {"backfilled": 2, "skipped": 0}
    conn = sqlite3.connect(db)
    rows = {r[0]: r[1] for r in conn.execute("SELECT id, orientation FROM factors")}
    conn.close()
    assert rows[fid_neg] == -1  # 全史均值 -0.04 → -1
    assert rows[fid_zero] == 0  # 无样本 → 0
    # 幂等：重跑零回填
    assert backfill_orientation(db_path=db) == {"backfilled": 0, "skipped": 0}
