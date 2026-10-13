# evaluation delta: add-screening-evaluation

## ADDED Requirements

### Requirement: 候选池前向命中率

系统 SHALL 对每日候选池自动跟踪入选后 5/10/20 交易日收益，并给出入选组与落选对照组的分层统计；收益计算 SHALL 使用与决策回放相同的时点截断纪律与撮合口径（matching-engine）。

#### Scenario: 前向跟踪数据产出

- **GIVEN** 候选池已持续落库 30 个交易日
- **WHEN** 生成前向跟踪报告
- **THEN** 报告含 Top-N 入选组与落选组在 5/10/20 日窗口的收益均值、胜率与配对置信区间
- **AND** 报告披露有效 n

### Requirement: 因子来源分层考核

前向统计 SHALL 按因子来源（builtin / LLM 挖掘）分层：分别报告两组因子各自驱动打分时的候选池前向表现；该对照 SHALL 作为因子挖掘管线的考核指标登记。

#### Scenario: 分层报告

- **GIVEN** 因子库同时含 active 的 builtin 与 LLM 因子
- **WHEN** 生成前向跟踪报告
- **THEN** 报告含「仅 builtin 打分」与「仅 LLM 因子打分」两个分层的前向收益对照
