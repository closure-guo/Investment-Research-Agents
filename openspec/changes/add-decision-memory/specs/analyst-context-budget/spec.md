# analyst-context-budget delta: add-decision-memory

## MODIFIED Requirements

### Requirement: 记忆块预算核算

记忆块（情景记忆 Top-5 + 语义条款）SHALL 计入分析师上下文预算；预算不足时 SHALL 按「语义条款优先于情景记忆、情景记忆按结算结果相关性降序截断」的顺序裁剪，并记录裁剪事件。

#### Scenario: 预算不足裁剪

- **GIVEN** 上下文预算剩余不足容纳全部 5 条情景记忆
- **WHEN** 上下文组装执行
- **THEN** 语义条款完整保留，情景记忆按相关性降序截断至预算内
- **AND** 裁剪事件记入 trace
