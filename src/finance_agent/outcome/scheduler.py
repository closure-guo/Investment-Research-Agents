"""日批结算定时任务(design 决策 6:APScheduler in-process,单 worker 无竞争)。

每个工作日 16:00(收盘后)触发 settle_open_decisions。
TESTING=1 或 DECISION_SETTLE_ENABLED=0 时禁用;job 异常不传播(旁路铁律)。
失败重试:意外异常指数退避重试 3 次(5s/20s,time.sleep 低频日批足够),
全失败记 ERROR 等下交易日;job 内 settle_open_decisions 幂等,重跑安全。

cohort 盘后跑批(delta add-forward-paper-trading-cohort):工作日 18:00 触发
``run_cohort_batch``。**per-job 门控**——cohort 不并入本模块顶部那条全局
early-return(TESTING / DECISION_SETTLE_ENABLED 是结算链路的开关,并入会连带
禁用 settle/marking/metrics/integrity);门控由 runner 自身承担
(``COHORT_ENABLED != "1"`` → 立即返回 ``{"enabled": False}`` 且零 LLM 调用),
故开关关闭时 job 触发即空转,满足「开关关闭时 SHALL NOT 触发任何跑批」。

注:定时任务框架选型建议人工落 ADR(design 决策 6 注),agent 不自建 ADR。
"""

from __future__ import annotations

import logging
import os
import time

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from finance_agent.outcome.cohort.runner import run_cohort_batch
from finance_agent.outcome.track_record.job import settle_open_predictions
from finance_agent.outcome.track_record.marking import persist_metrics_snapshot, run_daily_marking
from finance_agent.outcome.track_record.model import integrity_check

logger = logging.getLogger(__name__)

_MAX_ATTEMPTS = 3


def _env_int(name: str, default: int, *, lo: int, hi: int) -> int:
    """读整型 env:缺省/非数值/越界 → WARN 并回退默认(ops 手滑不得炸 scheduler)。"""
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except (TypeError, ValueError):
        logger.warning("%s 非数值(%r),回退默认 %s", name, raw, default)
        return default
    if not lo <= value <= hi:
        logger.warning("%s 越界(%s),回退默认 %s", name, value, default)
        return default
    return value


def _with_retry(name: str, fn) -> None:
    """日批统一入口：异常吞掉(旁路铁律)，指数退避重试 3 次。"""
    for attempt in range(_MAX_ATTEMPTS):
        try:
            result = fn()
            logger.info("%s job 完成: %s", name, result)
            return
        except Exception as e:  # noqa: BLE001
            if attempt < _MAX_ATTEMPTS - 1:
                backoff = 5 * (4**attempt)  # 5s / 20s
                logger.warning("%s job 第 %d 次失败(%s),%ds 后重试", name, attempt + 1, e, backoff)
                time.sleep(backoff)
            else:
                logger.exception("%s job 重试 3 次全部失败(下交易日再试)", name)


def _settle_job() -> None:
    _with_retry("prediction settle", settle_open_predictions)


def _marking_job() -> None:
    """add-track-record-stage-b：每日盯市 + 净值曲线（16:30，settle 之后）。"""
    _with_retry("daily marking", run_daily_marking)


def _metrics_job() -> None:
    """add-track-record-stage-b：指标快照重算（16:35）。"""
    _with_retry("metrics snapshot", persist_metrics_snapshot)


def _integrity_job() -> None:
    """add-track-record-stage-c：快照哈希完整性校验（16:40，篡改告警）。"""
    _with_retry("integrity check", integrity_check)


def _cohort_job() -> None:
    """add-forward-paper-trading-cohort：探班池盘后跑批（默认 18:00）。

    无参调用 ``run_cohort_batch``：**门控在 runner 内**（``COHORT_ENABLED != "1"``
    → 立即返回且零调用），本 job 不重复判定，避免出现「job 级开关与 runner 级
    开关不一致」的第二真相源。
    """
    _with_retry("cohort batch", run_cohort_batch)


def start_scheduler() -> BackgroundScheduler | None:
    """启动日批 scheduler;TESTING/禁用时返回 None。"""
    if os.getenv("TESTING") == "1" or os.getenv("DECISION_SETTLE_ENABLED") == "0":
        return None
    # cohort 时段在**此处**读 env（非模块级冻结）：测试与运维改 env 后重新
    # start_scheduler 即生效，无需重启进程以外的手段。非法/越界值回退默认 + WARN。
    cohort_hour = _env_int("COHORT_HOUR", 18, lo=0, hi=23)
    cohort_minute = _env_int("COHORT_MINUTE", 0, lo=0, hi=59)
    scheduler = BackgroundScheduler()
    scheduler.add_job(
        _settle_job,
        CronTrigger(day_of_week="mon-fri", hour=16, minute=0, timezone="Asia/Shanghai"),
        id="decision_settle_daily",
        replace_existing=True,
    )
    # stage-b：先结算（16:00）再盯市（16:30）——已结算观点不再盯市；
    # 指标快照（16:35）独立任务，可手动重算
    scheduler.add_job(
        _marking_job,
        CronTrigger(day_of_week="mon-fri", hour=16, minute=30, timezone="Asia/Shanghai"),
        id="daily_marking",
        replace_existing=True,
    )
    scheduler.add_job(
        _metrics_job,
        CronTrigger(day_of_week="mon-fri", hour=16, minute=35, timezone="Asia/Shanghai"),
        id="metrics_snapshot",
        replace_existing=True,
    )
    # stage-c：完整性校验（16:40）——快照哈希逐条比对，篡改写审计并告警
    scheduler.add_job(
        _integrity_job,
        CronTrigger(day_of_week="mon-fri", hour=16, minute=40, timezone="Asia/Shanghai"),
        id="integrity_check",
        replace_existing=True,
    )
    # Δ3：cohort 盘后跑批（默认 18:00，晚于结算链路，避免与日批争资源）。
    # 注意：本 job 只负责「何时跑」，不负责「跑不跑」——开关在 runner 内。
    scheduler.add_job(
        _cohort_job,
        CronTrigger(
            day_of_week="mon-fri",
            hour=cohort_hour,
            minute=cohort_minute,
            timezone="Asia/Shanghai",
        ),
        id="cohort_batch",
        replace_existing=True,
    )
    scheduler.start()
    logger.info(
        "decision settle scheduler 已启动(工作日 16:00);daily marking 16:30;"
        "metrics snapshot 16:35;integrity check 16:40;"
        "cohort batch %02d:%02d(COHORT_ENABLED=1 才实际跑批)",
        cohort_hour,
        cohort_minute,
    )
    return scheduler


def stop_scheduler(scheduler: BackgroundScheduler | None) -> None:
    """关闭 scheduler(None 安全)。"""
    if scheduler is not None:
        scheduler.shutdown(wait=False)
