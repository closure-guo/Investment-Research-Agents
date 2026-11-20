# Delta for market-snapshot

## ADDED Requirements

### Requirement: 面板区间读取与进程内缓存

快照读路径 SHALL 提供区间读取原语 `read_universe_range(start, end)`（单次
`WHERE trade_date BETWEEN` 查询），因子面板加载（`_load_panel`）SHOULD 经由该
原语一次取齐整个回看窗口的行，取代逐日连接；并 SHALL 按
`(请求日期序列, 快照库 mtime_ns)` 键控做有界进程内缓存（LRU ≤4）。

#### Scenario: 区间读取一次取齐

- **GIVEN** 快照库中存在 `[start, end]` 内若干交易日的行
- **WHEN** `read_universe_range(start, end)` 被调用
- **THEN** 单次 SQL（`WHERE trade_date BETWEEN ? AND ?`）返回全部行，按
  `trade_date, code` 升序，行含 `trade_date` 键
- **AND** 全程只建立一条连接（不逐日重连）

#### Scenario: 面板加载语义与逐日读取逐值等价

- **GIVEN** 请求日期序列 `dates`（升序交易日）
- **WHEN** `_load_panel` 经区间读取构建
- **THEN** 每个请求日期的行集与逐日 `read_universe(date)` 逐值一致
  （`rows_present` 按「当日有行」逐日构帧，跨日列并集不引入 NaN→True）
- **AND** 任一请求日期无行 → 抛 `SnapshotEmptyError`（保留逐日 fail-loud 语义，
  不静默跳过该日）

#### Scenario: 区间读取空语义与防前视

- **GIVEN** 库/表缺失
- **WHEN** `read_universe_range` 被调用
- **THEN** 抛 `SnapshotEmptyError`（同 `read_universe` 空语义，不建库建表）
- **AND** `end` 晚于库内最新快照日期 → 抛 `SnapshotNotFreshError`；
  `end < start` → 抛 `SnapshotError`

#### Scenario: 缓存命中与失效

- **GIVEN** 同一进程内以相同日期序列重复加载面板
- **WHEN** 快照库文件自上次加载后未发生写入（`mtime_ns`/`size` 未变）
- **THEN** 第二次起命中进程内缓存，返回同一 `MarketPanel` 实例（零 DB 连接）
- **AND** 快照库发生写入（mtime_ns 变化）→ 缓存失效，下次加载重读
- **AND** 缓存条目数超过 4 → 淘汰最久未用键（LRU）
