# Delta for session-persistence

## ADDED Requirements

### Requirement: SQLite 连接纪律

SQLite store 家族 SHALL 收敛连接层开销：journal 模式 MUST 只在模块初始化（DDL 前）设置一次，
每连接 helper MUST NOT 执行 `PRAGMA journal_mode`（只设 `busy_timeout`）；只读路径 MUST NOT
执行 DDL（缺表时 SHALL 按该读函数既有的空语义返回，MUST NOT 静默建表）。

#### Scenario: 每连接不再设置 WAL

- **GIVEN** 任一 store 的 `_connect` 家族 helper 被调用
- **WHEN** 连接建立
- **THEN** 只执行 `PRAGMA busy_timeout`，不执行 `PRAGMA journal_mode=WAL`
- **AND** 库文件 journal 模式由 `init_*` 首次调用时设置并持久化

#### Scenario: 读路径缺表不建表

- **GIVEN** 快照库不存在（或 `market_snapshot` 表未建）
- **WHEN** `latest_snapshot_date` 被调用
- **THEN** 返回 None（不执行 DDL、不创建库/表）
- **AND** `read_universe` 同场景抛 `SnapshotEmptyError`（既有空语义），不建表

#### Scenario: 写路径首次建库仍设 WAL

- **GIVEN** 全新库文件
- **WHEN** 模块 `init_*`（或首个写入路径经 init）执行
- **THEN** DDL 前设置 `journal_mode=WAL` 且持久化到库头
