# Proposal: add-report-revision-view

## Why

真实使用场景是反复分析同一只标的，但现在每份报告都从零讲起——10-08 的报告与 9-30 的报告 90% 内容相同，重读用户要再翻一遍约 7,400 字才能发现「什么都没变」。同时，多空辩论的核心分歧（本报告最有价值的一句话）埋在 465 字段落里，要读者自己提炼。

sessions 库已持久化每份报告的 markdown/chart_data/agent_process，diff 的数据基础存在；缺的只是「终稿决策持久化」（`final_trade_decision` 目前只进预测池快照、不进会话行）和一个回溯查询。

## What Changes

- 会话完成时持久化 `final_trade_decision`（终稿决策 JSON：方向/置信度/触发位/价位）进会话行
- 新增按 `stock_code` 回溯「上一份已完成报告」的读取能力
- 报告头（研究聚焦之后）渲染「距上次报告」增量摘要：方向/置信度变化、触发位变化、现价与 PE 对比；全维度对比均走结构化数据，MUST NOT 解析上一报告 markdown 文本；首份报告整节不渲染
- 「多空辩论结论」节头部渲染分歧卡（结构化评级 + 置信度 + 结论首句）

**非目标**：公告/舆情等事件流 diff（数据源维度，后续）；前端会话列表改动（本 delta 只动报告内容，前端无代码变更）；历史报告互相链接导航。

## Capabilities

- **Modified Capabilities**: 无
- **New Capabilities**: 无（均为既有 capability 的 ADDED requirement）
  - `session-persistence`：终稿决策持久化与同标的回溯查询
  - `report-decision-rendering`：报告头增量摘要 + 辩论结论分歧卡

## Impact

- 代码：`session_store.py`（持久化字段 + 回溯查询）、`nodes/report.py`（增量摘要与分歧卡渲染）、`api.py`/`agent_factory.py`（持久化调用点传参）
- 软依赖：`add-watch-trigger-tracking` 的 `trigger_high`/`trigger_low` 字段（增量摘要的触发位维度；字段未落地时该维度如实「未申报」，不阻塞本 delta）
- 交互类判定：纯后端报告内容变更（markdown 节），前端无代码变更 → 不适用 E2E 门禁；报告内容质量适用人工验证
- 无 DDL 变更（复用 sessions 既有 JSON 列或加一 JSON 列）
