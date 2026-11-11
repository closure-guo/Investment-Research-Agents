"""小票执行/结算封装（add-microcap-lane-analysis T4）。"""

import sqlite3

from finance_agent.execution.brokers.paper import run_paper_settle_microcap
from finance_agent.execution.driver import run_execution_microcap
from finance_agent.screening.constraints import (
    MICROCAP_SLIPPAGE_TIER_BUMP,
    MICROCAP_SUGGESTED_SINGLE_CAP,
)


def test_constants_wired():
    assert MICROCAP_SUGGESTED_SINGLE_CAP == 0.05
    assert MICROCAP_SLIPPAGE_TIER_BUMP == 1


def test_microcap_runner_writes_separate_db(tmp_path, monkeypatch):
    pred_db = tmp_path / "pred.db"
    exec_db = tmp_path / "exec-mc.db"
    from finance_agent.execution.store import EXECUTION_DDL
    from finance_agent.outcome.track_record.model import PREDICTIONS_DDL

    conn = sqlite3.connect(pred_db)
    conn.executescript(PREDICTIONS_DDL)
    # confidence 显式造 0.8（同 T3 fixture 口径）：信号契约 _coerce_strength(None)
    # 拒收 → 缺 confidence 的观点不会生成信号
    conn.execute(
        "INSERT INTO predictions (prediction_id, source_type, symbol, direction,"
        " confidence, rationale_snapshot, status, created_at, updated_at, lane)"
        " VALUES ('p-mc-1', 'live', '002749', 'long', 0.8,"
        ' \'{"fund_manager_decision":"approve","action":"buy"}\', \'open\','
        " '2026-01-05T17:00:00', '2026-01-05T17:00:00', 'microcap')"
    )
    conn.commit()
    conn.close()
    conn = sqlite3.connect(exec_db)
    conn.executescript(EXECUTION_DDL)
    conn.close()

    monkeypatch.setenv("EXECUTION_MICROCAP_DB_PATH", str(exec_db))
    summary = run_execution_microcap(
        as_of="2026-01-06",
        db_path=pred_db,
        snapshot_db_path=tmp_path / "empty-snapshot.db",
        regime_db_path=tmp_path / "empty-regime.db",
    )
    assert summary["skipped_reason"] == "no_snapshot_data"
    from finance_agent.execution.store import list_signals

    assert {s.decision_id for s in list_signals(db_path=exec_db)} == {"p-mc-1"}


def test_microcap_settle_empty_account(tmp_path, monkeypatch):
    from finance_agent.execution.store import EXECUTION_DDL

    exec_db = tmp_path / "exec-mc.db"
    conn = sqlite3.connect(exec_db)
    conn.executescript(EXECUTION_DDL)
    conn.close()
    monkeypatch.setenv("EXECUTION_MICROCAP_DB_PATH", str(exec_db))
    # 快照库一并隔离（同 run_paper_settle_daily 空快照用例先例）：不读开发库
    monkeypatch.setenv("SNAPSHOT_DB_PATH", str(tmp_path / "empty-snapshot.db"))
    result = run_paper_settle_microcap()
    # 空快照 → no_snapshot_data 旁路；不报错
    assert result["reason"] == "no_snapshot_data"
