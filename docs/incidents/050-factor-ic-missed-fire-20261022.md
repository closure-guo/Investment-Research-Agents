# Incident 050 — 15:45 IC 采集触发落在进程重建窗口：MemoryJobStore 不补跑，筛选降级延长一天

**日期**: 2026-10-22
**严重度**: 低（数据延迟一天；当日已手工补齐）
**关联**: [046](046-sessions-db-wal-handle-index-corruption-20261020.md)（同日进程重建家族）、`add-screening-ic-bootstrap-guard`（IC 样本门槛）、因子收割/筛选链路

## 现象

- 2026-10-22 15:45 的 `factor_decay_monitor` **没有任何运行记录**（`job_runs` 在该时刻为空档）。
- 当日 `factor_ic` 最新日期停在 2026-10-21 之前：因子 1–4 各 3 条，新因子 5–9 为 0 条。
- 筛选 10-21 批次 `selection_mode=fallback`（流动性降级）：`select_scorable_factors` 要求每因子 ≥10 条 RankIC 样本（`MIN_IC_SAMPLES_FOR_SCORING`），实际 2–3 条 → 全部被排除。

## 时间线（job_runs 重建）

| 时刻 | 事实 |
|---|---|
| 15:30 / 15:35 | `market_snapshot_daily` / `regime_daily` 正常 |
| **15:45** | `factor_decay_monitor` **无记录** |
| 15:50 | `factor_builtin_register` 失败：`stale_process_restart`（进程重启遗留的悬挂运行行） |
| 15:55 → 17:22 | `factor_harvest_daily` 悬挂运行行在 17:22 被扫成 failed |
| 16:45 → 17:13 | `microcap_screening_daily` 失败：`cannot schedule new futures after interpreter shutdown`（解释器收尾时线程池已关） |
| 17:29 | 后端容器重建（`docker inspect` StartedAt 09:29Z） |
| 18:00 | `cohort_batch` 在新进程正常跑完 |

## 根因

1. **APScheduler 用 `MemoryJobStore`**：进程重启后 job 按 cron 重新注册，调度器**不回溯**已经错过的触发点；`misfire_grace_time=3600` 只覆盖「调度器存活但执行被延迟」，覆盖不了「进程在触发点不在」。
2. 15:45 正处于一次进程重建窗口（15:35 之后、15:50 之前），该次触发被永久丢弃。
3. IC 采集日批因此少跑一天，`factor_ic` 停留在 10-20；10-21 的 IC 行没有写入，bootstrap 计数不增长，筛选继续走降级通道。

## 处置

- **已手工补齐**：`POST /api/v1/ops/jobs/factor_decay_monitor/run`（20:55–20:56），`recorded=10`；`factor_ic` 现为 21 行、最新 2026-10-21（IC 用到 t+1 收益，10-22 收盘后的 IC 本身不可算，属正常）。
- **已补跑**：`factor_builtin_register`（20:57→21:07，`registered=0, rejected=5`，全部 gate 2 WEAK_ICIR）；`microcap_screening_daily` 20:57 起重跑（见当日晚间运行记录）。
- 结论：IC 采集链本身没坏，坏的是「错过触发点后的补偿机制」。

## 防复发

1. **启动补跑（已落，2026-10-23）**：delta `add-ops-missed-run-catchup` —— 调度器启动时注册一次性 `date` job（启动后 5s），对「当天已过点且当天无成功运行」的白名单日批补跑；白名单 9 项（快照/拥挤度/选股/因子监控/内置注册/判定/盯市/指标快照/完整性），时间窗 6h，单次上限 3 个，`market_snapshot_daily` 返回 `skipped=non_trading_day` 时中止下游链。账务/下单类与 LLM 烧钱类**不**自动重放（仍人工补跑）。`OPS_CATCHUP_ON_STARTUP=0` 可整体关闭。
   重放验证（只读，`tmp/dryrun_catchup_050.py`）：以 2026-10-22 17:29（当日重建完成时刻）为时点、只取该时刻之前的运行行，规划器输出 `screening_daily`(15:40) / `factor_decay_monitor`(15:45) / `factor_builtin_register`(15:50) 三项——正好覆盖当日被窗口打掉的三件事；其中 `factor_decay_monitor` 就是本次 IC 缺口的直接来源。
2. **缺失告警（已落，2026-10-23）**：`GET /api/v1/ops/jobs` 每个任务行新增 `missed_today`（当天计划时刻已过且当天无任何运行行，`running` 也算有）。「压根没跑」与「跑了但失败」刻意分列：后者由 `last_run.status` 表达。前端渲染未在本轮（属交互类变更，另立 delta 并配 E2E）。
3. **与 046 同族（待落）**：进程重建窗口内的长任务（microcap/harvest）应能识别「解释器收尾」并落可读终态，而非线程池异常。`_sweep_stale_running` 目前只把悬挂行改判 `failed`，不提供终态成因。
