# Proposal: add-factor-ic-gap-visibility

## Why

incident 050 的诊断需要人工翻 `factor_ic` 日期序列才能确认「IC 样本缺口」——选股持续走流动性降级时，运维侧看不到缺口维度。issue #454 三项待决已经 owner 裁决（2026-10-27，评审材料见 issue 评论）：回填边界（90 日）与 IC 口径（历史日重算）**维持现状并落 `docs/evals/metrics.md` §1.21**，本 delta 只补缺口可见性。

## What Changes

- 因子信号就绪 payload（`factor_signal_payload`）为每个 active 因子新增 `ic_gap_days`（最近落库 IC 日期与面板 as_of 之间缺失的交易日数，按快照交易日历）
- payload 顶层新增 `ic_gap_present`（任一因子缺口 > 0 即 true），供前端/日报醒目披露
- `/factor-research` 页因子信号就绪区与运营日报的因子区展示缺口读数

## Capabilities

- **New Capabilities**: `factor-ic-gap-visibility`（specs/factor-ic-gap-visibility/spec.md）

## Impact

- `finance_agent/api.py`（payload 组装）、`finance_agent/screening/ranker.py` 或 library 读数侧（IC 序列读取已有）、前端 `frontend/src/pages/factors/`、日报生成器
- 只读展示：不改变 IC 采集、回填、五道闸任何行为；无口径影响（口径条文见 metrics.md §1.21）
