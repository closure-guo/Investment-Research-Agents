# causal-ablation delta: add-screening-evaluation

## ADDED Requirements

### Requirement: 选股消融三变体

消融框架 SHALL 支持「因子打分选股 / 随机选股（固定种子）/ 等权指数基线」三变体对照，共享同一时段与持有窗口（horizon 对齐），以配对 bootstrap（B=10000）报告增量；CI 含 0 时 SHALL 仅写「无显著差异」。

#### Scenario: 随机变体可复现

- **GIVEN** 随机选股变体固定种子 42
- **WHEN** 同一时段重复执行两次消融
- **THEN** 两次随机选股的标的序列完全一致

### Requirement: L3 粗筛增量变体

消融框架 SHALL 支持「±L3 LLM 粗筛」变体：比较粗筛前候选池与粗筛后精选池的前向收益；仅当 drop 组后续 20 日收益显著低于 keep 组时，报告方可陈述粗筛具有排雷价值。

#### Scenario: 粗筛价值判定口径

- **GIVEN** 某评估窗口内 keep 组 20 日均值收益 +2.1%，drop 组 -3.4%，配对 CI 不含 0
- **WHEN** 生成消融报告
- **THEN** 报告陈述 L3 粗筛在该窗口具有排雷价值，并披露窗口范围与有效 n
