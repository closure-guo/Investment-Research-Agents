# Design: add-factor-monotonicity-gate

## Approach

闸②内加判据、不加闸编号：`validate.py` 阶段 A 在 ICIR 通过后追加 `check_monotonicity(values_matrix, forward_returns)`——按当日截面因子值分十分位（有值票 ≥ 20 才计当日，与值因子广度下限共用常量口径；不足日跳过该日），逐日等权累计收益 → 各分位累计收益向量 → Spearman(分位序, 收益) 与端点差 Q10−Q1。判定阈值 `MIN_DECILE_MONOTONICITY = 0.6` 落 `constraints.py`（首月观测期不调，调整须过 metrics.md 口径修订）。读数记 `GateResult.detail.monotonicity`，随事件史与准入账本既有结构走，无迁移。

存量不回溯：判据只挂在候选路径（`admit_candidates`），对库内 active 因子无任何重裁逻辑；详情接口的观测读数 = 同函数对现役因子在当前面板上的一次性计算（复用，无第二实现）。

## Alternatives Considered

- **新增独立闸⑥**：拒绝——闸编号是编排契约（`rejected_gate` 0–5 枚举、账本、事件史、前端展示全链引用），为一个判据改编号面得不偿失。
- **分层单调性做成分披露（非裁决）**：可作为首月观测期的降级预案（若真实候选拒收率异常高，先把判据降为 detail 记录、 observational 一个观察窗再升格裁决）；默认直接裁决——对标 G2 的语义就是 gate，且现有 10 个 active 因子回测读数（已有面板）可作为阈值 0.6 的 sanity 锚。

## Risks

- **阈值标定风险**（已实证收口）：首跑 sanity 锚按「正相关 ≥0.6」判 10/10 拒收——读数形态揭示口径 bug（10 因子 IC 均值全负、7 个 Spearman ∈ [−1.0, −0.758] 方向一致），判据修正为 |Spearman| ≥ 0.6 且符号与 IC 均值一致；修正后 7/10 通过、3/10（0.248/0.297/0.358）真弱被拒——阈值 0.6 校准健康，维持。
- **与 `add-analyst-revision-factors` 同改「五道闸准入管线」**：后归档方合并重写（既有惯例）；建议本 delta 先归档。
