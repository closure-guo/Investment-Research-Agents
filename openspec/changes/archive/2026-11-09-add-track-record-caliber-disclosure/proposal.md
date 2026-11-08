# Proposal: add-track-record-caliber-disclosure

## Why

战绩页「跑赢指数对比」卡片直接对比「本组合区间收益」与五大宽基指数，但组合净值的真实构成从未披露：

- 组合日收益只聚合 `long`/`short` 观点，`neutral`（观望/持有）一律排除（`metrics.md` §1.12②）；
- 当前生产库 337 条观点里 `neutral` 占 66%，方向性观点只有 `short` 一类，且窗口内无任何 `long` 仓位——**卡片比较的实际是一个 100% 空头账本与只做多的宽基指数**；
- 用户看到「跑输 2/5 个指数」时，无从得知样本只有 20 个净值点、期内已结算 5 笔、多头 0 条。

2026-11-04 的归因排查（`reports/2026-11-04-策略跑输上证与沪深300归因.md`）确认：同批数据换分母即可把结论从「跑输」翻成「跑赢」，指标当前不可解释。在不改任何数字的前提下，先把口径摆到用户面前。

## What Changes

- `GET /api/v1/track-record/index-compare` 响应追加 `caliber` 对象：`nav_points` / `directional_views` / `long_views` / `short_views` / `settled_count`（纯追加字段，不改既有字段与算法）。
- 前端 `IndexCompareCard` 追加口径披露行：明示「仅统计买入/卖出观点，观望/持有不计入组合仓位」+「窗口 N 个交易日」+「多 X / 空 Y」+「期内已结算 Z 笔」。
- 旧响应（无 `caliber` 键）不渲染披露行、不报错。

**不改**：`agent_return`、各指数 `return`/`beat`、`equity_curve`、`daily_marks`、判定链、`metrics.md` 已登记口径。

## Capabilities

- **New Capabilities**: 无
- **Modified Capabilities**: `index-comparison`（追加披露要求，既有「指数区间收益对比读数」需求不动）

## Impact

- 后端：`src/finance_agent/outcome/track_record/index_compare.py`（新增 `_build_caliber`）、`src/finance_agent/api.py`（透传，无需改动——`{**data}` 已带出）。
- 前端：`frontend/src/pages/trackRecord/IndexCompareCard.tsx`、`frontend/src/types.ts`。
- 测试：`tests/outcome/test_index_compare.py`、`frontend/src/test/trackRecord/indexCompareCard.test.tsx`。
- 数据：无迁移、无回填、无重算；只读查询 `predictions`/`daily_marks` 计数。
