# Delta for market-snapshot

## ADDED Requirements

### Requirement: 停牌行合成判定

日批构造当日快照行时，系统 SHALL 把「当日无成交」的标的合成为停牌行（`is_suspended=1`），无论 spot 源以哪种形态表达无成交：

- spot 缺行，或「最新价」为 NaN/0（既有 D2 形态）；
- spot 行「最新价」有效，但「今开」「最高」「最低」全为 0 且「成交量」为 0（源回填昨收的形态，2026-10 实测东财与新浪对停牌标的均会出现）。

合成行 SHALL 承接昨行 `close_raw`/`close_qfq` 作为 OHLC 与收盘，`volume`/`amount` 为 0，`pct_chg` 为 0；无昨行或昨行无 `close_raw` 时 SHALL 跳过该标的（MUST NOT 编造价格）。OHLC 有效的正常交易行 SHALL NOT 被误判为停牌。

#### Scenario: 零 OHLC 形态判停牌

- **GIVEN** spot 行「最新价=3.57、昨收=3.57、今开=最高=最低=0、成交量=0、成交额=0」，且昨行 `close_raw=3.57`、`close_qfq=3.57`
- **WHEN** 日批构造当日快照行
- **THEN** 该标的当日行为停牌行：`is_suspended=1`
- **AND** `open`/`high`/`low`/`close_raw` 均为 3.57（承接昨行），`close_qfq=3.57`
- **AND** `volume=0`、`amount=0`、`pct_chg=0`

#### Scenario: 零 OHLC 且无昨行跳过

- **GIVEN** spot 行为「最新价有效、今开=最高=最低=0、成交量=0」形态，且快照库无该标的昨行
- **WHEN** 日批构造当日快照行
- **THEN** 该标的当日不落行（跳过）
- **AND** MUST NOT 以 0 或编造价格落行

#### Scenario: 正常交易行不误判

- **GIVEN** spot 行「今开=3.50、最高=3.60、最低=3.40、最新价=3.55、成交量=123456」
- **WHEN** 日批构造当日快照行
- **THEN** 该标的当日为正常行：`is_suspended=0`，OHLC 取 spot 原值

#### Scenario: 既有 NaN 形态回归不变

- **GIVEN** spot 行「最新价」为 NaN 且昨行存在
- **WHEN** 日批构造当日快照行
- **THEN** 该标的当日为停牌行（既有 D2 语义不变）
