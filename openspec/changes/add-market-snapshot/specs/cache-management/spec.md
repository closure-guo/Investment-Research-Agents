# cache-management delta: add-market-snapshot

## ADDED Requirements

### Requirement: 快照表 TTL 豁免

行情快照表与历史成分表 SHALL 视为不可变历史数据，不适用现有行情缓存的 TTL 过期策略（TTL 至当日收盘）；两表数据一经写入 SHALL NOT 被缓存清理逻辑删除或更新。

#### Scenario: 缓存清理不影响快照

- **GIVEN** 快照表含 2025 年全年记录，行情缓存 TTL 到期触发清理
- **WHEN** 缓存清理任务执行
- **THEN** 快照表与成分表行数不变
