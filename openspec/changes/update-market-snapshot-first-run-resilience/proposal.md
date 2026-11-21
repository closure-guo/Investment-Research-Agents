# Proposal: update-market-snapshot-first-run-resilience

## Why

快照日批 15:30 整点首跑连续 3 交易日五连败（run 576/633/669/701/770，issue #602 坐实）：东财主源 #325 IP 封禁后 `fetch_market_spot` 恒走新浪 `stock_zh_a_spot`，15:30:00 为全市场调度峰值（新浪限频），首跑重试窗内耗尽。系统有 catchup 自愈（60s 冷却后 ~15:31 补齐，五例全成）但每日产一条 failed run + D5 告警噪声。#602 处置清单：错峰调度 + 首跑有界重试 + 失败细节可读。

## What Changes

- **错峰**：`MARKET_SNAPSHOT_SCHEDULE` 15:30 → **15:31**（issue 候选①给的是 15:33 或抖动；选 15:31 的裁定：catchup 五例「15:31+ 必成」是实证成功的时槽，同等于把已证时槽提前为排程；且级联约束——`regime_daily` 15:35 读快照库、`screening_daily` 15:40——要求「首轮失败 + 65s 重试 + 完整跑完」最坏完成 ≤15:34，15:33 起跑最坏 ~15:35:40 会撞 regime 读库时点）
- **首跑有界重试**：spot 双源皆 None 后不立即 fail，65s 冷却后同 run 内重试一轮（把自愈从「下一跳 catchup」提前到「同 run 内」，节奏复用已证有效的 60s 冷却 + 余量）；命中时摘要 `spot_retry=true` 留痕
- **失败细节可读**：`_call_ak` 经可选 `failure_sink` 上报末次失败（源名/异常类型/attempt 数），`fetch_market_spot` 留痕 `last_spot_failure`；两源皆败（含重试轮）后抛出的 `SnapshotError` 消息内嵌源级明细——经 `run_job` 落 `job_runs.error`、经批失败路径落快照 `meta.last_error`，均为 D5 告警读取面（ops 层失败 run 的 `summary` 语义保持 None 不动）

## Capabilities

- **Modified Capabilities**: `market-snapshot`（快照落库时点 15:31；spot 数据源回退失败明细）、`stock-screening`（日批排程括注同步 15:31）
- **Added Requirements**: `market-snapshot` 新增「spot 双源失败同 run 有界重试」requirement

## Impact

- `src/finance_agent/data/snapshot_job.py`：schedule minute、重试编排、错误消息
- `src/finance_agent/data/akshare_client.py`：`_call_ak` 可选 sink（向后兼容）、`fetch_market_spot` 留痕
- 不动 ops 层（`run_job` 失败 `summary=None` 语义保持）；不动 scheduler/catchup 机制
- E2E：daily-ops 冻结时钟 09:00，与排程分钟无耦合，无需改 spec
