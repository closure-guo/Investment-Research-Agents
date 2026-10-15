# Delta: market-snapshot（add-market-data-fallback）

## ADDED Requirements

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
