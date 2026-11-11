"""执行链 lane/single_cap/tier_bump 参数化（add-microcap-lane-analysis T3）。

覆盖两部分：

1. matching 滑点档位 bump（``slippage_rate_for_amount`` / ``match_order`` 新增
   ``tier_bump``）：选中档索引 +N 后 clamp 末档；0 = 主档行为不变；
2. 驱动器 lane 过滤（``run_execution_daily`` 新增 ``lane``）：主档通道只消费
   ``lane='main'`` 的观点，microcap 观点不生成信号（lane 隔离）。

新增参数全部带默认值=既有行为（主档回归红线）：既有用例不传新参行为逐字节不变。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from finance_agent.execution.config import default_matching_config
from finance_agent.execution.matching import MatchingConfig, slippage_rate_for_amount

# ── 1. matching：tier_bump（滑点档位提升） ───────────────────────────────────


# 两档配置：分位 ≤0.5 → 0.002；>0.5 → 0.004
def _tier(quantile_max: float, rate: float):
    from finance_agent.execution.matching import SlippageTier

    return SlippageTier(quantile_max=quantile_max, slippage_rate=rate)


def _two_tier_config() -> MatchingConfig:
    base = default_matching_config()  # 费用字段沿用内置默认（本组用例只动 slippage_tiers）
    return MatchingConfig(
        commission_rate=base.commission_rate,
        min_commission=base.min_commission,
        stamp_tax_rate=base.stamp_tax_rate,
        slippage_tiers=(
            _tier(0.5, 0.002),
            _tier(1.0, 0.004),
        ),
    )


def test_tier_bump_zero_is_unchanged():
    amounts = [100.0, 200.0, 300.0, 400.0]  # 250 → 分位 0.5 → 首档
    cfg = _two_tier_config()
    assert slippage_rate_for_amount(250.0, amounts, cfg) == 0.002
    assert slippage_rate_for_amount(250.0, amounts, cfg, tier_bump=0) == 0.002


def test_tier_bump_one_shifts_and_clamps():
    amounts = [100.0, 200.0, 300.0, 400.0]
    cfg = _two_tier_config()
    assert slippage_rate_for_amount(250.0, amounts, cfg, tier_bump=1) == 0.004
    # 末档再 bump 封顶
    assert slippage_rate_for_amount(900.0, amounts, cfg, tier_bump=1) == 0.004


def test_missing_sample_still_conservative_with_bump():
    cfg = _two_tier_config()
    # 样本缺失 → 最保守档（最大 slippage 0.004），bump 不越界
    assert slippage_rate_for_amount(250.0, None, cfg, tier_bump=1) == 0.004


# ── 2. 驱动器：lane 过滤（主档只吃 lane='main'） ────────────────────────────


def _seed_prediction(
    db: Path, *, pid: str, symbol: str, lane: str, created: str = "2026-01-05T10:00:00"
):
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    cols = {r[1] for r in conn.execute("PRAGMA table_info(predictions)")}
    if "lane" not in cols:
        conn.execute("ALTER TABLE predictions ADD COLUMN lane TEXT NOT NULL DEFAULT 'main'")
    # confidence 显式造 0.8：信号契约 _coerce_strength(None) 拒收 → 观点会被跳过
    conn.execute(
        "INSERT INTO predictions (prediction_id, source_type, symbol, direction,"
        " confidence, rationale_snapshot, status, created_at, updated_at, lane)"
        " VALUES (?, 'live', ?, 'long', 0.8, ?, 'open', ?, ?, ?)",
        (pid, symbol, '{"fund_manager_decision":"approve","action":"buy"}', created, created, lane),
    )
    conn.commit()
    conn.close()


def test_main_lane_excludes_microcap(tmp_path):
    pred_db = tmp_path / "pred.db"
    exec_db = tmp_path / "exec.db"
    # 建最小 predictions 表（PREDICTIONS_DDL 已含 lane 列——Task 2 归属列）
    from finance_agent.outcome.track_record.model import PREDICTIONS_DDL

    conn = sqlite3.connect(pred_db)
    conn.executescript(PREDICTIONS_DDL)
    conn.close()
    from finance_agent.execution.store import EXECUTION_DDL

    conn = sqlite3.connect(exec_db)
    conn.executescript(EXECUTION_DDL)
    conn.close()
    _seed_prediction(pred_db, pid="p-main", symbol="600519", lane="main")
    _seed_prediction(pred_db, pid="p-mc", symbol="002749", lane="microcap")

    from finance_agent.execution.driver import run_execution_daily

    summary = run_execution_daily(
        as_of="2026-01-06",
        db_path=pred_db,
        execution_db_path=exec_db,
        snapshot_db_path=tmp_path / "empty-snapshot.db",  # 空快照 → no_snapshot_data，
        regime_db_path=tmp_path / "empty-regime.db",  # 但信号段已可断言
    )
    from finance_agent.execution.store import list_signals

    ids = {s.decision_id for s in list_signals(db_path=exec_db)}
    assert "p-main" in ids and "p-mc" not in ids
    assert summary["skipped_reason"] == "no_snapshot_data"
