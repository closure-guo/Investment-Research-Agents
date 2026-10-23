# Proposal: extend-factor-ic-gap-backfill

## Why

衰减监控日批（`factor_decay_monitor`）每日只记录每个 active 因子**最新一个**可算 IC 日（`monitor._record_latest_ic`）。当监控连缺多个交易日（进程重建窗口 / 调度中断，incident 050 家族），恢复后的单次运行只补上最新一天，中间日期**永久缺失**——启动补跑（add-ops-missed-run-catchup）只覆盖「当天错过的触发」，跨日中断的洞无人填。IC 序列留洞会让 60 日滚动衰减判定与选股打分权重窗的实际日历跨度悄悄变长（窗口按记录条数计，不按日历）。

## What Changes

- 衰减监控日批的 IC 记录语义从「最新可算日恰好一条」扩展为「**面板窗口内全部缺失的可算日一次补齐**」（`record_ic` 幂等 upsert；已存在日期不重写）；
- 运行摘要新增 `days_recorded`（本次运行写入的 IC 行数；`recorded` 现有语义不变 = 有可算 IC 的因子数）；
- 超出监控面板窗口（`DECAY_WINDOW+30` 交易日）的更早缺口无法回补——诚实留洞，不虚构。

## Capabilities

- **Modified Capabilities**: factor-library（新增「IC 采集记录与缺口回填」需求；现有衰减监控/降级判定需求不变）

## Impact

- `src/finance_agent/factors/monitor.py`（`_record_latest_ic` → 缺口回填实现；`run_factor_monitor` 摘要键）
- `tests/factors/test_monitor.py`（缺口回填/幂等场景）
- 消费方（ops API 摘要透传、ranker/recent_ic_stats 读 `factor_ic`）无需改动：写侧扩为补齐，读侧口径（升序序列）不变
