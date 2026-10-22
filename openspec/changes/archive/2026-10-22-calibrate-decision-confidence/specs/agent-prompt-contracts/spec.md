# Delta for agent-prompt-contracts

## ADDED Requirements

### Requirement: 决策层 confidence 分档判据

决策层三个节点（trader / risk_judge / fund_manager）的 prompt SHALL 给出可执行的 confidence 分档判据与三档示例（高/中/低），MUST NOT 只给「≥0.7 高、0.4–0.7 中、<0.4 低」的区间描述而示例值全部落在中段。

#### Scenario: 高置信必须有证据组合

- **GIVEN** 决策证据包含多源一致的数字锚点、且风险辩论中的主要矛盾已处理
- **WHEN** 节点输出 confidence
- **THEN** confidence SHALL ≥ 0.70
- **AND** reasoning SHALL 引用支撑该强度的具体证据（锚点或证据条数）

#### Scenario: 低置信必须有明确触发

- **GIVEN** 关键数据缺失、或风险辩论存在未决矛盾
- **WHEN** 节点输出 confidence
- **THEN** confidence SHALL < 0.40，并在 reasoning 说明缺失/矛盾项

#### Scenario: 校准观测进实验收口

- **GIVEN** 一次 hosted 全量实验完成
- **WHEN** 收口报告生成
- **THEN** SHALL 登记 confidence 分布（按 action 分桶的直方图与 ≥0.70 / <0.40 尾部占比）
- **AND** 该观测 MUST NOT 以新增 Langfuse Score 的形式进入 judge 口径
