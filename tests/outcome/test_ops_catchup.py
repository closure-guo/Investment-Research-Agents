"""错过的日批触发补偿：规划口径 / 上限 / 顺序 / 非交易日中止 / 失败不阻断。

delta add-ops-missed-run-catchup。规划是纯函数（注入 ``now`` 与状态读数），执行注入
``runner``——都不碰真实库、不碰真实调度器，因此可以逐例钉死「哪天几点该不该补」。
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from finance_agent.outcome.ops.catchup import (
    CATCH_UP_ALLOWLIST,
    CATCH_UP_MAX_JOBS,
    plan_catch_up,
    run_catch_up,
    scheduled_fire_time,
)
from finance_agent.outcome.ops.jobs import JOB_IDS, JobAlreadyRunning

TZ = ZoneInfo("Asia/Shanghai")

# 2026-10-22 是周四（工作日）；10-24 周六；11-01 周日（月批排程日）
THURSDAY = (2026, 10, 22)
SATURDAY = (2026, 10, 24)


def _at(day: tuple[int, int, int], hh: int, mm: int) -> datetime:
    return datetime(day[0], day[1], day[2], hh, mm, tzinfo=TZ)


class TestScheduledFireTime:
    def test_weekday_spec_returns_today_fire(self):
        spec = {"day_of_week": "mon-fri", "hour": 15, "minute": 45, "timezone": "Asia/Shanghai"}
        assert scheduled_fire_time(spec, _at(THURSDAY, 17, 29)) == _at(THURSDAY, 15, 45)

    def test_weekend_returns_none(self):
        spec = {"day_of_week": "mon-fri", "hour": 15, "minute": 45, "timezone": "Asia/Shanghai"}
        assert scheduled_fire_time(spec, _at(SATURDAY, 17, 0)) is None

    def test_star_spec_fires_every_day(self):
        spec = {"day_of_week": "*", "hour": 15, "minute": 55, "timezone": "Asia/Shanghai"}
        assert scheduled_fire_time(spec, _at(SATURDAY, 16, 0)) == _at(SATURDAY, 15, 55)

    def test_monthly_spec_only_on_its_day(self):
        spec = {"day_of_month": 1, "hour": 9, "minute": 0, "timezone": "Asia/Shanghai"}
        assert scheduled_fire_time(spec, _at((2026, 11, 1), 10, 0)) == _at((2026, 11, 1), 9, 0)
        assert scheduled_fire_time(spec, _at((2026, 11, 2), 10, 0)) is None

    def test_naive_now_is_interpreted_as_local(self):
        """注入 naive datetime 时按排程时区解释（不得因缺 tzinfo 抛错）。"""
        spec = {"day_of_week": "mon-fri", "hour": 15, "minute": 45, "timezone": "Asia/Shanghai"}
        naive = datetime(2026, 10, 22, 17, 29)
        assert scheduled_fire_time(spec, naive) == _at(THURSDAY, 15, 45)


class TestAllowlistBoundary:
    def test_allowlist_only_contains_registered_jobs(self):
        assert set(CATCH_UP_ALLOWLIST) <= set(JOB_IDS)

    @pytest.mark.parametrize(
        "job_id",
        [
            # 账务/下单类：宁可人工重放动账，不自动补
            "paper_execute_daily",
            "paper_settle_daily",
            "event_settle_daily",
            # LLM 烧钱类：夜间自愈不值这个钱
            "cohort_batch",
            "daily_brief_daily",
            "factor_harvest_daily",
            "earnings_triage_daily",
            "auto_pool_analysis_daily",
        ],
    )
    def test_risky_jobs_are_not_auto_replayed(self, job_id):
        assert job_id not in CATCH_UP_ALLOWLIST


class TestPlanCatchUp:
    def test_missed_within_window_is_planned_in_schedule_order(self):
        plan = plan_catch_up(now=_at(THURSDAY, 17, 29), statuses_of=lambda _jid: [])
        # 15:30 → 15:35 → 15:40（按计划时刻升序，不是白名单声明顺序）
        assert [item["job_id"] for item in plan] == [
            "market_snapshot_daily",
            "regime_daily",
            "screening_daily",
        ]
        assert plan[0]["late_seconds"] == 119 * 60  # 15:30 → 17:29

    def test_successful_run_is_not_replayed(self):
        statuses = {"regime_daily": ["ok"]}
        plan = plan_catch_up(
            now=_at(THURSDAY, 17, 29), statuses_of=lambda jid: statuses.get(jid, [])
        )
        assert "regime_daily" not in [item["job_id"] for item in plan]

    def test_failed_run_is_still_replayed(self):
        """失败与漏跑都要补：刚崩过的任务值得在启动时再试一次。"""
        statuses = {"market_snapshot_daily": ["failed"]}
        plan = plan_catch_up(
            now=_at(THURSDAY, 17, 29), statuses_of=lambda jid: statuses.get(jid, [])
        )
        assert plan[0]["job_id"] == "market_snapshot_daily"

    def test_running_row_is_skipped(self):
        """正在跑（别的进程/手动补跑）→ 不补，避免单飞锁冲突噪音。"""
        statuses = {"market_snapshot_daily": ["running"]}
        plan = plan_catch_up(
            now=_at(THURSDAY, 17, 29), statuses_of=lambda jid: statuses.get(jid, [])
        )
        assert "market_snapshot_daily" not in [item["job_id"] for item in plan]

    def test_outside_window_plans_nothing(self):
        # 15:45 + 6h = 21:45；23:59 已超窗
        plan = plan_catch_up(now=_at(THURSDAY, 23, 59), statuses_of=lambda _jid: [])
        assert plan == []

    def test_before_fire_time_plans_nothing(self):
        plan = plan_catch_up(now=_at(THURSDAY, 15, 0), statuses_of=lambda _jid: [])
        assert plan == []

    def test_weekend_plans_nothing(self):
        plan = plan_catch_up(now=_at(SATURDAY, 17, 0), statuses_of=lambda _jid: [])
        assert plan == []

    def test_window_boundary_is_inclusive(self):
        # 15:30 起算满 6h 是 21:30:00 —— 边界点仍算窗内
        plan = plan_catch_up(now=_at(THURSDAY, 21, 30), statuses_of=lambda _jid: [])
        assert plan[0]["job_id"] == "market_snapshot_daily"

    def test_cap_keeps_earliest(self):
        plan = plan_catch_up(now=_at(THURSDAY, 20, 0), statuses_of=lambda _jid: [])
        assert len(plan) == CATCH_UP_MAX_JOBS
        assert [item["job_id"] for item in plan] == [
            "market_snapshot_daily",
            "regime_daily",
            "screening_daily",
        ]

    def test_missing_schedule_entry_is_ignored(self):
        plan = plan_catch_up(
            now=_at(THURSDAY, 17, 29),
            statuses_of=lambda _jid: [],
            schedules={
                "market_snapshot_daily": {"hour": 15, "minute": 30, "day_of_week": "mon-fri"}
            },
        )
        assert [item["job_id"] for item in plan] == ["market_snapshot_daily"]


class TestRunCatchUp:
    def test_executes_planned_jobs_in_order(self):
        calls: list[str] = []

        def runner(job_id: str) -> dict[str, object]:
            calls.append(job_id)
            return {"status": "ok", "summary": {}, "run_id": len(calls)}

        result = run_catch_up(now=_at(THURSDAY, 17, 29), statuses_of=lambda _jid: [], runner=runner)
        assert calls == ["market_snapshot_daily", "regime_daily", "screening_daily"]
        assert result["aborted"] is None
        assert [item["status"] for item in result["executed"]] == ["ok", "ok", "ok"]
        assert [item["run_id"] for item in result["executed"]] == [1, 2, 3]

    def test_non_trading_day_aborts_the_rest_of_the_chain(self):
        calls: list[str] = []

        def runner(job_id: str) -> dict[str, object]:
            calls.append(job_id)
            return {"status": "ok", "summary": {"skipped": "non_trading_day"}, "run_id": 1}

        result = run_catch_up(now=_at(THURSDAY, 17, 29), statuses_of=lambda _jid: [], runner=runner)
        assert calls == ["market_snapshot_daily"]
        assert result["aborted"] == "non_trading_day"
        assert len(result["executed"]) == 1

    def test_skip_reason_other_than_non_trading_day_does_not_abort(self):
        def runner(job_id: str) -> dict[str, object]:
            if job_id == "market_snapshot_daily":
                return {"status": "ok", "summary": {"skipped": "stale_source"}, "run_id": 1}
            return {"status": "ok", "summary": {}, "run_id": 2}

        result = run_catch_up(now=_at(THURSDAY, 17, 29), statuses_of=lambda _jid: [], runner=runner)
        assert result["aborted"] is None
        assert len(result["executed"]) == 3

    def test_already_running_is_skipped_not_failed(self):
        def runner(_job_id: str) -> dict[str, object]:
            raise JobAlreadyRunning("已在运行（本次不排队）")

        result = run_catch_up(now=_at(THURSDAY, 17, 29), statuses_of=lambda _jid: [], runner=runner)
        assert [item["status"] for item in result["executed"]] == ["skipped_running"] * 3
        assert result["aborted"] is None

    def test_runner_exception_does_not_propagate_and_does_not_abort(self):
        def runner(job_id: str) -> dict[str, object]:
            if job_id == "market_snapshot_daily":
                raise RuntimeError("source down")
            return {"status": "ok", "summary": {}, "run_id": 2}

        result = run_catch_up(now=_at(THURSDAY, 17, 29), statuses_of=lambda _jid: [], runner=runner)
        first = result["executed"][0]
        assert first["status"] == "failed"
        assert "source down" in first["error"]
        assert [item["job_id"] for item in result["executed"]] == [
            "market_snapshot_daily",
            "regime_daily",
            "screening_daily",
        ]

    def test_empty_plan_touches_nothing(self):
        def runner(_job_id: str) -> dict[str, object]:
            raise AssertionError("空计划不得调用 runner")

        result = run_catch_up(now=_at(THURSDAY, 23, 59), statuses_of=lambda _jid: [], runner=runner)
        assert result == {"planned": [], "executed": [], "aborted": None}

    def test_planned_list_is_reported_for_audit(self):
        result = run_catch_up(
            now=_at(THURSDAY, 17, 29),
            statuses_of=lambda _jid: [],
            runner=lambda _jid: {"status": "ok", "summary": {}, "run_id": 0},
        )
        assert result["planned"] == [
            "market_snapshot_daily",
            "regime_daily",
            "screening_daily",
        ]


def test_window_is_six_hours():
    from finance_agent.outcome.ops import catchup

    assert catchup.CATCH_UP_WINDOW.total_seconds() == 6 * 3600
