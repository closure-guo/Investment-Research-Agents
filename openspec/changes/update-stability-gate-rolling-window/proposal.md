# Proposal: update-stability-gate-rolling-window

## Why

「连续 3 日在榜」与「排序口径换挡」耦合（docs/audit/2026-10-23-screening-funnel-review.md P0-1）：
`consecutive_days_in_top` 只问「在不在榜」、不问「为什么在榜」，一次 factor↔fallback 换挡把
所有人的在榜连击清零、池子清空 2–3 天——10-20 池 1 只、10-23 池 96 只（95 只同日齐满 3 日门槛），
11-02 因子模式恢复（IC 样本预计当日补齐）时将重演一次清零。

纯滚动窗口（近 5 日命中 ≥3）治不了换挡：换挡周标的确实整周不在另一口径的榜上。对症解是
**同模式轴**上的滚动窗口——其他口径的日子不进轴、也不打断计数，模式内单日跌出榜不再一票否决。

## What Changes

- 精选池资格判定改为「同选股口径最近 5 个交易日命中 ≥ 3」（`min_days` 可配置语义不变）：
  模式口径取自 `screening_batches.selection_mode`（与 L2 榜同 `(trade_date, pool)` 一行，零
  schema 变更）；批次行缺模式的日子按同模式计入（宽待存量）；`as_of` 批次行缺模式时退回
  全轴滚动；`as_of` 当日不在榜 → 无资格（不变量保留）。
- 新增 `days_in_top_recent`（同模式滚动命中计数）；`consecutive_days_in_top` 原语义保留，
  仍供精选池 `consecutive_days` 信息列使用——信息列如实记「连续 1 日」与资格判定（同模式
  命中 3 次）并存是刻意设计，前端不必改。
- 不做：前端展示同模式命中数（`consecutive_days` 信息列语义未变）；streak 分段的历史回填。

## Capabilities

### Modified Capabilities

- `stock-screening`：MODIFIED「在榜稳定性门槛」——连续 3 日门槛改为同模式 5 日窗 ≥3 命中。

## Impact

- 代码：`src/finance_agent/screening/store.py`（新增 `days_in_top_recent` + 改写
  `eligible_for_selection`）；调用点 `pipeline.py` 零改动（`min_days=POOL_MIN_DAYS` 语义不变）。
- 测试：`tests/screening/test_screening_store.py` 新增 7 例（先红后绿）；1 例旧契约断言随
  delta 翻转（单日断档不再一票否决）。
- 行为：11-02 因子模式恢复当日，因子票的同模式历史命中直接可用，池子不再清零重攒；
  模式内偶发单日出榜不再把票踢出池外一天。
