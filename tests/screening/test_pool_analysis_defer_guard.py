"""池分析日批前置顺延护栏（add-pool-analysis-defer-guard，issue #578）。

行为契约（specs/stock-screening delta）：
- 前置 job 当日 job_runs 最新行 running → 按间隔顺延重查（主档默认 3 轮 / 小票档 8 轮）；
- 等待耗尽仍 running → 照常提交（gave_up_still_running，stale 降级路径不变）；
- 前置终态或当日无行 → 零等待立即提交（深夜补跑不空等）；
- AUTO_POOL_ANALYSIS_DEFER_ENABLED=0 → 行为与落地前一致（outcome=disabled）。
"""

from __future__ import annotations

from typing import Any

import pytest

from finance_agent.screening import pool_analysis


def _sequence_reader(sequence: list[str | None]):
    calls: list[tuple[str, str]] = []

    def reader(job_id: str, day: str) -> str | None:
        calls.append((job_id, day))
        if len(sequence) > 1:
            return sequence.pop(0)
        return sequence[0]

    reader.calls = calls  # type: ignore[attr-defined]
    return reader


class TestWaitForBatchPrerequisite:
    def test_running_then_ok_waits_then_fresh_ready(self):
        sleeps: list[float] = []
        reader = _sequence_reader(["running", "running", "ok"])
        out = pool_analysis.wait_for_batch_prerequisite(
            prerequisite_job_id="screening_daily",
            day="2026-11-12",
            interval_seconds=300,
            max_rounds=5,
            sleep=sleeps.append,
            status_reader=reader,
        )
        assert out == {
            "prerequisite": "screening_daily",
            "outcome": "fresh_ready",
            "rounds": 2,
            "waited_seconds": 600,
        }
        assert sleeps == [300, 300]
        assert reader.calls == [("screening_daily", "2026-11-12")] * 3

    def test_gives_up_when_still_running_after_max_rounds(self):
        reader = _sequence_reader(["running"])
        out = pool_analysis.wait_for_batch_prerequisite(
            prerequisite_job_id="microcap_screening_daily",
            day="2026-11-12",
            interval_seconds=300,
            max_rounds=8,
            sleep=lambda _: None,
            status_reader=reader,
        )
        assert out["outcome"] == "gave_up_still_running"
        assert out["rounds"] == 8
        assert out["waited_seconds"] == 2400

    def test_no_row_zero_wait_not_running(self):
        sleeps: list[float] = []
        reader = _sequence_reader([None])
        out = pool_analysis.wait_for_batch_prerequisite(
            prerequisite_job_id="screening_daily",
            day="2026-11-12",
            interval_seconds=300,
            max_rounds=3,
            sleep=sleeps.append,
            status_reader=reader,
        )
        assert out == {
            "prerequisite": "screening_daily",
            "outcome": "not_running",
            "rounds": 0,
            "waited_seconds": 0,
        }
        assert sleeps == []

    def test_terminal_ok_zero_wait(self):
        reader = _sequence_reader(["ok"])
        out = pool_analysis.wait_for_batch_prerequisite(
            prerequisite_job_id="screening_daily",
            day="2026-11-12",
            interval_seconds=300,
            max_rounds=3,
            sleep=lambda _: None,
            status_reader=reader,
        )
        assert out["outcome"] == "fresh_ready"
        assert out["waited_seconds"] == 0

    def test_terminal_failed_zero_wait(self):
        reader = _sequence_reader(["failed"])
        out = pool_analysis.wait_for_batch_prerequisite(
            prerequisite_job_id="screening_daily",
            day="2026-11-12",
            interval_seconds=300,
            max_rounds=3,
            sleep=lambda _: None,
            status_reader=reader,
        )
        assert out["outcome"] == "prereq_failed"
        assert out["waited_seconds"] == 0

    def test_reader_failure_fails_open_as_not_running(self):
        def broken_reader(job_id: str, day: str) -> str | None:
            raise RuntimeError("db busy")

        out = pool_analysis.wait_for_batch_prerequisite(
            prerequisite_job_id="screening_daily",
            day="2026-11-12",
            interval_seconds=300,
            max_rounds=3,
            sleep=lambda _: None,
            status_reader=broken_reader,
        )
        assert out["outcome"] == "not_running"
        assert out["waited_seconds"] == 0


class TestEntryWiring:
    def _patch_default_reader(self, monkeypatch: pytest.MonkeyPatch, sequence: list[str | None]):
        reader = _sequence_reader(list(sequence))
        monkeypatch.setattr(pool_analysis, "_read_prerequisite_status", reader)
        return reader

    def test_run_daily_waits_then_submits_with_defer_summary(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("AUTO_POOL_ANALYSIS_ENABLED", "1")
        self._patch_default_reader(monkeypatch, ["running", "ok"])
        sleeps: list[float] = []
        monkeypatch.setattr(pool_analysis.time, "sleep", sleeps.append)
        submit_calls: list[dict[str, Any]] = []

        def fake_submit(**kwargs: Any) -> dict[str, Any]:
            submit_calls.append(kwargs)
            return {"status": "ok"}

        monkeypatch.setattr(pool_analysis, "_submit", fake_submit)
        out = pool_analysis.run_pool_analysis_daily()
        assert submit_calls == [{"max_symbols": 10}]
        assert out["defer"] == {
            "prerequisite": "screening_daily",
            "outcome": "fresh_ready",
            "rounds": 1,
            "waited_seconds": 300,
        }
        assert sleeps == [300]

    def test_run_daily_disabled_guard_submits_immediately(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("AUTO_POOL_ANALYSIS_ENABLED", "1")
        monkeypatch.setenv("AUTO_POOL_ANALYSIS_DEFER_ENABLED", "0")
        reader = self._patch_default_reader(monkeypatch, ["running"])
        sleeps: list[float] = []
        monkeypatch.setattr(pool_analysis.time, "sleep", sleeps.append)
        monkeypatch.setattr(pool_analysis, "_submit", lambda **kwargs: {"status": "ok"})
        out = pool_analysis.run_pool_analysis_daily()
        assert out["defer"]["outcome"] == "disabled"
        assert out["defer"]["waited_seconds"] == 0
        assert sleeps == []
        assert reader.calls == []

    def test_run_microcap_uses_microcap_rounds_default(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("AUTO_POOL_MICROCAP_ENABLED", "1")
        monkeypatch.delenv("AUTO_POOL_ANALYSIS_DEFER_MAX_ROUNDS", raising=False)
        monkeypatch.delenv("AUTO_POOL_MICROCAP_DEFER_MAX_ROUNDS", raising=False)
        self._patch_default_reader(monkeypatch, ["running"] * 8 + ["ok"])
        sleeps: list[float] = []
        monkeypatch.setattr(pool_analysis.time, "sleep", sleeps.append)
        monkeypatch.setattr(pool_analysis, "_submit", lambda **kwargs: {"status": "ok"})
        out = pool_analysis.run_pool_analysis_microcap_daily()
        assert out["defer"]["prerequisite"] == "microcap_screening_daily"
        assert out["defer"]["outcome"] == "fresh_ready"
        assert out["defer"]["rounds"] == 8
        assert out["defer"]["waited_seconds"] == 2400
        assert sleeps == [300] * 8


class TestDeferConfigResolution:
    def test_main_defaults(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.delenv("AUTO_POOL_ANALYSIS_DEFER_ENABLED", raising=False)
        monkeypatch.delenv("AUTO_POOL_ANALYSIS_DEFER_INTERVAL_SECONDS", raising=False)
        monkeypatch.delenv("AUTO_POOL_ANALYSIS_DEFER_MAX_ROUNDS", raising=False)
        assert pool_analysis._resolve_defer_config(
            rounds_env="AUTO_POOL_ANALYSIS_DEFER_MAX_ROUNDS", default_rounds=3
        ) == (True, 300, 3)

    def test_microcap_defaults_and_overrides(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.delenv("AUTO_POOL_MICROCAP_DEFER_MAX_ROUNDS", raising=False)
        assert pool_analysis._resolve_defer_config(
            rounds_env="AUTO_POOL_MICROCAP_DEFER_MAX_ROUNDS", default_rounds=8
        ) == (True, 300, 8)
        monkeypatch.setenv("AUTO_POOL_ANALYSIS_DEFER_INTERVAL_SECONDS", "60")
        monkeypatch.setenv("AUTO_POOL_MICROCAP_DEFER_MAX_ROUNDS", "2")
        assert pool_analysis._resolve_defer_config(
            rounds_env="AUTO_POOL_MICROCAP_DEFER_MAX_ROUNDS", default_rounds=8
        ) == (True, 60, 2)

    def test_invalid_values_fall_back(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("AUTO_POOL_ANALYSIS_DEFER_INTERVAL_SECONDS", "abc")
        monkeypatch.setenv("AUTO_POOL_ANALYSIS_DEFER_MAX_ROUNDS", "-1")
        assert pool_analysis._resolve_defer_config(
            rounds_env="AUTO_POOL_ANALYSIS_DEFER_MAX_ROUNDS", default_rounds=3
        ) == (True, 300, 3)


class TestPrerequisiteStatusReader:
    def test_reads_latest_row_for_day(self, tmp_path):
        import sqlite3

        db = tmp_path / "sessions.db"
        conn = sqlite3.connect(db)
        conn.execute(
            "CREATE TABLE job_runs (run_id INTEGER PRIMARY KEY, job_id TEXT, kind TEXT,"
            " source TEXT, status TEXT, started_at TEXT, finished_at TEXT, summary TEXT,"
            " error TEXT)"
        )
        rows = [
            # 按时间序插入（run_id 自增 = 最新行 id 最大）：running 先落、ok 后落
            ("screening_daily", "scheduled", "scheduled", "running", "2026-11-12T15:40:00", None),
            ("screening_daily", "scheduled", "scheduled", "ok", "2026-11-12T16:21:00", None),
            ("other_job", "scheduled", "scheduled", "running", "2026-11-12T15:40:00", None),
            ("screening_daily", "scheduled", "scheduled", "ok", "2026-11-11T16:12:00", None),
        ]
        for job_id, kind, source, status, started, finished in rows:
            conn.execute(
                "INSERT INTO job_runs (job_id, kind, source, status, started_at, finished_at)"
                " VALUES (?,?,?,?,?,?)",
                (job_id, kind, source, status, started, finished),
            )
        conn.commit()
        conn.close()
        status = pool_analysis._read_prerequisite_status(
            "screening_daily", "2026-11-12", db_path=db
        )
        assert status == "ok"
        assert (
            pool_analysis._read_prerequisite_status("screening_daily", "2026-11-13", db_path=db)
            is None
        )
