"""悬挂 running 行清扫 × 启动补跑次序（issue #455）。

进程被杀时运行行永远停在 ``running``；清扫此前只在有人读状态接口时懒执行，晚于
启动补跑规划（启动后 5s），导致「跑了一半被杀」的任务被规划的 running-skip 规则
排除、6h 窗内无人接管（2026-10-23 两次重启两度实证）。本组测试钉死：

1. 清扫函数独立可用（供补跑前置调用），僵尸行 → ``failed(stale_process_restart)``；
2. 所有权（在跑集合）与时钟（早于进程启动）双判据仍在——本进程开出的行不误杀；
3. 清扫后规划立即接管该任务（failed 不拦补跑）；
4. ``catch_up_job`` 先清扫后规划，前置清扫异常不阻断补跑。
"""

from __future__ import annotations

import sqlite3
from datetime import date, datetime
from zoneinfo import ZoneInfo

from finance_agent.outcome.ops import catchup as catchup_module
from finance_agent.outcome.ops.catchup import catch_up_job, plan_catch_up
from finance_agent.outcome.ops.jobs import (
    mark_run_active,
    sweep_stale_running,
)
from finance_agent.outcome.ops.model import (
    insert_job_run,
    job_run_statuses_on,
    list_job_runs,
)

TZ = ZoneInfo("Asia/Shanghai")
# 周末无关的排程（scheduled_fire_time 对 "*" 每天触发），避免 CI 时历敏依赖
_SCHEDULE = {"day_of_week": "*", "hour": 15, "minute": 40, "timezone": "Asia/Shanghai"}


def _today() -> str:
    return date.today().isoformat()


def _today_at(hh: int, mm: int) -> datetime:
    d = date.today()
    return datetime(d.year, d.month, d.day, hh, mm, tzinfo=TZ)


def _backdate(db, run_id: int) -> None:
    """把运行行改成「今天凌晨」——生产中僵尸行就是当天被杀进程留下的：既算当天行
    （挡补跑规划），又早于本进程启动（被清扫的时钟判据命中）。"""
    con = sqlite3.connect(db)
    con.execute(
        "UPDATE job_runs SET started_at = ? WHERE run_id = ?",
        (f"{date.today().isoformat()}T00:00:01.000000", run_id),
    )
    con.commit()
    con.close()


class TestSweepStaleRunning:
    def test_zombie_row_swept_to_failed(self, tmp_path):
        db = tmp_path / "ops.db"
        run_id = insert_job_run("screening_daily", "scheduled", "running", db_path=db)
        _backdate(db, run_id)

        swept = sweep_stale_running(db_path=db)

        assert swept == [run_id]
        row = list_job_runs("screening_daily", db_path=db)[0]
        assert row["status"] == "failed"
        assert "stale_process_restart" in str(row["error"])

    def test_active_row_protected_by_ownership(self, tmp_path):
        db = tmp_path / "ops.db"
        run_id = insert_job_run("screening_daily", "scheduled", "running", db_path=db)
        _backdate(db, run_id)
        mark_run_active(run_id, db_path=db)

        swept = sweep_stale_running(db_path=db)

        assert swept == []
        assert list_job_runs("screening_daily", db_path=db)[0]["status"] == "running"

    def test_fresh_row_protected_by_clock(self, tmp_path):
        db = tmp_path / "ops.db"
        insert_job_run("screening_daily", "scheduled", "running", db_path=db)

        swept = sweep_stale_running(db_path=db)

        assert swept == []
        assert list_job_runs("screening_daily", db_path=db)[0]["status"] == "running"

    def test_sweep_failure_returns_empty(self, tmp_path, monkeypatch):
        def boom(*args, **kwargs):
            raise RuntimeError("db gone")

        monkeypatch.setattr("finance_agent.outcome.ops.jobs.list_job_runs", boom)
        assert sweep_stale_running(db_path=tmp_path / "ops.db") == []


class TestSweepUnblocksCatchup:
    def test_zombie_swept_then_plan_takes_over(self, tmp_path):
        db = tmp_path / "ops.db"
        run_id = insert_job_run("screening_daily", "scheduled", "running", db_path=db)
        _backdate(db, run_id)

        # 清扫前：running 行把任务挡在补跑计划外
        plan_before = plan_catch_up(
            now=_today_at(16, 30),
            statuses_of=lambda j: job_run_statuses_on(j, _today(), db_path=db),
            schedules={"screening_daily": _SCHEDULE},
            allowlist=["screening_daily"],
        )
        assert plan_before == []

        sweep_stale_running(db_path=db)

        plan_after = plan_catch_up(
            now=_today_at(16, 30),
            statuses_of=lambda j: job_run_statuses_on(j, _today(), db_path=db),
            schedules={"screening_daily": _SCHEDULE},
            allowlist=["screening_daily"],
        )
        assert [item["job_id"] for item in plan_after] == ["screening_daily"]


class TestCatchUpJobOrder:
    def test_sweep_runs_before_planning(self, monkeypatch):
        calls: list[str] = []
        monkeypatch.setattr(
            catchup_module, "sweep_stale_running", lambda **kw: calls.append("sweep")
        )
        monkeypatch.setattr(
            catchup_module,
            "run_catch_up",
            lambda **kw: calls.append("plan") or {"planned": [], "executed": [], "aborted": None},
        )
        catch_up_job()
        assert calls == ["sweep", "plan"]

    def test_sweep_failure_does_not_block_catchup(self, monkeypatch):
        def boom(**kwargs):
            raise RuntimeError("sweep broken")

        monkeypatch.setattr(catchup_module, "sweep_stale_running", boom)
        monkeypatch.setattr(
            catchup_module,
            "run_catch_up",
            lambda **kw: {"planned": ["screening_daily"], "executed": [], "aborted": None},
        )
        outcome = catch_up_job()
        assert outcome["planned"] == ["screening_daily"]
