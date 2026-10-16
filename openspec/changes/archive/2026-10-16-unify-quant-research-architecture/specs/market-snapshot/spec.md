# Delta: market-snapshot（unify-quant-research-architecture）

## ADDED Requirements

### Requirement: 历史回填补齐成交额

历史回填补取数（`fetch_symbol_history_raw_qfq` 与回填脚本）SHALL 输出并落库成交额（`amount`）列，单位口径与日批一致（元）；数据源未提供 amount 的票 SHALL 按取数失败处理（计入失败留痕），MUST NOT 以 NULL 直通落行——amount 类因子（如 `ts_mean(amount,20)`）在回填历史上的可计算性由此保证。

#### Scenario: 回填行含成交额

- **GIVEN** 新浪历史日线返回含 amount 列的双口径数据
- **WHEN** 回填执行
- **THEN** `market_snapshot` 回填行 `amount` 非空且单位归一
- **AND** `ts_mean(amount, 20)` 类因子在回填区间上可计算

#### Scenario: 源缺成交额按失败处理

- **GIVEN** 某票历史数据源未返回 amount 列
- **WHEN** 回填执行
- **THEN** 该票计入 `symbols_failed` 留痕，不落 amount 为 NULL 的半口径行
