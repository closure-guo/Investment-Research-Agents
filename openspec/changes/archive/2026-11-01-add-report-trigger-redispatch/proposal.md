# Proposal: add-report-trigger-redispatch

## Why

`update-redispatch-fresh-session`（PR #552，#546）把研报触发的失败重派从「复用终态会话（死路）」
改为「全新会话」，代码已合并；其 report-triggered-analysis 部分因 archive 守卫（MODIFIED 块
MUST 覆盖现有场景集）与校验器（同名禁止 REMOVED+ADDED 并存）以「整条 REMOVED」归档，本
change 紧随其后把新语义同名 ADDED 回主规范——两段合起来等价于一次整条重写。

## What Changes

- 主规范 `report-triggered-analysis` 重建「触发幂等与当日去重」requirement：`dispatch_failed`
  行重派 SHALL 创建全新会话（旧会话保留作审计，`session_id` upsert 回写 `queued`）。

## Capabilities

- **Modified Capabilities**: `report-triggered-analysis`（触发幂等与当日去重——经 REMOVED 后
  同名 ADDED）

## Impact

- 仅规范层（代码已在 PR #552 落地并部署验证）；无代码变更。
