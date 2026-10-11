"""list_predictions 在无 daily_marks 表的库上不得崩（main 红修复回归）。

背景（2026-10-10 main CI 红）：#253 新增 collect_settled_day_masters 消费端在
predictions-only fixture 库上走 list_predictions → _latest_marks_for 无条件
查 daily_marks → OperationalError。生产路径 api 启动恒建表，但通用读路径
应对部分初始化库（测试 fixture / 新副本）稳健——缺失视为无盯市，open 行
latest_mark=None。
"""

from __future__ import annotations

import sqlite3

import pytest

from finance_agent.outcome.track_record.model import (
    init_predictions,
    insert_prediction,
    list_predictions,
)

BASE = {
    "source_type": "live",
    "symbol": "600519.SH",
    "direction": "long",
    "rationale_snapshot": {"markdown": "x"},
}


def test_list_predictions_tolerates_missing_daily_marks(tmp_path):
    db = tmp_path / "marks-less.db"
    init_predictions(db)  # 仅 predictions 表，无 daily_marks
    with pytest.raises(sqlite3.OperationalError):
        # 前置断言：库确实没有 daily_marks（防止 init_predictions 未来顺带建表
        # 使本测试空转——届时守护逻辑由 init 语义吸收，本回归可退役）
        conn = sqlite3.connect(db)
        try:
            conn.execute("SELECT 1 FROM daily_marks").fetchone()
        finally:
            conn.close()

    insert_prediction(dict(BASE), db_path=db)
    rows = list_predictions(limit=10, db_path=db)
    assert len(rows) == 1
    assert rows[0]["latest_mark"] is None
