"""scheduler 挂载:TESTING/env 禁用、cron 注册、启停、job 异常不传播、失败重试。"""

import os
from unittest.mock import MagicMock, patch

from finance_agent.outcome.scheduler import start_scheduler, stop_scheduler


def _settle_job_fn(sched):
    """从注册的 3 个日批 job 中取 decision_settle_daily 的任务函数。"""
    for call in sched.add_job.call_args_list:
        if call.kwargs.get("id") == "decision_settle_daily":
            return call.args[0]
    raise AssertionError("decision_settle_daily job 未注册")


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


def _job_trigger_str(sched, job_id) -> str:
    """按 id 取 CronTrigger 的字符串表示(不锁 __str__ 细节,只查字段子串)。"""
    call = _job_call(sched, job_id)
    trigger = call.args[1] if len(call.args) > 1 else call.kwargs.get("trigger")
    return str(trigger)


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
        trigger = _job_trigger_str(sched, "decision_settle_daily")
        # CronTrigger: 工作日 16:00(锁语义,不锁 __str__ 格式)
        assert "16" in trigger
        assert "mon-fri" in trigger
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
        job_fn = _settle_job_fn(sched)
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
        job_fn = _settle_job_fn(sched)
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
        job_fn = _settle_job_fn(sched)
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
        cron = _job_trigger_str(sched, "cohort_batch")
        assert "mon-fri" in cron
        assert "hour='18'" in cron
        assert "minute='0'" in cron

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
        cron = _job_trigger_str(sched, "cohort_batch")
        assert "hour='7'" in cron
        assert "minute='30'" in cron

    @patch.dict(os.environ, {"TESTING": "", "DECISION_SETTLE_ENABLED": "1"})
    @patch("finance_agent.outcome.scheduler.BackgroundScheduler")
    def test_cohort_hour_env_change_takes_effect_on_next_start(self, mock_sched_cls):
        """同进程内改 env 再启动 → 新 cron 生效（证明非模块级冻结）。"""
        sched_a = MagicMock()
        mock_sched_cls.return_value = sched_a
        os.environ.pop("COHORT_HOUR", None)
        start_scheduler()
        assert "hour='18'" in _job_trigger_str(sched_a, "cohort_batch")

        sched_b = MagicMock()
        mock_sched_cls.return_value = sched_b
        os.environ["COHORT_HOUR"] = "9"
        start_scheduler()
        assert "hour='9'" in _job_trigger_str(sched_b, "cohort_batch")


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
    @patch.dict(os.environ, {"TESTING": "", "DECISION_SETTLE_ENABLED": "1"})
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

    @patch.dict(os.environ, {"TESTING": "", "DECISION_SETTLE_ENABLED": "1"})
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

    @patch.dict(os.environ, {"TESTING": "", "DECISION_SETTLE_ENABLED": "1"})
    @patch("finance_agent.outcome.scheduler.BackgroundScheduler")
    def test_cohort_job_calls_runner_without_args(self, mock_sched_cls):
        """job 只调用无参 run_cohort_batch——门控（COHORT_ENABLED）由 runner 自担。"""
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
