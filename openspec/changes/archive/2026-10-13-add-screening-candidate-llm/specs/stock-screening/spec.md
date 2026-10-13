# stock-screening delta: add-screening-candidate-llm

## ADDED Requirements

### Requirement: L3 LLM 排雷粗筛

系统 SHALL 对 L2 Top-N 候选逐只执行 LLM 粗筛（quick profile），输入含综合分、因子贡献摘要与最近事件；输出 SHALL 满足合同：`verdict ∈ {keep, drop}` 且 `reason ≤ 50 字`；单只调用失败 SHALL 按 keep 降级处理并记 trace warning，不阻断本批其他标的。

#### Scenario: 事件雷剔除

- **GIVEN** 候选 X 综合分排名第 5，但最近事件含「收到证监会立案告知书」
- **WHEN** 执行 L3 粗筛
- **THEN** X 的 verdict 为 drop，reason 提及立案
- **AND** 该 keep/drop 决策落库

#### Scenario: 单只失败降级

- **GIVEN** 一批 50 只候选，其中 1 只的 LLM 调用抛出 typed error
- **WHEN** 批次执行
- **THEN** 其余 49 只正常产出 verdict；失败标的按 keep 处理且 trace 含 warning

### Requirement: 在榜稳定性门槛

标的 SHALL 连续 3 个交易日（可配置）处于 L2 Top-N 内方可进入精选池；中间断档 SHALL 重新计数。

#### Scenario: 断档重新计数

- **GIVEN** 标的 Y 在第 1、2 日在榜，第 3 日跌出 Top-N，第 4、5 日重新在榜
- **WHEN** 第 5 日评估精选池资格
- **THEN** Y 的连续在榜计数为 2，不进入精选池
