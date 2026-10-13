# causal-ablation delta: add-analyst-factor-tools

## ADDED Requirements

### Requirement: 因子工具消融变体

消融框架 SHALL 支持「技术分析师+因子工具」与「裸技术分析师」配对变体，共享同一 state 快照并按配对 bootstrap 报告增量；judge 维度按变体适用性过滤的既有纪律 SHALL 适用；CI 含 0 时报告 SHALL 仅写「无显著差异」，禁止引申为「无价值」。

#### Scenario: 消融报告口径

- **GIVEN** 消融批次 judge 某维度增量 95% CI 为 [-0.1, 0.3]
- **WHEN** 生成消融报告
- **THEN** 报告该维度结论为「无显著差异」，且不出现「因子工具无效」类表述
