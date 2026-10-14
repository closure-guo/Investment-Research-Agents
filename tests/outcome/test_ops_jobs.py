"""Task 2（delta add-eval-ops-console）：统一任务执行入口 ``run_job``。

契约：单飞锁（进程内，单 uvicorn worker 前提）+ ``job_runs`` 运行历史 + cohort
门控（关闭 → 零 LLM 调用、历史记 ``skipped-disabled``）；任务失败落 ``failed``
行并返回 dict，**不向调用方上抛**（旁路铁律）。
"""

from __future__ import annotations

import threading
import time

import pytest

from finance_agent.outcome.ops.jobs import (
    JOB_FUNCS,
    JOB_IDS,
    JOB_LABELS,
    SCHEDULES,
    CohortDisabled,
    JobAlreadyRunning,
    run_job,
)
from finance_agent.outcome.ops.model import (
    COHORT_ENABLED_KEY,
    init_ops,
    last_job_run,
    list_job_runs,
    set_config,
)


class TestRegistry:
    def test_ids_labels_funcs_schedules_aligned(self):
        assert set(JOB_IDS) == set(JOB_LABELS) == set(JOB_FUNCS) == set(SCHEDULES)
        assert (
            len(JOB_IDS) == 9
        )  # 6 → 8：+factor_decay_monitor(1) +paper_settle_daily(12)；8 → 9：+screening_daily
        assert all(JOB_LABELS[jid] for jid in JOB_IDS)


class TestScreeningDailyRegistration:
    """add-screening-batch-scheduling：选股日批接线（15:40，晚于快照 15:30）。"""

    def test_registered_aligned_and_ordered(self):
        assert "screening_daily" in JOB_IDS
        assert JOB_LABELS["screening_daily"] == "选股"
        assert set(JOB_IDS) == set(JOB_LABELS) == set(JOB_FUNCS) == set(SCHEDULES)
        assert len(JOB_IDS) == 9  # 8 → 9：+screening_daily（选股日批接线）
        assert (
            JOB_IDS.index("market_snapshot_daily")
            < JOB_IDS.index("screening_daily")
            < JOB_IDS.index("factor_decay_monitor")
        ), "展示顺序 = 时刻顺序（快照 15:30 → 选股 15:40 → 因子衰减 15:45）"

    def test_schedule_spec_is_1540_monfri(self):
        assert SCHEDULES["screening_daily"] == {
            "day_of_week": "mon-fri",
            "hour": 15,
            "minute": 40,
            "timezone": "Asia/Shanghai",
        }

    def test_daily_wrapper_raises_on_failed_batch(self, monkeypatch):
        """批次 failed（快照过期等）→ 任务上抛：run_job 记 failed，运维界面可见。"""
        import finance_agent.screening.pipeline as pipeline_mod

        monkeypatch.setattr(
            pipeline_mod,
            "run_screening_batch",
            lambda: {"trade_date": "2026-10-13", "status": "failed", "error": "快照过期"},
        )
        with pytest.raises(RuntimeError, match="快照过期"):
            pipeline_mod.run_screening_daily()

    def test_daily_wrapper_passes_through_ok_summary(self, monkeypatch):
        import finance_agent.screening.pipeline as pipeline_mod

        payload = {
            "trade_date": "2026-10-13",
            "status": "ok",
            "l1_passed": 10,
            "l2_top": 5,
            "l3_keep": 3,
            "pool_size": 3,
        }
        monkeypatch.setattr(pipeline_mod, "run_screening_batch", lambda: payload)
        assert pipeline_mod.run_screening_daily() == payload

    def test_paper_settle_schedule_spec_is_1620_monfri(self):
        """add-paper-broker T2：paper 结算日批 16:20（mon-fri，Asia/Shanghai）逐字钉死。"""
        assert SCHEDULES["paper_settle_daily"] == {
            "day_of_week": "mon-fri",
            "hour": 16,
            "minute": 20,
            "timezone": "Asia/Shanghai",
        }
        assert JOB_LABELS["paper_settle_daily"] == "paper 结算"

    def test_market_snapshot_schedule_spec_is_1530_monfri(self):
        """add-market-snapshot：行情快照日批 15:30（mon-fri，Asia/Shanghai）逐字钉死。"""
        assert SCHEDULES["market_snapshot_daily"] == {
            "day_of_week": "mon-fri",
            "hour": 15,
            "minute": 30,
            "timezone": "Asia/Shanghai",
        }
        assert JOB_IDS[0] == "market_snapshot_daily"  # 展示顺序 = 时刻顺序（15:30 最早）
        assert JOB_LABELS["market_snapshot_daily"] == "行情快照"

    def test_settlement_chain_schedules_unchanged(self):
        """结算链四任务时刻固定（16:00/16:30/16:35/16:40，mon-fri，Asia/Shanghai）。"""
        expected = {
            "decision_settle_daily": (16, 0),
            "daily_marking": (16, 30),
            "metrics_snapshot": (16, 35),
            "integrity_check": (16, 40),
        }
        for job_id, (hour, minute) in expected.items():
            spec = SCHEDULES[job_id]
            assert (spec["hour"], spec["minute"]) == (hour, minute)
            assert spec["day_of_week"] == "mon-fri"
            assert spec["timezone"] == "Asia/Shanghai"

    def test_cohort_schedule_spec_is_wellformed(self):
        """cohort 项在 start_scheduler/reschedule_cohort 时被配置覆写，规格键齐备。"""
        spec = SCHEDULES["cohort_batch"]
        assert spec["day_of_week"] == "mon-fri" and spec["timezone"] == "Asia/Shanghai"
        assert 0 <= spec["hour"] <= 23 and 0 <= spec["minute"] <= 59


class TestRunJobLifecycle:
    def test_run_job_records_ok_with_summary(self, tmp_path, monkeypatch):
        monkeypatch.setitem(JOB_FUNCS, "integrity_check", lambda: {"issues": 0})
        out = run_job("integrity_check", db_path=tmp_path / "o.db")
        assert out["status"] == "ok" and out["summary"] == {"issues": 0}
        row = last_job_run("integrity_check", tmp_path / "o.db")
        assert row["run_id"] == out["run_id"]
        assert row["status"] == "ok" and row["summary"] == {"issues": 0}
        assert row["kind"] == "manual" and row["source"] == "manual"
        assert row["finished_at"] is not None

    def test_run_job_records_failed_with_error(self, tmp_path, monkeypatch):
        def boom():
            raise RuntimeError("db locked")

        monkeypatch.setitem(JOB_FUNCS, "integrity_check", boom)
        out = run_job("integrity_check", db_path=tmp_path / "o.db")
        assert out["status"] == "failed" and "db locked" in out["error"]
        row = last_job_run("integrity_check", tmp_path / "o.db")
        assert row["status"] == "failed" and "db locked" in row["error"]

    def test_non_dict_result_is_wrapped(self, tmp_path, monkeypatch):
        monkeypatch.setitem(JOB_FUNCS, "metrics_snapshot", lambda: "2026-09-24")
        out = run_job("metrics_snapshot", db_path=tmp_path / "o.db")
        assert out["status"] == "ok" and out["summary"] == {"result": "2026-09-24"}

    def test_source_scheduled_maps_to_kind_scheduled(self, tmp_path, monkeypatch):
        monkeypatch.setitem(JOB_FUNCS, "integrity_check", lambda: {"issues": 0})
        out = run_job("integrity_check", source="scheduled", db_path=tmp_path / "o.db")
        row = next(r for r in list_job_runs("integrity_check", db_path=tmp_path / "o.db"))
        assert row["run_id"] == out["run_id"]
        assert row["kind"] == "scheduled" and row["source"] == "scheduled"

    def test_func_override_wins_over_registry(self, tmp_path, monkeypatch):
        """scheduler 传入的模块级名字是调用期的打桩缝（不得被注册表静态引用覆盖）。"""
        monkeypatch.setitem(JOB_FUNCS, "integrity_check", lambda: {"from": "registry"})
        out = run_job(
            "integrity_check",
            source="scheduled",
            db_path=tmp_path / "o.db",
            func=lambda: {"from": "override"},
        )
        assert out["summary"] == {"from": "override"}

    def test_unknown_job_id_raises(self, tmp_path):
        with pytest.raises(ValueError):
            run_job("nope", db_path=tmp_path / "o.db")
        assert list_job_runs(db_path=tmp_path / "o.db") == []


class TestSingleFlight:
    def test_run_job_is_single_flight(self, tmp_path, monkeypatch):
        gate = threading.Event()

        def slow():
            gate.wait(2)
            return {"ok": True}

        monkeypatch.setitem(JOB_FUNCS, "integrity_check", slow)
        t = threading.Thread(
            target=run_job,
            args=("integrity_check",),
            kwargs={"db_path": tmp_path / "o.db"},
        )
        t.start()
        time.sleep(0.2)
        try:
            with pytest.raises(JobAlreadyRunning):
                run_job("integrity_check", db_path=tmp_path / "o.db")
        finally:
            gate.set()
            t.join()
        assert len(list_job_runs("integrity_check", db_path=tmp_path / "o.db")) == 1

    def test_lock_released_after_failure(self, tmp_path, monkeypatch):
        """失败也必须释放锁——否则调度器重试第 2 次会撞 JobAlreadyRunning。"""

        def boom():
            raise RuntimeError("boom")

        monkeypatch.setitem(JOB_FUNCS, "integrity_check", boom)
        first = run_job("integrity_check", db_path=tmp_path / "o.db")
        second = run_job("integrity_check", db_path=tmp_path / "o.db")
        assert first["status"] == second["status"] == "failed"
        assert first["run_id"] != second["run_id"]

    def test_lock_released_when_history_insert_fails(self, tmp_path, monkeypatch):
        """历史开行失败也必须释放锁——否则该 job 在本进程内被永久占用（手动补跑永远 409）。

        语义：历史层故障不伪装成任务失败（上抛，scheduler 按失败尝试重试），但锁
        必须在 finally 里释放。
        """
        import finance_agent.outcome.ops.jobs as jobs_mod

        db = tmp_path / "o.db"
        real_insert = jobs_mod.insert_job_run
        state = {"n": 0}

        def flaky_insert(*args, **kwargs):
            state["n"] += 1
            if state["n"] == 1:
                raise RuntimeError("db locked")
            return real_insert(*args, **kwargs)

        monkeypatch.setattr(jobs_mod, "insert_job_run", flaky_insert)
        monkeypatch.setitem(JOB_FUNCS, "integrity_check", lambda: {"issues": 0})

        with pytest.raises(RuntimeError, match="db locked"):
            run_job("integrity_check", db_path=db)
        second = run_job("integrity_check", db_path=db)
        assert second["status"] == "ok", "锁必须已释放"
        assert len(list_job_runs("integrity_check", db_path=db)) == 1

    def test_lock_is_per_job(self, tmp_path, monkeypatch):
        """不同 job 各有一把锁，互不阻塞。"""
        gate = threading.Event()
        monkeypatch.setitem(JOB_FUNCS, "integrity_check", lambda: gate.wait(2) or {"ok": True})
        monkeypatch.setitem(JOB_FUNCS, "metrics_snapshot", lambda: {"date": "2026-09-24"})
        t = threading.Thread(
            target=run_job,
            args=("integrity_check",),
            kwargs={"db_path": tmp_path / "o.db"},
        )
        t.start()
        time.sleep(0.2)
        try:
            assert run_job("metrics_snapshot", db_path=tmp_path / "o.db")["status"] == "ok"
        finally:
            gate.set()
            t.join()


class TestCohortGating:
    def test_cohort_manual_run_refused_when_disabled(self, tmp_path, monkeypatch):
        monkeypatch.delenv("COHORT_ENABLED", raising=False)
        with pytest.raises(CohortDisabled):
            run_job("cohort_batch", db_path=tmp_path / "o.db")  # 默认关
        row = last_job_run("cohort_batch", tmp_path / "o.db")
        assert row["status"] == "skipped-disabled"
        assert row["summary"] == {"reason": "switch_off"}
        assert row["finished_at"] is not None

    def test_cohort_manual_run_not_refused_when_enabled(self, tmp_path, monkeypatch):
        monkeypatch.setitem(JOB_FUNCS, "cohort_batch", lambda: {"success": 0})
        init_ops(tmp_path / "o.db")
        set_config(COHORT_ENABLED_KEY, "1", tmp_path / "o.db")
        out = run_job("cohort_batch", db_path=tmp_path / "o.db")
        assert out["status"] == "ok"

    def test_cohort_scheduled_disabled_records_skip_without_raising(self, tmp_path, monkeypatch):
        """定时触发：关闭只在历史留痕、不抛（无人等待结果），且零调用。"""
        monkeypatch.delenv("COHORT_ENABLED", raising=False)
        called: list[bool] = []
        monkeypatch.setitem(JOB_FUNCS, "cohort_batch", lambda: called.append(True) or {})
        out = run_job("cohort_batch", source="scheduled", db_path=tmp_path / "o.db")
        assert out["status"] == "skipped-disabled"
        assert called == [], "开关关闭不得调用 runner（零 LLM 调用）"
        row = last_job_run("cohort_batch", tmp_path / "o.db")
        assert row["status"] == "skipped-disabled" and row["kind"] == "scheduled"

    def test_cohort_scheduled_enabled_calls_runner(self, tmp_path, monkeypatch):
        init_ops(tmp_path / "o.db")
        set_config(COHORT_ENABLED_KEY, "1", tmp_path / "o.db")
        monkeypatch.setitem(JOB_FUNCS, "cohort_batch", lambda: {"enabled": True, "success": 1})
        out = run_job("cohort_batch", source="scheduled", db_path=tmp_path / "o.db")
        assert out["status"] == "ok" and out["summary"]["success"] == 1
