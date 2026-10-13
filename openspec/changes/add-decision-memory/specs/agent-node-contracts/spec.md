# agent-node-contracts delta: add-decision-memory

## ADDED Requirements

### Requirement: 分析师与 Trader 上下文输入契约

分析师与 Trader 节点的上下文契约 SHALL 扩展可选「记忆块」（情景记忆条目列表 + 已确认语义条款）；记忆块缺省时节点行为与现状一致（向后兼容）。

#### Scenario: 无记忆时行为不变

- **GIVEN** 一只无任何历史决策的新标的
- **WHEN** 发起深度分析
- **THEN** 各节点上下文不含记忆块，产出与扩展前同结构
