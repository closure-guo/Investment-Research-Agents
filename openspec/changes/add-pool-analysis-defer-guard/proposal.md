# Proposal: add-pool-analysis-defer-guard

## Why

自动池分析 16:22 定时触发与选股日批收尾之间只剩 38~60 秒时序余量（issue #578；11-10 补跑收尾 16:21:22、11-11 scheduled 收尾 16:21:28，两日靠侥幸用上 fresh 批）。选股 L2/L3 耗时在 26~41 分钟间波动，任何一次变慢或宿主盘中重启，池分析就会静默降级 stale（用昨日池冒充当日池）。小票档更存在结构性错位：`auto_pool_analysis_microcap` 排在 16:47，而其前置 `microcap_screening_daily` 16:45 才起跑、连续 8 日 17:04–17:17 收尾——scheduled 档首日（11-11 run 653）直接 `skipped_empty_pool` 零产出，之后将永远吃前一交易日批次。

## What Changes

- 自动池分析日批提交前增加**前置顺延护栏**：前置选股 job 当日 `job_runs` 最新行为 `running` 时，按间隔（默认 300s）顺延重查，主档至多 3 轮 / 小票档至多 8 轮；等待耗尽仍 running 则照常提交（既有 stale 降级路径不变），前置已终态或当日无运行行则立即提交（深夜补跑零等待）。
- 顺延轮数与等待秒数计入运行摘要 `defer` 字段（`outcome` / `rounds` / `waited_seconds`），运维可观测。
- 三个环境变量开关：`AUTO_POOL_ANALYSIS_DEFER_ENABLED`（默认 1）、`AUTO_POOL_ANALYSIS_DEFER_INTERVAL_SECONDS`（默认 300）、`AUTO_POOL_ANALYSIS_DEFER_MAX_ROUNDS`（主档默认 3）与 `AUTO_POOL_MICROCAP_DEFER_MAX_ROUNDS`（小票档默认 8，覆盖其 25~32 分钟的前置时长）。

## Capabilities

- **Modified Capabilities**: `stock-screening`（新增 ADDED requirement：池分析日批前置顺延护栏；既有「精选池自动深度分析」语义不变）

## Impact

- `src/finance_agent/screening/pool_analysis.py`：新增护栏函数与两个日批入口接线
- 运行摘要新增 `defer` 字段（消费方：ops jobs API / daily brief 读 summary，均透传，无破坏）
- 调度线程占用：主档最坏 16:22+15min、小票档最坏 16:47+40min 阻塞各自 job 线程（APScheduler 默认池 10，与相邻作业无锁冲突）
