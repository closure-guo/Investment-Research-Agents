"""cohort 读数导出（delta add-forward-paper-trading-cohort §「Cohort 读数导出」）。

join 唯一键 = ``langfuse_trace_id``（``predictions`` 无 ``session_id`` 列）；
``since`` = ``trade_date`` 下界（复用 ``list_cohort_runs`` 语义）；导出只读。
"""

import json
import sqlite3
from pathlib import Path

import pytest
from evals.outcome.cohort_readings import collect_cohort_readings, main

from finance_agent.outcome.cohort.model import init_cohort_runs, insert_cohort_run
from finance_agent.outcome.track_record.model import init_track_record_tables


def _db(tmp_path: Path) -> Path:
    db = tmp_path / "sessions.db"
    init_track_record_tables(db)
    init_cohort_runs(db)
    return db


def _run(
    db: Path,
    *,
    ticker: str,
    version: str = "v1",
    trade_date: str = "2026-09-24",
    status: str = "success",
    trace: str | None = None,
    failure_reason: str | None = None,
    tokens_total: int | None = 100,
    llm_calls: int | None = 5,
    run_seq: int = 0,
) -> None:
    insert_cohort_run(
        {
            "universe_version": version,
            "ticker": ticker,
            "trade_date": trade_date,
            "status": status,
            "failure_reason": failure_reason,
            "run_seq": run_seq,
            "session_id": f"sess-{ticker}-{run_seq}",
            "langfuse_trace_id": trace,
            "llm_calls": llm_calls,
            "tokens_prompt": tokens_total,
            "tokens_completion": 0,
            "tokens_total": tokens_total,
            "trigger_time": f"{trade_date}T18:00:00",
        },
        db,
    )


def _pred(
    db: Path,
    *,
    symbol: str,
    trace: str | None,
    status: str = "resolved_win",
    direction: str = "long",
    avoidance_status: str | None = None,
    raw_return: float | None = 0.05,
    excess_return: float | None = 0.03,
    entry_price: float | None = 100.0,
    settle_entry_price: float | None = 100.0,
    confidence: float | None = 0.6,
    created_at: str = "2026-09-24T18:05:00",
    prediction_id: str | None = None,
) -> None:
    conn = sqlite3.connect(db)
    try:
        conn.execute(
            "INSERT INTO predictions (prediction_id, source_type, symbol, direction, entry_price,"
            " horizon_days, confidence, rationale_snapshot, langfuse_trace_id, status, created_at,"
            " updated_at, raw_return, excess_return, avoidance_status, settle_entry_price)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                prediction_id or f"p-{symbol}-{status}-{created_at}",
                "live",
                symbol,
                direction,
                entry_price,
                20,
                confidence,
                json.dumps({"action": "watch", "s": symbol}),
                trace,
                status,
                created_at,
                created_at,
                raw_return,
                excess_return,
                avoidance_status,
                settle_entry_price,
            ),
        )
        conn.commit()
    finally:
        conn.close()


def _snapshot(db: Path) -> tuple[list, list]:
    conn = sqlite3.connect(db)
    try:
        runs = [tuple(r) for r in conn.execute("SELECT * FROM cohort_runs ORDER BY run_id")]
        preds = [tuple(r) for r in conn.execute("SELECT * FROM predictions ORDER BY prediction_id")]
        return runs, preds
    finally:
        conn.close()


class TestBatchAndRows:
    def test_two_linked_success_runs_full_summary(self, tmp_path: Path):
        db = _db(tmp_path)
        _run(db, ticker="600519", trace="t1")
        _run(db, ticker="600520", trace="t2")
        _pred(db, symbol="600519", trace="t1", status="resolved_win", direction="long")
        _pred(
            db,
            symbol="600520",
            trace="t2",
            status="avoidance",
            direction="neutral",
            avoidance_status="avoidance_win",
            created_at="2026-09-24T18:06:00",
        )

        out = collect_cohort_readings(db)
        batch = out["batch"]
        assert batch["planned"] == 2
        assert batch["success"] == 2
        assert batch["failure"] == 0 and batch["skipped"] == 0
        assert batch["run_success_rate"] == 1.0
        assert batch["opinions_persisted"] == 2
        assert batch["unlinked"] == 0
        assert batch["unlinked_trace_missing"] == 0
        assert batch["unlinked_no_opinion"] == 0
        assert batch["settlement_status_counts"] == {"resolved_win": 1, "avoidance": 1}
        assert batch["failure_reasons"] == {}
        assert batch["skipped_reasons"] == {}
        assert batch["tokens_total_sum"] == 200
        assert batch["tokens_null_runs"] == 0
        assert batch["llm_calls_sum"] == 10
        assert batch["universe_versions"] == ["v1"]
        assert batch["trade_dates"] == ["2026-09-24"]

        assert len(out["rows"]) == 2
        first = out["rows"][0]
        assert set(first) == {
            "ticker",
            "created_at",
            "direction",
            "status",
            "avoidance_status",
            "raw_return",
            "excess_return",
            "entry_price",
            "settle_entry_price",
            "confidence",
            "trace_id",
        }
        assert [r["ticker"] for r in out["rows"]] == ["600519", "600520"]  # 按 created_at 排序
        assert out["rows"][1]["status"] == "avoidance"
        assert out["rows"][1]["avoidance_status"] == "avoidance_win"
        assert out["rows"][1]["trace_id"] == "t2"

    def test_unlinked_success_run_counted_not_dropped(self, tmp_path: Path):
        """success 但无对应观点：trace 缺失 / trace 无匹配 → unlinked（非静默丢弃）。"""
        db = _db(tmp_path)
        _run(db, ticker="600519", trace="t1")
        _run(db, ticker="600520", trace=None)  # trace 缺失（无法 join）
        _run(db, ticker="600521", trace="t9")  # trace 有值但无对应 predictions
        _pred(db, symbol="600519", trace="t1")

        out = collect_cohort_readings(db)
        batch = out["batch"]
        assert batch["success"] == 3
        assert batch["opinions_persisted"] == 1
        assert batch["unlinked"] == 2
        assert batch["unlinked_trace_missing"] == 1  # 其中 1 行 trace 为 NULL
        assert batch["unlinked_no_opinion"] == 1  # trace 有值但无观点行
        assert batch["unlinked_trace_missing"] + batch["unlinked_no_opinion"] == batch["unlinked"]
        assert batch["failure_reasons"]["unlinked"] == 2
        assert len(out["rows"]) == 1

    def test_filters_by_universe_version_and_since(self, tmp_path: Path):
        db = _db(tmp_path)
        _run(db, ticker="600519", version="v1", trade_date="2026-09-24", trace="t1")
        _run(db, ticker="600520", version="v1", trade_date="2026-09-20", trace="t2")
        _run(db, ticker="600521", version="v2", trade_date="2026-09-25", trace="t3")
        _pred(db, symbol="600519", trace="t1")
        _pred(db, symbol="600520", trace="t2", created_at="2026-09-20T18:05:00")
        _pred(db, symbol="600521", trace="t3", created_at="2026-09-25T18:05:00")

        out = collect_cohort_readings(db, universe_version="v1", since="2026-09-24")
        assert out["batch"]["planned"] == 1
        assert out["batch"]["success"] == 1
        assert out["batch"]["universe_versions"] == ["v1"]
        assert out["batch"]["trade_dates"] == ["2026-09-24"]
        assert [r["ticker"] for r in out["rows"]] == ["600519"]

        # 只按版本过滤
        v1 = collect_cohort_readings(db, universe_version="v1")
        assert v1["batch"]["planned"] == 2
        assert len(v1["rows"]) == 2

    def test_empty_db_zero_values_and_none_rate(self, tmp_path: Path):
        out = collect_cohort_readings(_db(tmp_path))
        batch = out["batch"]
        assert batch["planned"] == 0
        assert batch["success"] == 0 and batch["failure"] == 0 and batch["skipped"] == 0
        assert batch["run_success_rate"] is None  # 分母 0 → None（不得报 0%）
        assert batch["opinions_persisted"] == 0
        assert batch["unlinked"] == 0
        assert batch["unlinked_no_opinion"] == 0
        assert batch["settlement_status_counts"] == {}
        assert batch["failure_reasons"] == {}
        assert batch["skipped_reasons"] == {}
        assert batch["tokens_total_sum"] == 0
        assert batch["tokens_null_runs"] == 0
        assert batch["llm_calls_sum"] == 0
        assert out["rows"] == []

    def test_failure_and_skipped_reasons_split_and_rate_excludes_skipped(self, tmp_path: Path):
        """真失败码与 skipped 码分桶（budget 不得混读为失败）；skipped 不进成功率分母。"""
        db = _db(tmp_path)
        _run(db, ticker="600519", trace="t1")
        _run(db, ticker="600520", status="failure", failure_reason="no_report_ready", trace=None)
        _run(db, ticker="600521", status="failure", failure_reason="no_report_ready", trace=None)
        _run(db, ticker="600522", status="failure", failure_reason="exception", trace=None)
        _run(
            db,
            ticker="600523",
            status="skipped",
            failure_reason="budget",
            tokens_total=None,
            llm_calls=None,
        )
        _run(
            db,
            ticker="600524",
            status="skipped",
            failure_reason="budget_unknown",
            tokens_total=None,
            llm_calls=None,
        )

        out = collect_cohort_readings(db)
        batch = out["batch"]
        assert batch["planned"] == 6
        assert batch["success"] == 1
        assert batch["failure"] == 3
        assert batch["skipped"] == 2
        assert batch["run_success_rate"] == 0.25  # 1/(1+3)，skipped 非执行尝试不计分母
        assert batch["failure_reasons"] == {
            "no_report_ready": 2,
            "exception": 1,
            "unlinked": 1,  # success 但 trace 缺失
        }
        assert "budget" not in batch["failure_reasons"]
        assert "budget_unknown" not in batch["failure_reasons"]
        assert batch["skipped_reasons"] == {"budget": 1, "budget_unknown": 1}

    def test_tokens_null_runs_counted_for_attempted_only(self, tmp_path: Path):
        db = _db(tmp_path)
        _run(db, ticker="600519", trace="t1", tokens_total=500, llm_calls=7)
        _run(db, ticker="600520", trace="t2", tokens_total=None, llm_calls=3)
        _run(
            db,
            ticker="600521",
            status="skipped",
            failure_reason="budget",
            tokens_total=None,
            llm_calls=None,
        )
        _pred(db, symbol="600519", trace="t1")
        _pred(db, symbol="600520", trace="t2")

        out = collect_cohort_readings(db)
        batch = out["batch"]
        assert batch["tokens_null_runs"] == 1  # skipped 行不计（未执行）
        assert batch["tokens_total_sum"] == 500
        assert batch["llm_calls_sum"] == 10

    def test_export_is_read_only(self, tmp_path: Path):
        db = _db(tmp_path)
        _run(db, ticker="600519", trace="t1")
        _pred(db, symbol="600519", trace="t1")
        before = _snapshot(db)
        collect_cohort_readings(db)
        assert _snapshot(db) == before

    def test_default_db_path_from_env(self, tmp_path: Path, monkeypatch):
        db = _db(tmp_path)
        _run(db, ticker="600519", trace="t1")
        _pred(db, symbol="600519", trace="t1")
        monkeypatch.setenv("SESSIONS_DB_PATH", str(db))
        assert collect_cohort_readings(None)["batch"]["planned"] == 1

    def test_missing_db_raises(self, tmp_path: Path):
        with pytest.raises(FileNotFoundError):
            collect_cohort_readings(tmp_path / "nope.db")

    def test_missing_tables_treated_as_empty(self, tmp_path: Path):
        db = tmp_path / "bare.db"
        sqlite3.connect(db).close()  # 空文件（无表）→ 零值，不得抛
        out = collect_cohort_readings(db)
        assert out["batch"]["planned"] == 0
        assert out["batch"]["run_success_rate"] is None
        assert out["rows"] == []


class TestCli:
    def test_cli_json_output(self, tmp_path: Path, capsys):
        db = _db(tmp_path)
        _run(db, ticker="600519", trace="t1")
        _pred(db, symbol="600519", trace="t1")
        code = main(["--db", str(db), "--universe-version", "v1", "--since", "2026-09-24"])
        captured = capsys.readouterr()
        out = json.loads(captured.out)
        assert code == 0
        assert out["batch"]["success"] == 1 and out["batch"]["opinions_persisted"] == 1
        assert "\n  " in captured.out  # indent=2
        assert "600519" in captured.out  # ensure_ascii=False

    def test_cli_missing_db_exit_two(self, tmp_path: Path, capsys):
        assert main(["--db", str(tmp_path / "nope.db")]) == 2
        assert "DB 不存在" in capsys.readouterr().err
