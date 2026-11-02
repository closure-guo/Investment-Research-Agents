# Delta for stock-screening

## ADDED Requirements

### Requirement: Pool Analysis Dispatch Terminal Status Writeback

The auto pool analysis dispatch record in `pool_analysis_runs` SHALL be updated to the terminal session
status when the associated analysis finishes, instead of remaining `queued` indefinitely.

#### Scenario: Analysis session completes

- GIVEN a `pool_analysis_runs` row exists for `(trade_date, symbol)` with a `session_id`
- WHEN that session transitions to `completed`
- THEN the row status becomes `completed`
- AND `updated_at` is refreshed

#### Scenario: Analysis session fails or is interrupted

- GIVEN a `pool_analysis_runs` row exists for `(trade_date, symbol)` with a `session_id`
- WHEN that session transitions to `failed` or `interrupted`
- THEN the row status becomes the matching terminal status
- AND the row `error` carries the session failure reason prefix (truncated)

#### Scenario: Manual re-run satisfies the same row

- GIVEN the dispatch row still points at a failed session
- AND a later manual pool analysis succeeds for the same symbol and trade date
- WHEN the terminal writeback runs
- THEN the row may point at the newer successful session
- AND the daily brief reads the row's dispatch status and session status separately
