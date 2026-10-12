# llm-policy-router delta: add-factor-proposal-agent

## ADDED Requirements

### Requirement: 因子提案用途注册

PolicyRouter SHALL 注册新用途 `factor_proposal`，能力要求为 `json_output`；fallback 链与 typed error 切换行为复用现有机智，且该用途的快速/深度两档分别解析到 quick 与 deep profile。

#### Scenario: 能力不满足时禁用

- **GIVEN** 当前激活 profile 的能力探测结果为 `json_output=False`
- **WHEN** 因子提案节点请求 LLM 调用
- **THEN** 按 fallback 链切换至满足能力的 profile；链耗尽时抛出 typed error，不发起裸调用
