"""daily_brief._factor_facts 拒绝池排除回归（add-rejected-factor-pool T6）。

拒绝池行（rejected）不进日报因子区：既有读路径不可见——per_factor 只含
非 rejected 行，active_count 不受 rejected 行影响。
"""

from __future__ import annotations

from pathlib import Path

from finance_agent.factors.library import (
    add_factor,
    init_library,
    record_ic,
    record_rejected_factor,
)
from finance_agent.outcome.daily_brief import _factor_facts


def test_factor_facts_excludes_rejected_rows(tmp_path: Path) -> None:
    db = tmp_path / "factor_library.db"
    init_library(db)
    active_id = add_factor(
        expression="ts_mean(close,5)",
        hypothesis="测试假设：短均线与收益的关系描述文本。",
        category="动量",
        status="active",
        db_path=str(db),
    )
    record_ic(active_id, "2026-10-27", 0.05, db_path=str(db))
    rejected_id = record_rejected_factor(
        expression="volume",
        hypothesis="闸②拒收的量因子",
        category="价量",
        ast_json="volume",
        source="harvest",
        gate=2,
        reason="WEAK_ICIR",
        detail={},
        db_path=str(db),
    )["factor_id"]
    record_ic(rejected_id, "2026-10-27", -0.02, db_path=str(db))

    facts = _factor_facts(db)

    assert facts["active_count"] == 1
    assert [row["factor_id"] for row in facts["per_factor"]] == [active_id]
    assert all(row["status"] != "rejected" for row in facts["per_factor"])
