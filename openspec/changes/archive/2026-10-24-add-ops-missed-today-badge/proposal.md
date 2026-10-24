# Proposal: add-ops-missed-today-badge

## Why

issue #453（add-ops-missed-run-catchup 5.1 边界移交）：后端 `GET /api/v1/ops/jobs` 已逐行返回 `missed_today`（「当天计划时刻已过、且当天无任何运行行（含 running）」），但 ops 控制台不渲染——值班只能靠「未跑」灰徽章与「失败」红徽章自行拼凑「今天该跑却没跑」这一最高优先级信号。「压根没跑」与「跑了但失败」是两种故障：前者可能是调度器挂了，后者是任务本身的问题。

## What Changes

- ops 控制台日批时间线消费后端 `missed_today`：为 `true` 的任务渲染「今日漏跑」告警位（degraded 琥珀样式，**与 failed 红区分**），摘要文案明示「排程时刻已过、当天无任何运行行」。
- 「跑了但失败」维持 failed 红徽章，**不误报为漏跑**（后端口径：当天有失败行 → `missed_today=false`，失败仍由 `last_run.status` 表达）。
- 未到排程时刻、非排程日（周末 / 月批非排程日）不告警，沿用既有「尚未到排程时刻」「未到本期排程日」文案。
- 后端 `missed_today` 字段为权威（服务器时钟 + running 行感知 + 补跑清扫感知）；字段缺失（旧载荷）时回退既有客户端推断，行为不变。
- TESTING 造数扩展：`/api/test/seed` 新增 `ops` 块（`now` 冻结 ops 状态时钟 + `runs` 造 `job_runs` 行 + `runs_reset` 清运行行），供 E2E 确定性构造漏跑 / 失败 / 未到点 / 非排程日四态（真实时钟下全部排程均在 15:35 后，上午跑 E2E 无法自然出现漏跑态）。

## Capabilities

- **New Capabilities**: 无
- **Modified Capabilities**: `frontend`（「日批运维页」：徽章枚举从五态扩为六态——新增「今日漏跑」，及四态渲染契约）

## Impact

- 前端：`frontend/src/types.ts`（`OpsJobStatus.missed_today`）、`frontend/src/pages/ops/DailyOpsPage.tsx`（`describeJob` 消费后端字段 + 漏跑徽章）。
- 后端（仅 TESTING 面）：`src/finance_agent/ops_api.py`（`_now` 可覆盖）、`src/finance_agent/api.py`（seed 端点 `ops` 块）。
- E2E：`tests/e2e/playwright/tests/ops-missed-badge.spec.ts`（复用 `playwright.isolated.config.ts`，8001/5174 + 独立库）。
- 不影响：`/api/v1/ops/jobs` 响应契约（字段已存在）、补跑通道、调度器。
