"""内置因子注册日批（wire-builtin-factor-registration）单测。

覆盖 delta spec 场景：缺失条目过闸注册 / 日批注册入口幂等（含 retired 不回填）/
面板不可得 fail loud。合成强信号面板复用 ``tests/factors/test_builtin.py`` 的
``_strong_panel``（同种子同结构，准入结论逐位可复现：强面板上 reversal_5 被拒于
闸②，其余四条 active）——本文件只测**任务编排**（存在性幂等 / 子集提交 / 失败语义），
五道闸本身的统计口径由 ``test_builtin.py`` / ``test_validate.py`` 覆盖。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from finance_agent.factors.builtin import BUILTIN_FACTORS, run_factor_builtin_register
from finance_agent.factors.library import add_factor, list_factors, set_status
from tests.factors.test_builtin import _STRONG_SEED, _strong_panel
from tests.factors.test_monthly import _seed_snapshot_dates

_REVERSAL_EXPRESSION = "div(ts_mean(close,5),close)"


def _db(tmp_path: Path) -> str:
    return str(tmp_path / "factor_library.db")


def test_empty_library_registers_missing_on_strong_panel(tmp_path):
    """空库 + 强信号面板：五条逐条过闸；过闸者入库（category/source 逐字），未过者留原因码。"""
    db = _db(tmp_path)

    summary = run_factor_builtin_register(panel=_strong_panel(), seed=_STRONG_SEED, db_path=db)

    assert summary["skipped"] == 0
    assert summary["registered"] + summary["rejected"] == len(BUILTIN_FACTORS)
    assert summary["registered"] >= 1  # 强面板上至少一个 active
    assert len(summary["reasons"]) == summary["rejected"]
    for item in summary["reasons"]:
        assert set(item) == {"expression", "gate", "reason"}
    rows = list_factors(db_path=db)
    assert len(rows) == summary["registered"]
    assert all(row["category"] == "builtin" and row["source"] == "builtin" for row in rows)


def test_all_present_including_retired_skips_without_panel(tmp_path):
    """五条已存在（含 retired）→ 全量跳过且不触面板（快照库不存在亦不失败）；retired 不复活。"""
    db = _db(tmp_path)
    run_factor_builtin_register(panel=_strong_panel(), seed=_STRONG_SEED, db_path=db)
    retired_id = add_factor(
        expression=_REVERSAL_EXPRESSION,
        hypothesis="测试用：5 日反转（以退役态充当存在性判据的样本，长度达标）。",
        category="builtin",
        status="active",
        source="builtin",
        db_path=db,
    )
    set_status(retired_id, "retired", db_path=db)
    rows_before = list_factors(db_path=db)
    assert len(rows_before) == len(BUILTIN_FACTORS)

    summary = run_factor_builtin_register(
        db_path=db, snapshot_db_path=tmp_path / "missing_snapshot.db"
    )

    assert summary == {
        "registered": 0,
        "rejected": 0,
        "skipped": len(BUILTIN_FACTORS),
        "reasons": [],
    }
    rows_after = list_factors(db_path=db)
    assert rows_after == rows_before  # 零新增；retired 条目保持 retired（不回填）
    retired_after = next(row for row in rows_after if row["id"] == retired_id)
    assert retired_after["status"] == "retired"


def test_partial_missing_submits_only_missing(tmp_path):
    """部分缺失：已存在者计入 skipped 不再提交；仅缺失条目过闸（原因码逐条）。"""
    db = _db(tmp_path)
    first = run_factor_builtin_register(panel=_strong_panel(), seed=_STRONG_SEED, db_path=db)
    assert first["rejected"] == 1  # 强面板上 reversal_5 被拒（不落库）

    second = run_factor_builtin_register(panel=_strong_panel(), seed=_STRONG_SEED, db_path=db)

    assert second["skipped"] == first["registered"]
    assert second["registered"] == 0 and second["rejected"] == 1
    assert [item["expression"] for item in second["reasons"]] == [_REVERSAL_EXPRESSION]
    assert len(list_factors(db_path=db)) == first["registered"]  # 库零新增


def test_missing_entries_without_panel_fail_loud(tmp_path):
    """缺失条目存在且快照库为空 → ValueError（不以空面板放行注册）。"""
    db = _db(tmp_path)

    with pytest.raises(ValueError, match="快照库为空"):
        run_factor_builtin_register(db_path=db, snapshot_db_path=tmp_path / "empty_snapshot.db")


def test_shallow_panel_fails_loud(tmp_path):
    """快照可用历史不足准入下限（150 交易日）→ ValueError（unify-quant-research-architecture）。"""
    db = _db(tmp_path)
    snap = tmp_path / "shallow_snapshot.db"
    _seed_snapshot_dates(snap, n=90)

    with pytest.raises(ValueError, match="深度不足"):
        run_factor_builtin_register(db_path=db, snapshot_db_path=snap)
