# Proposal: fix-track-record-holdings-semantics

> **状态：提案，待 owner 拍板。未拍板前不改任何代码、不动主规范库。**
> 出处：2026-11-06 零买入修复计划 Task 8（docs/superpowers/plans/2026-11-06-zero-buy-decision-fix.md）。

## Why

账本把 `sell`（含 `sell_type=exit`）一律映射为 `short` 方向性观点（`judgment.py:54`
`direction_for_action`，注释明言「结算层不感知 sell_type」）。但系统受众是 A 股普通
证券账户投资者，持仓默认「未申报」——对未持有者，`sell_type=exit` 的真实语义是
「别买」，不是「做空」。已盯市 60 条看空判断方向性收益 -2.68%、胜率 12%（predictions
⨝ daily_marks），净值曲线实际是「假设做空」的盈亏，与散户可执行动作无关。

## What Changes

- `predictions` 增加 `holdings_state` 字段，三态：
  - `held`（受众已申报持有）：exit 按既有 `short` 方向性结算（持有人退出敞口，
    价格下跌获益的数学对持有人成立）；
  - `not_held`（受众已申报未持有）：看空记 `avoidance` 语义——收益记 0，
    机会成本（回避后价格实际跌幅）单列展示，不进主账本收益；
  - `unreported`（默认，未申报）：保持现状映射但单独打标，**不混入主账本**。
- 战绩页 / API 对 `avoidance` 与 `short` 分别展示，不共用一条净值。
- 顺带修正 Trader 提示词已引入的持仓分档（「受众持仓: 已申报持有/未持有/未申报」）
  与账本口径的对齐缺口：管线采集受众持仓申报时写入 `holdings_state`，未申报落
  `unreported`。

**BREAKING**（对主账本口径）：`unreported` 退出主账本后，现有方向性样本量骤减——
这正是本提案要的效果（宁缺毋假），但需要 owner 知晓并确认。

## Capabilities

- **Modified Capabilities**: `track-record`（判定规则（Outcome Resolution）：
  direction 映射从「sell 一律 short」改为「按 holdings_state 分流」）

## Impact

- `src/finance_agent/outcome/track_record/`：ingest（落库）、judgment（映射）、
  model（迁移 + 结算）、report/API（展示分流）。
- 存量 56 条方向性观点**不回填**：打口径来源标注（`legacy_short_mapping`），
  新口径从切点起算，双轨展示至 owner 确认切换。
- 净值曲线历史不可比：切换点前后分段展示（与 track-record-versioning 的版本
  机制衔接）。
