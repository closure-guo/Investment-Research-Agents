# Proposal: add-settlement-price-caliber

## Why

结算/盯市用的历史行情取数口径（复权方式）目前**泄漏给每个调用方自行决定**：`fetch_kline(code, days, adjust)` 把「用哪种价」甩给调用方——`track_record/job.py:168` 与 `events/journal.py:119` 记得传 `SETTLEMENT_ADJUST`（hfq），`track_record/marking.py:131` 刻意不传（qfq 盯市近似口径），`outcome/settle.py`（已删，C6②）当年压根没管。口径拿错 → 收益算错 → 战绩读数错。事故史：track-record 口径混用多次复发（盘面价 vs hfq 结算价），incident 054 刚用 PR #607 修过「hfq 锁定新浪 fail-closed」——但那是点状修补，纪律以「调用方各自记忆」存在。

同时，日期归一 / 基准对齐收盘两个小工具在 judgment.py / marking.py / settle_legacy.py（原 settle.py）三处拷贝。

## What Changes

- 新增 `data/market_caliber.py`（取价口径 owner，seam 建在 akshare_client 之上）：
  - `settlement_series(client, symbol, *, days)`——结算专用 hfq 序列：内部锁定 `SETTLEMENT_ADJUST`，fail-closed（取数失败返回 None，SHALL NOT 静默降级 qfq）
  - `monitor_series(client, symbol, *, days)`——盯市专用 qfq 序列（marking 现状口径显式命名）
  - `benchmark_close_on_or_before(benchmark_df, date)`——基准对齐收盘唯一实现（#57 排序防御随迁）
  - `normalize_dates(df)`——中文列日期归一唯一实现
- 消费方收编：job.py / journal.py 改走 `settlement_series`；marking.py 改走 `monitor_series`；三处 helper 拷贝归一
- SETTLEMENT_ADJUST 常量随迁 caliber（akshare_client 保留原值，readout 式单一真相源）

**非目标**：execution/ 的撮合取数（快照口径，独立语义）不动；akshare_client 内部的源选择逻辑（新浪 fail-closed/腾讯回退）不搬——caliber 是口径 seam，不是源 seam。

## Capabilities

- **New Capabilities**: 无（并入既有 capability）
- **Modified Capabilities**: track-record（判定与盯市行情口径契约——ADDED requirement）

## Impact

- `src/finance_agent/data/market_caliber.py`（新增）
- `src/finance_agent/outcome/track_record/{job,marking,judgment}.py`（消费方收编）
- `src/finance_agent/outcome/events/journal.py`（消费方收编）
- `src/finance_agent/data/akshare_client.py`（SETTLEMENT_ADJUST 注释指向 caliber）
- 纯后端（非交互类，无 E2E 门禁）；行为契约：结算 hfq fail-closed / 盯市 qfq 披露——现状行为不变，只是「想拿错都拿不到」
