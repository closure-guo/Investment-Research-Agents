# Delta for market-snapshot

## MODIFIED Requirements

### Requirement: 全市场日频快照落库

系统 SHALL 在每个 A 股交易日收盘后，将全市场标的的日线行情（开/高/低/收/成交量/成交额）以不可变记录写入 SQLite 快照表；每条记录 SHALL 同时包含前复权收盘价与不复权收盘价两个口径，且字段命名显式区分（`close_qfq` / `close_raw`）。日批排程 SHALL 错开 15:30 整点（新浪全市场拉取峰值，issue #602 五连败根因），注册为工作日 15:31（Asia/Shanghai），且 MUST NOT 迟于下游 `regime_daily`（15:35）读库时点起跑——首轮失败 + 同 run 重试的最坏完成时点 SHALL 早于 15:35。

#### Scenario: 收盘后日批写入

- **GIVEN** 一个 A 股交易日且当前时间晚于 15:31
- **WHEN** 日批任务执行
- **THEN** 快照表中该交易日的记录数等于当日全市场标的数（含停牌标的，停牌标的成交量为 0 仍须有记录）
- **AND** 对同一交易日重复执行任务，记录数不变（幂等）

### Requirement: 全市场 spot 数据源回退

系统在拉取全市场 spot 表（`fetch_market_spot`，日批唯一取数入口）时 SHALL 以东财 `stock_zh_a_spot_em` 为主源；当主源拉取失败（重试耗尽/空表/缺「代码」列）时 SHALL 回退新浪 `stock_zh_a_spot`。备用源产出 SHALL 经列映射归一到日批期望的东财列形（列名逐列对应；新浪缺失的列 SHALL 补 NaN 保持列存在，MUST NOT 改变列集合）。主源与备用源均不可用时 SHALL 返回 `None`，由调用方按既有失败语义处置（抛错 + 失败标记告警），MUST NOT 静默返回空表或残缺表（不写空快照）。实际生效源 SHALL 记入客户端 `sources_seen["market_spot"]` 与日批摘要 `spot_source`。两源皆失败时客户端 SHALL 留痕各源末次失败明细（源名/失败形态/尝试次数）于 `last_spot_failure`，供调用方生成可判读的错误消息。

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

#### Scenario: 两源皆失败返回 None 并留痕失败明细

- **GIVEN** 东财与新浪均拉取失败/返回空表/缺「代码」列
- **WHEN** 调用 `fetch_market_spot`
- **THEN** SHALL 返回 None 并留 ERROR 级日志
- **AND** SHALL NOT 返回空 DataFrame 或残缺列表
- **AND** `last_spot_failure` SHALL 含东财与新浪两源的末次失败明细（失败形态如 超时/连接不可达/空表，及尝试次数）
- **AND** 日批 `run_market_snapshot_batch` SHALL 抛 `SnapshotError` 并写失败标记（不写空快照），异常消息 SHALL 内嵌上述源级明细

#### Scenario: 日批摘要记录实际生效源

- **GIVEN** 日批 spot 拉取成功
- **WHEN** `run_market_snapshot_batch` 返回成功摘要
- **THEN** 摘要 SHALL 含 `spot_source` 键，值为实际生效源（`eastmoney`/`sina`）；客户端未上报时 SHALL 为 None

## ADDED Requirements

### Requirement: spot 双源失败同 run 有界重试

日批 `run_market_snapshot_batch` 在 spot 双源皆失败时 SHALL NOT 立即终止：SHALL 经有界冷却（默认 65s，环境变量 `MARKET_SNAPSHOT_SPOT_RETRY_DELAY_S` 可覆盖）后在**同 run 内**重试一轮 spot 拉取；重试命中则批次继续正常编排并在摘要 `spot_retry=true` 留痕，重试后仍失败才按既有失败语义抛 `SnapshotError`（消息含源级明细）。每 run 至多重试一轮（有界）；spot 首轮成功时 SHALL NOT 产生冷却等待，摘要 `spot_retry` 为 false。

#### Scenario: 重试一轮命中不失败

- **GIVEN** 15:31 排程首跑 spot 双源皆失败（新浪限频峰值）
- **WHEN** 65s 冷却后同 run 重试一轮且 spot 拉取成功
- **THEN** 批次继续正常编排并成功落库（不再依赖 catchup 下一跳）
- **AND** 成功摘要含 `spot_retry: true`

#### Scenario: 重试后仍失败按既有语义失败

- **GIVEN** 同 run 重试一轮后 spot 双源仍皆失败
- **WHEN** 批次结束
- **THEN** 抛 `SnapshotError`（消息含 东财/新浪 各自失败形态与尝试次数）
- **AND** `job_runs.error` 与快照 `meta.last_error` 均可判读到源级明细
- **AND** 每 run 至多重试一轮，不进入无界重试循环

#### Scenario: 首轮成功零额外等待

- **GIVEN** 排程首跑 spot 首轮即成功
- **WHEN** 批次结束
- **THEN** 摘要 `spot_retry` 为 false
- **AND** 全程无冷却 sleep（不增加正常日批耗时）
