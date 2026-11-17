# Proposal: enforce-signal-freshness-at-planning

## Why

flat（退出）信号一经创建即永久存活：执行规划不重验来源决策的当前状态、不感知同标的更新的方向决策——2026-11-17 生产实证（issue #596）：12 天前已 resolved_win 的 short 决策遗留的 flat 信号，击穿了 11-12 buy 决策开的新仓（34,400 股 @5.83 全数卖出）。决策层最新裁定被信号层最老裁定处决，语义完全颠倒。

## What Changes

- **来源状态重验**：规划层消费 pending 信号前逐信号重验来源决策当前 `status`；非 `open`（resolved*/superseded/duplicate_of_day/void）来源的信号跳过并留痕 `STALE_SOURCE`（long 同样生效——旧 buy 来源结算后不再追高开仓）
- **newest-wins 方向冲突消解**：同标的多条待执行信号仅决策日最新者参与规划；更旧的跳过并留痕 `SUPERSEDED_BY_NEWER`（含胜者 signal_id）
- 校验只作用于「尚未产生订单的 pending 信号」——已执行信号与其订单不动（幂等锚点不变）

## Capabilities

- **Modified Capabilities**: 无（既有「信号到订单的日批执行」需求文本不动）
- **New Capabilities**: `trade-execution` 追加 1 条 ADDED Requirement（信号执行前校验）

## Impact

- `src/finance_agent/execution/driver.py`（planning 前过滤层 + skipped_details 两个新原因码）
- 无表结构变更、无 prompt 变更；存量待执行信号在下一执行日批被校验清场（600780.SH:2026-11-12 的第二颗同款地雷同批拆除）
- 纯后端逻辑变更，不触发 E2E 门禁
