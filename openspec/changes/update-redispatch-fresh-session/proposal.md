# Proposal: update-redispatch-fresh-session

## Why

10-28 opencode 宕机恢复实操（issue #546）实证：两处「失败行复用旧 `session_id` 重派」的路径是死路——
终态会话从不被快路径重置为 `running`，复用入队后队列终态轮询（`TERMINAL_STATES`）首次读会话即判死，
9 只复用会话入队→拉起→秒灭、零事件产出。该语义在 `report-triggered-analysis` 主规范中被显式钉死
（SHALL 复用原 session_id…MUST NOT 新建会话），在精选池侧只存在于 docstring——规范本身错了，按 §4.2 修契约。

## What Changes

- **精选池重派**（`stock-screening`）：`failed` / `dispatch_failed` / `session_failed` 行重派
  SHALL 走**全新会话**（旧会话保留作审计，运行行 `session_id` 由 upsert 更新为新会话）；
  `queued` / `completed` 的幂等语义不变。原「至多创建一次会话」语言补失败重派例外。
- **研报触发重派**（`report-triggered-analysis`）：`dispatch_failed` 行重派同样全新会话
  （替换原「复用原 session_id、MUST NOT 新建会话」）；并修复复用路径顺带缺陷——
  成功派发后触发行不回写 `queued`，行永远停在 `dispatch_failed` 会被无限重派。
- 代码侧 `pool_analysis.py` 与 `report_trigger.py` 两处同款死路一并移除，docstring 同步改。

## Capabilities

- **Modified Capabilities**: `stock-screening`（精选池自动深度分析）、`report-triggered-analysis`
  （触发幂等与当日去重）

## Impact

- `src/finance_agent/screening/pool_analysis.py`、`src/finance_agent/screening/report_trigger.py`
- 测试：`tests/screening/test_pool_analysis.py`（复用断言翻转）、`tests/screening/test_report_trigger.py`（新增重派用例）
- 运维语义：10-28 当天的人工处置口径（清空 session_id 重派）即为修复后行为，无需数据迁移
