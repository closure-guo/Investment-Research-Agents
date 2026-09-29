# Delta for decision-outcome

## ADDED Requirements

### Requirement: 参考价采信交叉校验

落库参考价 `entry_price` 取实时 quote 价时，SHALL 与最近 K 线收盘价交叉校验：两者偏离超过阈值（配置项 `TRACK_ENTRY_PRICE_MAX_DEVIATION`，默认 0.30，覆盖主板 10%/创业板科创板 20%/北交所 30% 涨跌幅限制）时 SHALL 拒绝采信 quote 价，降级采用最近 K 线收盘价作为参考价，并记 WARN（含 quote 价、K 线收盘价与偏离度）。K 线不可得、无法交叉校验时 SHALL 保留 quote 价并记 WARN。校验失败不阻断落库（旁路语义与现行一致）。

#### Scenario: quote 偏离过大降级 K 线收盘

- **GIVEN** 某次分析 quote 返回价格 1800，而最近 K 线收盘为 1330（偏离 35%）
- **WHEN** 观点落库
- **THEN** `entry_price` SHALL 取 1330（K 线收盘）
- **AND** SHALL 记 WARN 日志说明采信降级原因

#### Scenario: quote 正常采信

- **GIVEN** 某次分析 quote 返回价格 100.5，最近 K 线收盘 100.0（偏离 0.5%）
- **WHEN** 观点落库
- **THEN** `entry_price` SHALL 取 100.5（现行 quote 优先语义保留）

#### Scenario: K 线不可得保留 quote

- **GIVEN** quote 可得但 K 线缺失（无法交叉校验）
- **WHEN** 观点落库
- **THEN** `entry_price` SHALL 取 quote 价并记 WARN（维持现行兜底链）
