"""全链集成（三 delta 接线）：准入写定向 → 监控反转降级 → 月批复核。

各闸/各判据的语义由各自专测覆盖（test_validate / test_monitor / test_review /
test_monthly）；本文件只钉**跨模块接线**：

1. ``admit_candidates`` 入库负向/正向因子 → 行内 ``orientation`` = 闸④训练段符号
   （监控的消费输入）；
2. 同一因子落库翻转 IC 序列 → ``run_factor_monitor`` 走 ``DIRECTION_REVERSAL``
   （衰减与反转判据同时命中时 reason 取反转——优先级接线）；
3. 降级因子对 ``run_post_admission_review`` 不可见（degraded 不在 active 集合，
   MUST NOT 复核恢复），同时复核仍能处置独立种子出的弱因子。
"""

from __future__ import annotations

import sqlite3

from finance_agent.factors.library import (
    add_factor,
    list_events,
    list_factors,
    record_ic,
)
from finance_agent.factors.monitor import run_factor_monitor
from finance_agent.factors.review import run_post_admission_review
from finance_agent.factors.validate import admit_candidates
from tests.factors.test_monitor import _MONITOR_DATES, _day, _monitor_panel
from tests.factors.test_validate import _candidate, _signal_panel


def test_admission_feeds_orientation_to_monitor_and_review(tmp_path):
    db = str(tmp_path / "factor_library.db")
    panel = _signal_panel()

    # 1) 准入：volume 在合成面板上真实预测次日收益 → 过五道闸入库，定向落库
    result = admit_candidates([_candidate("volume")], panel, seed=1, db_path=db)[0]
    assert result.status == "active"
    gate4 = next(gate for gate in result.gates if gate.gate == 4)
    factor_id = result.factor_id
    assert factor_id is not None
    conn = sqlite3.connect(db)
    orientation = conn.execute(
        "SELECT orientation FROM factors WHERE id=?", (factor_id,)
    ).fetchone()[0]
    conn.close()
    assert orientation == gate4.detail["orientation"]
    assert orientation in (-1, 1)

    # 2) 监控：同一因子在监控面板上落库全反号的 IC 序列（历史 79 天 -0.05 + 面板
    #    记录日 -0.02）→ 滚动均值全负；衰减判据（负且弱）与反转判据（与定向相反）
    #    同时命中 → reason 取 DIRECTION_REVERSAL（反转优先接线）
    for offset in range(79):
        record_ic(factor_id, _day(offset), -0.05, db_path=db)
    monitor_result = run_factor_monitor(_monitor_panel(), _day(_MONITOR_DATES - 1), db_path=db)
    assert monitor_result["reversed"] == [factor_id]
    events = list_events(factor_id, db_path=db)
    assert events[-1]["to_status"] == "degraded"
    assert events[-1]["detail"]["reason"] == "DIRECTION_REVERSAL"
    assert events[-1]["detail"]["orientation"] == orientation

    # 3) 复核：降级因子不在 active 集合 → 复核不可见（不复活）；独立种子的弱因子
    #    （80 条恒定 IC → ICIR=0）被复核降级
    weak_id = add_factor(
        expression="ts_std(close,10)",
        hypothesis="h",
        category="测试",
        status="active",
        db_path=db,
    )
    for offset in range(80):
        record_ic(weak_id, _day(offset), 0.04, db_path=db)
    review = run_post_admission_review(db_path=db)
    assert weak_id in review["degraded"]
    assert factor_id not in review["degraded"]
    assert factor_id not in review["skipped"]
    rows = {row["id"]: row["status"] for row in list_factors(db_path=db)}
    assert rows[factor_id] == "degraded"  # 反转降级未被复核复活
    assert rows[weak_id] == "degraded"
