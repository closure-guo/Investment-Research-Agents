# citation-retry-policy Specification

## Purpose
TBD - created by archiving change improve-analyst-throughput. Update Purpose after archive.
## Requirements
### Requirement: 引用校验重试降级

自动重试默认停用：`CITATION_AUTO_RETRY_ENABLED = False` 时，引用校验非 PASS 状态 SHALL 直接放行渲染，SHALL NOT 派发分析师重跑轮；fail_buckets / retry_targets / fail_rates 的计算与 trace 观测 SHALL 保留。重试启用时，既有降级控制继续生效：失败率不低于上一轮 80% 提前终止、轮数上限 3 不放宽、轻微失败（FAIL ≤ 1 且失败率 ≤ 5%）直接放行。重试触发 SHALL 仅来自「重试准入桶」（`CITATION_RETRY_ADMITTED_BUCKETS`，当前为空集）——桶进入准入集合的前提是有逐条人工终裁记录确认其为真错误。重试启用且目标分析师在重跑后输出内容不变时，系统 SHALL 立即放行渲染并在 trace 留下可判读标记（不等待失败率停滞判定）。
(Previously: value_mismatch/direction_mismatch 桶与 coverage-gap 自动触发定向重试；incident 026 终裁两桶 100% 误报，r2 九条 deep 分析师生成 75 次、fundamental 25 次，零收益且以降级版覆盖正常报告)

#### Scenario: 停用状态下非 PASS 直接放行

- **WHEN** 引用校验存在 FAIL 或覆盖缺口，且 `CITATION_AUTO_RETRY_ENABLED = False`（默认）
- **THEN** `after_citation` SHALL 返回 `render`，SHALL NOT 派发任何分析师重跑轮

#### Scenario: 停用状态下观测不退化

- **WHEN** 停用状态下的任意一轮校验
- **THEN** fail_buckets / retry_targets / fail_rates / 覆盖缺口 SHALL 照常计算并写入 state 与 trace

#### Scenario: 既有失败率无改善提前放行

- **WHEN** 重试启用且最新一轮失败率 ≥ 上一轮 × 80%
- **THEN** 系统 SHALL 提前终止重试并放行渲染，轮数上限 3 不放宽，trace 留可判读标记

#### Scenario: 既有轻微失败免除重试

- **WHEN** 重试启用且单轮 FAIL 数 ≤ 1 且失败率 ≤ 5%
- **THEN** 系统 SHALL 直接放行渲染并留可判读标记

#### Scenario: 未准入桶不触发重试

- **WHEN** 重试启用且 FAIL 仅落在 `CITATION_RETRY_ADMITTED_BUCKETS` 之外的桶（如 value_mismatch / direction_mismatch / path_unresolvable）
- **THEN** 系统 SHALL 放行渲染；桶 SHALL 照常计入观测指标

#### Scenario: 准入条件

- **WHEN** 拟将某失败桶加入 `CITATION_RETRY_ADMITTED_BUCKETS`
- **THEN** SHALL 先存在该桶逐条人工终裁记录（incidents 或 tests/validation）确认桶内为真错误，方可加入

#### Scenario: 重写无进展立即停

- **WHEN** 重试启用且目标分析师重跑后 markdown 哈希与重跑前一致
- **THEN** 系统 SHALL 立即放行渲染并在 trace 留下可判读标记（`citation_retry_no_progress`），SHALL NOT 等待失败率停滞判定

- **WHEN** 拟将某失败桶加入 `CITATION_RETRY_ADMITTED_BUCKETS`
- **THEN** SHALL 先存在该桶逐条人工终裁记录（incidents 或 tests/validation）确认桶内为真错误，方可加入

#### Scenario: 重写无进展立即停

- **WHEN** 重试启用且目标分析师重跑后 markdown 哈希与重跑前一致
- **THEN** 系统 SHALL 立即放行渲染并在 trace 留下可判读标记（`citation_retry_no_progress`），SHALL NOT 等待失败率停滞判定
### Requirement: 定向重试反馈携带 direction 申报提示

校验失败触发的定向重试反馈（value_mismatch / direction_mismatch 桶）与 coverage 打回（coverage_gap）SHALL 在反馈条目中携带 direction 申报提示：未申报 direction 的覆盖缺口 SHALL 提示「补登记时同步申报 direction」；direction_mismatch 的重试反馈 SHALL 携带校验器解析的真值符号，分析师 SHALL 据此修正 stated_value 与 direction 的组合而非仅改数值。

#### Scenario: direction_mismatch 重试反馈含真值符号

- **WHEN** claim 因 direction_mismatch 判 FAIL 进入定向重试
- **THEN** 反馈条目 SHALL 包含 ground_truth 数值及其符号，与 direction 申报格式示例

#### Scenario: 覆盖缺口补登记携带 direction 提示

- **WHEN** coverage 打回生成 coverage_gap 反馈条目
- **THEN** 条目 SHALL 包含 direction 申报提示字段，提示分析师补 claim 时一并申报方向

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

### Requirement: 修复调用预算记账

每次单点修复 LLM 调用 SHALL 计入管线 LLM 预算记账（与分析师/辩论调用同口径），并 SHALL 在 trace 观测中记录调用模型、输入输出 token 与修复目标 claim 标识。

#### Scenario: 修复调用进预算

- **WHEN** 一轮校验触发 2 处单点修复
- **THEN** 该 2 次 LLM 调用 SHALL 出现在预算记账与 trace usage 中

