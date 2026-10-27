# Delta for factor-library

## ADDED Requirements

### Requirement: 入库后业绩复核

系统 SHALL 每月执行一次入库后业绩复核（挂因子提案月批尾部执行）：对 `active` 且**入库后已记录 IC 样本 ≥ 60** 的因子，在**入库后全样本** IC 序列上复核两项判据（IC 口径与闸②同源：RankIC 的 mean/std，非年化）：

1. **入池水位复核**：入库后 |ICIR| < `MIN_ABS_ICIR`（0.3，与闸②同一水位——入池依据在后验中不成立）；
2. **全局多重检验复核**：「跨批次候选检验账本」的全局 BH 反事实（账本全部非空 p 值作单一家族，q=0.1）下该因子 `p_adj > q`（其入池批内显著性在跨批次视角下不可维持——批内过闸可能是家族运气）。

任一命中 SHALL 将该因子置 `degraded`（`reason=POST_ADMISSION_REVIEW`，detail 含入库后 ICIR 与全局反事实 p_adj 两项读数），落降级事件；两项均不命中 SHALL 保持 `active` 不落任何事件。样本不足（< 60）SHALL 计入 `skipped` 不判定。复核 SHALL NOT 修改闸③批内校正语义、准入时点结论与账本行（消费账本既有只读读数做运行期处置，不触碰账本 requirement 的准入侧边界）。已 `degraded`/`retired` 的因子不在 active 集合内，天然不参与复核（MUST NOT 复核恢复——恢复仍走既有复核通道）。

本复核与「衰减监控与自动降级」分工：衰减监控为**日频短窗灵敏告警**（60 日滚动窗、ICIR 0.1 地板，抓短窗变弱与方向反转）；本复核为**月频全样本严格后验**（入库后全样本、0.3 入池水位 + 跨批次视角，抓批内运气与长期不达水位）。两者互补，处置动作同归 `degraded`。

月批摘要 SHALL 含 `{reviewed, degraded, skipped}` 读数；复核步骤异常 SHALL 上抛（job 记 failed），MUST NOT 静默跳过复核。

#### Scenario: 入库后强度不达水位降级

- **GIVEN** 因子 F 入库 80 个交易日，入库后全样本 |ICIR| = 0.18（< 0.3）
- **WHEN** 月度复核执行
- **THEN** F 置 `degraded`，事件 reason 为 `POST_ADMISSION_REVIEW`，detail 含 ICIR 读数
- **AND** 次月选股打分不再包含 F

#### Scenario: 批内显著但跨批次视角不显著降级

- **GIVEN** 因子 F 入池时批内 BH 校正 p_adj = 0.06（过闸③），账本全局反事实（全家族 BH、q=0.1）下 p_adj = 0.14；入库后 ICIR = 0.35（水位过关）
- **WHEN** 月度复核执行
- **THEN** F 置 `degraded`（判据 2 命中），detail 含全局反事实 p_adj 读数

#### Scenario: 样本不足跳过

- **GIVEN** 因子 G 入库 30 个交易日（IC 样本 30 < 60）
- **WHEN** 月度复核执行
- **THEN** G 计入 `skipped`，状态不变、无事件

#### Scenario: 复核通过无动作

- **GIVEN** 因子 H 入库 120 个交易日，入库后 |ICIR| = 0.42 且全局反事实 p_adj = 0.04
- **WHEN** 月度复核执行
- **THEN** H 保持 `active`，无事件落库，摘要 `reviewed` 含 H

#### Scenario: 摘要读数完整

- **GIVEN** 本月复核：8 条样本充足（2 条命中降级）、4 条样本不足
- **WHEN** 月批完成
- **THEN** 月批摘要含 `{reviewed: 8, degraded: 2, skipped: 4}`，任务运行历史可查

#### Scenario: 复核不触碰准入侧账本边界

- **GIVEN** 本月复核将因子 F 置 `degraded`
- **WHEN** 复核完成
- **THEN** `factor_admission_ledger` 任何行未被修改，闸③批内 `p_adj`/`n_hypotheses` 读数原样保留
- **AND** F 的入库批准入结论（历史事件）不变
