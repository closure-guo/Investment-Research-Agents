"""scheduler 挂载:TESTING/env 禁用、cron 注册、启停、job 异常不传播、失败重试。

Task 2（delta add-eval-ops-console）新增:定时批经 ``ops.jobs.run_job`` 统一入口
落运行历史（每次尝试一行 + 最终行记 retries）、cohort 时刻配置化、运行时重排。
"""

import logging
import os
from unittest.mock import MagicMock, patch

import pytest

from finance_agent.outcome.ops.jobs import JobAlreadyRunning
from finance_agent.outcome.ops.model import (
    COHORT_ENABLED_KEY,
    COHORT_HOUR_KEY,
    COHORT_MINUTE_KEY,
    init_ops,
    last_job_run,
    list_job_runs,
    set_config,
)
from finance_agent.outcome.scheduler import (
    get_scheduler,
    reschedule_cohort,
    start_scheduler,
    stop_scheduler,
)

_SCHED_LOGGER = "finance_agent.outcome.scheduler"


@pytest.fixture(autouse=True)
def _reset_scheduler_handle(monkeypatch):
    """Task 2:start_scheduler 会登记进程内句柄——逐用例从 None 起步,防用例间串味。"""
    monkeypatch.setattr("finance_agent.outcome.scheduler._scheduler", None)


def _job_call(sched, job_id):
    """按 id 取 add_job 调用(含 args/kwargs),未注册则 AssertionError。"""
    for call in sched.add_job.call_args_list:
        if call.kwargs.get("id") == job_id:
            return call
    registered = [c.kwargs.get("id") for c in sched.add_job.call_args_list]
    raise AssertionError(f"{job_id} job 未注册(已注册: {registered})")


def _job_fn(sched, job_id):
    """按 id 取 job 任务函数。"""
    return _job_call(sched, job_id).args[0]


def _job_trigger(sched, job_id):
    """按 id 取 CronTrigger 对象（字段断言用，不锁 __str__ 形态）。"""
    call = _job_call(sched, job_id)
    return call.args[1] if len(call.args) > 1 else call.kwargs.get("trigger")


def _job_field(sched, job_id, name) -> list[str]:
    """按 id 取 CronTrigger 指定字段的表达式（每项有 .name / .expressions）。"""
    for field in _job_trigger(sched, job_id).fields:
        if field.name == name:
            return [str(expr) for expr in field.expressions]
    raise AssertionError(f"{job_id} trigger 无字段 {name}")


class TestStartGating:
    @patch.dict(os.environ, {"TESTING": "1"})
    def test_testing_disables(self):
        assert start_scheduler() is None

    @patch.dict(os.environ, {"DECISION_SETTLE_ENABLED": "0", "TESTING": ""})
    def test_env_disables(self):
        assert start_scheduler() is None

    @patch.dict(os.environ, {"TESTING": "", "DECISION_SETTLE_ENABLED": "1"})
    @patch("finance_agent.outcome.scheduler.BackgroundScheduler")
    def test_registers_weekday_1600_cron(self, mock_sched_cls):
        sched = MagicMock()
        mock_sched_cls.return_value = sched
        result = start_scheduler()
        assert result is sched
        # Δ3T5：job 数增至 5，末次 add_job 不再是 settle——按 id 取，断言不变
        # CronTrigger: 工作日 16:00（按字段断言，不锁 __str__ 形态）
        assert _job_field(sched, "decision_settle_daily", "hour") == ["16"]
        assert _job_field(sched, "decision_settle_daily", "minute") == ["0"]
        assert "mon-fri" in _job_field(sched, "decision_settle_daily", "day_of_week")
        sched.start.assert_called_once()

    @patch.dict(os.environ, {"TESTING": ""})
    @patch("finance_agent.outcome.scheduler.BackgroundScheduler")
    def test_enabled_by_default_when_env_unset(self, mock_sched_cls):
        """DECISION_SETTLE_ENABLED 未设置且非 TESTING 时默认启用,返回 scheduler。"""
        os.environ.pop("DECISION_SETTLE_ENABLED", None)
        sched = MagicMock()
        mock_sched_cls.return_value = sched
        result = start_scheduler()
        assert result is sched
        sched.start.assert_called_once()


class TestStop:
    def test_stop_none_safe(self):
        stop_scheduler(None)  # 不抛异常

    def test_stop_calls_shutdown(self):
        sched = MagicMock()
        stop_scheduler(sched)
        sched.shutdown.assert_called_once_with(wait=False)


class TestJobIsolation:
    @patch.dict(os.environ, {"TESTING": "", "DECISION_SETTLE_ENABLED": "1"})
    @patch("finance_agent.outcome.scheduler.BackgroundScheduler")
    @patch("finance_agent.outcome.scheduler.time.sleep")
    def test_job_wrapper_swallows_exceptions(self, mock_sleep, mock_sched_cls):
        """job 内部异常不传播到 scheduler(旁路铁律);sleep 打桩避免真实退避等待。"""
        sched = MagicMock()
        mock_sched_cls.return_value = sched
        start_scheduler()
        job_fn = _job_fn(sched, "decision_settle_daily")
        with patch(
            "finance_agent.outcome.scheduler.settle_open_predictions",
            side_effect=RuntimeError("boom"),
        ):
            job_fn()  # 不抛异常


class TestJobRetry:
    @patch.dict(os.environ, {"TESTING": "", "DECISION_SETTLE_ENABLED": "1"})
    @patch("finance_agent.outcome.scheduler.BackgroundScheduler")
    @patch("finance_agent.outcome.scheduler.time.sleep")
    def test_job_retries_3_times_with_backoff(self, mock_sleep, mock_sched_cls):
        """job 意外异常重试 3 次(指数退避),全失败记 ERROR 不传播。"""
        sched = MagicMock()
        mock_sched_cls.return_value = sched
        start_scheduler()
        job_fn = _job_fn(sched, "decision_settle_daily")
        with patch(
            "finance_agent.outcome.scheduler.settle_open_predictions",
            side_effect=RuntimeError("boom"),
        ) as mock_settle:
            job_fn()  # 不抛异常
        assert mock_settle.call_count == 3
        assert mock_sleep.call_count == 2  # 3 次尝试间 2 次退避

    @patch.dict(os.environ, {"TESTING": "", "DECISION_SETTLE_ENABLED": "1"})
    @patch("finance_agent.outcome.scheduler.BackgroundScheduler")
    @patch("finance_agent.outcome.scheduler.time.sleep")
    def test_job_retry_succeeds_on_second_attempt(self, mock_sleep, mock_sched_cls):
        sched = MagicMock()
        mock_sched_cls.return_value = sched
        start_scheduler()
        job_fn = _job_fn(sched, "decision_settle_daily")
        with patch(
            "finance_agent.outcome.scheduler.settle_open_predictions",
            side_effect=[RuntimeError("boom"), {"settled": 1}],
        ) as mock_settle:
            job_fn()
        assert mock_settle.call_count == 2


class TestCohortJobRegistration:
    """Δ3 Task 5：cohort 盘后跑批接线（per-job 门控，不并入全局 early-return）。"""

    @patch.dict(os.environ, {"TESTING": "", "DECISION_SETTLE_ENABLED": "1"})
    @patch("finance_agent.outcome.scheduler.BackgroundScheduler")
    def test_registers_five_jobs_including_cohort_batch(self, mock_sched_cls):
        os.environ.pop("COHORT_HOUR", None)
        os.environ.pop("COHORT_MINUTE", None)
        sched = MagicMock()
        mock_sched_cls.return_value = sched
        assert start_scheduler() is sched
        ids = [call.kwargs.get("id") for call in sched.add_job.call_args_list]
        assert len(ids) == 5, ids
        assert ids[-1] == "cohort_batch"
        assert set(ids) == {
            "decision_settle_daily",
            "daily_marking",
            "metrics_snapshot",
            "integrity_check",
            "cohort_batch",
        }

    @patch.dict(os.environ, {"TESTING": "", "DECISION_SETTLE_ENABLED": "1"})
    @patch("finance_agent.outcome.scheduler.BackgroundScheduler")
    def test_cohort_cron_defaults_to_weekday_1800(self, mock_sched_cls):
        os.environ.pop("COHORT_HOUR", None)
        os.environ.pop("COHORT_MINUTE", None)
        sched = MagicMock()
        mock_sched_cls.return_value = sched
        start_scheduler()
        assert _job_field(sched, "cohort_batch", "hour") == ["18"]
        assert _job_field(sched, "cohort_batch", "minute") == ["0"]
        assert "mon-fri" in _job_field(sched, "cohort_batch", "day_of_week")

    @patch.dict(
        os.environ,
        {
            "TESTING": "",
            "DECISION_SETTLE_ENABLED": "1",
            "COHORT_HOUR": "7",
            "COHORT_MINUTE": "30",
        },
    )
    @patch("finance_agent.outcome.scheduler.BackgroundScheduler")
    def test_cohort_hour_minute_read_at_runtime(self, mock_sched_cls):
        """env 在 start_scheduler() 内运行时读取(不得模块级冻结)。"""
        sched = MagicMock()
        mock_sched_cls.return_value = sched
        start_scheduler()
        assert _job_field(sched, "cohort_batch", "hour") == ["7"]
        assert _job_field(sched, "cohort_batch", "minute") == ["30"]

    @patch.dict(os.environ, {"TESTING": "", "DECISION_SETTLE_ENABLED": "1"})
    @patch("finance_agent.outcome.scheduler.BackgroundScheduler")
    def test_cohort_hour_env_change_takes_effect_on_next_start(self, mock_sched_cls):
        """同进程内改 env 再启动 → 新 cron 生效（证明非模块级冻结）。"""
        sched_a = MagicMock()
        mock_sched_cls.return_value = sched_a
        os.environ.pop("COHORT_HOUR", None)
        start_scheduler()
        assert _job_field(sched_a, "cohort_batch", "hour") == ["18"]

        sched_b = MagicMock()
        mock_sched_cls.return_value = sched_b
        os.environ["COHORT_HOUR"] = "9"
        start_scheduler()
        assert _job_field(sched_b, "cohort_batch", "hour") == ["9"]


class TestCohortEnvIntDefense:
    """Δ3T5 审查 I2：COHORT_HOUR/MINUTE 非数值或越界 → 回退默认 + WARN，不炸启动。"""

    @patch.dict(
        os.environ,
        {"TESTING": "", "DECISION_SETTLE_ENABLED": "1", "COHORT_HOUR": "abc"},
    )
    @patch("finance_agent.outcome.scheduler.BackgroundScheduler")
    def test_non_numeric_hour_falls_back_with_warning(self, mock_sched_cls, caplog):
        sched = MagicMock()
        mock_sched_cls.return_value = sched
        with caplog.at_level(logging.WARNING, logger=_SCHED_LOGGER):
            assert start_scheduler() is sched  # 不抛异常
        assert _job_field(sched, "cohort_batch", "hour") == ["18"], "回退默认 18"
        assert "非数值" in caplog.text

    @patch.dict(
        os.environ,
        {"TESTING": "", "DECISION_SETTLE_ENABLED": "1", "COHORT_MINUTE": "99"},
    )
    @patch("finance_agent.outcome.scheduler.BackgroundScheduler")
    def test_out_of_range_minute_falls_back_with_warning(self, mock_sched_cls, caplog):
        sched = MagicMock()
        mock_sched_cls.return_value = sched
        with caplog.at_level(logging.WARNING, logger=_SCHED_LOGGER):
            assert start_scheduler() is sched  # 不抛异常
        assert _job_field(sched, "cohort_batch", "minute") == ["0"], "越界回退默认 0"
        assert "越界" in caplog.text


class TestCohortGlobalGating:
    @patch.dict(os.environ, {"TESTING": "1"})
    @patch("finance_agent.outcome.scheduler.BackgroundScheduler")
    def test_testing_disables_all_jobs_including_cohort(self, mock_sched_cls):
        assert start_scheduler() is None
        mock_sched_cls.return_value.add_job.assert_not_called()

    @patch.dict(os.environ, {"DECISION_SETTLE_ENABLED": "0", "TESTING": ""})
    @patch("finance_agent.outcome.scheduler.BackgroundScheduler")
    def test_settle_disabled_also_disables_cohort(self, mock_sched_cls):
        """全局开关关闭 → 连 cohort 一起不注册（既有关闭语义不被绕过）。"""
        assert start_scheduler() is None
        mock_sched_cls.return_value.add_job.assert_not_called()


class TestCohortJobIsolation:
    # Task 2 起 cohort 开关由 ``run_job`` 在调用 runner 前判（ops_config 表 → env）：
    # 关闭时 job 层短路记 ``skipped-disabled``、不调 runner。故本组用例显式开闸
    # （COHORT_ENABLED=1）以覆盖「已开闸后 runner 异常/重试」路径；关闸路径见
    # TestOpsWiring::test_scheduled_cohort_disabled_records_skip_without_calling_runner。
    _GATE_ON = {"TESTING": "", "DECISION_SETTLE_ENABLED": "1", "COHORT_ENABLED": "1"}

    @patch.dict(os.environ, _GATE_ON)
    @patch("finance_agent.outcome.scheduler.BackgroundScheduler")
    @patch("finance_agent.outcome.scheduler.time.sleep")
    def test_cohort_job_swallows_exceptions(self, mock_sleep, mock_sched_cls):
        """cohort job 异常不传播（旁路铁律），与 settle job 同款。"""
        sched = MagicMock()
        mock_sched_cls.return_value = sched
        start_scheduler()
        job_fn = _job_fn(sched, "cohort_batch")
        with patch(
            "finance_agent.outcome.scheduler.run_cohort_batch",
            side_effect=RuntimeError("boom"),
        ):
            job_fn()  # 不抛异常

    @patch.dict(os.environ, _GATE_ON)
    @patch("finance_agent.outcome.scheduler.BackgroundScheduler")
    @patch("finance_agent.outcome.scheduler.time.sleep")
    def test_cohort_job_retries_then_gives_up(self, mock_sleep, mock_sched_cls):
        sched = MagicMock()
        mock_sched_cls.return_value = sched
        start_scheduler()
        job_fn = _job_fn(sched, "cohort_batch")
        with patch(
            "finance_agent.outcome.scheduler.run_cohort_batch",
            side_effect=RuntimeError("boom"),
        ) as mock_batch:
            job_fn()
        assert mock_batch.call_count == 3
        assert mock_sleep.call_count == 2

    @patch.dict(os.environ, _GATE_ON)
    @patch("finance_agent.outcome.scheduler.BackgroundScheduler")
    def test_cohort_job_calls_runner_without_args(self, mock_sched_cls):
        """job 只调用无参 run_cohort_batch——开关判定在 job 层（run_job）已过闸，
        runner 内仍保留同源门控（双保险，任何调用方都不得绕过）。"""
        sched = MagicMock()
        mock_sched_cls.return_value = sched
        start_scheduler()
        job_fn = _job_fn(sched, "cohort_batch")
        with patch(
            "finance_agent.outcome.scheduler.run_cohort_batch",
            return_value={"enabled": False},
        ) as mock_batch:
            job_fn()
        mock_batch.assert_called_once_with()


class TestOpsWiring:
    """Task 2（delta add-eval-ops-console）：定时批经统一入口落运行历史 + 配置化 cohort 时刻。"""

    @patch.dict(os.environ, {"TESTING": "", "DECISION_SETTLE_ENABLED": "1"})
    @patch("finance_agent.outcome.scheduler.BackgroundScheduler")
    @patch("finance_agent.outcome.scheduler.time.sleep")
    def test_settle_job_success_lands_ok_row(
        self, mock_sleep, mock_sched_cls, tmp_path, monkeypatch
    ):
        db = tmp_path / "s.db"
        monkeypatch.setenv("SESSIONS_DB_PATH", str(db))
        sched = MagicMock()
        mock_sched_cls.return_value = sched
        start_scheduler()
        job_fn = _job_fn(sched, "decision_settle_daily")
        with patch(
            "finance_agent.outcome.scheduler.settle_open_predictions",
            return_value={"settled": 2},
        ) as mock_settle:
            job_fn()
        mock_settle.assert_called_once_with()
        rows = list_job_runs("decision_settle_daily", db_path=db)
        assert len(rows) == 1
        assert rows[0]["status"] == "ok" and rows[0]["summary"] == {"settled": 2}
        assert rows[0]["kind"] == "scheduled" and rows[0]["source"] == "scheduled"
        assert mock_sleep.call_count == 0, "成功不得退避"

    @patch.dict(os.environ, {"TESTING": "", "DECISION_SETTLE_ENABLED": "1"})
    @patch("finance_agent.outcome.scheduler.BackgroundScheduler")
    @patch("finance_agent.outcome.scheduler.time.sleep")
    def test_scheduled_failure_lands_in_history_with_retries(
        self, mock_sleep, mock_sched_cls, tmp_path, monkeypatch
    ):
        """3 次尝试各一行 failed，最终行记 retries；旁路铁律：不上抛。"""
        db = tmp_path / "s.db"
        monkeypatch.setenv("SESSIONS_DB_PATH", str(db))
        sched = MagicMock()
        mock_sched_cls.return_value = sched
        start_scheduler()
        job_fn = _job_fn(sched, "decision_settle_daily")
        with patch(
            "finance_agent.outcome.scheduler.settle_open_predictions",
            side_effect=RuntimeError("boom"),
        ) as mock_settle:
            job_fn()  # 不抛异常
        assert mock_settle.call_count == 3
        assert mock_sleep.call_count == 2  # 5s / 20s
        rows = list_job_runs("decision_settle_daily", db_path=db)
        assert [r["status"] for r in rows] == ["failed", "failed", "failed"]
        latest = rows[0]
        assert latest["summary"] == {"attempts": 3, "retries": 2}
        assert "boom" in latest["error"]

    @patch.dict(os.environ, {"TESTING": "", "DECISION_SETTLE_ENABLED": "1"})
    @patch("finance_agent.outcome.scheduler.BackgroundScheduler")
    @patch("finance_agent.outcome.scheduler.time.sleep")
    def test_job_already_running_skips_round_without_retry(
        self, mock_sleep, mock_sched_cls, tmp_path, monkeypatch
    ):
        """手动补跑占用单飞锁 → 定时轮放弃（不是失败，不重试、不抛）。"""
        monkeypatch.setenv("SESSIONS_DB_PATH", str(tmp_path / "s.db"))
        sched = MagicMock()
        mock_sched_cls.return_value = sched
        start_scheduler()
        job_fn = _job_fn(sched, "decision_settle_daily")
        with patch(
            "finance_agent.outcome.scheduler.run_job",
            side_effect=JobAlreadyRunning("decision_settle_daily 已在运行"),
        ) as mock_run:
            job_fn()  # 不抛异常
        assert mock_run.call_count == 1
        assert mock_sleep.call_count == 0

    @patch.dict(os.environ, {"TESTING": "", "DECISION_SETTLE_ENABLED": "1"})
    @patch("finance_agent.outcome.scheduler.BackgroundScheduler")
    @patch("finance_agent.outcome.scheduler.time.sleep")
    def test_job_entry_exception_is_swallowed_and_retried(
        self, mock_sleep, mock_sched_cls, tmp_path, monkeypatch
    ):
        """入口自身异常（如落库故障）也按失败尝试重试且不上抛（旁路铁律）。"""
        monkeypatch.setenv("SESSIONS_DB_PATH", str(tmp_path / "s.db"))
        sched = MagicMock()
        mock_sched_cls.return_value = sched
        start_scheduler()
        job_fn = _job_fn(sched, "decision_settle_daily")
        with patch(
            "finance_agent.outcome.scheduler.run_job",
            side_effect=RuntimeError("history db locked"),
        ) as mock_run:
            job_fn()  # 不抛异常
        assert mock_run.call_count == 3
        assert mock_sleep.call_count == 2

    @patch.dict(os.environ, {"TESTING": "", "DECISION_SETTLE_ENABLED": "1"})
    @patch("finance_agent.outcome.scheduler.BackgroundScheduler")
    def test_scheduled_cohort_disabled_records_skip_without_calling_runner(
        self, mock_sched_cls, tmp_path, monkeypatch
    ):
        """表说关（env 说开）→ 定时触发记 skipped-disabled 且零调用 runner。"""
        db = tmp_path / "s.db"
        init_ops(db)
        set_config(COHORT_ENABLED_KEY, "0", db)
        monkeypatch.setenv("COHORT_ENABLED", "1")
        monkeypatch.setenv("SESSIONS_DB_PATH", str(db))
        sched = MagicMock()
        mock_sched_cls.return_value = sched
        start_scheduler()
        job_fn = _job_fn(sched, "cohort_batch")
        with patch("finance_agent.outcome.scheduler.run_cohort_batch") as mock_batch:
            job_fn()
        mock_batch.assert_not_called()
        row = last_job_run("cohort_batch", db)
        assert row["status"] == "skipped-disabled"
        assert row["summary"] == {"reason": "switch_off"}

    @patch.dict(
        os.environ,
        {
            "TESTING": "",
            "DECISION_SETTLE_ENABLED": "1",
            "COHORT_HOUR": "7",
            "COHORT_MINUTE": "30",
        },
    )
    @patch("finance_agent.outcome.scheduler.BackgroundScheduler")
    def test_cohort_hour_minute_from_config_table_beats_env(
        self, mock_sched_cls, tmp_path, monkeypatch
    ):
        """Task 2：cohort 时刻真源是 ops_config（表值 19:45 压过 env 7:30）。"""
        db = tmp_path / "s.db"
        init_ops(db)
        set_config(COHORT_HOUR_KEY, "19", db)
        set_config(COHORT_MINUTE_KEY, "45", db)
        monkeypatch.setenv("SESSIONS_DB_PATH", str(db))
        sched = MagicMock()
        mock_sched_cls.return_value = sched
        start_scheduler()
        assert _job_field(sched, "cohort_batch", "hour") == ["19"]
        assert _job_field(sched, "cohort_batch", "minute") == ["45"]


class TestMisfireHardening:
    """#57 子项4:进程沉睡/重启错过触发时刻 → 1h 宽限内补火,多次 miss 合并为一次。

    默认 misfire_grace_time=1s 下,进程被冻结/重建略过 16:00 即整日丢批;
    job 幂等(次日补结)所以宽限补火安全。五个日批 job 统一配置。
    """

    @patch.dict(os.environ, {"TESTING": "", "DECISION_SETTLE_ENABLED": "1"})
    @patch("finance_agent.outcome.scheduler.BackgroundScheduler")
    def test_all_jobs_have_misfire_grace_and_coalesce(self, mock_sched_cls):
        sched = MagicMock()
        mock_sched_cls.return_value = sched
        start_scheduler()
        ids = [call.kwargs.get("id") for call in sched.add_job.call_args_list]
        assert len(ids) == 5, ids
        for call in sched.add_job.call_args_list:
            job_id = call.kwargs.get("id")
            assert call.kwargs.get("misfire_grace_time") == 3600, job_id
            assert call.kwargs.get("coalesce") is True, job_id


class TestRescheduleCohort:
    @patch.dict(os.environ, {"TESTING": "1"})
    def test_start_returns_none_and_handle_stays_empty(self):
        assert start_scheduler() is None
        assert get_scheduler() is None

    @patch.dict(os.environ, {"TESTING": "1"})
    def test_returns_false_when_no_scheduler(self, monkeypatch):
        """TESTING=1（无调度器）→ False 而非抛错（状态接口不得 500）。"""
        monkeypatch.setenv("COHORT_HOUR", "19")
        assert reschedule_cohort(19, 30) is False

    def test_rejects_out_of_range(self, monkeypatch):
        monkeypatch.setattr(
            "finance_agent.outcome.scheduler._scheduler", MagicMock(), raising=False
        )
        assert reschedule_cohort(24, 0) is False
        assert reschedule_cohort(0, 60) is False

    def test_moves_next_fire_and_clears_on_stop(self, tmp_path, monkeypatch):
        monkeypatch.delenv("TESTING", raising=False)
        monkeypatch.delenv("DECISION_SETTLE_ENABLED", raising=False)
        monkeypatch.setenv("SESSIONS_DB_PATH", str(tmp_path / "s.db"))
        sched = start_scheduler()
        assert sched is not None
        try:
            assert get_scheduler() is sched
            assert reschedule_cohort(19, 30) is True
            job = next(j for j in sched.get_jobs() if j.id == "cohort_batch")
            field = job.trigger.fields[job.trigger.FIELD_NAMES.index("hour")]
            assert str(field) == "19"
            field = job.trigger.fields[job.trigger.FIELD_NAMES.index("minute")]
            assert str(field) == "30"
        finally:
            stop_scheduler(sched)
        assert get_scheduler() is None
