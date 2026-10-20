"""每日 LLM 运营汇报测试（Task 4）。"""

from __future__ import annotations

import json
import sqlite3
from types import SimpleNamespace

import pytest

from finance_agent.outcome import daily_brief
from finance_agent.outcome.daily_brief import (
    collect_daily_facts,
    get_daily_brief,
    list_daily_briefs,
    run_daily_brief,
)
from finance_agent.outcome.track_record.model import init_predictions
from finance_agent.screening.pool_analysis import init_pool_analysis_runs
from finance_agent.screening.store import record_screening_batch

AS_OF = "2026-10-20"


def _seed_sessions(path) -> None:
    init_predictions(path)
    daily_brief.init_daily_briefs(path)
    con = sqlite3.connect(path)
    con.executescript(
        """
        CREATE TABLE IF NOT EXISTS sessions (
          session_id TEXT PRIMARY KEY, status TEXT, display_name TEXT
        );
        CREATE TABLE IF NOT EXISTS job_runs (
          run_id INTEGER PRIMARY KEY, job_id TEXT, status TEXT, error TEXT,
          started_at TEXT, finished_at TEXT
        );
        """
    )
    con.execute(
        "INSERT OR REPLACE INTO sessions (session_id, status, display_name) VALUES (?,?,?)",
        ("s1", "completed", "600519 自动选股分析"),
    )
    con.execute(
        "INSERT INTO predictions "
        "(prediction_id, source_type, symbol, direction, rationale_snapshot, status, "
        "created_at, resolved_at, raw_return, updated_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?)",
        (
            "p1",
            "live",
            "600519",
            "long",
            json.dumps({"action": "buy"}),
            "open",
            f"{AS_OF}T18:00:00",
            None,
            None,
            f"{AS_OF}T18:00:00",
        ),
    )
    con.execute(
        "INSERT INTO job_runs (job_id, status, error, started_at, finished_at) "
        "VALUES (?,?,?,?,?)",
        ("screening_daily", "failed", "快照缺失", f"{AS_OF}T15:40:00", f"{AS_OF}T15:41:00"),
    )
    con.commit()
    con.close()


def _seed_screening(path) -> None:
    record_screening_batch(
        AS_OF,
        status="ok",
        started_at=f"{AS_OF}T15:40:00",
        finished_at=f"{AS_OF}T16:09:00",
        l1_passed=100,
        l2_top=10,
        l3_keep=1,
        pool_rows=[
            {
                "symbol": "600519",
                "composite_score": 99.0,
                "rank": 1,
                "consecutive_days": 3,
                "l3_verdict": "keep",
                "l3_reason": "测试",
                "factor_contribs": [],
            }
        ],
        db_path=path,
    )
    init_pool_analysis_runs(path)
    con = sqlite3.connect(path)
    con.execute(
        "INSERT INTO pool_analysis_runs "
        "(trade_date, symbol, session_id, status, error, created_at, updated_at) "
        "VALUES (?,?,?,?,?,?,?)",
        (AS_OF, "600519", "s1", "queued", None, f"{AS_OF}T16:22:00", f"{AS_OF}T16:22:00"),
    )
    con.commit()
    con.close()


def _seed_execution(path) -> None:
    con = sqlite3.connect(path)
    con.executescript(
        """
        CREATE TABLE IF NOT EXISTS orders (
          order_id TEXT PRIMARY KEY, symbol TEXT, side TEXT, qty INTEGER,
          status TEXT, reason TEXT, fill_price REAL, fee REAL,
          decision_id TEXT, signal_id TEXT, created_at TEXT, attempts INTEGER
        );
        CREATE TABLE IF NOT EXISTS positions (
          symbol TEXT PRIMARY KEY, qty INTEGER, avg_cost REAL, updated_at TEXT
        );
        CREATE TABLE IF NOT EXISTS cash (id INTEGER PRIMARY KEY, balance REAL, updated_at TEXT);
        CREATE TABLE IF NOT EXISTS paper_equity (
          trade_date TEXT PRIMARY KEY, cash REAL, positions_value REAL, equity REAL
        );
        """
    )
    con.execute(
        "INSERT INTO orders (order_id, symbol, side, qty, status, created_at) "
        "VALUES (?,?,?,?,?,?)",
        ("o1", "600519", "buy", 100, "filled", f"{AS_OF}T16:10:00"),
    )
    con.execute(
        "INSERT INTO positions (symbol, qty, avg_cost, updated_at) VALUES (?,?,?,?)",
        ("600519", 100, 1000.0, f"{AS_OF}T16:20:00"),
    )
    con.execute("INSERT INTO cash (balance, updated_at) VALUES (?,?)", (900000.0, AS_OF))
    con.execute(
        "INSERT INTO paper_equity (trade_date, cash, positions_value, equity) VALUES (?,?,?,?)",
        (AS_OF, 900000.0, 100000.0, 1000000.0),
    )
    con.execute(
        "INSERT INTO paper_equity (trade_date, cash, positions_value, equity) VALUES (?,?,?,?)",
        ("2026-10-19", 1000000.0, 0.0, 1000000.0),
    )
    con.commit()
    con.close()


def _seed_factors(path) -> None:
    con = sqlite3.connect(path)
    con.executescript(
        """
        CREATE TABLE IF NOT EXISTS factors (
          id INTEGER PRIMARY KEY, expression TEXT, status TEXT
        );
        CREATE TABLE IF NOT EXISTS factor_ic (
          factor_id INTEGER, trade_date TEXT, rank_ic REAL
        );
        """
    )
    con.execute("INSERT INTO factors (id, expression, status) VALUES (1, 'x', 'active')")
    con.execute(
        "INSERT INTO factor_ic (factor_id, trade_date, rank_ic) VALUES (1, ?, 0.08)",
        (AS_OF,),
    )
    con.commit()
    con.close()


@pytest.fixture
def seeded_dbs(tmp_path):
    sessions = tmp_path / "sessions.db"
    screening = tmp_path / "screening.db"
    execution = tmp_path / "execution.db"
    factor = tmp_path / "factor.db"
    _seed_sessions(sessions)
    _seed_screening(screening)
    _seed_execution(execution)
    _seed_factors(factor)
    return {
        "sessions_db_path": sessions,
        "screening_db_path": screening,
        "execution_db_path": execution,
        "factor_db_path": factor,
    }


def test_collect_facts_includes_sections(seeded_dbs):
    facts = collect_daily_facts(AS_OF, **seeded_dbs)
    for key in ("screening", "predictions", "execution", "factors", "failures"):
        assert key in facts
    assert facts["screening"]["batch"]["status"] == "ok"
    assert facts["screening"]["pool_top"][0]["symbol"] == "600519"
    assert facts["screening"]["auto_analysis_runs"][0]["session_status"] == "completed"
    assert facts["predictions"]["created_today"] == 1
    assert facts["predictions"]["created_actions"] == {"buy": 1}
    assert facts["execution"]["orders_count"] == 1
    assert facts["execution"]["fills_count"] == 1
    assert facts["factors"]["ic_window_days"] == 1
    assert facts["failures"][0]["job_id"] == "screening_daily"


def test_collect_facts_degrades_on_missing_dbs(tmp_path):
    facts = collect_daily_facts(
        AS_OF,
        sessions_db_path=tmp_path / "missing-sessions.db",
        screening_db_path=tmp_path / "missing-screening.db",
        execution_db_path=tmp_path / "missing-execution.db",
        factor_db_path=tmp_path / "missing-factor.db",
    )
    assert facts["warnings"]
    assert facts["screening"] == {}
    assert facts["failures"] == []


def test_llm_success_persists_and_writes_file(seeded_dbs, tmp_path, monkeypatch):
    monkeypatch.setenv("REPORTS_DIR", str(tmp_path / "reports"))
    monkeypatch.setattr(
        daily_brief,
        "load_prompt_with_meta",
        lambda name: SimpleNamespace(template="{facts}", prompt_version=3),
    )
    calls = []

    def fake_complete_text(messages, **kwargs):
        calls.append(kwargs)
        return "# 日报\n\n## 今日结论\n内容", {"model": "openai/deepseek-v4.1-flash"}

    monkeypatch.setattr(daily_brief, "complete_text", fake_complete_text)
    out = run_daily_brief(AS_OF, **seeded_dbs)
    assert out["status"] == "ok"
    assert out["model"] == "openai/deepseek-v4.1-flash"
    assert calls[0]["purpose"] == "daily_brief"
    assert calls[0]["max_tokens"] == 4000
    row = get_daily_brief(AS_OF, db_path=seeded_dbs["sessions_db_path"])
    assert row is not None and row["status"] == "ok"
    assert row["prompt_version"] == "3"
    assert (tmp_path / "reports" / f"daily-brief-{AS_OF}.md").read_text(encoding="utf-8").startswith(
        "# 日报"
    )


def test_llm_failure_writes_fallback_and_raises(seeded_dbs, tmp_path, monkeypatch):
    monkeypatch.setenv("REPORTS_DIR", str(tmp_path / "reports"))
    monkeypatch.setattr(
        daily_brief,
        "load_prompt_with_meta",
        lambda name: SimpleNamespace(template="{facts}", prompt_version=3),
    )
    monkeypatch.setattr(
        daily_brief,
        "complete_text",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    with pytest.raises(RuntimeError, match="daily_brief_llm_failed"):
        run_daily_brief(AS_OF, **seeded_dbs)
    row = get_daily_brief(AS_OF, db_path=seeded_dbs["sessions_db_path"])
    assert row is not None
    assert row["status"] == "failed"
    assert "boom" in (row["error"] or "")
    assert "LLM 生成失败" in row["markdown"]
    assert (tmp_path / "reports" / f"daily-brief-{AS_OF}.md").exists()


def test_same_date_rerun_upserts(seeded_dbs, tmp_path, monkeypatch):
    monkeypatch.setenv("REPORTS_DIR", str(tmp_path / "reports"))
    monkeypatch.setattr(
        daily_brief,
        "load_prompt_with_meta",
        lambda name: SimpleNamespace(template="{facts}", prompt_version=3),
    )
    responses = iter([("# 第一版", {"model": "m"}), ("# 第二版", {"model": "m"})])
    monkeypatch.setattr(
        daily_brief, "complete_text", lambda *a, **k: next(responses)
    )
    run_daily_brief(AS_OF, **seeded_dbs)
    run_daily_brief(AS_OF, **seeded_dbs)
    rows = list_daily_briefs(db_path=seeded_dbs["sessions_db_path"])
    assert len(rows) == 1
    assert rows[0]["markdown"] == "# 第二版"
