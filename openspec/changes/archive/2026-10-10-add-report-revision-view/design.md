# Design: add-report-revision-view

## Approach

增量摘要 = 一次回溯查询 + 确定性渲染，全链路零 LLM 调用：

1. **持久化补漏**：`update_session_report` 调用点（`api.py` / `agent_factory.py` 两处）把 `final_trade_decision` 序列化传入，存会话行（新增 JSON 列或并入既有 `agent_process` JSON——实现时择一，倾向并入 `agent_process` 避免 DDL）。
2. **回溯查询**：`session_store` 加一个只读查询（`WHERE stock_code=? AND status='completed' AND session_id<>? ORDER BY created_at DESC LIMIT 1`），返回 diff 所需字段子集。sessions 表量级小，`stock_code` 无索引可接受；若后续量级上来再加索引。
3. **渲染**：report 节点在「研究聚焦」节后插入增量摘要节；每个维度一个确定性 diff 函数（输入新旧值，输出「旧 → 新 / 无变化 / 未申报」）。分歧卡复用 RM 结构化评级字段 + 结论文本首句截取。

## Alternatives Considered

- **LLM 生成「距上次」叙述**：被否。确定性 diff 足够且零成本零幻觉风险；叙述润色是后续可选项，不进本 delta。
- **独立报告快照表**：被否。sessions 行已含 chart_data/agent_process/report_markdown，只缺 final_trade_decision 一个字段；新建表是冗余。
- **解析上一报告 markdown 提取数字**：被否（规格里已显式禁止）。markdown 是展示产物不是数据源；解析自身产物是自我引用脆弱性。

## Risks

- **同日重跑同标的**：diff 目标变成几小时前的自己，摘要全是「无变化」。可接受——如实渲染正是设计意图（幂等重跑=无增量是真实信息）。
- **`final_trade_decision` 历史行为 NULL**：首批增量摘要的决策维度会显示「未申报」，随新报告积累自愈。规格已要求逐维度降级而非整节不渲染。
- **软依赖 add-watch-trigger-tracking**：触发位维度依赖其字段落地；未落地时该维度「未申报」。两个 delta 可独立交付、独立归档。
