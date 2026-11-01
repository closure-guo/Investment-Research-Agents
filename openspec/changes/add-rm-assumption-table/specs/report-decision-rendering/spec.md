# Delta for report-decision-rendering

## ADDED Requirements

### Requirement: 多空假设表渲染

「多空辩论结论」节 SHALL 在分歧卡与结论文本之后渲染 `research_manager_assumptions` 假设表：每行呈现维度（含 H1-H4 编码）、可核验命题、多空证据、拐点信号与状态判定，状态判定（成立/注意/不成立）SHALL 有视觉区分。结构化字段缺失或为 None（历史会话、解析降级、表被丢弃）时，该节退化为现状渲染（分歧卡 + 结论文本），MUST NOT 渲染空表或编造行。

#### Scenario: 假设表渲染

- **GIVEN** state 含 4 行合法 `research_manager_assumptions`
- **WHEN** 报告生成「多空辩论结论」节
- **THEN** 分歧卡与结论文本之后渲染假设表，每行含维度、命题、多空证据、拐点信号与状态判定
- **AND** 状态判定（成立/注意/不成立）有视觉区分

#### Scenario: 结构化字段缺失降级

- **GIVEN** 历史会话的 state 无 `research_manager_assumptions`（或为 None）
- **WHEN** 报告生成「多空辩论结论」节
- **THEN** 该节按现状渲染（分歧卡 + 结论文本），MUST NOT 渲染空表或编造行
