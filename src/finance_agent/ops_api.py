"""运维 API 端点族(delta add-eval-ops-console Task 3/5):``/api/v1/ops/*``。

承载「日批状态 / 手动补跑 / cohort 开关与时刻 / 回测与探针 / 健康检查 / 报告注册表 /
预登记版本化 / 口径草稿」八个面,前端设置中心「评估运维」分区按本文件契约实现
(字段名逐字对齐实施计划 §Task 3、§Task 5)。

设计要点
--------
- **状态**：六任务排程 / 下次触发 / 最近运行 / 历史。``scheduler_running`` 以「句柄是真实
  ``BackgroundScheduler`` 且 ``running``」判定,不只看非 None——其它测试模块会把
  ``scheduler._scheduler`` 泄漏成 Mock,不得据此冒充「已运行」;未运行(TESTING=1 或
  显式禁用)如实返回 ``false``(200),不以 500 或空列表冒充正常。
- **cohort 行与 cohort 块同源**：``SCHEDULES["cohort_batch"]`` 只是调度器启动期刷新的
  快照(TESTING 下永不刷新),状态接口按 ``get_cohort_settings()`` 渲染该行,二者不可能矛盾。
- **悬挂 running 行清扫**：进程死亡遗留的 ``running`` 行在状态接口读到时就地收尾为
  ``failed``(reason ``stale_process_restart``);判定依据**首选所有权**——本进程开出的
  运行行登记在 ``ops.jobs.active_run_ids()``(按库作用域),在跑的行一律不扫(时钟可被回拨/人工回填,
  不可作硬证据);次要条件是「行开始时刻早于本进程启动时刻」,两者同时满足才清扫。
  清扫同一路径顺带 ``prune_job_runs(keep_per_job=PRUNE_KEEP_PER_JOB)`` 防历史表膨胀
  (裁剪不另记审计行:审计行本身会随下次裁剪被删,且每次轮询各记一行反而制造膨胀)。
- **运行历史取最新行**：定时失败每次尝试各留一行(重试语义),``last_run`` 只取最新行,
  ``history`` 保留最近 ``HISTORY_LIMIT`` 行供「重试了几次」可查;单条运行经
  ``get_job_run`` 直读(不依赖列表窗口,旧行仍可查)。
- **``GET /jobs`` 不得 500**：载荷组装整段兜底——意外 DB 故障(如 ``last_job_run`` 抛错)
  时返回降级但合法的载荷(调度器态 + 六任务骨架 ``last_run=null`` + 默认 cohort 块)
  并在顶层带 ``error`` 字段说明降级原因(spec「调度器未启动显式态」的「SHALL NOT 500」
  同款意图,故障不得表现为空白页或 500)。
- **手动补跑**：``run_job`` 在 ``asyncio.to_thread`` 中执行(不阻塞事件循环)。任务失败
  返回 dict(``status="failed"``)而非抛异常,端点必须把它翻成 **500**(非 2xx),
  失败原因同时经响应 ``detail`` 与运行历史双通道可见,不得伪装成 202 成功。
  异常映射:未知 job → 端点先按 ``JOB_IDS`` 显式 404(不靠 ``ValueError`` 兜底,
  否则 summary 序列化等真故障会被误报成「未知任务」);其余 ``ValueError`` → 500;
  ``JobAlreadyRunning`` / ``CohortDisabled`` → 409。
- **长任务(回测/探针/健康检查)**：同类单飞锁 + 后台线程执行,端点立即 202 返回
  ``run_id``;结果落 ``job_runs``(job_id = backtest/probe/health),经
  ``GET /runs/{run_id}`` 读取。summary 只放裁剪版(逐题 details / 逐标的一致率明细
  不进 summary),完整报告在 md/json。
- **formal 批门禁在任何回放之前**：预登记(本文件直接调 ``assert_preregistered``)
  → 抽样 → 干净窗口(``batches.prepare_backtest``),不过 → 409 + 原因,任务不派发;
  门禁通过的结果随任务透传(不重复取数)。``CleanWindowError`` 为 Task 4 层异常,
  运行期解析(见 ``_is_clean_window_refusal``),避免本模块导入期耦合 batch 层。
- **预登记版本化 / 口径草稿(Task 5,spec R6)**：``GET/PUT /prereg`` 列出/新建预登记版本
  (校验复用 CLI 同一套判定,不过 → 422 且不落盘);``PUT`` 可带可选 ``base_path`` 指名
  本次编辑的基准版本——该版本已有读数(``is_locked``)→ **409 ``locked`` 且磁盘零改动**
  (spec「已有读数的预登记锁定」),缺省保持「新建版本」语义;``GET /caliber`` 只读当前旋钮;
  ``POST /caliber-draft`` 只生成 OpenSpec delta 草稿(**不写**台账与代码常量,同旋钮已有
  草稿 → 409)。两类编辑各记一行 ``config-change`` 审计(旧值→新值)。

红线：烧钱语义(预算熔断 / 串行 / usage 真值记账)一行不改——本模块只做查看与触发,
全部安全语义沿用既有实现;cohort 开关关闭时手动跑批拒绝且零 LLM 调用。口径编辑只出草稿,
台账(``docs/evals/metrics.md``)与常量(``evals/outcome/caliber.py``)本模块零写入口。
"""

from __future__ import annotations

import asyncio
import logging
import re
import threading
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any, cast

from apscheduler.schedulers.background import BackgroundScheduler
from evals.causal_ablation.preregister import (
    OUTCOME_REQUIRED_FIELDS,
    MissingPreregistrationError,
    assert_preregistered,
)
from evals.causal_ablation.status_index import collect_status_index
from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from finance_agent.outcome.cohort.model import init_cohort_runs, list_cohort_runs
from finance_agent.outcome.cohort.runner import _resolve_budget as _cohort_budget
from finance_agent.outcome.daily_brief import get_daily_brief
from finance_agent.outcome.ops.catchup import scheduled_fire_time
from finance_agent.outcome.ops.jobs import (
    JOB_IDS,
    JOB_LABELS,
    SCHEDULES,
    CohortDisabled,
    JobAlreadyRunning,
    mark_run_active,
    mark_run_finished,
    run_job,
)
from finance_agent.outcome.ops.jobs import sweep_stale_running as sweep_stale_running_shared
from finance_agent.outcome.ops.model import (
    COHORT_DEFAULT_ENABLED,
    COHORT_DEFAULT_HOUR,
    COHORT_DEFAULT_MINUTE,
    COHORT_ENABLED_KEY,
    COHORT_HOUR_KEY,
    COHORT_MINUTE_KEY,
    finish_job_run,
    get_cohort_settings,
    get_job_run,
    insert_job_run,
    job_run_statuses_on,
    last_job_run,
    list_job_runs,
    prune_job_runs,
    set_config,
)
from finance_agent.outcome.ops.prereg import (
    KNOB_SOURCE,
    DraftExists,
    InvalidPreregistration,
    current_knobs,
    is_locked,
    list_prereg_versions,
    save_prereg_version,
    write_caliber_draft,
)
from finance_agent.outcome.scheduler import get_scheduler, reschedule_cohort

logger = logging.getLogger("finance_agent.ops_api")

router = APIRouter(prefix="/api/v1/ops", tags=["ops"])

# 每任务返回的最近运行条数(定时失败每次尝试各一行,故不只取 1 行——重试次数可查)
HISTORY_LIMIT = 10

# 运行历史裁剪线:每 job 保留最新 N 行(与悬挂清扫同一路径调用;值可按需调大)
PRUNE_KEEP_PER_JOB = 500

# 回测报告注册表目录(相对仓库根;测试可 patch)
BACKTEST_RESULTS_DIR = Path("evals/backtest/results")
# 正式批预登记门禁目录 + 本实验文档过滤(与 evals.backtest.run_backtest 同源约定)
PREREGISTER_DIR = Path("evals/ablation/preregister")
PREREGISTER_NAME_CONTAINS = "outcome"
# 口径 delta 草稿落点(测试可 patch;本模块对台账/代码常量零写入口)
# 落在 openspec/ 之外：草稿尚未成为 change，放进 changes/ 会让仓库级
# `openspec validate --all --strict` 因骨架不完整而报错（实测 56/0 → 56/1）。
# 人工评审通过后再由 owner 用 openspec new change 正式立项。
CALIBER_CHANGES_DIR = Path("docs/evals/caliber-drafts")
# R6「编辑留审计」:预登记保存与口径草稿各记一行 config-change(job_id 只作审计,不进 JOB_IDS)
PREREG_AUDIT_JOB = "prereg_save"
CALIBER_AUDIT_JOB = "caliber_draft"

# 报告定位标签:复用 evals 的 pathway/skill 词表 + 泄漏降级态(「上界证据」)
POSITIONING_PATHWAY = "pathway"
POSITIONING_SKILL = "skill"
POSITIONING_UPPER_BOUND = "upper_bound"

# 长任务:kind → (batch 层函数名, summary 裁剪器)
_TASK_FUNC_NAMES: dict[str, str] = {
    "backtest": "run_backtest_task",
    "probe": "run_probe_task",
    "health": "run_health_task",
}
# 同类长任务单飞锁(进程内;单 uvicorn worker 前提)
_KIND_LOCKS: dict[str, threading.Lock] = {kind: threading.Lock() for kind in _TASK_FUNC_NAMES}

# 本进程启动时刻:早于它的 running 行必为上一进程遗留(悬挂行清扫依据)
_PROCESS_STARTED_AT = datetime.now().isoformat()

_HIT_RATE_RE = re.compile(r"direction_hit_rate[：:]\s*([0-9]+(?:\.[0-9]+)?)\s*%")
_ROOT_KEYS = ("enabled", "hour", "minute")


# ── 请求模型 ──


class CohortUpdate(BaseModel):
    """cohort 开关/时刻局部更新(字段缺省 = 不改该项);越界由 pydantic 翻 422。"""

    enabled: bool | None = None
    hour: int | None = Field(default=None, ge=0, le=23)
    minute: int | None = Field(default=None, ge=0, le=59)


class BacktestRequest(BaseModel):
    batch_kind: str = "pathway"
    codes: list[str]
    per_regime: int = Field(default=10, ge=1, le=50)
    repeats: int = Field(default=3, ge=1, le=10)
    as_of: str | None = None


class ProbeRequest(BaseModel):
    codes: list[str]
    decision_date: str = Field(min_length=1)
    window_days: int = Field(default=20, ge=1, le=250)
    n_tickers: int = Field(default=10, ge=1, le=100)
    seed: int = 42


# ── 批次层函数解析(延迟导入:本模块导入期不拉起 evals/backtest 重依赖)──


def _resolve_batch_func(name: str) -> Callable[..., Any]:
    """按名解析 ``outcome.ops.batches`` 的进程内封装函数(Task 4)。"""
    from finance_agent.outcome.ops import batches

    return cast(Callable[..., Any], getattr(batches, name))


def _is_clean_window_refusal(exc: BaseException) -> bool:
    """是否 Task 4 的干净窗口拒绝(运行期解析该异常类型,避免导入期耦合 batch 层)。"""
    from finance_agent.outcome.ops.batches import CleanWindowError

    return isinstance(exc, CleanWindowError)


# ── 状态:六任务 + cohort ──


def _scheduler_running() -> bool:
    """调度器是否**真在运行**;未启动/句柄被换成就地 Mock → False。"""
    scheduler = get_scheduler()
    if not isinstance(scheduler, BackgroundScheduler):
        return False
    try:
        return bool(scheduler.running)
    except Exception:  # noqa: BLE001 - 状态接口不得因调度器内部异常 500
        logger.warning("读取 scheduler.running 失败,按未运行处理", exc_info=True)
        return False


def _next_fire_time(job_id: str) -> str | None:
    """该任务的下次触发时间(ISO);调度器未运行/无该任务 → None(不冒充)。"""
    scheduler = get_scheduler()
    if not isinstance(scheduler, BackgroundScheduler):
        return None
    try:
        jobs = scheduler.get_jobs()
    except Exception:  # noqa: BLE001 - 同上
        logger.warning("读取 scheduler jobs 失败,next_fire_time 按 None 处理", exc_info=True)
        return None
    for job in jobs:
        if getattr(job, "id", None) == job_id:
            next_run = getattr(job, "next_run_time", None)
            return next_run.isoformat() if next_run is not None else None
    return None


def _sweep_stale_running() -> list[int]:
    """清扫上一进程遗留的悬挂 ``running`` 行 → ``failed``(stale_process_restart)。

    实现共享自 ``outcome.ops.jobs.sweep_stale_running``(issue #455 起,启动补跑的
    前置清扫是同一函数):所有权 + 时钟双判据、失败只记日志。本模块的
    ``_PROCESS_STARTED_AT`` 经参数注入(既有测试从外部 patch 该值)。
    """
    return sweep_stale_running_shared(process_started_at=_PROCESS_STARTED_AT)


def _prune_history() -> int:
    """每 job 只保留最新 ``PRUNE_KEEP_PER_JOB`` 行(与悬挂清扫同一路径调用,防表膨胀)。

    不记审计行:审计行本身会被后续裁剪删除,且每次状态轮询各记一行反而制造膨胀——
    裁剪是幂等的维护动作,返回值只进日志。
    """
    try:
        deleted = prune_job_runs(keep_per_job=PRUNE_KEEP_PER_JOB)
    except Exception:  # noqa: BLE001 - 裁剪失败不得阻断状态读取
        logger.warning("job_runs 历史裁剪失败(忽略)", exc_info=True)
        return 0
    if deleted:
        logger.info("job_runs 历史裁剪:每 job 保留 %d 行,删除 %d 行", PRUNE_KEEP_PER_JOB, deleted)
    return deleted


def _today_cohort_stats() -> dict[str, int]:
    """今日 cohort 记账汇总(花费 = 已记 token 真值之和;skipped 行无 usage 不计)。"""
    day = datetime.now().strftime("%Y-%m-%d")
    try:
        init_cohort_runs()  # 幂等建表(读接口首次调用不因缺表报 no such table)
        rows = list_cohort_runs(trade_date=day)
    except Exception:  # noqa: BLE001 - 今日花费读取失败不得使状态接口 500
        logger.warning("读取今日 cohort 记账失败,按零披露", exc_info=True)
        return {"spend": 0, "success": 0, "failure": 0}
    return {
        "spend": sum(int(row["tokens_total"] or 0) for row in rows),
        "success": sum(1 for row in rows if row["status"] == "success"),
        "failure": sum(1 for row in rows if row["status"] == "failure"),
    }


def _cohort_block(settings: dict[str, Any]) -> dict[str, Any]:
    """cohort 状态块(``/jobs`` 与 ``/cohort`` 共用同一形状,前端逐字消费)。"""
    stats = _today_cohort_stats()
    return {
        "enabled": settings["enabled"],
        "hour": settings["hour"],
        "minute": settings["minute"],
        "budget_tokens": _cohort_budget(None),
        "today_spend": stats["spend"],
        "today_success": stats["success"],
        "today_failure": stats["failure"],
    }


def _now() -> datetime:
    """状态接口的时钟（本地时区）；测试 patch 它即可冻结「今天几点」。

    与 ``scheduled_fire_time`` 的 naive 约定一致：naive 时刻按其排程时区解释
    （本系统所有日批均为 Asia/Shanghai）。
    """
    return datetime.now()


def _missed_today(job_id: str, spec: dict[str, Any], now: datetime) -> bool:
    """当天计划时刻已过，且当天**没有任何运行行**（``running`` 也算有）。

    「压根没跑」与「跑了但失败」是两种故障：后者由 ``last_run.status`` 表达，
    混成一个布尔会让值班分不清该重跑还是该查日志。非排程日（周末 / 月批非排程日）
    或尚未到点 → ``False``（当天本无触发点，不构成漏跑）。
    """
    fire = scheduled_fire_time(spec, now)
    if fire is None:
        return False
    moment = now.replace(tzinfo=fire.tzinfo) if now.tzinfo is None else now.astimezone(fire.tzinfo)
    if moment < fire:
        return False
    return not job_run_statuses_on(job_id, moment.date().isoformat())


def _job_rows(cohort: dict[str, Any]) -> list[dict[str, Any]]:
    """六任务状态行(顺序 = JOB_IDS = 调度注册顺序)。"""
    rows: list[dict[str, Any]] = []
    moment = _now()  # 一次取齐：同一响应内各行不得跨越不同的「今天」
    for job_id in JOB_IDS:
        spec = dict(SCHEDULES[job_id])
        if job_id == "cohort_batch":
            # 快照可能陈旧(只在 start_scheduler/reschedule_cohort 刷新):以配置真源为准,
            # 保证该行与 cohort 块不可能互相矛盾。
            spec["hour"], spec["minute"] = cohort["hour"], cohort["minute"]
        rows.append(
            {
                "job_id": job_id,
                "label": JOB_LABELS[job_id],
                "schedule": spec,
                "next_fire_time": _next_fire_time(job_id),
                "last_run": last_job_run(job_id),
                "history": list_job_runs(job_id, limit=HISTORY_LIMIT),
                "missed_today": _missed_today(job_id, spec, moment),
            }
        )
    return rows


def _jobs_payload() -> dict[str, Any]:
    """状态接口全量载荷(阻塞式实现;端点经 ``asyncio.to_thread`` 调用)。

    整段兜底(spec「SHALL NOT 500」):意外 DB 故障不得把状态接口打成 500,降级载荷
    见 :func:`_degraded_jobs_payload`(顶层多一个 ``error`` 字段说明降级原因)。
    """
    try:
        _sweep_stale_running()
        _prune_history()
        settings = get_cohort_settings()
        cohort = _cohort_block(settings)
        return {
            "scheduler_running": _scheduler_running(),
            "jobs": _job_rows(cohort),
            "cohort": cohort,
        }
    except Exception as exc:  # noqa: BLE001 - 状态接口不得因任一读失败 500
        logger.exception("状态载荷组装失败,返回降级载荷")
        return _degraded_jobs_payload(exc)


def _degraded_jobs_payload(exc: BaseException) -> dict[str, Any]:
    """降级载荷:调度器态 + 六任务骨架(``last_run=None``)+ 默认 cohort 块 + ``error``。

    ``error`` 是本端点正常载荷没有的**额外字段**(前端按可选字段消费):出现即表示
    状态读数不完整,界面应显式披露而不是把 ``last_run=null`` 读成「从未运行过」。
    """
    error = f"{type(exc).__name__}: {exc}"
    try:
        cohort = _cohort_block(get_cohort_settings())
    except Exception:  # noqa: BLE001 - 配置读取也失败 → 用模型默认值兜底(仍是合法 cohort 块)
        logger.warning("降级载荷读取 cohort 配置失败,按默认值披露", exc_info=True)
        cohort = {
            "enabled": COHORT_DEFAULT_ENABLED,
            "hour": COHORT_DEFAULT_HOUR,
            "minute": COHORT_DEFAULT_MINUTE,
            "budget_tokens": _cohort_budget(None),
            "today_spend": 0,
            "today_success": 0,
            "today_failure": 0,
        }
    jobs: list[dict[str, Any]] = [
        {
            "job_id": job_id,
            "label": JOB_LABELS[job_id],
            "schedule": dict(SCHEDULES[job_id]),
            "next_fire_time": _next_fire_time(job_id),
            "last_run": None,
            "history": [],
        }
        for job_id in JOB_IDS
    ]
    return {
        "scheduler_running": _scheduler_running(),
        "jobs": jobs,
        "cohort": cohort,
        "error": error,
    }


# ── 端点:状态 / 补跑 / 单条运行 ──


@router.get("/stream-backlog")
async def get_stream_backlog() -> dict[str, Any]:
    """活跃会话流投递积压快照(session-streaming「发布积压可观测与背压」)。

    返回 ``{"sessions": {session_id: {drops, subscribers, queue_depths, last_seq}}}``。
    纯内存读(registry 进程内结构,单 worker 前提),无活跃会话时返回空映射,
    不以 500 或空数据冒充异常。
    """
    from finance_agent.stream_registry import registry as stream_registry

    return {"sessions": stream_registry.all_backlog_stats()}


@router.get("/jobs")
async def get_jobs() -> dict[str, Any]:
    """六任务排程 / 下次触发 / 最近运行 / 历史 + cohort 块(调度器未运行显式披露)。

    正常载荷键:``scheduler_running`` / ``jobs`` / ``cohort``;降级时额外带顶层 ``error``
    字符串(见 :func:`_degraded_jobs_payload`),前端按可选字段消费。
    """
    return await asyncio.to_thread(_jobs_payload)


@router.post("/jobs/{job_id}/run", status_code=202)
async def trigger_job(job_id: str) -> dict[str, Any]:
    """手动补跑(补漏跑):与定时触发同一入口(单飞锁 + 运行历史)。

    202 = 已执行且成功(``run_job`` 同步完成,经线程执行不阻塞事件循环);
    404 = 未知任务(端点先按 ``JOB_IDS`` 显式判定);409 = 已在运行 / cohort 开关关闭;
    500 = 任务执行失败(原因在 detail 与运行历史,不得伪装 202)。
    """
    if job_id not in JOB_IDS:
        raise HTTPException(status_code=404, detail=f"未知 job_id: {job_id!r}")
    try:
        outcome = await asyncio.to_thread(run_job, job_id, source="manual")
    except ValueError as exc:
        # 未知 job 已在上面拦掉;此处 ValueError 是真故障(summary 序列化失败等)→ 500,
        # 不得一律翻 404 把真错误伪装成「未知任务」。
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    except JobAlreadyRunning as exc:
        raise HTTPException(status_code=409, detail="already_running") from exc
    except CohortDisabled as exc:
        raise HTTPException(status_code=409, detail="cohort_disabled") from exc
    if outcome["status"] == "failed":
        raise HTTPException(
            status_code=500,
            detail=f"run_failed（run_id={outcome['run_id']}）：{outcome['error']}",
        )
    return {"run_id": outcome["run_id"]}


@router.get("/runs/{run_id}")
async def get_run(run_id: int) -> dict[str, Any]:
    """单条运行(含 summary/error):补跑、定时批与长任务(backtest/probe/health)同源可查。

    按 ``run_id`` 直读(``get_job_run``),不经「最新 1000 行」窗口——旧行仍可达。
    """
    row = await asyncio.to_thread(get_job_run, run_id)
    if row is None:
        raise HTTPException(status_code=404, detail="run_not_found")
    return row


@router.get("/daily-brief")
async def get_daily_brief_by_date(date: str | None = None) -> Any:
    """每日 LLM 运营汇报（add-auto-pool-analysis-daily-brief）。

    指定 ``date=YYYY-MM-DD`` 读取该日；缺省读取最新一份。无行 → 404 +
    ``daily_brief_not_found``（不以空文档冒充已生成）。
    """
    row = await asyncio.to_thread(get_daily_brief, date)
    if row is None:
        return JSONResponse(status_code=404, content={"error": "daily_brief_not_found"})
    return row


@router.get("/daily-brief/latest")
async def get_latest_daily_brief() -> Any:
    """最新一份每日汇报（按 ``brief_date`` 倒序）。"""
    row = await asyncio.to_thread(get_daily_brief, None)
    if row is None:
        return JSONResponse(status_code=404, content={"error": "daily_brief_not_found"})
    return row


@router.get("/research/analyst-scorecard")
async def get_analyst_scorecard(
    snapshot_date: str | None = None,
    bucket_type: str | None = None,
    bucket_key: str | None = None,
) -> dict[str, Any]:
    """分析师事后检验只读记分卡（add-analyst-ex-post-verification）。

    缺省读取最新快照；按桶类型/键过滤。空态返回 ``status=empty`` + 空列表，
    不以伪造计数冒充有样本。
    """
    from finance_agent.data import analyst_claims_store

    def _load() -> dict[str, Any]:
        date_value = snapshot_date or analyst_claims_store.latest_snapshot_date()
        rows = analyst_claims_store.list_scorecard(
            snapshot_date=date_value,
            bucket_type=bucket_type,
            bucket_key=bucket_key,
        )
        return {
            "snapshot_date": date_value,
            "bucket_type": bucket_type,
            "bucket_key": bucket_key,
            "rows": rows,
            "status": "ok" if rows else "empty",
        }

    return await asyncio.to_thread(_load)


@router.get("/research/foreign-sentiment")
async def get_foreign_sentiment(
    broker: str | None = None,
    action: str | None = None,
    target_type: str | None = None,
    direction: str | None = None,
    start: str | None = None,
    end: str | None = None,
) -> dict[str, Any]:
    """外资二手快讯只读情绪序列（add-foreign-broker-sentiment）。

    v1 只读观测：返回事件与方向/机构计数；不进入任何分析师上下文或决策路径。
    """
    from finance_agent.data.foreign_broker_news import list_foreign_sentiment

    return await asyncio.to_thread(
        list_foreign_sentiment,
        broker=broker,
        action=action,
        target_type=target_type,
        direction=direction,
        start=start,
        end=end,
    )


@router.get("/research/scorecards")
async def get_scorecards(
    snapshot_date: str | None = None,
    foreign_snapshot_date: str | None = None,
) -> dict[str, Any]:
    """内外资分开打分统一只读视图（add-region-split-broker-scoring）。

    两块各自保留指标族：内资 event_study（胜率/超额），外资 sentiment（命中率/情绪净分）。
    任一块为空时显式 status=empty，不互相填充。
    """
    from finance_agent.data import analyst_claims_store
    from finance_agent.outcome.foreign_broker_scoring import (
        latest_snapshot_date,
        list_foreign_scorecard,
    )

    def _load() -> dict[str, Any]:
        domestic_date = snapshot_date or analyst_claims_store.latest_snapshot_date()
        domestic_rows = analyst_claims_store.list_scorecard(snapshot_date=domestic_date)
        foreign_date = foreign_snapshot_date or latest_snapshot_date()
        foreign_rows = list_foreign_scorecard(snapshot_date=foreign_date)
        return {
            "domestic": {
                "snapshot_date": domestic_date,
                "score_family": "event_study",
                "status": "ok" if domestic_rows else "empty",
                "rows": domestic_rows,
            },
            "foreign": {
                "snapshot_date": foreign_date,
                "score_family": "sentiment",
                "status": "ok" if foreign_rows else "empty",
                "rows": foreign_rows,
            },
        }

    return await asyncio.to_thread(_load)


# ── 端点:cohort 开关与时刻 ──


@router.get("/cohort")
async def get_cohort() -> dict[str, Any]:
    """cohort 开关 / 时刻 / 预算上限 / 今日花费与成功失败。"""
    return await asyncio.to_thread(lambda: _cohort_block(get_cohort_settings()))


def _apply_cohort_update(req: CohortUpdate) -> dict[str, Any]:
    """写配置 + 即时重排 + config-change 审计行(旧值→新值)。阻塞式,由端点入线程。"""
    before = get_cohort_settings()
    if req.enabled is not None:
        set_config(COHORT_ENABLED_KEY, "1" if req.enabled else "0")
    if req.hour is not None:
        set_config(COHORT_HOUR_KEY, str(req.hour))
    if req.minute is not None:
        set_config(COHORT_MINUTE_KEY, str(req.minute))
    after = get_cohort_settings()
    # 时刻改动即时生效(无需重启);无调度器(TESTING)时返回 False,配置已落库重启即生效
    rescheduled = reschedule_cohort(after["hour"], after["minute"])
    summary = {
        "from": {key: before[key] for key in _ROOT_KEYS},
        "to": {key: after[key] for key in _ROOT_KEYS},
        "rescheduled": rescheduled,
    }
    audit_run_id = insert_job_run(
        "cohort_batch", "config-change", "ok", source="manual", summary=summary
    )
    finish_job_run(audit_run_id, status="ok")
    logger.info(
        "cohort 配置变更: %s → %s(rescheduled=%s)", summary["from"], summary["to"], rescheduled
    )
    return {
        **_cohort_block(after),
        "audit_run_id": audit_run_id,
        "rescheduled": rescheduled,
    }


@router.put("/cohort")
async def update_cohort(req: CohortUpdate) -> dict[str, Any]:
    """更新 cohort 开关/时刻(局部字段语义);返回新状态 + 审计行 id + 是否已重排。"""
    return await asyncio.to_thread(_apply_cohort_update, req)


# ── 端点:报告注册表 ──


def _positioning_of(text: str) -> str | None:
    """从报告正文判定位标签:泄漏降级(上界证据) > 通路验证;未标注 → None。"""
    if "泄漏污染下的上界证据" in text:
        return POSITIONING_UPPER_BOUND
    if "通路验证" in text:
        return POSITIONING_PATHWAY
    if re.search(r"定位[：:]\s*skill|positioning[：:]\s*skill", text):
        return POSITIONING_SKILL
    return None


def _probe_hit_rate_of(text: str) -> float | None:
    """从正文取探针方向命中率(md 渲染为百分数);缺失/「未提供」→ None(不折算 0)。"""
    match = _HIT_RATE_RE.search(text)
    return round(float(match.group(1)) / 100.0, 4) if match else None


def _reports_payload() -> list[dict[str, Any]]:
    """回测报告注册表(只读):status 徽章 + 取代者 + 定位标签 + 探针方向命中率。"""
    try:
        entries, _unstamped = collect_status_index(BACKTEST_RESULTS_DIR)
    except ValueError as exc:
        # 有缺陷的报告(如 superseded-by 缺目标)不静默跳过:整表显式报错
        raise HTTPException(status_code=500, detail=f"报告注册表存在缺陷报告:{exc}") from exc
    reports: list[dict[str, Any]] = []
    for entry in entries:
        try:
            text = Path(entry.path).read_text(encoding="utf-8")
        except OSError:
            logger.warning(
                "报告正文读取失败,定位/探针字段按缺失披露: %s", entry.path, exc_info=True
            )
            text = ""
        reports.append(
            {
                "name": Path(entry.path).stem,
                "path": entry.path,
                "status": entry.status,
                "target": entry.target,
                "positioning": _positioning_of(text),
                "probe_direction_hit_rate": _probe_hit_rate_of(text),
            }
        )
    return reports


@router.get("/reports")
async def get_reports() -> list[dict[str, Any]]:
    """回测报告注册表(``evals/backtest/results/*.md`` 的 status 头 + 披露摘要)。"""
    return await asyncio.to_thread(_reports_payload)


# ── 端点:长任务(回测 / 探针 / 健康检查) ──


def _trim_backtest_summary(report: dict[str, Any]) -> dict[str, Any]:
    """报告 → 运行历史 summary(裁剪版)。

    实施计划 §Task 4 的裁剪清单按**字段路径**落地为嵌套 JSON:
    ``conclusion`` / ``positioning`` / ``n_sample`` / ``leakage_probe.direction_hit_rate``
    / ``perf_table.system.Sharpe``;另补 ``batch_kind`` / ``n_consistent`` /
    ``clean_window`` / 探针三态读数 / ``report_paths``——逐标的一致率明细不进 summary,
    完整报告在 md/json(``report_paths`` 指路)。
    """
    probe = report.get("leakage_probe") or {}
    system = (report.get("perf_table") or {}).get("system") or {}
    return {
        "batch_kind": report.get("batch_kind"),
        "conclusion": report.get("conclusion"),
        "positioning": report.get("positioning"),
        "n_sample": report.get("n_sample"),
        "n_consistent": report.get("n_consistent"),
        "clean_window": report.get("clean_window"),
        "leakage_probe": {
            key: probe.get(key)
            for key in (
                "state",
                "direction_hit_rate",
                "unknown_ratio",
                "threshold",
                "probe_n",
                "probe_window",
                "downgraded",
            )
        },
        "perf_table": {"system": {"Sharpe": system.get("Sharpe")}},
        "report_paths": report.get("report_paths"),
    }


def _trim_probe_summary(reading: dict[str, Any]) -> dict[str, Any]:
    """探针读数 → summary:三态读数 + 窗口披露字段,逐题 ``details`` 不进(体积大且非披露面)。

    ``probe_window`` / ``per_window`` 不是 ``run_leakage_probe`` 的返回键:单跑只有一个窗口,
    由 ``batches.run_probe_task`` 按请求入参(``decision_date`` + ``window_days``)合成一条
    逐窗口条目(形状对齐 ``report.aggregate_probes``),本裁剪器原样透传;若上游未给
    (旧行/异常路径)则如实为 null,不在此处编造窗口。
    """
    return {
        key: reading.get(key)
        for key in (
            "state",
            "probe_n",
            "questions_per_ticker",
            "direction_hit_rate",
            "magnitude_hit_rate",
            "event_hit_rate",
            "unknown_ratio",
            "threshold",
            "downgraded",
            "probe_window",
            "per_window",
            "probes_missing",
        )
    }


def _trim_health_summary(reading: dict[str, Any]) -> dict[str, Any]:
    """健康检查读数本身即门禁披露面(体量小),原样入 summary。"""
    return reading


_TASK_SUMMARY: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {
    "backtest": _trim_backtest_summary,
    "probe": _trim_probe_summary,
    "health": _trim_health_summary,
}


def _finish_run_safely(run_id: int, **kwargs: Any) -> None:
    try:
        finish_job_run(run_id, **kwargs)
    except Exception:  # noqa: BLE001 - 收尾落库失败只记日志(锁必须照常释放)
        logger.exception("长任务终态落库失败 run_id=%s", run_id)


def _run_task(kind: str, run_id: int, kwargs: dict[str, Any]) -> None:
    """后台线程体:跑长任务 → 落终态;任何失败都不向线程外抛(旁路铁律)。"""
    try:
        try:
            func = _resolve_batch_func(_TASK_FUNC_NAMES[kind])
            result = func(**kwargs)
            summary = (
                _TASK_SUMMARY[kind](result) if isinstance(result, dict) else {"result": str(result)}
            )
        except Exception as exc:  # noqa: BLE001 - 任务失败落历史,线程不得炸进程
            error = f"{type(exc).__name__}: {exc}"
            logger.warning(
                "ops 长任务失败 kind=%s run_id=%s: %s", kind, run_id, error, exc_info=True
            )
            _finish_run_safely(run_id, status="failed", error=error)
            return
        _finish_run_safely(run_id, status="ok", summary=summary)
        logger.info("ops 长任务完成 kind=%s run_id=%s", kind, run_id)
    finally:
        mark_run_finished(run_id)  # 收尾即注销所有权(悬挂清扫据此恢复时钟判定)
        _KIND_LOCKS[kind].release()


def _spawn_task(kind: str, kwargs: dict[str, Any]) -> int:
    """单飞 + 开运行行 + 起后台线程;同类任务已在运行 → 409(阻塞式,由端点在调用)。"""
    lock = _KIND_LOCKS[kind]
    if not lock.acquire(blocking=False):
        raise HTTPException(status_code=409, detail="already_running")
    try:
        run_id = insert_job_run(kind, "manual", "running", source="manual")
    except Exception:
        lock.release()
        raise
    # 所有权登记在建行之后、线程启动之前:清扫与派发之间不留「时钟可误杀」的窗口
    mark_run_active(run_id)
    thread = threading.Thread(
        target=_run_task,
        args=(kind, run_id, kwargs),
        name=f"ops-{kind}-{run_id}",
        daemon=True,
    )
    try:
        thread.start()
    except BaseException:
        mark_run_finished(run_id)
        lock.release()
        raise
    return run_id


async def _dispatch(kind: str, kwargs: dict[str, Any]) -> dict[str, Any]:
    """派发长任务:立即 202(不阻塞事件循环),结果经 ``GET /runs/{run_id}`` 读取。"""
    run_id = await asyncio.to_thread(_spawn_task, kind, kwargs)
    return {"run_id": run_id}


async def _preflight_formal(req: BacktestRequest) -> dict[str, Any]:
    """正式批同步前置门禁(**任何回放之前**):预登记 → 抽样 + 干净窗口。

    返回 ``prepare_backtest`` 的取数结果,随任务透传(不重复抽样/取数);
    不过时抛 ``MissingPreregistrationError`` / ``CleanWindowError``,由端点翻 409。
    """
    assert_preregistered(
        PREREGISTER_DIR,
        name_contains=PREREGISTER_NAME_CONTAINS,
        required_fields=OUTCOME_REQUIRED_FIELDS,
    )
    prepare = _resolve_batch_func("prepare_backtest")
    return await asyncio.to_thread(
        prepare,
        batch_kind=req.batch_kind,
        codes=req.codes,
        per_regime=req.per_regime,
        as_of=req.as_of,
    )


@router.post("/backtest", status_code=202)
async def trigger_backtest(req: BacktestRequest) -> dict[str, Any]:
    """发起回测批(通路验证 / 正式)。

    202 = 已派发(结果经 ``/runs/{run_id}`` 与报告注册表查看);409 = 门禁拒绝(预登记
    缺失/无效、干净窗口未过)或同类批已在运行;422 = 参数非法。
    """
    if req.batch_kind not in ("pathway", "formal"):
        raise HTTPException(status_code=422, detail=f"未知批次类型: {req.batch_kind!r}")
    if not req.codes:
        raise HTTPException(status_code=422, detail="codes 不得为空")
    kwargs: dict[str, Any] = {
        "batch_kind": req.batch_kind,
        "codes": req.codes,
        "per_regime": req.per_regime,
        "repeats": req.repeats,
        "as_of": req.as_of,
    }
    if req.batch_kind == "formal":
        try:
            kwargs["prepared"] = await _preflight_formal(req)
        except MissingPreregistrationError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ValueError as exc:
            # 标的池不足 per_regime / 指数历史未覆盖三 regime:参数或数据前置条件不满足,
            # 422 + 原因原文(裸 500 会让界面无话可说;这不是「已在运行」也不是门禁拒绝)
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except Exception as exc:  # noqa: BLE001 - 只把干净窗口拒绝翻 409,其余原样上抛
            if not _is_clean_window_refusal(exc):
                raise
            raise HTTPException(status_code=409, detail=str(exc)) from exc
    return await _dispatch("backtest", kwargs)


@router.post("/probe", status_code=202)
async def trigger_probe(req: ProbeRequest) -> dict[str, Any]:
    """发起泄漏探针单跑(烧钱动作:前端已确认);结果经 ``/runs/{run_id}`` 读三态读数。"""
    if not req.codes:
        raise HTTPException(status_code=422, detail="codes 不得为空")
    return await _dispatch(
        "probe",
        {
            "codes": req.codes,
            "decision_date": req.decision_date,
            "window_days": req.window_days,
            "n_tickers": req.n_tickers,
            "seed": req.seed,
        },
    )


@router.post("/health", status_code=202)
async def trigger_health() -> dict[str, Any]:
    """发起 outcome 收口健康检查(只读;门禁读数与阈值随结果披露)。"""
    return await _dispatch("health", {})


# ── 端点:预登记版本化(GET 列表 / PUT 新建版本) ──


def _prereg_payload() -> list[dict[str, Any]]:
    """预登记版本列表(按文件名升序;valid/issues 与 fields 出自 CLI 同一解析器)。

    ``locked`` = 该版本已产生读数(回测报告 ``**预登记**:`` 行引用,或 cohort 记账已成功——
    后者为退化判定,见 ``ops.prereg._cohort_readings_lock`` 的已知风险说明)。
    """
    return list_prereg_versions(
        dir=PREREGISTER_DIR,
        name_contains=PREREGISTER_NAME_CONTAINS,
        backtests_dir=BACKTEST_RESULTS_DIR,
    )


def _save_prereg_with_audit(fields: dict[str, str]) -> Path:
    """新建预登记版本 + config-change 审计行(旧最新版本 → 新版本);校验不过不落盘。"""
    previous = _prereg_payload()
    path = save_prereg_version(fields, dir=PREREGISTER_DIR)
    run_id = insert_job_run(
        PREREG_AUDIT_JOB,
        "config-change",
        "ok",
        source="manual",
        summary={
            "object": "prereg",
            "from": previous[-1]["path"] if previous else None,
            "to": path.as_posix(),
            "fields": sorted(fields),
        },
    )
    finish_job_run(run_id, status="ok")
    logger.info("预登记新版本(界面保存): %s → %s", previous[-1]["path"] if previous else None, path)
    return path


@router.get("/prereg")
async def get_prereg() -> list[dict[str, Any]]:
    """预登记版本列表:``[{"path","fields","valid","issues","locked"}]``(锁定 = 已有读数)。"""
    return await asyncio.to_thread(_prereg_payload)


@router.put("/prereg", status_code=201)
async def save_prereg(payload: dict[str, str | None]) -> dict[str, Any]:
    """保存预登记**新版本**(历史版本永不覆盖);字段不齐 / 阈值缺依据 → 422 + 缺字段清单。

    可选 ``base_path``(取自 ``GET /prereg`` 行的 ``path``)指名本次编辑的**基准版本**:
    该版本已有读数(``is_locked``)→ **409 ``locked`` 且磁盘零改动**(spec「已有读数的
    预登记锁定」:锁定版本不可编辑,须另建新版本)。缺省 ``base_path`` 保持「新建版本」
    语义(不读任何历史版本,历史版本本来就不被覆盖)。

    422 时磁盘零改动(校验在落盘之前):界面据此展示「缺哪些字段」且不会留下半成品版本。
    """
    base_path = payload.pop("base_path", None)
    fields = {key: value for key, value in payload.items() if value is not None}
    if base_path:
        locked = await asyncio.to_thread(
            is_locked,
            Path(base_path),
            db_path=None,
            backtests_dir=BACKTEST_RESULTS_DIR,
        )
        if locked:
            logger.info("预登记保存被拒绝(基准版本已有读数,锁定): %s", base_path)
            raise HTTPException(status_code=409, detail="locked")
    try:
        path = await asyncio.to_thread(_save_prereg_with_audit, fields)
    except InvalidPreregistration as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"path": path.as_posix()}


# ── 端点:口径查看(只读)+ delta 草稿 ──


@router.get("/caliber")
async def get_caliber() -> dict[str, Any]:
    """当前口径旋钮值(只读):UI 在口径草稿表单旁并列展示现值,防「看不见现值就改值」。"""
    return {"knobs": await asyncio.to_thread(current_knobs), "source": KNOB_SOURCE}


def _draft_with_audit(knobs: dict[str, float]) -> Path:
    """生成口径 delta 草稿 + config-change 审计行(旧值→新值);同旋钮已有草稿 → DraftExists。"""
    before = current_knobs()
    draft = write_caliber_draft(knobs, changes_dir=CALIBER_CHANGES_DIR)
    run_id = insert_job_run(
        CALIBER_AUDIT_JOB,
        "config-change",
        "ok",
        source="manual",
        summary={
            "object": "caliber",
            "from": {key: before[key] for key in knobs},
            "to": dict(knobs),
            "draft_dir": draft.as_posix(),
        },
    )
    finish_job_run(run_id, status="ok")
    logger.info("口径草稿(界面生成): %s（台账与代码常量未改动）", draft)
    return draft


@router.post("/caliber-draft", status_code=201)
async def create_caliber_draft(knobs: dict[str, float]) -> dict[str, Any]:
    """旋钮修改 → OpenSpec delta 草稿 + §2 切点行草稿(界面须明示「生效须走 delta 流程」)。

    201 = 草稿已生成(**``docs/evals/metrics.md`` 与 ``evals/outcome/caliber.py`` 零改动**);
    409 = 同一旋钮已有未处理草稿(``draft_exists``);422 = 未知旋钮 / 空变更。
    """
    try:
        draft = await asyncio.to_thread(_draft_with_audit, knobs)
    except DraftExists as exc:
        logger.info("口径草稿已存在,拒绝重复生成: %s", exc)
        raise HTTPException(status_code=409, detail="draft_exists") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"draft_dir": draft.as_posix()}
