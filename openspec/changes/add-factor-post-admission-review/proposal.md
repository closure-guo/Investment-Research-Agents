# Proposal: add-factor-post-admission-review

## Why

五道闸的全部检验都发生在**同一段 500 日历史面板**上：闸⑤的「样本外」只是同一分布内的时序切分，不是真前样本外。闸③的 BH-FDR 校正以**批**为家族（批上限 60、收割日内多批化后家族数持续膨胀——2026-10-27 单日 6 批），跨批次无校正（「跨批次候选检验账本」requirement 已提供全局 BH 反事实**读数**，但明文只读不执行）。结果是：入库 12 条因子中，哪些是批内运气、哪些在真前样本外成立，体系内没有任何后验裁决——`factor_ic` 表已积累每因子最长 90 日的实盘 IC（唯一真样本外），却只用于展示与衰减监控。

账本读数（2026-10-27）：546 个候选进入闸口检验、38 个批次家族、12 条入库、0 条被后验复核过。衰减监控只覆盖「短窗变弱」（60 日窗、ICIR 0.1 灵敏地板），覆盖不了「入库后全样本未达入池水位（0.3）」与「跨批次多重检验视角不显著」这两种失效形态。

## What Changes

- 新增**月度入库后业绩复核**（挂 `factor_proposal_monthly` 尾部执行）：对 `active` 且**入库后已记录 IC 样本 ≥ 60** 的因子，在入库后全样本上复核两项：
  1. 入库后 |ICIR|（mean/std，与闸②同口径）< `MIN_ABS_ICIR`（0.3，入池同水位）；
  2. 账本全局 BH 反事实（q=0.1，把账本全部非空 p 值作单一家族）下该因子 `p_adj > q`（跨批次多重检验视角不显著）。
  任一命中 → `degraded`（`reason=POST_ADMISSION_REVIEW`，detail 带两项读数），复用既有状态机（degraded 可复核恢复、不参与打分）。
- 不改闸③批内校正语义、不改变准入时点结论（复核是**运行期处置**，消费账本既有只读读数，不触碰「跨批次候选检验账本」requirement 的准入侧边界）。
- 月批摘要新增 `{reviewed, degraded, skipped}` 读数。

## Capabilities

- **New Capabilities**: 无（`factor-library` ADDED 需求「入库后业绩复核」）

## Impact

- `src/finance_agent/factors/monthly.py`（月批尾部复核步骤 + 摘要键）
- `src/finance_agent/factors/monitor.py` 或新模块（复核判据：入库后 IC 序列 ICIR + 账本全局反事实查询的复用封装）
- `src/finance_agent/factors/library.py`（如需 `record_status_change` 既有事件路径——零新增）
- 无 schema 变更（`factor_ic` / `factor_admission_ledger` / 状态机全部复用）
- 无前端交互变更（`/factor-research` 降级事件展示复用现有事件流）
