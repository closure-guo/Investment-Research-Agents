"""job_run_statuses_on：按天读运行状态（delta add-ops-missed-run-catchup Task 1）。

``missed_today``（只管「有没有行」）与启动补跑规划（看「有没有成功行」）共用本读数
——一处口径，两处消费。
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest

from finance_agent.outcome.ops.model import (
    finish_job_run,
    insert_job_run,
    job_run_statuses_on,
)


def _today() -> str:
    return date.today().isoformat()


def test_returns_statuses_of_today_in_start_order(tmp_path):
    db = tmp_path / "ops.db"
    first = insert_job_run("integrity_check", "scheduled", "running", db_path=db)
    finish_job_run(first, status="failed", db_path=db)
    second = insert_job_run("integrity_check", "manual", "running", db_path=db)
    finish_job_run(second, status="ok", db_path=db)
    assert job_run_statuses_on("integrity_check", _today(), db_path=db) == ["failed", "ok"]


def test_other_day_returns_empty(tmp_path):
    db = tmp_path / "ops.db"
    run_id = insert_job_run("integrity_check", "scheduled", "running", db_path=db)
    finish_job_run(run_id, status="ok", db_path=db)
    assert job_run_statuses_on("integrity_check", "2000-01-01", db_path=db) == []


def test_other_job_returns_empty(tmp_path):
    db = tmp_path / "ops.db"
    run_id = insert_job_run("integrity_check", "scheduled", "running", db_path=db)
    finish_job_run(run_id, status="ok", db_path=db)
    assert job_run_statuses_on("daily_marking", _today(), db_path=db) == []


def test_unknown_job_or_empty_library_returns_empty(tmp_path):
    assert job_run_statuses_on("integrity_check", _today(), db_path=tmp_path / "ops.db") == []


@pytest.mark.parametrize("bad", ["2026/10/23", "2026-10-23T00:00:00", "20261023", ""])
def test_non_canonical_day_raises(tmp_path, bad):
    with pytest.raises(ValueError):
        job_run_statuses_on("integrity_check", bad, db_path=tmp_path / "ops.db")


def test_day_boundary_is_inclusive_of_that_day_only(tmp_path):
    """昨天的行不能算进今天（ISO 字符串比较的边界守卫）。"""
    db = tmp_path / "ops.db"
    run_id = insert_job_run("integrity_check", "scheduled", "running", db_path=db)
    finish_job_run(run_id, status="ok", db_path=db)
    yesterday = (datetime.now() - timedelta(days=1)).date().isoformat()
    assert job_run_statuses_on("integrity_check", yesterday, db_path=db) == []
