"""日批结算定时任务(design 决策 6:APScheduler in-process,单 worker 无竞争)。

每个工作日 16:00(收盘后)触发 settle_open_decisions。
TESTING=1 或 DECISION_SETTLE_ENABLED=0 时禁用;job 异常不传播(旁路铁律)。

失败重试:意外异常指数退避重试 3 次(5s/20s,time.sleep 低频日批足够),全失败记
ERROR 等下交易日;job 内逻辑幂等,重跑安全。**Task 2(delta add-eval-ops-console)
起,每次尝试都经 ``ops.jobs.run_job`` 统一入口**:单飞锁(与手动补跑共用)+
``job_runs`` 运行历史(每次失败各留一行,最终行记 ``retries``);入口自身故障、
锁占用、开关关闭一律吞在 job 边界内,不抛给 scheduler/API 进程。

cohort 盘后跑批(delta add-forward-paper-trading-cohort;Task 2 改为配置驱动):
工作日触发 ``run_cohort_batch``。开关与时刻的**唯一真源是 ``ops_config`` 表**
(表值 → env → 默认,见 ``ops.model.get_cohort_settings``):关闭时 ``run_job``
记 ``skipped-disabled`` 且**不调用** runner(零 LLM 调用)——不再只看
``COHORT_ENABLED`` env。**per-job 门控**:cohort 不并入本模块顶部那条全局
early-return(TESTING / DECISION_SETTLE_ENABLED 是结算链路的开关,并入会连带禁用
settle/marking/metrics/integrity)。

运行时重排:``reschedule_cohort(hour, minute)`` 经 ``modify_job`` 改 cohort cron
(cohort 页签保存后立即生效,无需重启);未运行时(``TESTING=1`` 或未启动)返回
``False`` 而非抛错——状态接口在 TESTING 下必须可用。

注:定时任务框架选型建议人工落 ADR(design 决策 6 注),agent 不自建 ADR。
"""

from __future__ import annotations

import logging
import os
import time
from collections.abc import Callable
from typing import Any

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from finance_agent.outcome.cohort.runner import run_cohort_batch
from finance_agent.outcome.ops.jobs import (
    SCHEDULES,
    CohortDisabled,
    JobAlreadyRunning,
    run_job,
)
from finance_agent.outcome.ops.model import finish_job_run, get_cohort_settings
from finance_agent.outcome.track_record.job import settle_open_predictions
from finance_agent.outcome.track_record.marking import persist_metrics_snapshot, run_daily_marking
from finance_agent.outcome.track_record.model import integrity_check

logger = logging.getLogger(__name__)

_MAX_ATTEMPTS = 3

# #57 子项4:misfire 硬化。默认 misfire_grace_time=1s 下进程被冻结/重建略过
# 触发时刻即整日丢批;job 幂等(次日补结),1h 宽限内补火安全;多次 miss 合并
# 一次(coalesce),防追赶风暴。注:MemoryJobStore 重启后 job 重注册,该参数
# 管的是进程活着但调度 loop 延迟的场景。
_JOB_MISFIRE_KW: dict[str, Any] = {"misfire_grace_time": 3600, "coalesce": True}

# 进程内 scheduler 句柄(单 worker 前提):reschedule_cohort / get_scheduler 用。
_scheduler: BackgroundScheduler | None = None


def get_scheduler() -> BackgroundScheduler | None:
    """当前进程内的 scheduler;未启动(含 TESTING=1)→ None。"""
    return _scheduler


def _with_retry(job_id: str, label: str, fn: Callable[[], Any]) -> None:
    """日批统一入口:经 ``ops.jobs.run_job`` 落运行历史;保留 3 次退避重试语义。

    每次尝试各留一行 ``job_runs``(失败也留痕);全失败时最终行标注 ``retries``,
    下交易日再试。**旁路铁律**:任何异常都不得传播——入口自身故障按失败计数重试,
    单飞锁被手动补跑占用与 cohort 开关关闭则**不重试**(不是失败,直接放弃本轮)。
    """
    outcome: dict[str, Any] | None = None
    for attempt in range(_MAX_ATTEMPTS):
        try:
            outcome = run_job(job_id, source="scheduled", func=fn)
        except (JobAlreadyRunning, CohortDisabled) as exc:
            logger.info("%s job 本轮跳过(%s): %s", label, type(exc).__name__, exc)
            return
        except Exception as exc:  # noqa: BLE001 - 入口自身故障(如落库失败)也不得上抛
            logger.warning("%s job 入口异常(%s)", label, exc, exc_info=True)
            outcome = {
                "run_id": None,
                "status": "failed",
                "summary": None,
                "error": f"{type(exc).__name__}: {exc}",
            }
        if outcome["status"] != "failed":
            logger.info("%s job 完成: %s", label, outcome.get("summary"))
            return
        if attempt < _MAX_ATTEMPTS - 1:
            backoff = 5 * (4**attempt)  # 5s / 20s
            logger.warning(
                "%s job 第 %d 次失败(%s),%ds 后重试",
                label,
                attempt + 1,
                outcome.get("error"),
                backoff,
            )
            time.sleep(backoff)
    logger.error("%s job 重试 %d 次全部失败(下交易日再试)", label, _MAX_ATTEMPTS)
    _mark_retries(outcome)


def _mark_retries(outcome: dict[str, Any] | None) -> None:
    """最终失败行记 ``retries``(重试发生在 run_job 外层,每次尝试各一行)。"""
    run_id = (outcome or {}).get("run_id")
    if run_id is None:
        return
    try:
        finish_job_run(
            run_id,
            status="failed",
            summary={"attempts": _MAX_ATTEMPTS, "retries": _MAX_ATTEMPTS - 1},
        )
    except Exception:  # noqa: BLE001 - 留痕失败只记日志,不得上抛
        logger.exception("job_runs 重试计数落库失败 run_id=%s", run_id)


def _settle_job() -> None:
    _with_retry("decision_settle_daily", "prediction settle", settle_open_predictions)


def _marking_job() -> None:
    """add-track-record-stage-b：每日盯市 + 净值曲线（16:30，settle 之后）。"""
    _with_retry("daily_marking", "daily marking", run_daily_marking)


def _metrics_job() -> None:
    """add-track-record-stage-b：指标快照重算（16:35）。"""
    _with_retry("metrics_snapshot", "metrics snapshot", persist_metrics_snapshot)


def _integrity_job() -> None:
    """add-track-record-stage-c：快照哈希完整性校验（16:40，篡改告警）。"""
    _with_retry("integrity_check", "integrity check", integrity_check)


def _cohort_job() -> None:
    """add-forward-paper-trading-cohort：探班池盘后跑批（时刻取自 ops_config）。

    开关已在 ``run_job`` 判过(关闭 → 记 ``skipped-disabled``、不走到这里);开启时
    无参调用 ``run_cohort_batch``——runner 内保留同源门控作双保险,任何调用方都
    不得绕过开关。
    """
    _with_retry("cohort_batch", "cohort batch", run_cohort_batch)


def _cron_from_spec(spec: dict[str, Any]) -> CronTrigger:
    """按 SCHEDULES 规格建 cron(注册与运行时重排共用一种形态)。"""
    return CronTrigger(
        day_of_week=spec["day_of_week"],
        hour=spec["hour"],
        minute=spec["minute"],
        timezone=spec["timezone"],
    )


def _cron_for(job_id: str) -> CronTrigger:
    return _cron_from_spec(SCHEDULES[job_id])


def _apply_cohort_schedule(hour: int, minute: int) -> None:
    """把解析出的 cohort 时刻写回 SCHEDULES(cron 与状态接口共用一个快照)。"""
    spec = SCHEDULES["cohort_batch"]
    spec["hour"], spec["minute"] = hour, minute


def start_scheduler() -> BackgroundScheduler | None:
    """启动日批 scheduler;TESTING/禁用时返回 None(不登记句柄)。"""
    global _scheduler
    if os.getenv("TESTING") == "1" or os.getenv("DECISION_SETTLE_ENABLED") == "0":
        return None
    # cohort 时刻在**此处**读配置(ops_config 表 → env → 默认):运维在界面或 env
    # 改动后,重新 start_scheduler 或调 reschedule_cohort 即生效,无需冻结。
    cohort = get_cohort_settings()
    _apply_cohort_schedule(cohort["hour"], cohort["minute"])
    scheduler = BackgroundScheduler()
    scheduler.add_job(
        _settle_job,
        _cron_for("decision_settle_daily"),
        id="decision_settle_daily",
        replace_existing=True,
        **_JOB_MISFIRE_KW,
    )
    # stage-b：先结算（16:00）再盯市（16:30）——已结算观点不再盯市；
    # 指标快照（16:35）独立任务，可手动重算
    scheduler.add_job(
        _marking_job,
        _cron_for("daily_marking"),
        id="daily_marking",
        replace_existing=True,
        **_JOB_MISFIRE_KW,
    )
    scheduler.add_job(
        _metrics_job,
        _cron_for("metrics_snapshot"),
        id="metrics_snapshot",
        replace_existing=True,
        **_JOB_MISFIRE_KW,
    )
    # stage-c：完整性校验（16:40）——快照哈希逐条比对，篡改写审计并告警
    scheduler.add_job(
        _integrity_job,
        _cron_for("integrity_check"),
        id="integrity_check",
        replace_existing=True,
        **_JOB_MISFIRE_KW,
    )
    # Δ3：cohort 盘后跑批（默认 18:00，晚于结算链路，避免与日批争资源）。
    # 注意：本 job 的**时刻**来自 ops_config（界面可改，改后 reschedule_cohort 即时
    # 生效），**跑不跑**由 run_job 在调用前按同一份配置判定。
    scheduler.add_job(
        _cohort_job,
        _cron_for("cohort_batch"),
        id="cohort_batch",
        replace_existing=True,
        **_JOB_MISFIRE_KW,
    )
    scheduler.start()
    _scheduler = scheduler
    logger.info(
        "decision settle scheduler 已启动(工作日 16:00);daily marking 16:30;"
        "metrics snapshot 16:35;integrity check 16:40;"
        "cohort batch %02d:%02d(开关关闭时记 skipped-disabled,零 LLM 调用)",
        cohort["hour"],
        cohort["minute"],
    )
    return scheduler


def reschedule_cohort(hour: int, minute: int) -> bool:
    """运行时改 cohort cron(cohort 页签保存后即时生效,无需重启)。

    未运行调度器(TESTING=1 / 未启动 / 已 shutdown)→ ``False``,不抛(全局约束:
    TESTING 下状态接口不得 500);越界值同样 ``False`` + WARN(端点侧另有 422 校验)。
    """
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        logger.warning("reschedule_cohort 拒绝越界时刻(%s:%s)", hour, minute)
        return False
    scheduler = _scheduler
    if scheduler is None:
        logger.info("reschedule_cohort 忽略:调度器未运行(%02d:%02d 仅落配置)", hour, minute)
        return False
    spec = dict(SCHEDULES["cohort_batch"])
    spec["hour"], spec["minute"] = hour, minute
    try:
        scheduler.modify_job("cohort_batch", trigger=_cron_from_spec(spec))
    except Exception:  # noqa: BLE001 - 重排失败不得炸调用方(配置已落库,重启即生效)
        logger.exception("cohort cron 重排失败(%02d:%02d)", hour, minute)
        return False
    _apply_cohort_schedule(hour, minute)
    logger.info("cohort 跑批时刻已重排为 %02d:%02d", hour, minute)
    return True


def stop_scheduler(scheduler: BackgroundScheduler | None) -> None:
    """关闭 scheduler(None 安全);清进程内句柄(状态接口随即报未运行)。"""
    global _scheduler
    if scheduler is None:
        return
    try:
        scheduler.shutdown(wait=False)
    finally:
        if _scheduler is scheduler:
            _scheduler = None
