# Proposal: fix-suspended-spot-row-detection

## Why

2026-10-21 起，秦港股份（601326）停牌期间的 spot 行以「最新价=昨收（3.57）、今开/最高/最低=0、成交量=成交额=0」的形态返回（东财/新浪对「有价无成交」标的的实际行为），日批 D2 停牌合成只认「最新价为 NaN」一种形态，这些行被写成正常交易日（`is_suspended=0`、OHLC 全 0）。后果链：L1 停牌过滤拦不住 → 「零振幅 + 零成交量」对全部负向 IC 因子都是极端有利值 → 该票 10-27 排到 L2 第 2、10-28 进入精选池（issue #511）。全库该形态 35 行 / 14 只票，近 10 日每天 2–5 只。

## What Changes

- 日批停牌判定从单一形态（spot 缺行 / 最新价 NaN）扩展为「无成交即停牌」：新增第二判据——spot 行「最新价」有效但「今开」「最高」「最低」全为 0 且「成交量」为 0 → 同样走 D2 停牌合成（`is_suspended=1`，OHLC 承接昨行）。
- 无昨行或昨行无 `close_raw` 时维持既有跳过语义（MUST NOT 编造价格）。
- 正常交易行（OHLC 有效）不受影响；主源「最新价=0 且 OHLC=0」的形态也被第二判据兜住（0 不会直通写库污染 qfq）。

## Capabilities

- **New Capabilities**: 无
- **Modified Capabilities**: market-snapshot（ADDED Requirement「停牌行合成判定」——把既有「缺行/最新价 NaN」形态与新增「零 OHLC」形态钉进同一 Requirement）

## Impact

- `src/finance_agent/data/snapshot.py`：`build_snapshot_rows` 增加第二停牌判据（新纯函数 `_is_zero_trade_row`）。
- L1 选股过滤（`screening/filters.py`）零改动——`is_suspended=1` 后既有过滤自然生效。
- 因子面板/IC：停牌行恢复「平盘 + 零量」正确形态，负向因子不再把「零振幅」当满分。
- 关联：incident 053 与 issue #511 的生产污染由此修复；#514 的 orientation 回填已在生产库执行（与本 delta 无关）。
