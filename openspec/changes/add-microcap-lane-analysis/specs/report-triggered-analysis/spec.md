## MODIFIED Requirements

### Requirement: 触发上限与预算

系统 SHALL 对触发批次施加单篇与全局上限。

- 单篇 SHALL 至多触发 `REPORT_TRIGGER_MAX_SYMBOLS`（默认 3，正整数）只标的，按 `stocks[]` 顺序取前 N，其余 skip `per_report_cap`。
- 全局每日触发 SHALL 受 `REPORT_TRIGGER_DAILY_MAX` 约束；当日已用预算 SHALL 计入 `pool_analysis_runs`（仅 `pool='main'` 运行行）与 `report_analysis_runs` 两个来源的当日行数；小票通道（`pool='microcap'`）运行行 MUST NOT 计入主档触发预算。
- 达到上限时 SHALL skip `daily_cap` 并在摘要中显式计数，MUST NOT 静默丢弃。

#### Scenario: 单篇截断

- **GIVEN** 一篇研报含 8 只范围内标的，`REPORT_TRIGGER_MAX_SYMBOLS=3`
- **WHEN** 触发编排运行
- **THEN** 只取前 3 只建会话
- **AND** 其余 5 只 skip `per_report_cap`

#### Scenario: 日预算共享

- **GIVEN** 当日自动池分析已产生 8 个会话，`REPORT_TRIGGER_DAILY_MAX=10`
- **WHEN** 研报触发了 2 只后再次触发
- **THEN** 后续标的 SHALL skip `daily_cap`
- **AND** 摘要 SHALL 显示当日已用与上限

#### Scenario: 小票行不计入主档预算

- **GIVEN** 当日 pool_analysis_runs 含 main 行 8 条与 microcap 行 10 条，`REPORT_TRIGGER_DAILY_MAX=10`
- **WHEN** 研报触发预算计数执行
- **THEN** 当日已用 = 8（仅 main 行），可再触发 2 只
- **AND** microcap 行不计入、不挤占主档触发预算
