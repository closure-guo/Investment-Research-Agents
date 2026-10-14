# Proposal: add-screening-batch-scheduling

## Why

`add-screening-pipeline` 实现了选股日批编排（`run_screening_batch`）但**没有接线任何触发器**：全仓零调用方——唯一调度器（`outcome/scheduler.py`）的 8 个任务里没有选股，无端点、无脚本、前端无按钮。「每个交易日快照更新成功后依次执行」的规范意图（stock-screening「选股日批编排」）因此从未在运行态成立：精选池永远为空，前向跟踪 / 选股评估 / 记忆月批的素材链全部断在第一步。

## What Changes

- ops 任务注册表新增 `screening_daily`（JOB_IDS 8→9）：任务函数封装 `run_screening_batch`，沿用「旁路铁律」（失败落历史不炸进程）与单飞锁。
- 排程：工作日 15:40（Asia/Shanghai），晚于行情快照 15:30；快照未就绪时批次 fail loud（沿用既有守卫：快照库为空 / 不新鲜 → 批次 failed、不产残缺池）。
- 评估运维页自动出现该任务（数据驱动）+ 手动补跑（复用既有通道）；运行历史含 `source=scheduled/manual`。
- 顺带修正 `eval-ops-console` 规范漂移：既有 requirement 枚举「5 个任务」，实际注册表在三个 delta 扩容后已是 8 个——本次 MODIFIED 为「全部已注册任务（当前 9）」。
- 既有测试最小更新（计数类）：后端注册表断言、E2E eval-ops 计数 8→9（同 5→8 先例，逐条记录）。

非目标（Out of scope）：

- 不建独立「日批运维页」（见 `add-daily-ops-page`）。
- 不改选股管线内部逻辑（L1/L2/L3 与守卫不动）。
- 不引入新的调度器或触发通道（复用 ops 注册表）。

## Capabilities

### New Capabilities
（无）

### Modified Capabilities
- `stock-screening`: 新增「日批排程与手动补跑」要求。
- `eval-ops-console`: 修正「日批运行状态与历史可观测」的任务枚举漂移（5 → 全部已注册，当前 9）。

## Impact

- **后端**：`outcome/ops/jobs.py`（注册表 + 标签 + 排程）、`outcome/scheduler.py`（注册与重试包装）。
- **测试**：注册表 / 排程单测；job 函数（stub batch）单测；E2E `eval-ops-console.spec.ts` 计数更新。
- **前端**：无代码改动（运维页任务列表数据驱动自动呈现）。
