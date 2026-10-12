# decision-backtest delta: add-matching-engine

## MODIFIED Requirements

### Requirement: 回放成交计算来源

决策回放的成交判定 SHALL 由 `matching-engine` 的撮合函数计算，替换原有的"按收盘价成交"简化假设；时点截断（财报按披露截止日）与空 K 线显式报错的既有纪律 SHALL 保持不变。

#### Scenario: 回放经过涨跌停约束

- **GIVEN** 一条历史买入决策，其次日为一字涨停
- **WHEN** 执行决策回放
- **THEN** 该笔记为未成交，原因码 `LIMIT_UP_UNFILLABLE`
- **AND** 该笔不计入收益统计（而非按涨停价记成交）
