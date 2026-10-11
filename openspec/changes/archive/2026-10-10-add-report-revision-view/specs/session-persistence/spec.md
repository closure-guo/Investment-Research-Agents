# Delta for session-persistence

## ADDED Requirements

### Requirement: 终稿决策持久化与同标的回溯查询

会话完成落库（`update_session_report`）时 SHALL 一并持久化 `final_trade_decision`（终稿决策 JSON，至少含 action/confidence/entry_price/stop_loss/target_price/trigger_high/trigger_low 在场字段）；终稿不存在（管线阻断/未审批）时该字段落 NULL，MUST NOT 以 trader 层 plan 冒充终稿。

系统 SHALL 提供按 `stock_code` 回溯读取「上一份已完成报告」的能力：给定 stock_code 与当前会话标识，返回最近一条 `status='completed'` 且非当前会话的行（按 `created_at` 降序取一），携带增量摘要渲染所需的结构化字段（`final_trade_decision`、`chart_data` 中的关键指标、`created_at`）。无符合条件的历史行时返回 None。查询 MUST NOT 返回 running/failed 状态的会话。

#### Scenario: 终稿决策随报告落库

- **WHEN** 一条会话管线完成并调用 `update_session_report`
- **THEN** 会话行 SHALL 持久化 `final_trade_decision` JSON（含在场价位与触发位字段）
- **WHEN** 管线被门禁阻断、无终稿决策
- **THEN** 该字段落 NULL，落库不报错

#### Scenario: 回溯取最近完成报告

- **GIVEN** 同一 `stock_code` 存在三条历史会话：09-28 completed、09-30 completed、10-08 running
- **WHEN** 为 10-08 新完成的会话查询上一份报告
- **THEN** 返回 09-30 行（最近一条 completed），MUST NOT 返回 running 行，MUST NOT 返回当前会话自身

#### Scenario: 无历史报告返回 None

- **WHEN** 某 `stock_code` 无任何其他 completed 会话
- **THEN** 回溯查询返回 None，调用方按「首份报告」处理，不报错
