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

## ADDED Requirements

### Requirement: 连接重试预算与终态落库缓冲

`session_store` 连接重试 SHALL 采用预算制：瞬态错误下持续指数退避重试直至
累计等待达到预算（默认 120s，环境变量 `SESSION_DB_CONNECT_RETRY_BUDGET_S`
可覆盖，覆盖值 MUST 收敛到 [30, 300] 区间），取代固定 6 次/≈3.1s。
事件批量投递失败 SHALL 分型处置：`TimeoutError` 保持既有「跳过本批不崩管线」
语义（#490），其他异常 SHALL 把整批回填 pending 队首后继续推进（不终止管线、
不丢序）。终态落库重试耗尽时 SHALL 在 SQLite 旁路写 `terminal_pending`
文件标记（含 session_id/事件载荷/时间戳），供修复工具补落终态。

#### Scenario: 连接重试按预算而非固定次数

- **GIVEN** SQLite 连接持续抛瞬态错误（unable to open / locked / disk I/O）
- **WHEN** `_get_db` 被调用（预算 60s）
- **THEN** 重试跨越旧 6 次/3.1s 窗口持续进行，直到累计 sleep ≥ 预算才抛出
- **AND** 单次退避 sleep 封顶 2s（预算不被单次超长 sleep 挤占）

#### Scenario: flush 非 Timeout 异常回填不崩管线

- **GIVEN** `_flush_pending` 投递批次时 `publish_many` 抛出非 `TimeoutError` 异常
- **WHEN** 异常传播到 `_flush_pending`
- **THEN** 该批按原序回填 pending 队首，管线继续推进（无事件丢失、无乱序）
- **AND** `TimeoutError` 仍跳过本批（既有 #490 语义不变）

#### Scenario: 终态落库重试耗尽写旁路标记

- **GIVEN** 终态事件（done/interrupted/error）落库 5×1s 重试全部耗尽
- **WHEN** `publish_terminal` 释放 CAS 并 raise 前
- **THEN** 向 `data/terminal_pending/<session_id>.json` 写入标记（best-effort，
  标记本身写失败只记日志不改变 raise 行为）
- **AND** 修复脚本 `scripts/repair_terminal_pending.py` 逐个读取标记、
  经正常落库路径补写终态，成功即删除标记文件
