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

paper 仿真结算(add-paper-broker):工作日 16:20 触发 ``run_paper_settle_daily``
(读快照最新收盘价 mark 持仓 → ``paper_equity`` 曲线),经同一 ``_with_retry``
入口(单飞锁 + 运行历史 + 3 次退避重试);与虚拟结算链互不依赖。

paper 仿真执行(add-execution-chain-driver):工作日 16:10 触发 ``run_execution_daily``
(决策 → 信号 → 组合目标 → 风控门禁 → paper 下单,决策日次一交易日开盘口径),
排在 paper 结算(16:20)之前保证当日成交当日入账;快照不可用时返回跳过摘要
(job 记 ok),经同一 ``_with_retry`` 入口。

业绩预告事件线(add-event-driven-lane):工作日 16:05 抓取（``run_earnings_fetch_daily``）/
16:15 分诊（``run_earnings_triage_daily``）/ 16:25 结算（``run_event_settle_daily``），
按时刻序插在既有日批链空档；经同一 ``_with_retry`` 入口（单飞锁 + 运行历史 + 3 次退避）；
抓取失败上抛 → 记 failed（不静默），结算参考价缺失在 job 内保持 open + 留痕。

拥挤度日批(add-crowding-regime-positioning):工作日 15:35 触发 ``run_regime_daily``
(快照 15:30 之后、选股 15:40 之前;读快照库 + 一次 spot 抓取建流通市值 caps →
四指标 + 历史分位落 ``data/regime.db``,同日重跑幂等),经同一 ``_with_retry``
入口;快照/caps 不可用时返回跳过摘要(job 记 ok)不产伪行,执行链据此降级。

月批(add-monthly-batch-scheduling):每月 1 日 09:00 触发 ``run_memory_extract_monthly``
(已结算决策近窗口 → 候选条款落 pending),10:00 触发 ``run_factor_proposal_monthly``
(快照面板 → LLM 提案批 → 五道闸准入);粒度经 ``SCHEDULES`` 的 ``day_of_month`` 键
表达(``_cron_from_spec`` 二分支),经同一 ``_with_retry`` 入口(单飞锁 + 运行历史 +
3 次退避重试),注册位置在 cohort 之前(cohort 保持末位)。

注:定时任务框架选型建议人工落 ADR(design 决策 6 注),agent 不自建 ADR。
"""

from __future__ import annotations

import logging
import os
import time
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from finance_agent.data.foreign_broker_news import run_foreign_broker_news_daily
from finance_agent.data.research_reports import (
    ingest_inbox,
    run_forecasts_daily,
    run_ratings_daily,
    run_reports_daily,
)
from finance_agent.data.snapshot_job import run_market_snapshot_batch
from finance_agent.execution.brokers.paper import run_paper_settle_daily
from finance_agent.execution.driver import run_execution_daily
from finance_agent.factors.builtin import run_factor_builtin_register
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
from finance_agent.outcome.ops.catchup import catch_up_enabled, catch_up_job
from finance_agent.outcome.ops.jobs import (
    SCHEDULES,
    CohortDisabled,
    JobAlreadyRunning,
    run_job,
)
from finance_agent.outcome.ops.model import finish_job_run, get_cohort_settings
from finance_agent.outcome.regime.job import run_regime_daily
from finance_agent.outcome.track_record.job import settle_open_predictions
from finance_agent.outcome.track_record.marking import persist_metrics_snapshot, run_daily_marking
from finance_agent.outcome.track_record.model import integrity_check
from finance_agent.screening.pipeline import run_microcap_lane, run_screening_daily
from finance_agent.screening.pool_analysis import run_pool_analysis_daily

logger = logging.getLogger(__name__)

_MAX_ATTEMPTS = 3

# #57 子项4:misfire 硬化。默认 misfire_grace_time=1s 下进程被冻结/重建略过
# 触发时刻即整日丢批;job 幂等(次日补结),1h 宽限内补火安全;多次 miss 合并
# 一次(coalesce),防追赶风暴。注:MemoryJobStore 重启后 job 重注册,该参数
# 管的是进程活着但调度 loop 延迟的场景。
_JOB_MISFIRE_KW: dict[str, Any] = {"misfire_grace_time": 3600, "coalesce": True}

# 启动补跑（delta add-ops-missed-run-catchup）：进程重建/冻结会永久丢弃错过的触发点
# （MemoryJobStore 重启后只按 cron 重注册，不回溯）。补偿用**一次性 date job**在启动
# 后 5s 触发，而不是裸线程：测试里被 MagicMock 吸收（不触真实库），生产由调度器线程
# 执行，且与日批共享同一套 misfire/coalesce 语义。白名单、6h 时间窗与上限 3 见
# ``outcome.ops.catchup``。
CATCH_UP_JOB_ID = "ops_catch_up_once"
_CATCH_UP_DELAY = timedelta(seconds=5)
_CATCH_UP_TZ = ZoneInfo("Asia/Shanghai")

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


def _snapshot_job() -> None:
    """add-market-snapshot：全市场行情快照日批（15:30，收盘后；次日开盘前可读）。"""
    _with_retry("market_snapshot_daily", "market snapshot", run_market_snapshot_batch)


def _regime_daily_job() -> None:
    """add-crowding-regime-positioning：拥挤度指标日批（15:35，快照之后、选股之前）。

    读快照库 + 一次 spot 抓取（流通市值）→ 四指标 + 历史分位落 ``regime.db``；
    快照不可用/caps 缺失 → 返回跳过摘要（job 记 ok，原因码入摘要），不产伪行。
    """
    _with_retry("regime_daily", "regime daily", run_regime_daily)


def _screening_job() -> None:
    """add-screening-batch-scheduling：选股日批（15:40，快照之后、因子监控之前）。

    快照 15:30 先落库，选股据此跑 L1→L2→L3 产出精选池；批次 failed 时任务上抛
    （run_job 记 failed），运维界面可见。快照缺失时快照守卫 fail loud，不产残缺池。
    """
    _with_retry("screening_daily", "screening batch", run_screening_daily)


def _factor_monitor_job() -> None:
    """add-factor-library-and-monitor：因子衰减监控日批（15:45，快照之后）。

    快照 15:30 先落库，监控据此构造面板记录上一交易日 IC 并判定滚动降级；
    job 幂等（同日重跑覆盖同一 ``(factor, date)``），重试安全。
    """
    _with_retry("factor_decay_monitor", "factor decay monitor", run_factor_decay_monitor)


def _factor_builtin_register_job() -> None:
    """wire-builtin-factor-registration：内置因子注册日批（15:50，快照与因子监控之后）。

    存在性幂等（已注册条目跳过，含 retired 不回填）；缺失条目以快照面板过五道闸；
    面板不可得上抛（run_job 记 failed）。注册本身零 LLM 调用。
    """
    _with_retry("factor_builtin_register", "factor builtin register", run_factor_builtin_register)


def _factor_harvest_job() -> None:
    """add-factor-harvest-bridge：因子收割日批（每天 15:55，内置注册之后、结算链之前）。

    RD-Agent 候选 → LLM 转译 → 翻译器 → 同一五道闸；**内部串行多批直到清空待处理**
    （每批 ≤60，闸③ BH-FDR 的 K≤100 分辨率不变，批间是独立检验家族）；
    ``max_batches`` 兜底防无限拉长。无新候选零摘要（非错误）；
    网关/面板不可得上抛（run_job 记 failed，下次重试；state 幂等不重复消费）。
    """
    _with_retry(
        "factor_harvest_daily", "factor harvest daily", run_factor_harvest_until_drained
    )


def _settle_job() -> None:
    _with_retry("decision_settle_daily", "prediction settle", settle_open_predictions)


def _earnings_fetch_job() -> None:
    """add-event-driven-lane：业绩预告抓取日批（16:05，判定之后、仿真执行之前）。

    东财业绩预告按报告期幂等落库（窗口 EVENTS_FETCH_PERIODS，首跑回填近一年）；
    抓取失败上抛 → ``run_job`` 记 failed + 原因（不以零条新增冒充成功）。
    """
    _with_retry("earnings_fetch_daily", "earnings fetch daily", run_earnings_fetch_daily)


def _earnings_triage_job() -> None:
    """add-event-driven-lane：预告分诊日批（16:15，抓取之后、paper 结算之前）。

    扫未分诊（``available_at <= 当日``，时点截断）→ LLM 分诊（单条失败降级 in_line
    不阻断批次）→ 构建事件观察清单；零未分诊记录时零 LLM 调用。
    """
    _with_retry("earnings_triage_daily", "earnings triage daily", run_earnings_triage_daily)


def _event_settle_job() -> None:
    """add-event-driven-lane：事件记账结算日批（16:25，paper 结算之后、盯市之前）。

    入场（公告日次一交易日开盘；停牌/一字板顺延留痕）+ 到期结算（参考价缺失保持
    open + 留痕重试，MUST NOT 折算 0）；两步摘要入运行历史。
    """
    _with_retry("event_settle_daily", "event settle daily", run_event_settle_daily)


def _paper_execute_job() -> None:
    """add-execution-chain-driver：执行链日批（16:10，settle 之后、paper 结算之前）。

    决策 → 信号 → 组合目标 → 门禁 → paper 下单（决策日次一交易日开盘口径）；
    快照库为空时返回跳过摘要（job 记 ok），信号顺延至下一交易日。
    """
    _with_retry("paper_execute_daily", "paper execute", run_execution_daily)


def _paper_settle_job() -> None:
    """add-paper-broker：paper 仿真账户日结算（16:20，settle 之后、marking 之前）。

    读快照库最新交易日收盘价 mark 持仓 → paper_equity 曲线（幂等 upsert）；
    快照库为空时返回跳过摘要（job 记 ok），持仓缺价时抛错走重试。
    """
    _with_retry("paper_settle_daily", "paper settle", run_paper_settle_daily)


def _auto_pool_analysis_job() -> None:
    """add-auto-pool-analysis-daily-brief：自动选股分析（16:22）。

    读当日成功精选池 → 时效性校验 → 建会话并入 AnalyzeQueue；任务只等待入队，
    不等待分析完成。空池/过期池显式 skipped，不消费 LLM、不建会话。
    """
    _with_retry("auto_pool_analysis_daily", "auto pool analysis", run_pool_analysis_daily)


def _marking_job() -> None:
    """add-track-record-stage-b：每日盯市 + 净值曲线（16:30，settle 之后）。"""
    _with_retry("daily_marking", "daily marking", run_daily_marking)


def _metrics_job() -> None:
    """add-track-record-stage-b：指标快照重算（16:35）。"""
    _with_retry("metrics_snapshot", "metrics snapshot", persist_metrics_snapshot)


def _integrity_job() -> None:
    """add-track-record-stage-c：快照哈希完整性校验（16:40，篡改告警）。"""
    _with_retry("integrity_check", "integrity check", integrity_check)


def _microcap_lane_job() -> None:
    """add-microcap-lane-guardrails：小票通道日批（16:45，完整性校验之后、cohort 之前）。

    独立档位（流动性下限 1000 万）跑同一 L1→L2→L3 链 → ``pool='microcap'`` 批次行 +
    前置门禁记录字段（退市池就绪 / regime 新鲜）；批次 failed 上抛（run_job 记 failed）。
    **v1 不接执行链、不产生订单**（observation_only）。
    """
    _with_retry("microcap_screening_daily", "microcap screening daily", run_microcap_lane)


def _cb_daily_job() -> None:
    """add-convertible-bond-coverage：转债日批（16:50，小票通道之后、cohort 之前）。

    标的/行情/强赎/下修落库 → 条款事件入观察清单（``source=cbb``，规则化标注，零 LLM）；
    核心源失败上抛（run_job 记 failed + 原因），次要源失败记摘要留痕不阻断。
    **仅观察通道**：不接执行链、不产生订单。
    """
    _with_retry("cb_daily", "cb daily", run_cb_daily)


def _research_reports_job() -> None:
    """add-research-report-dataplane：五类研报元数据日批（16:52，收盘后）。

    东财 reportapi 按发布日窗口增量（全市场），新浪计数交叉/降级记录，
    分析师档案随批重建；单类失败记 degraded，全部失败记 failed，不阻断兄弟 job。
    """
    _with_retry("research_reports_daily", "research reports", run_reports_daily)


def _rating_changes_job() -> None:
    """add-research-report-dataplane：巨潮评级变动日批（16:54，按日全市场）。"""
    _with_retry("rating_changes_daily", "rating changes", run_ratings_daily)


def _earnings_forecasts_job() -> None:
    """add-research-report-dataplane：全市场一致预期快照日批（16:56，一次约 2887 只）。"""
    _with_retry("earnings_forecasts_daily", "earnings forecasts", run_forecasts_daily)


def _research_report_inbox_job() -> None:
    """add-research-report-dataplane：投喂目录扫描（16:58，个人研究自用）。

    扫描 ``RESEARCH_REPORT_INBOX_DIR`` 下的 PDF → 全文解析入库（文件哈希幂等）；
    无文件为空态，解析失败记 degraded/failed，不阻断其他 job。
    抽取完成后按开关触发关联标的分析（add-report-triggered-analysis；默认关闭）。
    """
    _with_retry("research_report_inbox_daily", "research report inbox", ingest_inbox)
    try:
        from finance_agent.api import submit_report_trigger_batch

        submit_report_trigger_batch()
    except Exception as exc:  # noqa: BLE001 - 触发失败不得使投喂 job 失败
        logger.warning("研报触发分析批次失败（投喂结果不受影响）: %s", exc)


def _analyst_claim_settle_job() -> None:
    """add-analyst-ex-post-verification：分析师主张结算（17:00）。

    生成评级/EPS/目标价事件 → 结算到期主张 → 落当日分桶记分卡；零 LLM。
    单条结算失败保持 pending 次日重试（job degraded），生成阶段失败记 failed。
    """
    _with_retry(
        "analyst_claim_settle_daily",
        "analyst claim settle daily",
        run_analyst_claim_settle_daily,
    )


def _foreign_broker_news_job() -> None:
    """add-foreign-broker-sentiment：外资二手快讯日批（17:05）。

    抓取配置源 → 关键字过滤 → 事件抽取 → 落库；无源为空态，单源失败降级。
    v1 只读观测，不进入任何分析师上下文或决策路径。
    """
    _with_retry(
        "foreign_broker_news_daily",
        "foreign broker news",
        run_foreign_broker_news_daily,
    )


def _daily_brief_job() -> None:
    """add-auto-pool-analysis-daily-brief：每日 LLM 运营汇报（20:30）。

    汇总当日选股/分析/观点/执行/因子/失败任务事实 → ``purpose=daily_brief`` 生成
    Markdown → 落 ``daily_briefs`` 与 ``reports/daily-brief-YYYY-MM-DD.md``；LLM
    失败写确定性降级摘要并上抛，经 ``run_job`` 记 failed 并退避重试。
    """
    _with_retry("daily_brief_daily", "daily brief", run_daily_brief)


def _cohort_job() -> None:
    """add-forward-paper-trading-cohort：探班池盘后跑批（时刻取自 ops_config）。

    开关已在 ``run_job`` 判过(关闭 → 记 ``skipped-disabled``、不走到这里);开启时
    无参调用 ``run_cohort_batch``——runner 内保留同源门控作双保险,任何调用方都
    不得绕过开关。
    """
    _with_retry("cohort_batch", "cohort batch", run_cohort_batch)


def _memory_extract_job() -> None:
    """add-monthly-batch-scheduling：语义记忆月批提炼（每月 1 日 09:00 Asia/Shanghai）。

    已结算决策近窗口 → 提炼（LLM 优先/规则兜底）→ 候选态落库（未确认不注入）；
    提炼入口自身容错（缺库/LLM 失败均有既有降级语义），真实故障经 run_job
    记 failed 并退避重试。
    """
    _with_retry("memory_extract_monthly", "memory extract monthly", run_memory_extract_monthly)


def _factor_proposal_job() -> None:
    """add-monthly-batch-scheduling：因子提案月批（每月 1 日 10:00 Asia/Shanghai）。

    快照面板 → LLM 跨桶提案批 → 逐条五道闸准入（过闸入库/未过留事件史）；网关
    不可用或面板不可得 → 上抛，run_job 记 failed 如实（月批可重试，不静默）。
    """
    _with_retry("factor_proposal_monthly", "factor proposal monthly", run_factor_proposal_monthly)


def _cron_from_spec(spec: dict[str, Any]) -> CronTrigger:
    """按 SCHEDULES 规格建 cron(注册与运行时重排共用一种形态)。

    粒度二分支(add-monthly-batch-scheduling):规格含 ``day_of_month`` → 月批
    ``CronTrigger(day=…, hour, minute, timezone)``;否则维持 ``day_of_week`` 分支
    (日批,逐字不变)——同一形态两分支,不引入第二调度器/第二注册表。
    """
    if "day_of_month" in spec:
        return CronTrigger(
            day=spec["day_of_month"],
            hour=spec["hour"],
            minute=spec["minute"],
            timezone=spec["timezone"],
        )
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
    # 启动补跑（add-ops-missed-run-catchup）：一次性 date job，注册在日批之前——
    # 补跑逻辑按计划时刻自行排序（快照先于下游），与本处注册顺序无关；
    # ``OPS_CATCHUP_ON_STARTUP=0`` 时不注册（dev 反复重启止血）。
    if catch_up_enabled():
        scheduler.add_job(
            catch_up_job,
            "date",
            run_date=datetime.now(_CATCH_UP_TZ) + _CATCH_UP_DELAY,
            id=CATCH_UP_JOB_ID,
            replace_existing=True,
            **_JOB_MISFIRE_KW,
        )
    # add-market-snapshot：行情快照日批（15:30）先跑——结算/盯市链（16:00 起）
    # 与快照解耦，互不阻塞；job 幂等（同日重跑不二次复权）。
    scheduler.add_job(
        _snapshot_job,
        _cron_for("market_snapshot_daily"),
        id="market_snapshot_daily",
        replace_existing=True,
        **_JOB_MISFIRE_KW,
    )
    # add-crowding-regime-positioning：拥挤度日批（15:35）——快照（15:30）之后、
    # 选股（15:40）之前；读快照库 + 一次 spot 抓取（流通市值），零 LLM 调用；
    # 快照/caps 不可用返回跳过摘要（job 记 ok），不产伪行。
    scheduler.add_job(
        _regime_daily_job,
        _cron_for("regime_daily"),
        id="regime_daily",
        replace_existing=True,
        **_JOB_MISFIRE_KW,
    )
    # add-screening-batch-scheduling：选股日批（15:40）——快照（15:30）之后、
    # 因子监控（15:45）与结算链（16:00 起）之前；读快照库，L3 走 quick LLM。
    scheduler.add_job(
        _screening_job,
        _cron_for("screening_daily"),
        id="screening_daily",
        replace_existing=True,
        **_JOB_MISFIRE_KW,
    )
    # add-factor-library-and-monitor：因子衰减监控（15:45）——快照（15:30）之后、
    # 结算链（16:00 起）之前；读快照库 + 因子库，无 LLM 调用。
    scheduler.add_job(
        _factor_monitor_job,
        _cron_for("factor_decay_monitor"),
        id="factor_decay_monitor",
        replace_existing=True,
        **_JOB_MISFIRE_KW,
    )
    # wire-builtin-factor-registration：内置因子注册（15:50）——快照（15:30）与因子
    # 衰减监控（15:45）之后、结算链之前；存在性幂等，库空/缺条目时次日自愈。
    scheduler.add_job(
        _factor_builtin_register_job,
        _cron_for("factor_builtin_register"),
        id="factor_builtin_register",
        replace_existing=True,
        **_JOB_MISFIRE_KW,
    )
    # add-factor-harvest-bridge：因子收割日批（15:55）——RD-Agent 候选转译过闸；
    # 排在内置注册（15:50）之后、结算链（16:00 起）之前，不争 LLM 网关。
    scheduler.add_job(
        _factor_harvest_job,
        _cron_for("factor_harvest_daily"),
        id="factor_harvest_daily",
        replace_existing=True,
        **_JOB_MISFIRE_KW,
    )
    scheduler.add_job(
        _settle_job,
        _cron_for("decision_settle_daily"),
        id="decision_settle_daily",
        replace_existing=True,
        **_JOB_MISFIRE_KW,
    )
    # add-event-driven-lane：业绩预告抓取（16:05）——判定（16:00）之后、仿真执行
    # （16:10）之前；幂等 upsert，失败上抛记 failed。
    scheduler.add_job(
        _earnings_fetch_job,
        _cron_for("earnings_fetch_daily"),
        id="earnings_fetch_daily",
        replace_existing=True,
        **_JOB_MISFIRE_KW,
    )
    # add-execution-chain-driver：执行链日批（16:10）——决策 → 信号 → 目标 →
    # 门禁 → paper 下单；排在 paper 结算（16:20）之前，当日成交当日入账。
    scheduler.add_job(
        _paper_execute_job,
        _cron_for("paper_execute_daily"),
        id="paper_execute_daily",
        replace_existing=True,
        **_JOB_MISFIRE_KW,
    )
    # add-event-driven-lane：预告分诊（16:15）——抓取（16:05）之后、paper 结算（16:20）
    # 之前；未分诊为空时零 LLM 调用。
    scheduler.add_job(
        _earnings_triage_job,
        _cron_for("earnings_triage_daily"),
        id="earnings_triage_daily",
        replace_existing=True,
        **_JOB_MISFIRE_KW,
    )
    # add-paper-broker：paper 仿真账户日结算（16:20）——快照收盘价 mark 持仓；
    # 排在 decision settle（16:00）与 daily marking（16:30）之间，三条链互不依赖。
    scheduler.add_job(
        _paper_settle_job,
        _cron_for("paper_settle_daily"),
        id="paper_settle_daily",
        replace_existing=True,
        **_JOB_MISFIRE_KW,
    )
    # add-auto-pool-analysis-daily-brief：自动选股分析（16:22）——选股 15:40 完成、
    # paper 结算 16:20 之后；只建会话并入队，分析在 AnalyzeQueue 后台执行。
    scheduler.add_job(
        _auto_pool_analysis_job,
        _cron_for("auto_pool_analysis_daily"),
        id="auto_pool_analysis_daily",
        replace_existing=True,
        **_JOB_MISFIRE_KW,
    )
    # add-event-driven-lane：事件结算（16:25）——paper 结算之后、盯市（16:30）之前；
    # 记账入场（T+1 开盘口径）+ 到期结算（参考价缺失保持 open + 留痕）。
    scheduler.add_job(
        _event_settle_job,
        _cron_for("event_settle_daily"),
        id="event_settle_daily",
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
    # add-microcap-lane-guardrails：小票通道日批（16:45）——完整性校验（16:40）之后、
    # cohort（18:00）之前；独立档位 + 前置门禁（observation_only，不产生订单）。
    scheduler.add_job(
        _microcap_lane_job,
        _cron_for("microcap_screening_daily"),
        id="microcap_screening_daily",
        replace_existing=True,
        **_JOB_MISFIRE_KW,
    )
    # add-convertible-bond-coverage：转债日批（16:50）——小票通道（16:45）之后、
    # cohort（18:00）之前；标的/行情/强赎/下修落库 + 条款事件入观察清单（零 LLM，
    # 仅观察通道：不接执行链、不产生订单）。
    scheduler.add_job(
        _cb_daily_job,
        _cron_for("cb_daily"),
        id="cb_daily",
        replace_existing=True,
        **_JOB_MISFIRE_KW,
    )
    # add-research-report-dataplane：研报数据面三 job（16:52/16:54/16:56）——转债
    # 日批（16:50）之后、cohort（18:00）之前；只落元数据（不碰全文），互不阻塞，
    # 单 job 失败只影响自己的运行记录。
    scheduler.add_job(
        _research_reports_job,
        _cron_for("research_reports_daily"),
        id="research_reports_daily",
        replace_existing=True,
        **_JOB_MISFIRE_KW,
    )
    scheduler.add_job(
        _rating_changes_job,
        _cron_for("rating_changes_daily"),
        id="rating_changes_daily",
        replace_existing=True,
        **_JOB_MISFIRE_KW,
    )
    scheduler.add_job(
        _earnings_forecasts_job,
        _cron_for("earnings_forecasts_daily"),
        id="earnings_forecasts_daily",
        replace_existing=True,
        **_JOB_MISFIRE_KW,
    )
    # 研报投喂（个人研究自用）：16:58——扫描投喂目录 PDF，全文解析入库。
    scheduler.add_job(
        _research_report_inbox_job,
        _cron_for("research_report_inbox_daily"),
        id="research_report_inbox_daily",
        replace_existing=True,
        **_JOB_MISFIRE_KW,
    )
    # add-analyst-ex-post-verification：分析师主张结算（17:00）——研报数据面
    # （16:52–16:56）之后、cohort（18:00）之前；零 LLM，只读行情快照与指数收盘。
    scheduler.add_job(
        _analyst_claim_settle_job,
        _cron_for("analyst_claim_settle_daily"),
        id="analyst_claim_settle_daily",
        replace_existing=True,
        **_JOB_MISFIRE_KW,
    )
    # add-foreign-broker-sentiment：外资二手快讯（17:05）——分析师结算之后、
    # cohort 之前；只读观测，无源为空态。
    scheduler.add_job(
        _foreign_broker_news_job,
        _cron_for("foreign_broker_news_daily"),
        id="foreign_broker_news_daily",
        replace_existing=True,
        **_JOB_MISFIRE_KW,
    )
    # add-monthly-batch-scheduling：两月批任务（每月 1 日 09:00 记忆提炼 / 10:00
    # 因子提案）——盘前错峰，互不竞争 LLM 网关；排期与日批链（15:30 起）也错开。
    # 注册位置在 cohort 之前（cohort 仍是末位注册，既有断言不变）。
    scheduler.add_job(
        _memory_extract_job,
        _cron_for("memory_extract_monthly"),
        id="memory_extract_monthly",
        replace_existing=True,
        **_JOB_MISFIRE_KW,
    )
    scheduler.add_job(
        _factor_proposal_job,
        _cron_for("factor_proposal_monthly"),
        id="factor_proposal_monthly",
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
    # add-auto-pool-analysis-daily-brief：每日 LLM 运营汇报（20:30）——cohort 之后，
    # 给自动选股分析留出完成时间；失败写降级摘要并记 failed。
    scheduler.add_job(
        _daily_brief_job,
        _cron_for("daily_brief_daily"),
        id="daily_brief_daily",
        replace_existing=True,
        **_JOB_MISFIRE_KW,
    )
    scheduler.start()
    _scheduler = scheduler
    logger.info(
        "decision settle scheduler 已启动(market snapshot 15:30;regime 15:35;"
        "factor decay monitor 15:45;"
        "factor builtin register 15:50;factor harvest 15:55;"
        "decision settle 16:00;earnings fetch 16:05;paper execute 16:10;"
        "earnings triage 16:15;paper settle 16:20;event settle 16:25);"
        "daily marking 16:30;metrics snapshot 16:35;integrity check 16:40;"
        "microcap screening 16:45;cb daily 16:50;"
        "monthly: memory extract day-1 09:00 / factor proposal day-1 10:00;"
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
