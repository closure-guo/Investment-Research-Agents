"""任务执行入口(delta add-eval-ops-console Task 2)。

``run_job`` 是**唯一**落运行历史的入口:scheduler 的定时批(``source="scheduled"``)
与运维界面的手动补跑(``source="manual"``)都经它,于是「单飞锁 + 运行历史」天然
覆盖两条来源,手动与定时不会并发跑同一任务。

契约:
- 未知 ``job_id`` → ``ValueError``(端点翻 404);
- 同一 job 已在运行 → ``JobAlreadyRunning``(手动拒绝并发,不排队);
- cohort 开关关闭:手动 → ``CohortDisabled``(端点翻 409);定时 → 返回
  ``status="skipped-disabled"``(定时批没人在等返回值,记历史即完成)。两种来源
  都**不调用** runner(零 LLM 调用);开关真源是 ``ops_config`` 表(表值 → env
  → 默认,见 ``ops.model.get_cohort_settings``),不再只看 ``COHORT_ENABLED`` env;
- 任务函数抛异常 → 历史落 ``failed`` + 错误串,返回 dict **不上抛**(旁路铁律:
  定时批失败不得炸 API 进程;手动补跑的失败经返回值与历史展示);
- 历史层自身故障(``job_runs`` 建行失败)不伪装成任务失败:上抛给调用方
  (scheduler 按失败尝试重试),但单飞锁必在 ``finally`` 释放。
- 运行行的**所有权**:本进程开出的 running 行登记在 ``active_run_ids()``(收尾即注销),
  悬挂行清扫据此跳过——时钟只作次要条件,所有权才是「本进程在跑」的硬证据。

``JOB_FUNCS`` 是默认注册表;``run_job`` 的 ``func`` 参数供 scheduler 传入**它自己
模块级**的任务函数引用——既有测试在那里打桩,且保证调用期才解析(不把 env/config
冻结在导入期)。
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

from finance_agent.data.snapshot_job import MARKET_SNAPSHOT_SCHEDULE, run_market_snapshot_batch
from finance_agent.outcome.cohort.runner import run_cohort_batch
from finance_agent.outcome.ops.model import (
    finish_job_run,
    get_cohort_settings,
    insert_job_run,
)
from finance_agent.outcome.track_record.job import settle_open_predictions
from finance_agent.outcome.track_record.marking import persist_metrics_snapshot, run_daily_marking
from finance_agent.outcome.track_record.model import integrity_check

logger = logging.getLogger(__name__)

# 任务全集(顺序 = 调度注册与状态接口的展示顺序;按触发时刻升序)
JOB_IDS: tuple[str, ...] = (
    "market_snapshot_daily",
    "decision_settle_daily",
    "daily_marking",
    "metrics_snapshot",
    "integrity_check",
    "cohort_batch",
)

JOB_LABELS: dict[str, str] = {
    "market_snapshot_daily": "行情快照",
    "decision_settle_daily": "判定",
    "daily_marking": "盯市",
    "metrics_snapshot": "指标快照",
    "integrity_check": "完整性校验",
    "cohort_batch": "cohort 跑批",
}

# 默认注册表。值都是无参调用(任务函数签名全为可选关键字参数)。
JOB_FUNCS: dict[str, Callable[[], Any]] = {
    "market_snapshot_daily": run_market_snapshot_batch,
    "decision_settle_daily": settle_open_predictions,
    "daily_marking": run_daily_marking,
    "metrics_snapshot": persist_metrics_snapshot,
    "integrity_check": integrity_check,
    "cohort_batch": run_cohort_batch,
}

# 每 job 的 cron 规格:结算链四任务时刻固定(16:00/16:30/16:35/16:40,工作日);
# 行情快照日批 15:30(收盘后,add-market-snapshot);cohort 项的 hour/minute 由
# ``scheduler.start_scheduler``(启动时)与 ``scheduler.reschedule_cohort``(界面保存后)
# 按 config 覆写——本字典是调度与状态接口共用的时刻快照。
SCHEDULES: dict[str, dict[str, Any]] = {
    "market_snapshot_daily": MARKET_SNAPSHOT_SCHEDULE,
    "decision_settle_daily": {
        "day_of_week": "mon-fri",
        "hour": 16,
        "minute": 0,
        "timezone": "Asia/Shanghai",
    },
    "daily_marking": {
        "day_of_week": "mon-fri",
        "hour": 16,
        "minute": 30,
        "timezone": "Asia/Shanghai",
    },
    "metrics_snapshot": {
        "day_of_week": "mon-fri",
        "hour": 16,
        "minute": 35,
        "timezone": "Asia/Shanghai",
    },
    "integrity_check": {
        "day_of_week": "mon-fri",
        "hour": 16,
        "minute": 40,
        "timezone": "Asia/Shanghai",
    },
    "cohort_batch": {
        "day_of_week": "mon-fri",
        "hour": 18,
        "minute": 0,
        "timezone": "Asia/Shanghai",
    },
}


class JobAlreadyRunning(RuntimeError):  # noqa: N818 —— 名称是实施计划约定的跨任务接口
    """同一 job 已有运行中的实例(单飞锁占用),本次调用不排队。"""


class CohortDisabled(RuntimeError):  # noqa: N818 —— 名称是实施计划约定的跨任务接口
    """cohort 开关关闭时的手动跑批拒绝(端点翻 409;零 LLM 调用)。"""


# 每 job 一把进程内锁。前提:单 uvicorn worker + in-process APScheduler
# (全局约束),互斥只需进程内锁,不得据此假设多 worker 场景。
_LOCKS: dict[str, threading.Lock] = {job_id: threading.Lock() for job_id in JOB_IDS}

# 本进程正在执行的 run_id 集合:悬挂行清扫的**所有权**依据(时钟可被回拨/人工回填,
# 不可作为「这行是不是本进程在跑」的证据)。长任务经 ``ops_api._spawn_task`` 亦登记。
_ACTIVE_RUN_IDS: set[int] = set()
_ACTIVE_RUNS_LOCK = threading.Lock()


def mark_run_active(run_id: int) -> None:
    """登记本进程正在执行的运行行(悬挂清扫据此跳过该行)。"""
    with _ACTIVE_RUNS_LOCK:
        _ACTIVE_RUN_IDS.add(int(run_id))


def mark_run_finished(run_id: int) -> None:
    """注销已收尾的运行行(注销后不再受所有权保护,按时钟判定)。"""
    with _ACTIVE_RUNS_LOCK:
        _ACTIVE_RUN_IDS.discard(int(run_id))


def active_run_ids() -> frozenset[int]:
    """当前在跑的行 id 快照(清扫时一次取齐,避免逐行加锁)。"""
    with _ACTIVE_RUNS_LOCK:
        return frozenset(_ACTIVE_RUN_IDS)


def run_job(
    job_id: str,
    *,
    source: str = "manual",
    db_path: str | Path | None = None,
    func: Callable[[], Any] | None = None,
) -> dict[str, Any]:
    """跑一次任务并落运行历史,返回 ``{"run_id","status","summary","error"}``。

    ``source="manual"`` 记 ``kind="manual"``,其余记 ``kind="scheduled"``。
    ``func`` 覆盖注册表(仅供 scheduler 传它自己的模块级引用;默认 ``JOB_FUNCS``)。
    """
    if job_id not in JOB_IDS:
        raise ValueError(f"未知 job_id: {job_id!r}")
    kind = "manual" if source == "manual" else "scheduled"

    # cohort 门控:开关关闭时**不调用** runner(零 LLM 调用),只留一行 skipped-disabled。
    # 手动跑批由端点翻 409,故此处抛;定时批无人等返回值,返回终态结果即可。
    if job_id == "cohort_batch" and not get_cohort_settings(db_path)["enabled"]:
        summary = {"reason": "switch_off"}
        run_id = insert_job_run(
            job_id, kind, "skipped-disabled", source=source, summary=summary, db_path=db_path
        )
        finish_job_run(run_id, status="skipped-disabled", db_path=db_path)
        logger.info("cohort 开关未开启(%s),本次不跑批,记 skipped-disabled", source)
        if source == "manual":
            raise CohortDisabled("cohort 开关未开启,手动跑批被拒绝(零 LLM 调用)")
        return {"run_id": run_id, "status": "skipped-disabled", "summary": summary, "error": None}

    lock = _LOCKS[job_id]
    if not lock.acquire(blocking=False):
        raise JobAlreadyRunning(f"{job_id} 已在运行(本次不排队)")
    active_run_id: int | None = None
    try:
        # 历史层故障(建行失败)不伪装成任务失败:上抛给调用方(scheduler 按失败尝试
        # 计数重试);关键是 finally 必须释放锁——否则该 job 在本进程内永久被占。
        run_id = insert_job_run(job_id, kind, "running", source=source, db_path=db_path)
        mark_run_active(run_id)
        active_run_id = run_id
        try:
            result = (func or JOB_FUNCS[job_id])()
        except Exception as exc:  # noqa: BLE001 - 旁路铁律:任务失败落历史,不向调用链上抛
            error = f"{type(exc).__name__}: {exc}"
            try:
                finish_job_run(run_id, status="failed", error=error, db_path=db_path)
            except Exception:  # noqa: BLE001 - 终态落库失败只记日志(不得盖住真错误)
                logger.exception("job_runs 失败终态落库失败 job_id=%s run_id=%s", job_id, run_id)
            logger.warning("%s job 失败(%s)", job_id, error, exc_info=True)
            return {"run_id": run_id, "status": "failed", "summary": None, "error": error}
        summary = result if isinstance(result, dict) else {"result": str(result)}
        finish_job_run(run_id, status="ok", summary=summary, db_path=db_path)
        return {"run_id": run_id, "status": "ok", "summary": summary, "error": None}
    finally:
        if active_run_id is not None:
            mark_run_finished(active_run_id)
        lock.release()
