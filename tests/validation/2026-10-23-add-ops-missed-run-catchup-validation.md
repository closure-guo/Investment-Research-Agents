# add-ops-missed-run-catchup 验证报告

**日期**：2026-10-23
**delta**：`openspec/changes/add-ops-missed-run-catchup`（`openspec validate --strict` 通过）
**关联事故**：[050](../incidents/050-factor-ic-missed-fire-20261022.md)

## 1. 改动面

| 文件 | 改动 |
|---|---|
| `src/finance_agent/outcome/ops/catchup.py` | 新增：`scheduled_fire_time` / `plan_catch_up`（纯函数）+ `run_catch_up` / `catch_up_job`（顺序执行、非交易日链中止、异常内吞）+ 白名单/6h 窗/上限 3 |
| `src/finance_agent/outcome/ops/model.py` | 新增 `job_run_statuses_on(job_id, day)`：按天读状态（`missed_today` 与补跑规划共用口径） |
| `src/finance_agent/outcome/scheduler.py` | `start_scheduler()` 注册一次性 `date` job `ops_catch_up_once`（启动后 5s）；`OPS_CATCHUP_ON_STARTUP=0` 关闭 |
| `src/finance_agent/ops_api.py` | `/jobs` 每行新增 `missed_today`（计划时刻已过 + 当天无任何运行行） |

## 2. 测试证据

```
uv run pytest tests/outcome tests/factors/test_monitor.py -q          → 990 passed, 2 skipped
uv run pytest tests/test_ops_api.py tests/outcome/test_ops_catchup.py \
  tests/outcome/test_scheduler.py tests/outcome/test_ops_jobs.py \
  tests/outcome/test_ops_model.py tests/outcome/test_ops_statuses_on.py -q
                                                                       → 242 passed
uv run ruff check src/finance_agent/outcome src/finance_agent/ops_api.py <改动测试文件>
                                                                       → All checks passed
uv run mypy src/finance_agent/outcome/ops/catchup.py \
  src/finance_agent/outcome/ops/model.py src/finance_agent/outcome/scheduler.py \
  src/finance_agent/ops_api.py                                         → 无新增错误
```

mypy 报出的 7 条错误全部落在 `evals/extract.py`、`evals/judges.py`、`evals/task.py`、`evals/run.py`——单跑 `uv run mypy evals/extract.py` 复现同样的 4 条，为**既有基线**，与本 delta 无关。

新增用例：
- `tests/outcome/test_ops_catchup.py`（32 例）：窗内/超窗/窗口边界/未到点/周末/月批非排程日、`ok` 跳过、`failed` 重试、`running` 跳过、上限 3 与排序、非交易日中止链、非交易日以外的 skip 不中止、`JobAlreadyRunning` 记 skipped、异常不传播不中止、空计划零调用、白名单边界（8 个高风险任务不得入列）。
- `tests/outcome/test_scheduler.py::TestCatchUpOnStartup`（3 例）：注册一次性 job、`OPS_CATCHUP_ON_STARTUP=0` 不注册、TESTING=1 不注册。既有注册面守护（`TestSchedulerRegistryMirror` / `TestCohortJobRegistration` / `TestMisfireHardening`）同步显式记账：注册面 = `JOB_IDS` + 1 个非日批 job。
- `tests/outcome/test_ops_statuses_on.py`（9 例）：按天边界、跨日/跨任务不倒灌、非规范日期 `ValueError`。
- `tests/test_ops_api.py::TestMissedToday`（7 例）：已过点无行 → `true`；未到点 / 失败行 / running 行 / 周末 / 月批非排程日 → `false`；每行字段类型为 bool。

## 3. 真实链路重放（事故当天）

`tmp/dryrun_catchup_050.py`（只读 `data/sessions.db`，不执行任何 job）：以 2026-10-22 **17:29**（当日重建完成时刻）为时点、只取该时刻之前已发生的运行行：

```
planned=['screening_daily', 'factor_decay_monitor', 'factor_builtin_register']
  - screening_daily          15:40 迟到 109 分钟
  - factor_decay_monitor     15:45 迟到 104 分钟   ← 本次 IC 缺口的直接来源
  - factor_builtin_register  15:50 迟到  99 分钟
```

即：新补偿机制若当日已上线，这三件被窗口打掉的事会在启动时自动补上，无需 20:55 人工介入。真实时钟（2026-10-23 00:56，未到 15:30）规划为空，符合「无漏跑则零动作」。

## 4. 边界与未做

- **前端渲染 `missed_today` 未做**：属交互类变更（§3.5 E2E 门禁），本轮只到 API；值班当前可经接口或 `job_runs` 查。
- **不自动重放**：`paper_execute_daily` / `paper_settle_daily` / `event_settle_daily`（账务与下单）与 `cohort_batch` / `daily_brief_daily` / `factor_harvest_daily` / `earnings_triage_daily` / `auto_pool_analysis_daily`（LLM 烧钱），仍走人工补跑。
- **超窗（>6h）不补**：如 15:45 的任务在 23:00 之后才重启，仍需人工；这是刻意取舍（迟到过久对当日链无意义，且避免夜间重跑）。
- **`factor_decay_monitor` 多日回填窗口未做**：它每次只写「最新一个可算日」的 IC，连续漏两天会永久丢掉中间一天。补回填需改 IC 口径语义，另立 delta。
- **休市日**：依赖 `market_snapshot_daily` 的 `skipped=non_trading_day` 中止下游；若快照任务自身失败（非休市），下游仍会补跑并各自失败留痕——这是期望行为（失败可见优先于静默）。
