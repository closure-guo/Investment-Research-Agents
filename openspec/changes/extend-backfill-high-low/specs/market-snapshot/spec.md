# Delta: market-snapshot（extend-backfill-high-low）

## MODIFIED Requirements

### Requirement: 历史回填补齐成交额

历史回填补取数（`fetch_symbol_history_raw_qfq` 与回填脚本）SHALL 输出并落库成交额（`amount`）与最高/最低价（`high`/`low`，raw 口径）列，口径与日批一致（成交额单位元、最高/最低为未复权原值，面板按复权因子派生 qfq 口径）；数据源未提供 amount 的票 SHALL 按取数失败处理（计入失败留痕），MUST NOT 以 NULL 直通落行——amount 类因子（如 `ts_mean(amount,20)`）在回填历史上的可计算性由此保证；最高/最低价同为必需列（源缺列按取数失败处理，同留痕），日内结构类因子（如 `(close-low)/(high-low)`）在回填历史上的可计算性由此保证。断点续跑齐备判据 SHALL 覆盖 amount 与 high/low（任一缺失的既有行 SHALL 重取 upsert 补齐）。

#### Scenario: 回填行含成交额

- **GIVEN** 新浪历史日线返回含 amount 列的双口径数据
- **WHEN** 回填执行
- **THEN** `market_snapshot` 回填行 `amount` 非空且单位归一
- **AND** `ts_mean(amount, 20)` 类因子在回填区间上可计算

#### Scenario: 源缺成交额按失败处理

- **GIVEN** 某票历史数据源未返回 amount 列
- **WHEN** 回填执行
- **THEN** 该票计入 `symbols_failed` 留痕，不落 amount 为 NULL 的半口径行

#### Scenario: 回填行含最高最低价

- **GIVEN** 历史数据源返回含最高/最低价的双口径数据
- **WHEN** 回填执行
- **THEN** `market_snapshot` 回填行 `high`/`low` 非空（raw 口径）
- **AND** `(close-low)/(high-low)` 类日内结构因子在回填区间上可计算

#### Scenario: 源缺最高最低价按失败处理

- **GIVEN** 某票历史数据源未返回最高/最低价列
- **WHEN** 回填执行
- **THEN** 该票计入 `symbols_failed` 留痕，不落 high/low 为 NULL 的半口径行

#### Scenario: 缺 high/low 的既有行重取补齐

- **GIVEN** 区间内既有行 amount 非空但 high/low 为空（旧六列契约回填产物）
- **WHEN** 回填执行
- **THEN** 该票不构成断点续跑覆盖、重取 upsert 补齐 high/low
