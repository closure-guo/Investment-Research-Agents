# Delta for deploy

## ADDED Requirements

### Requirement: Hot SQLite Databases Run On Named Volumes

The backend SHALL serve hot SQLite databases from a Docker named volume rather than the Windows bind mount,
while cold artifacts (reports, charts, factor research files, research report inbox) MAY remain on the
bind mount.

#### Scenario: Backend starts with hot DBs on the named volume

- GIVEN the compose stack defines a named volume mounted at the hot-DB directory
- WHEN the backend container starts
- THEN `SESSIONS_DB_PATH`, `SCREENING_DB_PATH`, `EXECUTION_DB_PATH`, `EVENTS_DB_PATH`, `REGIME_DB_PATH`,
  `RESEARCH_REPORTS_DB_PATH` and `FACTOR_LIBRARY_DB_PATH` point into that volume
- AND every hot DB passes `PRAGMA integrity_check`

#### Scenario: Host-side artifact paths remain available

- GIVEN the migration has completed
- WHEN an operator reads `data/reports`, `data/charts`, `data/factor_research` or `data/research_report_inbox`
  from the host
- THEN those paths still exist on the bind mount
- AND hot `.db` files are absent from the bind mount to prevent split-brain writers

#### Scenario: Migration is reversible

- GIVEN the migration has been applied
- WHEN the documented rollback is executed
- THEN the named-volume databases are copied back to `data/` before the compose configuration is reverted
- AND the backend starts with the previous bind-mount configuration and passes the health probe
