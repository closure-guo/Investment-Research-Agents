## ADDED Requirements

### Requirement: 单点修复的自动处置授权与边界

`value_mismatch` 稀疏失败的单点修复属于**真错桶的受护栏自动处置**：下列三条护栏全部成立时，SHALL 免于「自动化处置须以逐条人工终裁为前置」的一般要求（AGENTS.md 红线，incident 026）；任一护栏失效 SHALL 视为策略变更（须走 delta）并恢复终裁前置。

1. **不隐藏真错**：修复回填条数 SHALL 计入 `citation_analyst_true_fail`（修复会把真错藏进 PASS，真错口径必须包含它们），并 SHALL 单独计数为 `citation_surgical_repaired` 供观测修复触发与成功率。
2. **重校验仲裁**：修复回填后 SHALL 立即整体重校验，并以重校验结果为该轮最终状态（改写引入的新错照常 FAIL，SHALL NOT 因「已修复过」放行）。
3. **最小改写 + 可审计**：修复指令 SHALL 限制「只改错误数字及其直接联动措辞、禁止改写其他内容、禁止引入新数字」；每次修复 SHALL 在 trace 留痕（修前句/修后句/ground_truth/重校验结果）。

**触发边界**：SHALL 仅当同一分析师同一轮 `value_mismatch` 失败数 < 3（稀疏）时发起；≥3 处 SHALL NOT 单点修复。自动重试关闭态下修复失败 SHALL 直接按阻断标记放行（`citation_blocked` 置位），SHALL NOT 触发分析师重跑。

**变更纪律**：放宽稀疏阈值、移除真错口径、跳过强制重校验、允许修复引入新数字——任一 SHALL 先经 delta 修改本要求，SHALL NOT 作为实现细节直接调整。

#### Scenario: 稀疏失败触发且计入真错

- **GIVEN** 某分析师同轮 1 处 value_mismatch
- **WHEN** 单点修复成功且重校验 PASS
- **THEN** `citation_surgical_repaired` SHALL +1
- **AND** `citation_analyst_true_fail` SHALL 同时计入该条（不因修复而消失）

#### Scenario: 密集失败不触发

- **WHEN** 同一分析师同轮 value_mismatch ≥ 3 处
- **THEN** 系统 SHALL NOT 发起单点修复，按既有重试/放行路由处理

#### Scenario: 护栏变更须走 delta

- **WHEN** 需求方要放宽稀疏阈值、移除真错口径或跳过强制重校验
- **THEN** SHALL 先开 delta 修改本要求，SHALL NOT 作为实现细节直接调整
