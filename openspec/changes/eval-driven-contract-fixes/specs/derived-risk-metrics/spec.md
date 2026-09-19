# derived-risk-metrics Delta

## MODIFIED Requirements

### Requirement: LLM 自算数值不作为下游真值

当 reasoning 文本中的自算数值与代码计算值冲突时，下游（裁决 context、报告渲染、评估材料）SHALL 以代码计算值为准；代码计算值缺失时按缺失处理，MUST NOT 回退采用 LLM 自算值。

**原位修正（2026-09-19 处置，证据：601899 终稿自称「赔率约1.7:1」而代码计算 1.24:1；000333 初稿自称「约2.6:1」而代码计算 2.00:1——两例横跨 Trader 与风控层，错数穿透全部下游原样渲染）**：trader / fund_manager 产出的 reasoning 文本 SHALL 经确定性自检——文本中与代码计算 `risk_reward_ratio` 冲突的 `N:1` 型赔率表述 SHALL 原位替换为代码计算值（保留原文其余内容，无 LLM 参与），替换 SHALL 记入 telemetry（`payout_ratio_corrected`）；代码计算值缺失（派生指标 None）时跳过修正、维持「按缺失处理」语义。相对容差 10% 以内的差异视为四舍五入，不修正。

#### Scenario: 冲突裁决

- **WHEN** reasoning 文本声称「赔率 2.5:1」而代码计算为 1.90:1
- **THEN** 注入辩论 context 与报告渲染的 SHALL 均为 1.90:1

#### Scenario: 文本赔率原位修正

- **WHEN** trader/FM 产出的 reasoning 含「赔率约1.7:1」而同一决策的代码计算 risk_reward_ratio = 1.24
- **THEN** 该 reasoning 文本 SHALL 被修正为「赔率约1.24:1」（其余文字不动），修正 SHALL 记入 telemetry，SHALL NOT 触发 LLM 重写

#### Scenario: 容差内不修正

- **WHEN** reasoning 声称「赔率约1.9:1」而代码计算为 1.90:1（或相对差 <10%）
- **THEN** 文本 SHALL 保持原样，无 telemetry
