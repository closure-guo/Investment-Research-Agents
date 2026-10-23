# 错过的日批触发补偿 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `MemoryJobStore` 进程重建打掉的日批触发点，由启动补跑在 6h 窗内自愈；`/api/v1/ops/jobs` 暴露 `missed_today` 让「压根没跑」可见。

**Architecture:** 新增纯函数模块 `outcome/ops/catchup.py`（规划与执行分离：`plan_catch_up` 无副作用、`run_catch_up` 顺序执行顺序链）；`start_scheduler()` 用 APScheduler 一次性 `date` job（启动后 5s）拉起——不另起线程、测试里被 MagicMock 吸收，不触碰真实库。`missed_today` 与补跑规划共用同一读数 `job_run_statuses_on`，避免两处口径漂移。

**Tech Stack:** Python 3.12 / APScheduler BackgroundScheduler / sqlite（job_runs）/ pytest / ruff / mypy

## Global Constraints

- 补跑白名单只含幂等日批：`market_snapshot_daily`、`regime_daily`、`screening_daily`、`factor_decay_monitor`、`factor_builtin_register`、`decision_settle_daily`、`daily_marking`、`metrics_snapshot`、`integrity_check`。账务/下单类（`paper_execute_daily`/`paper_settle_daily`/`event_settle_daily`）与 LLM 烧钱类（`cohort_batch`/`daily_brief_daily`/`factor_harvest_daily`/`earnings_triage_daily`/`auto_pool_analysis_daily`）严禁自动重放。
- 时间窗 6h；单次上限 3 个，按计划时刻升序。
- `market_snapshot_daily` 摘要 `skipped == "non_trading_day"` → 中止后续补跑。
- 补跑一律 `run_job(source="catchup")`；任何异常只记日志，不得向启动链路上抛。
- `OPS_CATCHUP_ON_STARTUP=0` → 完全不注册补跑 job。
- 旁路铁律：scheduler 启动失败不得影响 API 启动（既有 try/except 保持）。
- 单 uvicorn worker 前提不变；不改任何既有阈值与 IC 口径。

## File Structure

- Create：`src/finance_agent/outcome/ops/catchup.py` —— 规划（纯函数）+ 执行 + APScheduler 入口，唯一职责「错过的日批补偿」。
- Modify：`src/finance_agent/outcome/ops/model.py` —— 加 `job_run_statuses_on`（按天读状态，单一口径来源）。
- Modify：`src/finance_agent/outcome/ops/jobs.py` —— DDL 注释补 `catchup` 取值（无行为）。
- Modify：`src/finance_agent/outcome/scheduler.py` —— `start_scheduler` 注册一次性补跑 job。
- Modify：`src/finance_agent/ops_api.py` —— `_job_rows` 增加 `missed_today`。
- Test：`tests/outcome/test_ops_catchup.py`（新）、`tests/outcome/test_ops_statuses_on.py`（新，Task 1 单列一文件）、`tests/outcome/test_scheduler.py`（+3 例）、`tests/test_ops_api.py`（+7 例）。

---

### Task 1: 按天读运行状态（口径单点）

**Files:**
- Modify: `src/finance_agent/outcome/ops/model.py:363-375`（`last_job_run` 之后追加）
- Test: `tests/outcome/test_ops_model.py`

**Interfaces:**
- Produces: `job_run_statuses_on(job_id: str, day: str, *, db_path: str | Path | None = None) -> list[str]`——返回该任务在 `day`（`YYYY-MM-DD`）当天全部运行行的 `status`（按 `started_at` 升序）；无行 → `[]`；`day` 非规范日期 → `ValueError`。

- [ ] **Step 1: 写失败测试**

```python
def test_job_run_statuses_on_filters_by_day(tmp_path):
    db = tmp_path / "ops.db"
    rid = insert_job_run("integrity_check", "scheduled", "running", db_path=db)
    finish_job_run(rid, status="ok", db_path=db)
    assert job_run_statuses_on("integrity_check", datetime.now().strftime("%Y-%m-%d"), db_path=db) == [
        "ok"
    ]
    assert job_run_statuses_on("integrity_check", "2000-01-01", db_path=db) == []
    with pytest.raises(ValueError):
        job_run_statuses_on("integrity_check", "2026/10/23", db_path=db)
```

- [ ] **Step 2: 跑测试确认红**

Run: `uv run pytest tests/outcome/test_ops_model.py -k statuses_on -v`
Expected: FAIL — `ImportError: cannot import name 'job_run_statuses_on'`

- [ ] **Step 3: 实现**

```python
def job_run_statuses_on(
    job_id: str, day: str, *, db_path: str | Path | None = None
) -> list[str]:
    """某任务在 ``day``（规范 ``YYYY-MM-DD``）当天全部运行行的状态（按开始时刻升序）。

    ``missed_today`` 与启动补跑规划共用本读数：一处口径，两处消费。非规范日期
    → ``ValueError``（与 ``_parse_date_param`` 同纪律：不猜日期）。
    """
    start = date.fromisoformat(day)
    if start.isoformat() != day:
        raise ValueError(f"day 必须是规范 YYYY-MM-DD: {day!r}")
    end = (start + timedelta(days=1)).isoformat()
    init_ops(db_path)
    conn = _connect(db_path)
    try:
        rows = conn.execute(
            "SELECT status FROM job_runs WHERE job_id=? AND started_at>=? AND started_at<? "
            "ORDER BY started_at, run_id",
            (job_id, start.isoformat(), end),
        ).fetchall()
        return [str(row[0]) for row in rows]
    finally:
        conn.close()
```

- [ ] **Step 4: 跑测试确认绿**

Run: `uv run pytest tests/outcome/test_ops_model.py -k statuses_on -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add src/finance_agent/outcome/ops/model.py tests/outcome/test_ops_model.py
git commit -m "feat(ops): job_run_statuses_on —— 按天读运行状态（missed_today/补跑共用口径）"
```

---

### Task 2: 补跑规划（纯函数）

**Files:**
- Create: `src/finance_agent/outcome/ops/catchup.py`
- Test: `tests/outcome/test_ops_catchup.py`

**Interfaces:**
- Consumes: `SCHEDULES`（`ops.jobs`）、`job_run_statuses_on`（Task 1）
- Produces:
  - `CATCH_UP_ALLOWLIST: tuple[str, ...]`、`CATCH_UP_WINDOW: timedelta`、`CATCH_UP_MAX_JOBS: int = 3`、`CATCH_UP_SOURCE = "catchup"`、`CATCH_UP_ENV_SWITCH = "OPS_CATCHUP_ON_STARTUP"`
  - `catch_up_enabled() -> bool`
  - `scheduled_fire_time(spec: dict[str, Any], now: datetime) -> datetime | None`
  - `plan_catch_up(*, now: datetime, statuses_of: Callable[[str], Sequence[str]], schedules: Mapping[str, Mapping[str, Any]] | None = None, allowlist: Sequence[str] | None = None, window: timedelta = CATCH_UP_WINDOW, max_jobs: int = CATCH_UP_MAX_JOBS) -> list[dict[str, Any]]`

- [ ] **Step 1: 写失败测试**（`tests/outcome/test_ops_catchup.py` 前半）

```python
"""错过的日批触发补偿：规划口径 / 上限 / 顺序 / 非交易日中止 / 失败不阻断。

delta add-ops-missed-run-catchup。规划是纯函数（注入 now 与状态读数），
执行注入 runner——不碰真实库、不碰真实调度器。
"""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from finance_agent.outcome.ops.catchup import (
    CATCH_UP_ALLOWLIST,
    plan_catch_up,
    scheduled_fire_time,
)

TZ = ZoneInfo("Asia/Shanghai")


def _at(y, m, d, hh, mm):
    return datetime(y, m, d, hh, mm, tzinfo=TZ)


def _weekday_at(hh, mm):
    """2026-10-22 是周四（工作日）。"""
    return _at(2026, 10, 22, hh, mm)


class TestScheduledFireTime:
    def test_weekday_spec_past_time_returns_today_fire(self):
        spec = {"day_of_week": "mon-fri", "hour": 15, "minute": 45, "timezone": "Asia/Shanghai"}
        assert scheduled_fire_time(spec, _weekday_at(17, 29)) == _weekday_at(15, 45)

    def test_weekend_returns_none(self):
        spec = {"day_of_week": "mon-fri", "hour": 15, "minute": 45, "timezone": "Asia/Shanghai"}
        assert scheduled_fire_time(spec, _at(2026, 10, 24, 17, 0)) is None  # 周六

    def test_monthly_spec_only_first_day(self):
        spec = {"day_of_month": 1, "hour": 9, "minute": 0, "timezone": "Asia/Shanghai"}
        assert scheduled_fire_time(spec, _at(2026, 11, 1, 10, 0)) == _at(2026, 11, 1, 9, 0)
        assert scheduled_fire_time(spec, _at(2026, 11, 2, 10, 0)) is None

    def test_star_weekday_spec_every_day(self):
        spec = {"day_of_week": "*", "hour": 15, "minute": 55, "timezone": "Asia/Shanghai"}
        assert scheduled_fire_time(spec, _at(2026, 10, 24, 16, 0)) == _at(2026, 10, 24, 15, 55)


class TestPlanCatchUp:
    def test_missed_within_window_is_planned(self):
        plan = plan_catch_up(now=_weekday_at(17, 29), statuses_of=lambda _jid: [])
        assert [item["job_id"] for item in plan] == [
            "market_snapshot_daily",
            "regime_daily",
            "screening_daily",
        ]
        assert plan[0]["late_seconds"] == 119 * 60  # 15:30 → 17:29

    def test_successful_run_is_skipped(self):
        statuses = {"regime_daily": ["ok"]}
        plan = plan_catch_up(now=_weekday_at(17, 29), statuses_of=lambda jid: statuses.get(jid, []))
        assert "regime_daily" not in [item["job_id"] for item in plan]

    def test_running_is_skipped(self):
        statuses = {"market_snapshot_daily": ["running"]}
        plan = plan_catch_up(now=_weekday_at(17, 29), statuses_of=lambda jid: statuses.get(jid, []))
        assert "market_snapshot_daily" not in [item["job_id"] for item in plan]

    def test_outside_window_is_not_planned(self):
        plan = plan_catch_up(now=_weekday_at(23, 59), statuses_of=lambda _jid: [])
        assert plan == []

    def test_before_fire_time_is_not_planned(self):
        plan = plan_catch_up(now=_weekday_at(15, 0), statuses_of=lambda _jid: [])
        assert plan == []

    def test_weekend_plans_nothing(self):
        plan = plan_catch_up(now=_at(2026, 10, 24, 17, 0), statuses_of=lambda _jid: [])
        assert plan == []

    def test_cap_keeps_earliest_three(self):
        plan = plan_catch_up(now=_weekday_at(17, 29), statuses_of=lambda _jid: [])
        assert len(plan) == 3
        assert [item["job_id"] for item in plan] == sorted(
            CATCH_UP_ALLOWLIST, key=lambda jid: _expected_fire(jid)
        )[:3]
```

- [ ] **Step 2: 跑测试确认红**

Run: `uv run pytest tests/outcome/test_ops_catchup.py -v`
Expected: FAIL — `ModuleNotFoundError: finance_agent.outcome.ops.catchup`

- [ ] **Step 3: 实现（规划部分）**

```python
"""错过的日批触发补偿（delta add-ops-missed-run-catchup）。

``MemoryJobStore`` 重启后按 cron 重新注册、**不回溯**错过的触发点；``misfire_grace_time``
只覆盖「调度器存活但执行被延迟」，覆盖不了「进程在触发点不在」（incident 050）。
本模块在调度器启动时补一次：**当天已过点、当天无成功运行**的白名单日批。

四道约束（缺一不可）：
1. 白名单：只含幂等日批；账务/下单类与 LLM 烧钱类一律不自动重放（人工补跑照旧）。
2. 时间窗：计划时刻后 ``CATCH_UP_WINDOW``（6h）内才补；迟到过久对当日链无意义。
3. 上限：每次启动最多 ``CATCH_UP_MAX_JOBS``（3）个，按计划时刻升序截断。
4. 非交易日链中止：快照返回 ``skipped=non_trading_day`` 时不再补下游（休市日不产一串失败）。

规划与执行分离：``plan_catch_up`` 是纯函数（注入 ``now`` 与状态读数），``run_catch_up``
注入 runner——两者都不碰真实调度器，测试零副作用。
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

# 白名单：幂等 + 当日补齐有价值的日批。故意**不含**账务/下单类
# （paper_execute/paper_settle/event_settle：宁可人工重放动账）与 LLM 烧钱类
# （cohort/daily_brief/factor_harvest/earnings_triage/auto_pool：夜间自愈不值这个钱）。
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
# 时间窗：计划时刻后 6 小时内补（10-22 那次重建在 17:29，距 15:45 仅 1h44m）
CATCH_UP_WINDOW = timedelta(hours=6)
# 每次启动最多补 3 个（按计划时刻升序），防「一次重启把重任务全跑一遍」
CATCH_UP_MAX_JOBS = 3
# 运行历史来源标记（job_runs.source）；kind 仍记 scheduled（run_job 的既有映射）
CATCH_UP_SOURCE = "catchup"
# dev 反复重启止血开关
CATCH_UP_ENV_SWITCH = "OPS_CATCHUP_ON_STARTUP"

_DEFAULT_TZ = "Asia/Shanghai"


def catch_up_enabled() -> bool:
    """启动补跑总开关（``OPS_CATCHUP_ON_STARTUP=0`` 关闭；默认开）。"""
    return os.getenv(CATCH_UP_ENV_SWITCH, "1") != "0"


def scheduled_fire_time(spec: Mapping[str, Any], now: datetime) -> datetime | None:
    """该任务**当天**的计划触发时刻；当天不触发 → ``None``。

    与 ``scheduler._cron_from_spec`` 同一语义（月批看 ``day_of_month``，日批看
    ``day_of_week``：``mon-fri`` → 周末不触发，``*`` → 每天都触发）。
    """
    tz = ZoneInfo(str(spec.get("timezone", _DEFAULT_TZ)))
    local = now.astimezone(tz)
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
    ``failed`` **不**拦补跑——那正是需要重试的一类。
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
        late = now.astimezone(fire.tzinfo) - fire
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
```

- [ ] **Step 4: 跑测试确认绿**

Run: `uv run pytest tests/outcome/test_ops_catchup.py -v`
Expected: PASS（10 passed）

- [ ] **Step 5: 提交**

```bash
git add src/finance_agent/outcome/ops/catchup.py tests/outcome/test_ops_catchup.py
git commit -m "feat(ops): 启动补跑规划（白名单/6h 窗/上限 3/非交易日链中止）"
```

---

### Task 3: 补跑执行（顺序 + 中止 + 不阻断）

**Files:**
- Modify: `src/finance_agent/outcome/ops/catchup.py`
- Test: `tests/outcome/test_ops_catchup.py`

**Interfaces:**
- Produces:
  - `run_catch_up(*, now: datetime | None = None, db_path=None, runner: Callable[[str], dict[str, Any]] | None = None, statuses_of=None, **plan_kwargs) -> dict[str, Any]`
  - `catch_up_job() -> dict[str, Any]`（APScheduler 入口，吞异常）

- [ ] **Step 1: 写失败测试**

```python
class TestRunCatchUp:
    def test_runs_in_schedule_order(self):
        calls = []

        def runner(job_id):
            calls.append(job_id)
            return {"status": "ok", "summary": {}, "run_id": 1}

        result = run_catch_up(now=_weekday_at(17, 29), statuses_of=lambda _j: [], runner=runner)
        assert calls == ["market_snapshot_daily", "regime_daily", "screening_daily"]
        assert result["aborted"] is None
        assert [item["status"] for item in result["executed"]] == ["ok", "ok", "ok"]

    def test_non_trading_day_aborts_chain(self):
        calls = []

        def runner(job_id):
            calls.append(job_id)
            return {"status": "ok", "summary": {"skipped": "non_trading_day"}, "run_id": 1}

        result = run_catch_up(now=_weekday_at(17, 29), statuses_of=lambda _j: [], runner=runner)
        assert calls == ["market_snapshot_daily"]
        assert result["aborted"] == "non_trading_day"

    def test_job_already_running_is_skipped_not_failed(self):
        def runner(_job_id):
            raise JobAlreadyRunning("已在运行")

        result = run_catch_up(now=_weekday_at(17, 29), statuses_of=lambda _j: [], runner=runner)
        assert result["executed"][0]["status"] == "skipped_running"

    def test_runner_exception_does_not_propagate(self):
        def runner(job_id):
            if job_id == "market_snapshot_daily":
                raise RuntimeError("source down")
            return {"status": "ok", "summary": {}, "run_id": 2}

        result = run_catch_up(now=_weekday_at(17, 29), statuses_of=lambda _j: [], runner=runner)
        assert result["executed"][0]["status"] == "failed"
        assert "source down" in result["executed"][0]["error"]
        # 失败不中止：后续任务照跑
        assert [item["job_id"] for item in result["executed"]] == [
            "market_snapshot_daily",
            "regime_daily",
            "screening_daily",
        ]

    def test_empty_plan_executes_nothing(self):
        result = run_catch_up(now=_weekday_at(23, 59), statuses_of=lambda _j: [], runner=lambda _j: {})
        assert result == {"planned": [], "executed": [], "aborted": None}
```

- [ ] **Step 2: 跑测试确认红**

Run: `uv run pytest tests/outcome/test_ops_catchup.py::TestRunCatchUp -v`
Expected: FAIL — `ImportError: cannot import name 'run_catch_up'`

- [ ] **Step 3: 实现**

```python
def _default_runner(db_path: Any) -> Callable[[str], dict[str, Any]]:
    """生产 runner：经统一入口落历史，``source=catchup`` 可区分于定时/手动。"""

    def run(job_id: str) -> dict[str, Any]:
        return run_job(job_id, source=CATCH_UP_SOURCE, db_path=db_path)

    return run


def _statuses_reader(db_path: Any) -> Callable[[str], Sequence[str]]:
    day = datetime.now(ZoneInfo(_DEFAULT_TZ)).date().isoformat()

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

    顺序执行（快照先于下游）；单个任务失败**不**中止后续，只有「非交易日」中止链
    （休市日下游必失败，继续只会刷失败行）。异常一律内吞，只记日志。
    """
    moment = now if now is not None else datetime.now(ZoneInfo(_DEFAULT_TZ))
    read_statuses = statuses_of if statuses_of is not None else _statuses_reader(db_path)
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
    executed: list[dict[str, Any]] = []
    aborted: str | None = None
    logger.warning(
        "启动补跑：%d 个任务漏跑且在窗内（%s）",
        len(plan),
        ", ".join(item["job_id"] for item in plan),
    )
    for item in plan:
        job_id = str(item["job_id"])
        try:
            outcome = run(job_id)
        except JobAlreadyRunning:
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
    return {"planned": [item["job_id"] for item in plan], "executed": executed, "aborted": aborted}


def catch_up_job() -> dict[str, Any]:
    """APScheduler 入口（启动后 5s 一次性触发）：任何异常只记日志。"""
    try:
        outcome = run_catch_up()
    except Exception as exc:  # noqa: BLE001 - 旁路铁律
        logger.warning("启动补跑整体失败（忽略）：%s", exc, exc_info=True)
        return {"error": f"{type(exc).__name__}: {exc}"}
    if outcome["executed"]:
        logger.warning("启动补跑完成：%s", outcome)
    return outcome
```

- [ ] **Step 4: 跑测试确认绿**

Run: `uv run pytest tests/outcome/test_ops_catchup.py -v`
Expected: PASS（15 passed）

- [ ] **Step 5: 提交**

```bash
git add src/finance_agent/outcome/ops/catchup.py tests/outcome/test_ops_catchup.py
git commit -m "feat(ops): run_catch_up 顺序执行 + 非交易日链中止 + 失败不阻断"
```

---

### Task 4: scheduler 接线（一次性 date job）

**Files:**
- Modify: `src/finance_agent/outcome/scheduler.py:453-710`
- Test: `tests/outcome/test_scheduler.py`

**Interfaces:**
- Consumes: `catch_up_enabled()`、`catch_up_job()`（Task 3）
- Produces: `CATCH_UP_JOB_ID = "ops_catch_up_once"`

- [ ] **Step 1: 写失败测试**

```python
class TestCatchUpOnStartup:
    @patch.dict(os.environ, {"TESTING": "", "DECISION_SETTLE_ENABLED": "1"})
    @patch("finance_agent.outcome.scheduler.BackgroundScheduler")
    def test_registers_one_shot_catch_up_job(self, mock_sched_cls):
        sched = MagicMock()
        mock_sched_cls.return_value = sched
        start_scheduler()
        call = _job_call(sched, CATCH_UP_JOB_ID)
        assert call.args[0] is catch_up_job
        assert call.kwargs["trigger"] == "date" or call.args[1] == "date"
        assert call.kwargs["replace_existing"] is True

    @patch.dict(
        os.environ,
        {"TESTING": "", "DECISION_SETTLE_ENABLED": "1", "OPS_CATCHUP_ON_STARTUP": "0"},
    )
    @patch("finance_agent.outcome.scheduler.BackgroundScheduler")
    def test_env_switch_disables_catch_up_registration(self, mock_sched_cls):
        sched = MagicMock()
        mock_sched_cls.return_value = sched
        start_scheduler()
        ids = [c.kwargs.get("id") for c in sched.add_job.call_args_list]
        assert CATCH_UP_JOB_ID not in ids

    @patch.dict(os.environ, {"TESTING": "1"})
    def test_testing_never_registers_catch_up(self):
        assert start_scheduler() is None
```

- [ ] **Step 2: 跑测试确认红**

Run: `uv run pytest tests/outcome/test_scheduler.py -k catch_up -v`
Expected: FAIL — `ImportError: cannot import name 'CATCH_UP_JOB_ID'`

- [ ] **Step 3: 实现**

```python
# 启动补跑（delta add-ops-missed-run-catchup）：进程重建丢掉触发点的补偿。
# 用一次性 date job 而非裸线程——测试里被 MagicMock 吸收，不触真实库；
# 生产在 scheduler 启动后 5s 触发（此时各 job 已注册完）。
CATCH_UP_JOB_ID = "ops_catch_up_once"
_CATCH_UP_DELAY = timedelta(seconds=5)
```

在 `start_scheduler()` 的 `scheduler.start()` **之前**（紧跟 `cohort` 重排之后）插入：

```python
    if catch_up_enabled():
        scheduler.add_job(
            catch_up_job,
            "date",
            run_date=datetime.now(ZoneInfo("Asia/Shanghai")) + _CATCH_UP_DELAY,
            id=CATCH_UP_JOB_ID,
            replace_existing=True,
            misfire_grace_time=600,
            coalesce=True,
        )
```

- [ ] **Step 4: 跑测试确认绿，并修既有「注册面全等」守护**

Run: `uv run pytest tests/outcome/test_scheduler.py -v`
Expected: 新 3 例 PASS；`TestSchedulerRegistryMirror` 与 `TestCohortJobRegistration` 因多注册 1 个 id 而红 → 按下方 Step 5 更新（这是守护的正确行为：注册面变了要显式记账）。

- [ ] **Step 5: 更新注册面守护（显式记账，不放宽）**

```python
# TestSchedulerRegistryMirror
        assert set(ids) - {CATCH_UP_JOB_ID} == set(JOB_IDS)
        assert len(ids) == len(JOB_IDS) + 1

# TestCohortJobRegistration：28 → 29，并在注释里补
        assert len(ids) == 29, ids  # 27 日批/月批 + 1 个一次性启动补跑 job（ops_catch_up_once）
```

- [ ] **Step 6: 提交**

```bash
git add src/finance_agent/outcome/scheduler.py tests/outcome/test_scheduler.py
git commit -m "feat(ops): 调度器启动注册一次性补跑 job（OPS_CATCHUP_ON_STARTUP 可关）"
```

---

### Task 5: `/jobs` 暴露 missed_today

**Files:**
- Modify: `src/finance_agent/ops_api.py:317-336`（`_job_rows`）
- Test: `tests/test_ops_api.py`

**Interfaces:**
- Consumes: `job_run_statuses_on`（Task 1）、`scheduled_fire_time`（Task 2）
- Produces: 每行新增 `missed_today: bool`

- [ ] **Step 1: 写失败测试**

```python
class TestMissedToday:
    def test_true_when_time_passed_and_no_run(self, client, monkeypatch):
        monkeypatch.setattr(
            "finance_agent.ops_api._now", lambda: datetime(2026, 10, 22, 16, 10)
        )
        job = _job(client.get(f"{BASE}/jobs").json(), "factor_decay_monitor")
        assert job["missed_today"] is True

    def test_false_before_fire_time(self, client, monkeypatch):
        monkeypatch.setattr(
            "finance_agent.ops_api._now", lambda: datetime(2026, 10, 22, 15, 0)
        )
        job = _job(client.get(f"{BASE}/jobs").json(), "factor_decay_monitor")
        assert job["missed_today"] is False

    def test_false_when_run_recorded_even_if_failed(self, client, monkeypatch):
        monkeypatch.setattr(
            "finance_agent.ops_api._now", lambda: datetime(2026, 10, 22, 16, 10)
        )
        rows = list_job_runs("factor_decay_monitor", db_path=_db())
        _seed_run("factor_decay_monitor", status="failed", error="x")
        job = _job(client.get(f"{BASE}/jobs").json(), "factor_decay_monitor")
        assert job["missed_today"] is False
```

（`_seed_run` 已存在；若其写入时刻不可注入，则用 `insert_job_run` + `finish_job_run` 直写。）

- [ ] **Step 2: 跑测试确认红**

Run: `uv run pytest tests/test_ops_api.py -k missed -v`
Expected: FAIL — `KeyError: 'missed_today'`

- [ ] **Step 3: 实现**

```python
def _now() -> datetime:
    """状态接口的时钟（测试可 patch；默认本地时区）。"""
    return datetime.now()


def _missed_today(job_id: str, spec: dict[str, Any], now: datetime) -> bool:
    """当天计划时刻已过且当天**没有任何运行行**（``running`` 也算有）。

    「压根没跑」与「跑了但失败」是两种故障，后者由 ``last_run.status`` 表达；
    非排程日（周末 / 月批非排程日）或尚未到点 → ``False``（当天本无触发点）。
    """
    fire = scheduled_fire_time(spec, now.astimezone(ZoneInfo("Asia/Shanghai")))
    if fire is None or now.astimezone(fire.tzinfo) < fire:
        return False
    day = now.astimezone(fire.tzinfo).date().isoformat()
    return not job_run_statuses_on(job_id, day)
```

`_job_rows` 循环内取一次 `moment = _now()`，行里加：

```python
                "missed_today": _missed_today(job_id, spec, moment),
```

- [ ] **Step 4: 跑测试确认绿**

Run: `uv run pytest tests/test_ops_api.py -k "missed or TestJobsEndpoint" -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add src/finance_agent/ops_api.py tests/test_ops_api.py
git commit -m "feat(ops): /jobs 暴露 missed_today（计划时刻已过且当天无运行行）"
```

---

### Task 6: 收口

- [ ] **Step 1: 全量相关测试**

Run: `uv run pytest tests/outcome tests/test_ops_api.py -q`
Expected: 全绿

- [ ] **Step 2: 静态检查**

Run: `uv run ruff check src tests && uv run mypy src/finance_agent/outcome src/finance_agent/ops_api.py`
Expected: 无新增告警

- [ ] **Step 3: 真实链路干跑（不写库）**

```bash
uv run python -c "from finance_agent.outcome.ops.catchup import plan_catch_up; from datetime import datetime; print(plan_catch_up(now=datetime.now().astimezone(), statuses_of=lambda j: []))"
```

Expected: 打印今日待补任务（或空列表，说明当前无漏跑）

- [ ] **Step 4: 文档收口**

- `docs/incidents/050-factor-ic-missed-fire-20261022.md`：「防复发」第 1、2 条改为已落（引用 delta 与本 delta 的 tasks）；第 3 条保持待落。
- `docs/incidents/README.md`：050 行「处置」列补「启动补跑 + missed_today 已落」。
- `openspec validate add-ops-missed-run-catchup --strict`

- [ ] **Step 5: 提交**

```bash
git add docs/incidents/050-factor-ic-missed-fire-20261022.md docs/incidents/README.md
git commit -m "docs(incidents): 050 防复发第 1/2 条已落（启动补跑 + missed_today）"
```

---

## Self-Review

1. **Spec 覆盖**：`missed_today` 六条场景 → Task 5（true/false×3）；补跑七条场景 → Task 2（窗内/超窗/未到点/周末/已 ok/上限）+ Task 3（顺序/中止/失败/已运行）+ Task 4（注册与 env 开关）。
2. **占位符扫描**：无 TBD；每步含命令与预期输出；`_expected_fire` 辅助在 Task 2 测试内需按 `SCHEDULES` 现算（实现时写成局部函数，避免测试与实现共用同一算法成为同义反复——用硬编码期望顺序：market_snapshot 15:30 → regime 15:35 → screening 15:40）。
3. **类型一致**：`job_run_statuses_on` 返回 `list[str]`，`statuses_of` 接收 `Sequence[str]`；`run_catch_up` 返回三键 dict；`CATCH_UP_JOB_ID` 只在 scheduler 导出。
