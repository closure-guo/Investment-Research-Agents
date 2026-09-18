# citation-retry-policy Delta

## MODIFIED Requirements

### Requirement: value_mismatch 单点修复前置分支

value_mismatch 触发定向重试前，系统 SHALL 先评估单点修复适用性：同一分析师同一轮的 value_mismatch FAIL 数 < 3 时，SHALL 采用单点修复——将每处出错句（含所在章节局部上下文，窗口 SHALL 不少于出错句前后各一段）、对应 ground_truth 与申报格式示例，交一次轻量 LLM 调用做叙事一致性改写，改写结果回填正文 markdown。value_mismatch FAIL 数 ≥ 3 或修复调用失败时，SHALL 回退现有目标分析师全量定向重试路径（不重试单点修复失败处）。单点修复 SHALL NOT 改变重试轮数上限 3、停滞降级（本轮失败率 ≥ 上轮 × 80% 提前放行）与轻微失败直判放行的既有语义；单点修复轮次与全量重试轮次共享 iteration_count 计数。

**修复记账（incident 029 处置，2026-09-18）**：修复收益 SHALL 按**单条 claim** 记账——重校验后该 claim 数值与 ground_truth 一致即计入 `value_mismatch_repaired_claims`（分子 = 修好的 claim 条数，分母 = 进入修复的 claim 条数），SHALL NOT 以「同分析师名下全部 claim 通过（all_passed）」为记账前置。既有按分析师口径的 `value_mismatch_repaired` 字段保留为 deprecated（跨口径比较须标注切点），新读数以 per-claim 口径为准。

#### Scenario: 稀疏失败走单点修复

- **WHEN** 某分析师本轮 1 条 claim 判 value_mismatch（如 1/46）
- **THEN** 系统 SHALL 发起单点改写（1 次 LLM 调用）而非重跑该分析师全量报告

#### Scenario: 密集失败回退全量重试

- **WHEN** 同一分析师同一轮 4 条 claim 判 value_mismatch
- **THEN** 系统 SHALL 不做单点修复，直接走现有目标分析师定向重试

#### Scenario: 单点修复不改变止损语义

- **WHEN** 连续两轮失败率 35% → 31%（≥ 35% × 80%）
- **THEN** 即使本轮适用单点修复，系统 SHALL 仍按停滞降级提前放行渲染

#### Scenario: 同分析师另有未修 FAIL 不遮蔽已修 claim

- **WHEN** 单点修复使 claim A 数值改对（重校验一致），但同分析师名下另有无关 claim B 仍 FAIL（all_passed=False）
- **THEN** claim A SHALL 计入 `value_mismatch_repaired_claims`；SHALL NOT 因 all_passed=False 而漏记
