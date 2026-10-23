# Proposal: add-ops-missed-run-catchup

## Why

`MemoryJobStore` + in-process APScheduler 的固有缺陷在 2026-10-22 变成真实数据缺口：15:45 的 `factor_decay_monitor` 触发点正好落在一次进程重建窗口内（15:35 之后、15:50 之前），调度器重启后按 cron 重新注册、**不回溯**错过的触发点，`misfire_grace_time=3600` 也覆盖不了「进程在触发点不在」。结果当日 IC 采集零记录，`factor_ic` 停在 10-20，筛选因 IC 样本不足（≥10）继续走流动性降级（incident 050）。

更关键的是这不是一次性故障——`job_runs` 里同一族窗口已经打掉三次不同日批：

| 时间 | 任务 | 结果 |
|---|---|---|
| 10-20 15:45 | `factor_decay_monitor` | `stale_process_restart`（当晚 23:54 手工重跑） |
| 10-21 15:40 | `screening_daily` | `stale_process_restart` |
| 10-22 15:45 | `factor_decay_monitor` | **连运行行都没有**（进程当时不在） |

而补偿能力目前为零：`start_scheduler` 只注册 cron（无启动补跑）；`/api/v1/ops/jobs` 不暴露「今天该跑却没跑」；`_sweep_stale_running` 只把悬挂行改判 `failed`，不重跑。发现漏跑的唯一途径是人肉核对 `job_runs` 或发现下游数据不对——10-22 那次是 4 小时后才手工补上的。

## What Changes

- **启动补跑**：`start_scheduler()` 起调度器后，后台线程对「当天计划时刻已过、且当天无成功运行」的日批任务补跑一次。四道约束：
  - **白名单**：只覆盖幂等、当日补齐有价值的日批；账务/下单类（`paper_execute_daily`/`paper_settle_daily`/`event_settle_daily`）与烧钱类（`cohort_batch`/`daily_brief_daily`/`factor_harvest_daily`/`earnings_triage_daily`/`auto_pool_analysis_daily`）**一律不自动重放**，仍走人工补跑。
  - **时间窗**：只在计划时刻后 6 小时内补（更晚的漏跑交给人工——迟到太久的数据对当日链已无意义，且避免夜间重跑）。
  - **单次上限**：每次启动最多补 3 个任务，按计划时刻升序取前 3。
  - **非交易日链中止**：`market_snapshot_daily` 返回 `{"skipped": "non_trading_day"}` 时中止后续补跑（休市日不产生一串下游 failed）。
- **可见性**：`/api/v1/ops/jobs` 每个任务行新增 `missed_today`——当天计划时刻已过且当天无任何运行行（含 running）为 `true`。这是「压根没跑」，与「跑了但失败」（`last_run.status=failed` 已可见）刻意分开，值班不需要看两个地方才能分辨。
- **审计**：补跑经 `run_job(source="catchup")` 落历史，`job_runs.source` 可区分定时 / 手动 / 补跑。
- **旁路铁律不变**：补跑任何异常只记日志，不得阻断 API 启动；补跑在后台线程，不占用 lifespan。

## Capabilities

### Modified Capabilities

- `eval-ops-console`：状态接口增加 `missed_today`；新增「错过的日批触发补偿」要求。

### New Capabilities

（无）

## Impact

- 代码：
  - 新增 `src/finance_agent/outcome/ops/catchup.py`（纯规划 + 顺序执行 + 后台线程入口）
  - `src/finance_agent/outcome/scheduler.py`（`start_scheduler` 末尾拉起补跑线程）
  - `src/finance_agent/ops_api.py`（`_job_rows` 增加 `missed_today`）
  - `src/finance_agent/outcome/ops/model.py`（新增 `job_run_statuses_on`，按天取状态）
- 测试：`tests/outcome/test_ops_catchup.py`（新）、`tests/outcome/test_scheduler.py`（拉起/门控）、`tests/test_ops_api.py`（`missed_today` 四态）。
- 行为：夜间部署/重启打掉的日批在 6h 内自愈并留痕；其余（账务、烧钱、超窗）仍按人工补跑，不改变其口径。
- 不做：前端渲染 `missed_today`（本轮只到 API，属交互类变更，另立 delta 并配 E2E）；`factor_decay_monitor` 多日回填窗口（会改变 IC 口径语义，需单独评审）。
