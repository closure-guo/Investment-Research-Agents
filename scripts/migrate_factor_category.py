"""存量因子 category 机理分桶迁移（update-harvest-factor-category Task 3）。

对 ``category="外部"`` 行按 ``classify_category``（同一映射函数，单一真相源）
重写 + ``factor_events`` 落 ``category_migration`` 事件（detail 记 from/to）。
幂等可重跑（仅迁移仍为「外部」的行）。历史 BUCKET_QUOTA 事件不改写。
用法: uv run python scripts/migrate_factor_category.py [--db PATH]
"""

from __future__ import annotations

import argparse
import json

from finance_agent.factors.harvest import classify_category
from finance_agent.factors.library import (  # noqa: PLC2701 - 部署脚本私有导入（仓库先例）
    _connect,
    _now_iso,
    init_library,
    list_factors,
)

_OLD_CATEGORY = "外部"
EVENT_CATEGORY_MIGRATION = "category_migration"


def migrate_category(*, db_path: str | None = None) -> dict[str, int]:
    init_library(db_path)
    migrated = 0
    conn = _connect(db_path)
    try:
        for row in list_factors(db_path=db_path):
            if str(row.get("category")) != _OLD_CATEGORY:
                continue
            fid = int(row["id"])
            new_category = classify_category(str(row.get("expression")), str(row.get("hypothesis")))
            conn.execute("UPDATE factors SET category = ? WHERE id = ?", (new_category, fid))
            conn.execute(
                "INSERT INTO factor_events (factor_id, event, from_status, to_status, detail, "
                "created_at) VALUES (?, ?, NULL, NULL, ?, ?)",
                (
                    fid,
                    EVENT_CATEGORY_MIGRATION,
                    json.dumps(
                        {"from_category": _OLD_CATEGORY, "to_category": new_category},
                        ensure_ascii=False,
                    ),
                    _now_iso(),
                ),
            )
            migrated += 1
        conn.commit()
    finally:
        conn.close()
    return {"migrated": migrated, "skipped": 0}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default=None)
    args = parser.parse_args()
    print(migrate_category(db_path=args.db))


if __name__ == "__main__":
    main()
