# market-snapshot Specification

## Purpose
TBD - created by archiving change add-market-snapshot. Update Purpose after archive.
## Requirements
### Requirement: 全市场日频快照落库

系统 SHALL 在每个 A 股交易日收盘后，将全市场标的的日线行情（开/高/低/收/成交量/成交额）以不可变记录写入 SQLite 快照表；每条记录 SHALL 同时包含前复权收盘价与不复权收盘价两个口径，且字段命名显式区分（`close_qfq` / `close_raw`）。

#### Scenario: 收盘后日批写入

- **GIVEN** 一个 A 股交易日且当前时间晚于 15:30
- **WHEN** 日批任务执行
- **THEN** 快照表中该交易日的记录数等于当日全市场标的数（含停牌标的，停牌标的成交量为 0 仍须有记录）
- **AND** 对同一交易日重复执行任务，记录数不变（幂等）

### Requirement: 历史成分与状态快照

系统 SHALL 按交易日记录当日可交易标的全集及其当日状态（ST 标记 / 停牌 / 一字涨停 / 一字跌停 / 上市天数）；已退市标的在其存续期间的历史记录 SHALL NOT 被删除或回填修改。

#### Scenario: 退市标的历史可查

- **GIVEN** 标的 X 于 2025-06-30 退市
- **WHEN** 查询 as_of=2025-03-01 的当日成分
- **THEN** 结果包含标的 X 及其当日状态字段
- **AND** 查询 as_of=2025-09-01 的结果不包含标的 X

### Requirement: 时点截断防前视

快照读取接口 SHALL 要求调用方传入 `as_of` 日期，且仅返回 `date <= as_of` 的记录；当 `as_of` 晚于快照表最新数据日期时，接口 SHALL 返回显式错误而非静默返回现有最新数据。

#### Scenario: as_of 超出数据范围

- **GIVEN** 快照表最新数据日期为 2026-10-10
- **WHEN** 以 as_of=2026-10-20 调用读取接口
- **THEN** 接口抛出 `SnapshotNotFreshError`，消息含最新数据日期 2026-10-10
- **AND** 不返回任何记录

### Requirement: 日批失败告警

当日批任务失败或未在当日 18:00 前完成时，系统 SHALL 写 ERROR 级日志，并在前端所有页面顶部展示横幅「行情快照未更新（数据日期: YYYY-MM-DD）」，直至下一成功日批。

#### Scenario: 日批失败后的前端表现

- **GIVEN** 最近一次成功日批的数据日期为 2026-10-09，且当日任务失败
- **WHEN** 用户打开任意前端页面
- **THEN** 页面顶部展示横幅，文案为「行情快照未更新（数据日期: 2026-10-09）」
- **AND** 深度分析与选股相关入口正常可用（告警不阻断功能，仅告知）

### Requirement: 全市场 spot 数据源回退

系统在拉取全市场 spot 表（`fetch_market_spot`，日批唯一取数入口）时 SHALL 以东财 `stock_zh_a_spot_em` 为主源；当主源拉取失败（重试耗尽/空表/缺「代码」列）时 SHALL 回退新浪 `stock_zh_a_spot`。备用源产出 SHALL 经列映射归一到日批期望的东财列形（列名逐列对应；新浪缺失的列 SHALL 补 NaN 保持列存在，MUST NOT 改变列集合）。主源与备用源均不可用时 SHALL 返回 `None`，由调用方走既有失败语义（抛错 + 失败标记告警），MUST NOT 静默返回空表或残缺表（不写空快照）。实际生效源 SHALL 记入客户端 `sources_seen["market_spot"]` 与日批成功摘要 `spot_source`。

- 主源成功时 SHALL NOT 调用备用源。
- 备用源成功时输出列集合 SHALL 与主源一致；「代码」SHALL 归一去交易所前缀后的 6 位数字（日批/证券主表/复权基准均按 6 位匹配）。
- 备用源「成交量」单位为股、主源为手：输出 SHALL 按 ÷100 归一到手口径（与东财日线/日报同口径，混表可比）。
- 备用源停牌行「最新价」为 0.0（主源为 NaN）：输出 SHALL 归一为 NaN，交日批 D2 停牌合成路径（0 值直通会写出 close_raw=0 污染 qfq 序列）。

#### Scenario: 主源可用直接返回

- **GIVEN** 东财 `stock_zh_a_spot_em` 返回含「代码」列的非空表
- **WHEN** 调用 `fetch_market_spot`
- **THEN** SHALL 原样返回东财 DataFrame（不重塑列、不丢列）
- **AND** SHALL NOT 调用新浪 `stock_zh_a_spot`
- **AND** `sources_seen["market_spot"]` SHALL 含 `eastmoney`

#### Scenario: 主源失败回退新浪并保形

- **GIVEN** 东财 `stock_zh_a_spot_em` 拉取失败（返回 None/空表/缺「代码」列）
- **WHEN** 新浪 `stock_zh_a_spot` 返回非空表
- **THEN** SHALL 返回经 `_map_sina_spot_to_em` 映射的表
- **AND** 输出列 SHALL 含 代码/名称/最新价/涨跌幅/今开/最高/最低/昨收/成交量/成交额
- **AND** 「代码」SHALL 为去 sh/sz/bj 前缀的 6 位数字，「成交量」SHALL 为新浪原值 ÷100，「最新价」为 0.0 的行 SHALL 映射为 NaN
- **AND** `sources_seen["market_spot"]` SHALL 含 `sina`

#### Scenario: 两源皆失败返回 None

- **GIVEN** 东财与新浪均拉取失败/返回空表/缺「代码」列
- **WHEN** 调用 `fetch_market_spot`
- **THEN** SHALL 返回 None 并留 ERROR 级日志
- **AND** SHALL NOT 返回空 DataFrame 或残缺列表
- **AND** 日批 `run_market_snapshot_batch` SHALL 抛 `SnapshotError` 并写失败标记（不写空快照）

#### Scenario: 日批摘要记录实际生效源

- **GIVEN** 日批 spot 拉取成功
- **WHEN** `run_market_snapshot_batch` 返回成功摘要
- **THEN** 摘要 SHALL 含 `spot_source` 键，值为实际生效源（`eastmoney`/`sina`）；客户端未上报时 SHALL 为 None

### Requirement: 历史回填补齐成交额

历史回填补取数（`fetch_symbol_history_raw_qfq` 与回填脚本）SHALL 输出并落库成交额（`amount`）与最高/最低价（`high`/`low`，raw 口径）列，口径与日批一致（成交额单位元、最高/最低为未复权原值，面板按复权因子派生 qfq 口径）；数据源未提供 amount 的票 SHALL 按取数失败处理（计入失败留痕），MUST NOT 以 NULL 直通落行——amount 类因子（如 `ts_mean(amount,20)`）在回填历史上的可计算性由此保证；最高/最低价同为必需列（源缺列按取数失败处理，同留痕），日内结构类因子（如 `(close-low)/(high-low)`）在回填历史上的可计算性由此保证。断点续跑齐备判据 SHALL 覆盖 amount 与 high/low（任一缺失的既有行 SHALL 重取 upsert 补齐）。

#### Scenario: 回填行含成交额

- **GIVEN** 新浪历史日线返回含 amount 列的双口径数据
- **WHEN** 回填执行
- **THEN** `market_snapshot` 回填行 `amount` 非空且单位归一
- **AND** `ts_mean(amount, 20)` 类因子在回填区间上可计算

#### Scenario: 源缺成交额按失败处理

- **GIVEN** 某票历史数据源未返回 amount 列
- **WHEN** 回填执行
- **THEN** 该票计入 `symbols_failed` 留痕，不落 amount 为 NULL 的半口径行

#### Scenario: 回填行含最高最低价

- **GIVEN** 历史数据源返回含最高/最低价的双口径数据
- **WHEN** 回填执行
- **THEN** `market_snapshot` 回填行 `high`/`low` 非空（raw 口径）
- **AND** `(close-low)/(high-low)` 类日内结构因子在回填区间上可计算

#### Scenario: 源缺最高最低价按失败处理

- **GIVEN** 某票历史数据源未返回最高/最低价列
- **WHEN** 回填执行
- **THEN** 该票计入 `symbols_failed` 留痕，不落 high/low 为 NULL 的半口径行

#### Scenario: 缺 high/low 的既有行重取补齐

- **GIVEN** 区间内既有行 amount 非空但 high/low 为空（旧六列契约回填产物）
- **WHEN** 回填执行
- **THEN** 该票不构成断点续跑覆盖、重取 upsert 补齐 high/low

