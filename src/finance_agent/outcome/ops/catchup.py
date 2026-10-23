"""错过的日批触发补偿（delta add-ops-missed-run-catchup）。

``MemoryJobStore`` 重启后按 cron 重新注册、**不回溯**错过的触发点；``misfire_grace_time``
只覆盖「调度器存活但执行被延迟」，覆盖不了「进程在触发点不在」（incident 050：10-22
15:45 的因子衰减监控落在进程重建窗口内，当日 IC 采集零记录）。本模块在调度器启动时
补一次：**当天已过点、当天无成功运行**的白名单日批。

四道约束（缺一不可）：

1. **白名单**：只含幂等、当日补齐有价值的日批；账务/下单类（宁可人工重放动账）与
   LLM 烧钱类（夜间自愈不值这个钱）一律不自动重放，人工补跑照旧。
2. **时间窗**：计划时刻后 ``CATCH_UP_WINDOW``（6h）内才补；迟到过久对当日链已无意义。
3. **上限**：每次启动最多 ``CATCH_UP_MAX_JOBS``（3）个，按计划时刻升序截断——防「一次
   重启把重任务全跑一遍」。
4. **非交易日链中止**：快照返回 ``skipped=non_trading_day`` 时不再补下游（休市日下游
   必失败，继续只会刷一串 failed 行）。

规划与执行分离：``plan_catch_up`` 是纯函数（注入 ``now`` 与状态读数），``run_catch_up``
注入 ``runner``——两者都不碰真实调度器，测试零副作用。补跑一律经 ``run_job`` 落历史
（``source="catchup"``，可区分于 ``scheduled``/``manual``），共享单飞锁。
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from finance_agent.outcome.ops.jobs import (
    SCHEDULES,
    JobAlreadyRunning,
    run_job,
)
from finance_agent.outcome.ops.model import job_run_statuses_on

logger = logging.getLogger(__name__)

# 白名单：幂等 + 当日补齐有价值的日批（顺序无关，规划按计划时刻排序）。故意**不含**：
# - 账务/下单类 paper_execute_daily / paper_settle_daily / event_settle_daily
#   （自动重放动账的代价高于晚一天，交人工）；
# - LLM 烧钱类 cohort_batch / daily_brief_daily / factor_harvest_daily /
#   earnings_triage_daily / auto_pool_analysis_daily（夜间自愈不值得为此烧一次批）。
CATCH_UP_ALLOWLIST: tuple[str, ...] = (
    "market_snapshot_daily",
    "regime_daily",
    "screening_daily",
    "factor_decay_monitor",
    "factor_builtin_register",
    "decision_settle_daily",
    "daily_marking",
    "metrics_snapshot",
    "integrity_check",
)

# 时间窗：计划时刻后 6 小时内补（incident 050 的重建发生在 17:29，距 15:45 仅 1h44m）
CATCH_UP_WINDOW = timedelta(hours=6)
# 每次启动最多补 3 个（按计划时刻升序）
CATCH_UP_MAX_JOBS = 3
# 运行历史来源标记（job_runs.source）；kind 仍记 scheduled（run_job 既有映射不改）
CATCH_UP_SOURCE = "catchup"
# dev 反复重启的止血开关：=0 时调度器完全不注册补跑 job
CATCH_UP_ENV_SWITCH = "OPS_CATCHUP_ON_STARTUP"

_DEFAULT_TZ = "Asia/Shanghai"


def catch_up_enabled() -> bool:
    """启动补跑总开关（``OPS_CATCHUP_ON_STARTUP=0`` 关闭；默认开）。"""
    return os.getenv(CATCH_UP_ENV_SWITCH, "1") != "0"


def scheduled_fire_time(spec: Mapping[str, Any], now: datetime) -> datetime | None:
    """该任务**当天**的计划触发时刻；当天不触发 → ``None``。

    与 ``scheduler._cron_from_spec`` 同一语义：规格含 ``day_of_month`` → 只在那一天；
    否则看 ``day_of_week``（``mon-fri`` → 周末不触发，``*`` → 每天触发）。``now`` 为
    naive 时按排程时区解释（不猜系统时区，跨时区跑测试也确定）。
    """
    tz = ZoneInfo(str(spec.get("timezone", _DEFAULT_TZ)))
    local = now.replace(tzinfo=tz) if now.tzinfo is None else now.astimezone(tz)
    if "day_of_month" in spec:
        if local.day != int(spec["day_of_month"]):
            return None
    elif str(spec.get("day_of_week", "*")) == "mon-fri" and local.weekday() >= 5:
        return None
    return datetime(
        local.year,
        local.month,
        local.day,
        int(spec["hour"]),
        int(spec["minute"]),
        tzinfo=tz,
    )


def plan_catch_up(
    *,
    now: datetime,
    statuses_of: Callable[[str], Sequence[str]],
    schedules: Mapping[str, Mapping[str, Any]] | None = None,
    allowlist: Sequence[str] | None = None,
    window: timedelta = CATCH_UP_WINDOW,
    max_jobs: int = CATCH_UP_MAX_JOBS,
) -> list[dict[str, Any]]:
    """确定本次启动要补跑的任务（按计划时刻升序，已截断到 ``max_jobs``）。

    跳过条件：当天不触发 / 未到点 / 超出时间窗 / 当天已有 ``ok`` 或 ``running`` 行。
    ``failed`` **不**拦补跑——那正是值得在启动时再试一次的一类。
    """
    table = SCHEDULES if schedules is None else schedules
    jobs = CATCH_UP_ALLOWLIST if allowlist is None else allowlist
    due: list[dict[str, Any]] = []
    for job_id in jobs:
        spec = table.get(job_id)
        if spec is None:
            continue
        fire = scheduled_fire_time(spec, now)
        if fire is None:
            continue
        late = _as_aware(now, fire.tzinfo) - fire
        if late < timedelta(0) or late > window:
            continue
        statuses = set(statuses_of(job_id))
        if "ok" in statuses or "running" in statuses:
            continue
        due.append(
            {
                "job_id": job_id,
                "fire_time": fire.isoformat(),
                "late_seconds": int(late.total_seconds()),
            }
        )
    due.sort(key=lambda item: (item["fire_time"], item["job_id"]))
    return due[: max(0, int(max_jobs))]


def _as_aware(moment: datetime, tz: Any) -> datetime:
    """naive → 按目标时区补齐（aware 则原样转时区）。"""
    return moment.replace(tzinfo=tz) if moment.tzinfo is None else moment.astimezone(tz)


def _default_runner(db_path: Any) -> Callable[[str], dict[str, Any]]:
    """生产 runner：经统一入口（单飞锁 + 运行历史），``source=catchup`` 可审计。"""

    def run(job_id: str) -> dict[str, Any]:
        return run_job(job_id, source=CATCH_UP_SOURCE, db_path=db_path)

    return run


def _statuses_reader(db_path: Any, moment: datetime) -> Callable[[str], Sequence[str]]:
    day = moment.astimezone(ZoneInfo(_DEFAULT_TZ)).date().isoformat()

    def statuses(job_id: str) -> Sequence[str]:
        return job_run_statuses_on(job_id, day, db_path=db_path)

    return statuses


def run_catch_up(
    *,
    now: datetime | None = None,
    db_path: Any = None,
    runner: Callable[[str], dict[str, Any]] | None = None,
    statuses_of: Callable[[str], Sequence[str]] | None = None,
    schedules: Mapping[str, Mapping[str, Any]] | None = None,
    allowlist: Sequence[str] | None = None,
    window: timedelta = CATCH_UP_WINDOW,
    max_jobs: int = CATCH_UP_MAX_JOBS,
) -> dict[str, Any]:
    """按计划补跑今日漏掉的日批，返回 ``{"planned","executed","aborted"}``。

    顺序执行（快照先于下游，靠计划时刻序自然成立）；单个任务失败**不**中止后续，
    只有「非交易日」中止链——休市日下游必失败，继续只会刷失败行。异常一律内吞，
    只记日志（旁路铁律：补跑不得打断启动链路）。
    """
    moment = now if now is not None else datetime.now(ZoneInfo(_DEFAULT_TZ))
    read_statuses = statuses_of if statuses_of is not None else _statuses_reader(db_path, moment)
    plan = plan_catch_up(
        now=moment,
        statuses_of=read_statuses,
        schedules=schedules,
        allowlist=allowlist,
        window=window,
        max_jobs=max_jobs,
    )
    if not plan:
        return {"planned": [], "executed": [], "aborted": None}
    run = runner if runner is not None else _default_runner(db_path)
    logger.warning(
        "启动补跑：%d 个任务漏跑且在窗内（%s）",
        len(plan),
        ", ".join(str(item["job_id"]) for item in plan),
    )
    executed: list[dict[str, Any]] = []
    aborted: str | None = None
    for item in plan:
        job_id = str(item["job_id"])
        try:
            outcome = run(job_id)
        except JobAlreadyRunning:
            # 别的来源（手动/另一进程）正在跑：跳过而非失败，不制造噪音
            logger.info("启动补跑 %s 跳过：已在运行", job_id)
            executed.append({**item, "status": "skipped_running"})
            continue
        except Exception as exc:  # noqa: BLE001 - 补跑失败不得上抛到启动链路
            logger.warning("启动补跑 %s 异常：%s", job_id, exc, exc_info=True)
            executed.append({**item, "status": "failed", "error": f"{type(exc).__name__}: {exc}"})
            continue
        status = str(outcome.get("status", "unknown"))
        executed.append({**item, "status": status, "run_id": outcome.get("run_id")})
        summary = outcome.get("summary")
        if (
            job_id == "market_snapshot_daily"
            and isinstance(summary, dict)
            and summary.get("skipped") == "non_trading_day"
        ):
            aborted = "non_trading_day"
            logger.info("启动补跑中止：今日非交易日，下游不再补跑")
            break
    return {
        "planned": [str(item["job_id"]) for item in plan],
        "executed": executed,
        "aborted": aborted,
    }


def catch_up_job() -> dict[str, Any]:
    """APScheduler 入口（调度器启动后一次性触发）：任何异常只记日志。"""
    try:
        outcome = run_catch_up()
    except Exception as exc:  # noqa: BLE001 - 旁路铁律：补跑不得炸进程
        logger.warning("启动补跑整体失败（忽略）：%s", exc, exc_info=True)
        return {"error": f"{type(exc).__name__}: {exc}"}
    if outcome["executed"]:
        logger.warning("启动补跑完成：%s", outcome)
    return outcome
