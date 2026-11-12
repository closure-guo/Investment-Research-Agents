# Delta for stock-screening

## ADDED Requirements

### Requirement: 池分析日批前置顺延护栏

自动池分析日批（主档与小票档）SHALL 在提交批次前检查其前置选股日批（主档
`screening_daily`、小票档 `microcap_screening_daily`）当日 `job_runs` 最新行：状态为
`running` 时 SHALL 按间隔顺延重查（默认 300 秒；主档默认至多 3 轮、小票档默认至多 8 轮，
可由 `AUTO_POOL_ANALYSIS_DEFER_INTERVAL_SECONDS` / `AUTO_POOL_ANALYSIS_DEFER_MAX_ROUNDS` /
`AUTO_POOL_MICROCAP_DEFER_MAX_ROUNDS` 覆盖；`AUTO_POOL_ANALYSIS_DEFER_ENABLED=0` 一键关闭）；
等待耗尽前置仍在跑 SHALL 照常提交（既有 stale 降级路径不变），前置已终态或当日无运行行
SHALL 立即提交（MUST NOT 为未在运行的缺失前置空等，深夜补跑零延迟）。顺延轮数与等待秒数
SHALL 计入运行摘要 `defer` 字段（`outcome` / `rounds` / `waited_seconds`）。

#### Scenario: 前置仍在跑时顺延并吃到 fresh 批

- **GIVEN** 16:22 触发时 `screening_daily` 当日 `job_runs` 最新行 `status=running`（选股
  15:40 起跑未收尾）
- **WHEN** 主档自动池分析触发
- **THEN** 按间隔顺延重查，前置收尾（`status=ok`）后立即提交
- **AND** 摘要 `defer.outcome=fresh_ready`，批次用上当日 fresh（`stale_batch` 为空）

#### Scenario: 等待耗尽仍降级 stale

- **GIVEN** 前置 job 当日 `running` 超过全部轮次（如小票档 8×300s 后
  `microcap_screening_daily` 仍未收尾）
- **WHEN** 自动池分析触发
- **THEN** 照常提交批次，摘要 `defer.outcome=gave_up_still_running` 并披露等待秒数
- **AND** 既有 stale 降级路径行为不变（`stale_batch` 披露照旧）

#### Scenario: 前置未在跑不空等

- **GIVEN** 前置 job 当日无运行行（宿主宕机错窗）或当日行已终态（`ok` / `failed`）
- **WHEN** 自动池分析触发（含深夜手动补跑场景）
- **THEN** 立即提交，等待秒数为 0
- **AND** 摘要 `defer.outcome` 为 `fresh_ready`（前置已 ok）或 `not_running`（无行）

#### Scenario: 护栏可关闭

- **GIVEN** `AUTO_POOL_ANALYSIS_DEFER_ENABLED=0`
- **WHEN** 自动池分析触发且前置仍在跑
- **THEN** 行为与护栏落地前一致：立即提交，摘要不含 `defer` 等待记录（`outcome=disabled`）
