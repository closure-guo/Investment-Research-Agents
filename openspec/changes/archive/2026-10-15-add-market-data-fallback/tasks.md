# Tasks: add-market-data-fallback

- [x] 真实网络实证：新浪全市场 spot（`ak.stock_zh_a_spot`）可达性与耗时、列名、代码前缀、停牌 0 值形态
      —— 5575×14、12.4s、前缀 sz2906/sh2319/bj350、6 个 0 值停牌行（见验证报告 §6①）
- [x] 真实网络实证：新浪历史日线（`ak.stock_zh_a_daily`）raw/qfq 双口径可达性与列名
      —— raw/qfq 均 9 列（date/open/high/low/close/volume/amount/outstanding_share/turnover），除权窗口内 raw≠qfq 验证成立
- [x] `_map_sina_spot_to_em` 纯函数：列映射 / 缺列保形 / 空表 / 代码去前缀 / 成交量股→手 / 停牌 0 值→NaN（先红后绿单测 8 项）
- [x] `fetch_market_spot` 回退链：东财失败/空表/缺列 → 新浪；两源皆失败 → None（既有失败语义与告警不变；单测 6 项）
- [x] `sources_seen["market_spot"]` 与日批摘要 `spot_source` 如实记录实际生效源（单测 2 项 + live 命中）
- [x] `fetch_symbol_history_raw_qfq`（回填）同款新浪回退：双口径列名/单位归一 + 按日期内连接（单测 7 项 + live 命中）
- [x] 用真实数据跑一次快照日批（隔离库，不动主库）：行数 5569 / 抽样 3 行 / 耗时 54.7s / 生效源 sina
      —— 另含真实停牌行 6/6 D2 合成实测（见验证报告 §6②）
- [x] `openspec validate add-market-data-fallback --strict` 通过
- [x] 既有测试语义不变（`tests/data/`、`tests/scripts/test_backfill_market_snapshot.py`、`tests/outcome/test_cohort_universe.py` 全绿）
- [x] 验证报告落 `tests/validation/2026-10-15-add-market-data-fallback-validation.md`（含真实读数 + 未达成项声明）
