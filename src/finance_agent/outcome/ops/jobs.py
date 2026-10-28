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
- 运行行的**所有权**:本进程开出的 running 行登记在 ``active_run_ids()``(收尾即注销;
  按库作用域,run_id 只在其所属库内唯一),悬挂行清扫据此跳过——时钟只作次要条件,
  所有权才是「本进程在跑」的硬证据。

``JOB_FUNCS`` 是默认注册表;``run_job`` 的 ``func`` 参数供 scheduler 传入**它自己
模块级**的任务函数引用——既有测试在那里打桩,且保证调用期才解析(不把 env/config
冻结在导入期)。
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

from finance_agent.data.foreign_broker_news import run_foreign_broker_news_daily
from finance_agent.data.research_reports import (
    ingest_inbox,
    run_forecasts_daily,
    run_ratings_daily,
    run_reports_daily,
)
from finance_agent.data.snapshot_job import MARKET_SNAPSHOT_SCHEDULE, run_market_snapshot_batch
from finance_agent.execution.brokers.paper import run_paper_settle_daily
from finance_agent.execution.driver import run_execution_daily
from finance_agent.factors.builtin import run_factor_builtin_register
from finance_agent.factors.diagnostics import run_factor_diagnostics
from finance_agent.factors.harvest import run_factor_harvest_until_drained
from finance_agent.factors.monitor import run_factor_decay_monitor
from finance_agent.factors.monthly import run_factor_proposal_monthly
from finance_agent.memory.monthly import run_memory_extract_monthly
from finance_agent.outcome.analyst_claims.job import run_analyst_claim_settle_daily
from finance_agent.outcome.cohort.runner import run_cohort_batch
from finance_agent.outcome.daily_brief import run_daily_brief
from finance_agent.outcome.events.job import (
    run_cb_daily,
    run_earnings_fetch_daily,
    run_earnings_triage_daily,
    run_event_settle_daily,
)
from finance_agent.outcome.ops.model import (
    _default_db_path,
    finish_job_run,
    get_cohort_settings,
    insert_job_run,
    list_job_runs,
)
from finance_agent.outcome.regime.job import run_regime_daily
from finance_agent.outcome.track_record.job import settle_open_predictions
from finance_agent.outcome.track_record.marking import persist_metrics_snapshot, run_daily_marking
from finance_agent.outcome.track_record.model import integrity_check
from finance_agent.screening.pipeline import run_microcap_lane, run_screening_daily
from finance_agent.screening.pool_analysis import run_pool_analysis_daily

logger = logging.getLogger(__name__)

# 任务全集(顺序 = 调度注册与状态接口的展示顺序;前段按触发时刻升序(日批链),
# 月批任务(add-monthly-batch-scheduling)附于末尾:09:00/10:00 的「每月 1 日」时刻
# 不落在日批升序链上,附尾不移动既有任务下标(既有展示顺序断言不变))
JOB_IDS: tuple[str, ...] = (
    "market_snapshot_daily",
    "regime_daily",
    "screening_daily",
    "factor_decay_monitor",
    "factor_builtin_register",
    "factor_harvest_daily",
    "decision_settle_daily",
    # 业绩预告事件线（add-event-driven-lane）：16:05 抓取插在判定（16:00）之后
    "earnings_fetch_daily",
    "paper_execute_daily",
    # 16:15 分诊插在仿真执行（16:10）之后
    "earnings_triage_daily",
    "paper_settle_daily",
    # 自动选股分析（add-auto-pool-analysis-daily-brief）：16:22——选股 15:40 完成、
    # paper 结算 16:20 之后；只建会话并入队，不阻塞调度线程
    "auto_pool_analysis_daily",
    # 16:25 事件结算插在 paper 结算（16:20）之后
    "event_settle_daily",
    "daily_marking",
    "metrics_snapshot",
    "integrity_check",
    # 小票通道（add-microcap-lane-guardrails）：16:45——完整性校验（16:40）之后、
    # cohort（18:00）之前；独立档位 + 前置门禁记录，observation_only（不下单）
    "microcap_screening_daily",
    # 转债日批（add-convertible-bond-coverage）：16:50——小票通道（16:45）之后、
    # cohort（18:00）之前；仅观察通道（零 LLM 准入、不接执行链、不产生订单）
    "cb_daily",
    # 研报数据面（add-research-report-dataplane）：16:52/16:54/16:56——转债之后、
    # cohort 之前；只落元数据（五类研报/评级变动/一致预期），互不阻塞
    "research_reports_daily",
    "rating_changes_daily",
    "earnings_forecasts_daily",
    "research_report_inbox_daily",
    # 分析师事后检验（add-analyst-ex-post-verification）：17:00——研报数据面
    # （16:52–16:56）之后、cohort（18:00）之前；零 LLM，结算到期主张并落记分卡
    "analyst_claim_settle_daily",
    # 外资二手快讯（add-foreign-broker-sentiment）：17:05——只读观测，无源为空态
    "foreign_broker_news_daily",
    # 因子诊断读数日批（add-factor-diagnostics-batch）：17:15——日批窗口（15:30-17:10）
    # 之后、cohort 之前；读数生产移出 HTTP 详情路径（生产实测请求内现算 500 日面板
    # 把详情拖成数十分钟）。每天（含周末）跑：周末收割新准入的因子当日获得读数；
    # 快照无新交易日时全部已新鲜 → 零面板构建近零成本。
    "factor_diagnostics_daily",
    "cohort_batch",
    # 每日汇报（add-auto-pool-analysis-daily-brief）：20:30——cohort 之后，给自动分析
    # 留出完成时间；事实汇总 + LLM 生成，失败写降级摘要并记 failed
    "daily_brief_daily",
    "memory_extract_monthly",
    "factor_proposal_monthly",
)

JOB_LABELS: dict[str, str] = {
    "market_snapshot_daily": "行情快照",
    "regime_daily": "市场拥挤度",
    "screening_daily": "选股",
    "factor_decay_monitor": "因子衰减监控",
    "factor_builtin_register": "内置因子注册",
    "factor_harvest_daily": "因子收割",
    "decision_settle_daily": "判定",
    "earnings_fetch_daily": "业绩预告抓取",
    "paper_execute_daily": "仿真执行",
    "earnings_triage_daily": "预告分诊",
    "event_settle_daily": "事件结算",
    "paper_settle_daily": "paper 结算",
    "auto_pool_analysis_daily": "自动选股分析",
    "daily_marking": "盯市",
    "metrics_snapshot": "指标快照",
    "integrity_check": "完整性校验",
    # add-microcap-lane-guardrails：小票通道日批（独立档位；observation_only，不下单）
    "microcap_screening_daily": "小票通道",
    # add-convertible-bond-coverage：转债日批（规则化标注、零 LLM；仅观察通道）
    "cb_daily": "转债日批",
    # add-research-report-dataplane：研报数据面（只元数据）
    "research_reports_daily": "研报元数据",
    "rating_changes_daily": "评级变动",
    "earnings_forecasts_daily": "一致预期快照",
    "research_report_inbox_daily": "研报投喂",
    "analyst_claim_settle_daily": "分析师事后检验",
    "foreign_broker_news_daily": "外资快讯",
    "factor_diagnostics_daily": "因子诊断读数",
    "cohort_batch": "cohort 跑批",
    "daily_brief_daily": "每日汇报",
    "memory_extract_monthly": "记忆提炼",
    "factor_proposal_monthly": "因子提案",
}

# 默认注册表。值都是无参调用(任务函数签名全为可选关键字参数)。
JOB_FUNCS: dict[str, Callable[[], Any]] = {
    "market_snapshot_daily": run_market_snapshot_batch,
    "regime_daily": run_regime_daily,
    "screening_daily": run_screening_daily,
    "factor_decay_monitor": run_factor_decay_monitor,
    "factor_builtin_register": run_factor_builtin_register,
    "factor_harvest_daily": run_factor_harvest_until_drained,
    "decision_settle_daily": settle_open_predictions,
    "earnings_fetch_daily": run_earnings_fetch_daily,
    "paper_execute_daily": run_execution_daily,
    "earnings_triage_daily": run_earnings_triage_daily,
    "event_settle_daily": run_event_settle_daily,
    "paper_settle_daily": run_paper_settle_daily,
    "auto_pool_analysis_daily": run_pool_analysis_daily,
    "daily_marking": run_daily_marking,
    "metrics_snapshot": persist_metrics_snapshot,
    "integrity_check": integrity_check,
    "microcap_screening_daily": run_microcap_lane,
    "cb_daily": run_cb_daily,
    "research_reports_daily": run_reports_daily,
    "rating_changes_daily": run_ratings_daily,
    "earnings_forecasts_daily": run_forecasts_daily,
    "research_report_inbox_daily": ingest_inbox,
    "analyst_claim_settle_daily": run_analyst_claim_settle_daily,
    "foreign_broker_news_daily": run_foreign_broker_news_daily,
    "factor_diagnostics_daily": run_factor_diagnostics,
    "cohort_batch": run_cohort_batch,
    "daily_brief_daily": run_daily_brief,
    "memory_extract_monthly": run_memory_extract_monthly,
    "factor_proposal_monthly": run_factor_proposal_monthly,
}

# 每 job 的 cron 规格:结算链任务时刻固定(16:00/16:20/16:30/16:35/16:40,工作日);
# paper 仿真执行 16:10(decision settle 之后、paper 结算之前,add-execution-chain-driver)
# → 当日成交当日入结算;paper 仿真结算 16:20(decision settle 之后、daily marking 之前,
# add-paper-broker);行情快照日批 15:30(收盘后,add-market-snapshot);
# 拥挤度日批 15:35(快照之后、选股之前,add-crowding-regime-positioning);
# 选股日批 15:40(快照之后、因子监控之前,add-screening-batch-scheduling);
# 因子衰减监控 15:45(快照之后、结算链之前,add-factor-library-and-monitor);
# 月批任务(add-monthly-batch-scheduling)以 ``day_of_month=1`` 表达月度粒度
# (``_cron_from_spec`` 存在该键 → ``CronTrigger(day=…)``):记忆提炼 09:00、因子提案
# 10:00(月初盘前;两者互不竞争 LLM 网关);
# cohort 项的 hour/minute 由 ``scheduler.start_scheduler``(启动时)与
# ``scheduler.reschedule_cohort``(界面保存后)按 config 覆写——本字典是调度与
# 状态接口共用的时刻快照。
SCHEDULES: dict[str, dict[str, Any]] = {
    "market_snapshot_daily": MARKET_SNAPSHOT_SCHEDULE,
    # 拥挤度日批（add-crowding-regime-positioning）：15:35——快照（15:30）之后、
    # 选股（15:40）之前；读快照库 + 一次 spot 抓取（流通市值），零 LLM 调用
    "regime_daily": {
        "day_of_week": "mon-fri",
        "hour": 15,
        "minute": 35,
        "timezone": "Asia/Shanghai",
    },
    "screening_daily": {
        "day_of_week": "mon-fri",
        "hour": 15,
        "minute": 40,
        "timezone": "Asia/Shanghai",
    },
    "factor_decay_monitor": {
        "day_of_week": "mon-fri",
        "hour": 15,
        "minute": 45,
        "timezone": "Asia/Shanghai",
    },
    "factor_builtin_register": {
        "day_of_week": "mon-fri",
        "hour": 15,
        "minute": 50,
        "timezone": "Asia/Shanghai",
    },
    # 因子收割（add-factor-harvest-bridge）：15:55——快照/选股/衰减/内置注册之后、
    # 结算链之前；候选源为采集器当日累积的 RD-Agent 因子块，全自动过同一五道闸。
    # 每天（含周末）跑：RD-Agent 7×24 产出，周末批用最近交易日快照（周五）面板；
    # 单次运行内部串行多批直到清空待处理（每批 ≤60，BH-FDR K≤100 分辨率不变）。
    "factor_harvest_daily": {
        "day_of_week": "*",
        "hour": 15,
        "minute": 55,
        "timezone": "Asia/Shanghai",
    },
    "decision_settle_daily": {
        "day_of_week": "mon-fri",
        "hour": 16,
        "minute": 0,
        "timezone": "Asia/Shanghai",
    },
    # 业绩预告抓取（add-event-driven-lane）：16:05——判定（16:00）之后、仿真执行
    # （16:10）之前；东财业绩预告按报告期幂等落库（失败上抛，run_job 记 failed）
    "earnings_fetch_daily": {
        "day_of_week": "mon-fri",
        "hour": 16,
        "minute": 5,
        "timezone": "Asia/Shanghai",
    },
    "paper_execute_daily": {
        "day_of_week": "mon-fri",
        "hour": 16,
        "minute": 10,
        "timezone": "Asia/Shanghai",
    },
    # 预告分诊（add-event-driven-lane）：16:15——抓取之后、paper 结算之前；
    # 扫未分诊（available_at <= 当日）→ LLM 分诊 → 观察清单
    "earnings_triage_daily": {
        "day_of_week": "mon-fri",
        "hour": 16,
        "minute": 15,
        "timezone": "Asia/Shanghai",
    },
    "paper_settle_daily": {
        "day_of_week": "mon-fri",
        "hour": 16,
        "minute": 20,
        "timezone": "Asia/Shanghai",
    },
    # 自动选股分析（add-auto-pool-analysis-daily-brief）：16:22——选股 15:40 完成、
    # paper 结算 16:20 之后；任务只准备会话并入队，分析在 AnalyzeQueue 后台执行
    "auto_pool_analysis_daily": {
        "day_of_week": "mon-fri",
        "hour": 16,
        "minute": 22,
        "timezone": "Asia/Shanghai",
    },
    # 事件结算（add-event-driven-lane）：16:25——paper 结算之后、盯市（16:30）之前；
    # 记账入场（T+1 开盘）+ 到期结算（参考价缺失保持 open + 留痕）
    "event_settle_daily": {
        "day_of_week": "mon-fri",
        "hour": 16,
        "minute": 25,
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
    # 小票通道日批（add-microcap-lane-guardrails）：16:45——完整性校验（16:40）之后、
    # cohort（18:00）之前；独立档位（流动性 1000 万）+ 前置门禁记录；observation_only
    # （不产生订单，不接执行链）
    "microcap_screening_daily": {
        "day_of_week": "mon-fri",
        "hour": 16,
        "minute": 45,
        "timezone": "Asia/Shanghai",
    },
    # 转债日批（add-convertible-bond-coverage）：16:50——小票通道（16:45）之后、
    # cohort（18:00）之前；标的/行情/强赎/下修落库 + 条款事件入观察清单（零 LLM）
    "cb_daily": {
        "day_of_week": "mon-fri",
        "hour": 16,
        "minute": 50,
        "timezone": "Asia/Shanghai",
    },
    # add-research-report-dataplane：研报数据面三 job（16:52/16:54/16:56）——
    # 转债日批之后、cohort 之前；只元数据，东财侧串行限流 ≥1s，互不阻塞
    "research_reports_daily": {
        "day_of_week": "mon-fri",
        "hour": 16,
        "minute": 52,
        "timezone": "Asia/Shanghai",
    },
    "rating_changes_daily": {
        "day_of_week": "mon-fri",
        "hour": 16,
        "minute": 54,
        "timezone": "Asia/Shanghai",
    },
    "earnings_forecasts_daily": {
        "day_of_week": "mon-fri",
        "hour": 16,
        "minute": 56,
        "timezone": "Asia/Shanghai",
    },
    # 研报投喂（个人研究自用）：16:58——数据面三 job 之后、cohort 之前；
    # 扫描 RESEARCH_REPORT_INBOX_DIR，文件哈希幂等，无文件即空态
    "research_report_inbox_daily": {
        "day_of_week": "mon-fri",
        "hour": 16,
        "minute": 58,
        "timezone": "Asia/Shanghai",
    },
    # 分析师事后检验（add-analyst-ex-post-verification）：17:00——研报数据面
    # （16:52–16:56）之后、cohort（18:00）之前；零 LLM，结算到期主张并落记分卡
    "analyst_claim_settle_daily": {
        "day_of_week": "mon-fri",
        "hour": 17,
        "minute": 0,
        "timezone": "Asia/Shanghai",
    },
    # 外资二手快讯（add-foreign-broker-sentiment）：17:05——只读观测，无源为空态
    "foreign_broker_news_daily": {
        "day_of_week": "mon-fri",
        "hour": 17,
        "minute": 5,
        "timezone": "Asia/Shanghai",
    },
    # 因子诊断读数日批（add-factor-diagnostics-batch）：17:15——日批窗口之后、
    # cohort 之前；收割过晚（实测 17:56 结束）可能并发，均为进程内 CPU 任务无正确性影响。
    "factor_diagnostics_daily": {
        "day_of_week": "*",
        "hour": 17,
        "minute": 15,
        "timezone": "Asia/Shanghai",
    },
    "cohort_batch": {
        "day_of_week": "mon-fri",
        "hour": 18,
        "minute": 0,
        "timezone": "Asia/Shanghai",
    },
    # 每日汇报（add-auto-pool-analysis-daily-brief）：20:30——cohort 18:00 之后，
    # 给自动选股分析留出完成时间；汇总当日事实 → LLM → 落库/落盘
    "daily_brief_daily": {
        "day_of_week": "mon-fri",
        "hour": 20,
        "minute": 30,
        "timezone": "Asia/Shanghai",
    },
    "memory_extract_monthly": {
        "day_of_month": 1,
        "hour": 9,
        "minute": 0,
        "timezone": "Asia/Shanghai",
    },
    "factor_proposal_monthly": {
        "day_of_month": 1,
        "hour": 10,
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

# 本进程正在执行的运行行集合:悬挂行清扫的**所有权**依据(时钟可被回拨/人工回填,
# 不可作为「这行是不是本进程在跑」的证据)。长任务经 ``ops_api._spawn_task`` 亦登记。
#
# 键为 ``(库路径, run_id)``（issue #334）：run_id 只在单个库内唯一自增，进程级裸
# 集合会在多库共进程时串库——测试逐用例新建库 + 后台任务线程跨用例存活，他库同号
# 所有权会把本库悬挂行的清扫挡掉（CI flake 实证签名）。所有权证据顺带按库收窄后，
# 跨库误保护一并消除。
_ACTIVE_RUN_IDS: set[tuple[str, int]] = set()
_ACTIVE_RUNS_LOCK = threading.Lock()


def _active_key(run_id: int, db_path: str | Path | None) -> tuple[str, int]:
    """所有权键：run_id 解析到其所属库（与 ``insert_job_run`` 的落库解析同源）。"""
    path = Path(db_path) if db_path is not None else _default_db_path()
    return (str(Path(path).resolve()), int(run_id))


def mark_run_active(run_id: int, *, db_path: str | Path | None = None) -> None:
    """登记本进程正在执行的运行行(悬挂清扫据此跳过该行)。"""
    with _ACTIVE_RUNS_LOCK:
        _ACTIVE_RUN_IDS.add(_active_key(run_id, db_path))


def mark_run_finished(run_id: int, *, db_path: str | Path | None = None) -> None:
    """注销已收尾的运行行(注销后不再受所有权保护,按时钟判定)。"""
    with _ACTIVE_RUNS_LOCK:
        _ACTIVE_RUN_IDS.discard(_active_key(run_id, db_path))


def active_run_ids(*, db_path: str | Path | None = None) -> frozenset[int]:
    """指定库的当前在跑行 id 快照(清扫时一次取齐,避免逐行加锁)。"""
    db_key = _active_key(0, db_path)[0]
    with _ACTIVE_RUNS_LOCK:
        return frozenset(run_id for key_db, run_id in _ACTIVE_RUN_IDS if key_db == db_key)


# 本进程启动时刻(模块首次导入即计):悬挂清扫的时钟判据——上一进程开出的行
# started_at 必早于此;启动补跑在规划前调用清扫(issue #455),依赖同一判据。
_PROCESS_STARTED_AT = datetime.now().isoformat()


def sweep_stale_running(
    *, db_path: str | Path | None = None, process_started_at: str | None = None
) -> list[int]:
    """清扫上一进程遗留的悬挂 ``running`` 行 → ``failed``(stale_process_restart)。

    判定条件(须同时满足):①该 run_id **不在本进程对该库的在跑集合**内(所有权是硬证据
    ——时钟可被回拨/人工回填,单凭时刻会误杀正在跑的补跑/长任务;集合按库作用域,
    跨库 run_id 撞号不互相干扰,issue #334);②开始时刻早于本进程启动时刻
    (``process_started_at`` 可注入,ops_api 的既有测试从外部patch该值)。

    供两处调用:ops 状态接口的懒清扫、启动补跑规划前的前置清扫(issue #455——
    进程被杀留下的僵尸 running 行会挡住补跑规划的 running-skip,先清扫再规划才能
    让「跑了一半被杀」也进 6h 补跑窗)。清扫失败只记日志(不得阻断调用方)。
    """
    swept: list[int] = []
    cutoff = _PROCESS_STARTED_AT if process_started_at is None else process_started_at
    try:
        active = active_run_ids(db_path=db_path)
        for row in list_job_runs(limit=1000, db_path=db_path):
            run_id = int(row["run_id"])
            if row["status"] != "running" or run_id in active:
                continue
            if str(row["started_at"]) >= cutoff:
                continue
            finish_job_run(
                run_id,
                status="failed",
                summary={"stale": True, "reason": "stale_process_restart"},
                error="stale_process_restart: 进程重启遗留的悬挂运行行,按失败收尾",
                db_path=db_path,
            )
            swept.append(run_id)
    except Exception:  # noqa: BLE001 - 清扫是自愈动作,失败不得阻断调用方
        logger.exception("悬挂 running 行清扫失败(忽略)")
        return swept
    if swept:
        logger.warning("已清扫进程重启遗留的 running 行 %d 条: %s", len(swept), swept)
    # 池分析运行行的终态回写与悬挂行清扫同一时机(issue #489):会话已终结但运行行停在
    # queued/dispatch_failed 的记录不得挂到下一次编排(最长 24h)。幂等、只处理终态会话、
    # 读会话失败只记日志;惰性导入避免 outcome ↔ screening 导入期成环。
    try:
        from finance_agent.screening.pool_analysis import reconcile_pool_analysis_runs

        reconcile_pool_analysis_runs(None)
    except Exception:  # noqa: BLE001 - 回写是自愈动作,失败不得阻断调用方(#455 同纪律)
        logger.exception("池分析运行行终态回写失败(忽略)")
    return swept


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
        mark_run_active(run_id, db_path=db_path)
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
            mark_run_finished(active_run_id, db_path=db_path)
        lock.release()
