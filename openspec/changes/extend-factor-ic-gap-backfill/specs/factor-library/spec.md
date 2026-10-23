# Delta for factor-library

## ADDED Requirements

### Requirement: IC 采集记录与缺口回填

衰减监控日批 SHALL 为每个 active 因子记录「面板窗口内、`as_of` 及以前」的**全部**可算 RankIC 交易日中尚缺的日期（缺口回填）；`record_ic` 幂等 upsert，已存在日期 SHALL NOT 重复重写；运行摘要 SHALL 含 `days_recorded`（本次写入的 IC 行数），`recorded`（有可算 IC 的因子数）语义不变。超出监控面板窗口（`DECAY_WINDOW+30` 交易日）的更早缺口 MUST NOT 虚构回补（诚实留洞）。无任何可算日的因子照旧计入 `skipped`。

#### Scenario: 例行日恰好新增一条

- **GIVEN** active 因子 F 昨日及以前的 IC 均已记录，今日面板出现新的可算 IC 日
- **WHEN** 衰减监控日批执行
- **THEN** 该日被记录，`days_recorded` 含这一条，且不重写任何已存在日期

#### Scenario: 多日中断后缺口一次补齐

- **GIVEN** active 因子 F 的 IC 序列缺失面板窗口内的中间 20 个可算日（监控曾连缺多个交易日）
- **WHEN** 衰减监控恢复后的首次运行执行
- **THEN** 20 个缺口日全部补写（`days_recorded=20`），序列在面板窗口内无洞
- **AND** 同一运行重跑 `days_recorded=0`（已存在日期不重写，幂等）

#### Scenario: 无可算日照旧跳过

- **GIVEN** active 因子 F 在 `as_of` 前无可算 IC 日（窗口不足/全 NaN）
- **WHEN** 衰减监控日批执行
- **THEN** F 计入 `skipped`，不写任何 IC 行
