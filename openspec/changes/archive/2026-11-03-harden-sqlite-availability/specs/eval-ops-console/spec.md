# Delta for eval-ops-console

## ADDED Requirements

### Requirement: Page-Level SQLite Integrity Check

The `integrity_check` job SHALL verify SQLite page integrity of hot databases in addition to the existing
prediction snapshot hash check.

#### Scenario: All hot databases are healthy

- GIVEN every hot database is readable
- WHEN the `integrity_check` job runs
- THEN the job summary includes a per-database `integrity` entry with value `ok`
- AND the job status is `ok`

#### Scenario: A database reports corruption

- GIVEN at least one hot database returns anything other than `ok` from `PRAGMA integrity_check`
- WHEN the `integrity_check` job runs
- THEN the job status is `failed`
- AND the summary names the failing database and records a truncated `integrity_check` detail
- AND the existing snapshot hash mismatch audit behavior is unchanged
