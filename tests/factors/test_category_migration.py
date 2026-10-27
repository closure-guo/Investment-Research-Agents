"""scripts/migrate_factor_category.py 单测：幂等迁移 + 事件留痕。"""

from __future__ import annotations

import sqlite3

from scripts.migrate_factor_category import migrate_category

from finance_agent.factors.library import add_factor


def test_migrate_and_idempotent(tmp_path):
    db = str(tmp_path / "factor_library.db")
    fid_vol = add_factor(
        expression="ts_mean(volume,5)",
        hypothesis="[RD-Agent/volx] [Volume/Liquidity Factor] volume ratio",
        category="外部",
        status="active",
        db_path=db,
    )
    fid_range = add_factor(
        expression="div(sub(high,low),close)",
        hypothesis="[RD-Agent/rngx] [Intraday Range Factor] high-low range",
        category="外部",
        status="active",
        db_path=db,
    )
    result = migrate_category(db_path=db)
    assert result == {"migrated": 2, "skipped": 0}
    conn = sqlite3.connect(db)
    rows = {r[0]: r[1] for r in conn.execute("SELECT id, category FROM factors")}
    events = conn.execute(
        "SELECT COUNT(*) FROM factor_events WHERE event='category_migration'"
    ).fetchone()[0]
    conn.close()
    assert rows[fid_vol] == "量与流动性"
    assert rows[fid_range] == "波动率"
    assert events == 2
    # 幂等：重跑零迁移（category 已非「外部」）
    assert migrate_category(db_path=db) == {"migrated": 0, "skipped": 0}
