# 回填扩列：最高/最低价（extend-backfill-high-low）

## Why

2026-10-17 闸门验证实证：回填行 `high`/`low` 覆盖 **0.0%**（2026-01～09 全空，仅日批行有）
——回填脚本 `_history_rows` 对这两列硬编码 `None`（六列契约只覆盖 amount）。后果：
日内结构类因子（如 `(close-low)/(high-low)`，RD-Agent 候选 ClosePosition 即此类）在
准入面板上被闸① LOW_COVERAGE 结构性拒收——**数据缺口被误读为因子质量问题**。

数据源本就有货：东财 `stock_zh_a_hist` 与新浪 `stock_zh_a_daily` 均原生返回最高/最低价
（raw 口径；面板 high/low 由 raw×复权因子派生，与快照语义一致）。本 delta 把回填列契约
从「六列」扩为「八列」（+最高/最低，raw 口径），并扩展断点续跑齐备判据——既有回填行
经重跑自动补齐。

## What Changes

- `fetch_symbol_history_raw_qfq` 输出 +`最高`/`最低`（raw 腿；两源同款；源缺列按取数失败
  ——不产半口径帧）；东财/新浪双口径腿的必需列集与归一名映射同步扩展。
- 回填脚本：行映射 `high`/`low` 从硬编码 `None` 改为取「最高/最低」；断点续跑齐备判据
  从「amount 全覆盖」扩为「amount + high + low 全覆盖」（缺者重取 upsert 补齐）。
- spec：market-snapshot「历史回填补齐成交额」MODIFIED——列契约扩为八列，新增两个场景
  （回填行含最高最低价 / 源缺最高最低价按失败处理），既有两场景保留。
- 重跑：全量重取（齐备判据扩展后既有行自动重取），产出 high/low 全覆盖回填区。

## Impact

- 影响面：`data/akshare_client.py`、`scripts/backfill_market_snapshot.py`、两处测试。
- 重跑为长任务（~5600 票 × 190 日，16 进程并行，数小时）——按 incident 043 纪律：
  不硬杀进程；写入探针用隔离库。
- 不涉及：面板/闸门代码零改动（high/low 消费链路已存在）；日批路径零改动（本就写这两列）。
