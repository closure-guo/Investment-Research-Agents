# Delta for session-persistence

## ADDED Requirements

### Requirement: Database Availability Probe In Health Check

The health endpoint SHALL distinguish service liveness from database availability so that a single
unreadable database does not mark the whole container unhealthy without an actionable signal.

#### Scenario: Service and databases are both available

- GIVEN the backend process is running and hot databases are readable
- WHEN `GET /api/health` is called
- THEN the response status is 200
- AND the payload reports `status=ok` and a `databases` object in which every probed database is `ok`

#### Scenario: Service is alive but a database is unavailable

- GIVEN the backend process is running
- AND at least one hot database cannot be opened
- WHEN `GET /api/health` is called
- THEN the response status is 503
- AND the payload reports the failing database name and a short error class
- AND the process is not terminated or restarted by the probe

#### Scenario: Probe is bounded

- GIVEN a database open attempt blocks on I/O
- WHEN the health probe runs
- THEN the probe returns within its configured timeout
- AND it reports the database as `unavailable` rather than hanging the request
