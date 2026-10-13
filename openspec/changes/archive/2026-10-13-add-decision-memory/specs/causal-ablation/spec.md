# causal-ablation delta: add-decision-memory

## ADDED Requirements

### Requirement: 记忆消融变体

消融框架 SHALL 支持「+记忆注入 / 无记忆」配对变体（同 state 快照、配对 bootstrap）；judge 维度按适用性过滤与「CI 含 0 只写无显著差异」的既有纪律 SHALL 适用。

#### Scenario: 记忆消融报告

- **GIVEN** n=10 消融批次完成
- **WHEN** 生成报告
- **THEN** 报告含两变体各 judge 维度增量及 95% CI，并披露有效 n 与适用口径
