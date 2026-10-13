# decision-memory delta: add-decision-memory

## ADDED Requirements

### Requirement: 情景记忆注入

深度分析启动时，系统 SHALL 检索该标的及同申万三级行业标的的历史已结算决策（含决策方向、结算结果、论据摘要），取 Top-5 注入分析师与 Trader 上下文；注入条目 SHALL 标注来源决策 ID 与日期，并纳入既有 citation 校验覆盖。

#### Scenario: 同标的历史注入

- **GIVEN** 标的 300750 存在 3 条已结算历史决策
- **WHEN** 发起对 300750 的新一次深度分析
- **THEN** 分析师与 Trader 的上下文中包含该 3 条记忆条目（含来源 ID 与日期）
- **AND** Langfuse trace 含用途标签 `decision_memory`

### Requirement: 语义记忆人工确认

系统 SHALL 按月从已结算决策中提炼候选经验条款；候选条款 SHALL 经人工确认后方可生效注入风控辩论层；未确认条款 SHALL NOT 出现在任何 prompt 中。

#### Scenario: 未确认条款不生效

- **GIVEN** 月批产出候选条款 5 条，其中 2 条被人工确认
- **WHEN** 下一次风控辩论执行
- **THEN** prompt 中仅含已确认的 2 条
- **AND** 全部 LLM 调用路径中检索不到未确认的 3 条文本
