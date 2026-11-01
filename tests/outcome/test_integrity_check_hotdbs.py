"""integrity_check 热库页级校验单测（harden-sqlite-availability task 6，issue #515）。

契约（delta specs/eval-ops-console「Hot Database Page Integrity In integrity_check Job」）：
summary 带 per-database integrity 条目；任一库非 ok → job failed（异常上抛），
失败信息含库名与截断 detail；既有 predictions 快照哈希校验行为不变。

conftest `_isolate_runtime_dbs` 已把 SESSIONS_DB_PATH 指到 runtime-sessions.db
（自带 predictions schema）；本文件只覆写其余 7 库 env，sessions 侧沿用 runtime 库。
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

import pytest

from finance_agent.outcome.track_record.model import integrity_check

NON_SESSION_DB_ENV_KEYS = (
    "SCREENING_DB_PATH",
    "EXECUTION_DB_PATH",
    "EVENTS_DB_PATH",
    "REGIME_DB_PATH",
    "RESEARCH_REPORTS_DB_PATH",
    "FACTOR_LIBRARY_DB_PATH",
    "SNAPSHOT_DB_PATH",
)


def _non_session_env(tmp_path: Path, monkeypatch, *, corrupt: str | None = None) -> None:
    for key in NON_SESSION_DB_ENV_KEYS:
        p = tmp_path / f"{key.lower()}.db"
        if corrupt and key == corrupt:
            p.write_bytes(b"garbage not a database" * 16)
        else:
            conn = sqlite3.connect(p)
            conn.execute("CREATE TABLE IF NOT EXISTS t (x)")
            conn.commit()
            conn.close()
        monkeypatch.setenv(key, str(p))


def test_integrity_check_all_ok_reports_per_database(tmp_path, monkeypatch):
    _non_session_env(tmp_path, monkeypatch)
    result = integrity_check()
    assert set(result["databases"]) == {
        "sessions",
        "screening",
        "execution",
        "events",
        "regime",
        "research_reports",
        "factor_library",
        "market_snapshot",
    }
    assert all(v == "ok" for v in result["databases"].values())
    assert result["mismatch_count"] == 0


def test_integrity_check_corrupt_db_fails_loud_with_name_and_detail(tmp_path, monkeypatch):
    _non_session_env(tmp_path, monkeypatch, corrupt="REGIME_DB_PATH")
    with pytest.raises(RuntimeError) as excinfo:
        integrity_check()
    message = str(excinfo.value)
    assert "regime" in message
    assert "not a database" in message  # 截断 detail 进入失败信息


def test_integrity_check_keeps_snapshot_hash_behavior(tmp_path, monkeypatch):
    """既有行为保留：predictions 快照哈希不一致照旧记 mismatch（不因热库循环丢失）。"""
    _non_session_env(tmp_path, monkeypatch)
    from finance_agent.outcome.track_record import model as tr_model

    runtime_db = Path(os.environ["SESSIONS_DB_PATH"])
    # runtime 库只带 sessions/predictions 两表（conftest）；audit_log 由
    # init_track_record_tables 补齐（insert_prediction 内部 append_audit 依赖）
    tr_model.init_track_record_tables(runtime_db)
    prediction_id = tr_model.insert_prediction(
        {"source_type": "backtest", "symbol": "600519", "direction": "long"}, db_path=runtime_db
    )
    conn = sqlite3.connect(runtime_db)
    conn.execute(
        "UPDATE predictions SET snapshot_hash='bogus_hash' WHERE prediction_id=?", (prediction_id,)
    )
    conn.commit()
    conn.close()
    result = integrity_check()
    assert result["mismatch_count"] == 1
    assert result["mismatches"][0]["prediction_id"] == prediction_id
    assert result["databases"]["sessions"] == "ok"
