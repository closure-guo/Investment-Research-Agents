# Proposal: add-factor-direction-reversal-monitor

## Why

因子库现状（2026-10-27 读数）：10 个 active 因子**全部为负向因子**（入池依据 = 闸④训练段 IC 符号定向，全史 rank_ic 均值 -0.03~-0.09）。而衰减监控的降级判据是「近 60 日滚动 IC 均值连续 20 日**为负**且 |滚动 ICIR| < 0.1」——该判据隐含假设「负向因子的健康状态 = 负 IC」。由此产生一个方向语义盲区：**负向因子 IC 翻正（负向关系反转）时，`mean < 0` 不满足，永远不会触发降级**。

这不是理论风险：近 5 日已有 3/10 因子（volratio20、RANGE_EXPANSION_20D、F_range_expansion）IC 转正（5 日均值 +0.019~+0.028，最新单日最高 +0.14）。翻正的负向因子落在三个机制的缝里：衰减监控无感（判据只抓「负且弱」）、打分侧在 60 日滚动符号翻转后**默默跟随**（把入池依据已死的因子当正向因子用）、池管理无动作（继续占 active 桶位与结构查重域）。方向反转比衰减更严重——衰减只是信号变弱，反转意味着按原方向使用该因子开始亏钱。

根因：入池时的定向符号（闸④已计算并记入 `detail`）没有持久化到因子行，运行期没有任何机制以「入池定向」为锚检验方向一致性。

## What Changes

- `factors` 表新增 `orientation` 列（-1 / 0 / 1；0 = 不可定），入库时由闸④的候选训练段定向符号写入（闸④已算，落库即可；月批/builtin/收割/外部导入统一经 `admit_candidates` 单点写入）。
- 存量因子一次性回填：按全史已记录 `factor_ic` 均值符号回填（无样本或均值为 0 → 0，不参与反转监控）。
- 衰减监控日批新增**方向反转判据**：`orientation ≠ 0` 的因子，近 60 日滚动 IC 均值连续 20 个交易日**符号与入池定向相反** → 置 `degraded`（`reason=DIRECTION_REVERSAL`），落降级事件（含入池定向与触发时滚动读数）；原有「负且弱」判据逐字保留。
- 不改打分侧（L2 的 60 日滚动符号权重是独立机制；反转因子降级为 degraded 后经 `list_active_factors` 过滤自然离开打分）。

## Capabilities

- **Modified Capabilities**: `factor-library`（MODIFIED 需求「衰减监控与自动降级」）

## Impact

- `src/finance_agent/factors/library.py`（factors 表 `orientation` 列 + 存量回填）
- `src/finance_agent/factors/validate.py` / `external.py`（`admit_candidates` 写入候选定向）
- `src/finance_agent/factors/monitor.py`（反转判据分支 + 事件 reason）
- 存量迁移：12 条因子一次性回填（幂等，可重跑）
- 无前端交互变更；`/factor-research` 页降级事件展示复用现有事件流（如需展示 reason 中文文案属既有事件渲染路径）
