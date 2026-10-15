# Proposal: add-market-data-fallback

## Why

全市场快照（`market_snapshot_daily` 日批）的 spot 数据源单源硬绑东财（`fetch_market_spot` → `ak.stock_zh_a_spot_em`）。东财行情域 IP 封禁（#325，2026-10-15 实测 `stock_zh_a_hist` 0.4s 即 `RemoteDisconnected`）后该函数恒返回 None → 日批抛错、快照库停更 → 下游整条链路（选股 → 分析 → 执行）数据饿死。

对照事实（2026-10-15 实测）：非东财域可用——单标的行情主源腾讯可用、交易日历新浪源可用；本次新增实测新浪全市场 spot（`ak.stock_zh_a_spot()`，5575 行 12.7s）与新浪历史日线（`ak.stock_zh_a_daily`，raw/qfq 双口径均返回）可用。回填脚本（`fetch_symbol_history_raw_qfq`）同为东财单源，需同款回退。

## What Changes

- `fetch_market_spot` 增设备用源链：东财 → 新浪 → None（两源皆失败仍返回 None，调用方按既有失败语义告警/重试，**不得**静默写空快照）。
- 新增纯函数 `_map_sina_spot_to_em`：新浪 spot 列名/单位 → 日批期望的东财列形（缺列补 NaN 保形）；缺口（单位换算、停牌 0 值、估值列缺失）在 docstring 逐条注明。
- `run_market_snapshot_batch` 成功摘要新增 `spot_source`（实际生效源）；客户端以 `sources_seen["market_spot"]` + `last_spot_source` 留痕。
- `fetch_symbol_history_raw_qfq`（回填取数）增同款新浪回退：东财双口径失败 → 新浪 `stock_zh_a_daily` raw/qfq 双口径（列名/单位归一后按日期内连接）。

## Capabilities

- **Modified Capabilities**: `market-snapshot`（ADDED 一个 requirement：全市场 spot 数据源回退）

## Impact

- 代码：`src/finance_agent/data/akshare_client.py`（`fetch_market_spot` / `fetch_symbol_history_raw_qfq` / 新增 `_map_sina_spot_to_em`）、`src/finance_agent/data/snapshot_job.py`（摘要键）。
- 测试：`tests/data/test_market_data_fallback.py`（新增）；既有 `tests/data/test_market_snapshot_job.py` 语义不变。
- 依赖：无新增（新浪接口经既有 akshare 依赖提供）。
- 运维：日批单次多付一次新浪全市场拉取（≈13s）与一次东财失败重试（≈5s）；回填在东财封禁下每票仍先付一次东财失败成本（见 design.md Risks）。
