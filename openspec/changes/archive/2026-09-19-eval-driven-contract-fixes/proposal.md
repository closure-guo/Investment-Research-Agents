# 变更提案：eval-driven-contract-fixes

## Why

2026-09-18 消融评估产出四组**经人工终裁确认**的缺陷证据：辩论层无源断言率 12.5%（grounding 扫描 8/64，owner 终裁一致率 1.000）、字段解析同名异义列假 FAIL（601318，owner 终裁=误报）、claim 值槽类型错填假 FAIL（600276，owner 终裁=误报）、A4 修复记账与收益脱钩（实测改对 20/20 vs 记账 4/22，incident 029）。四组证据均已终裁成桶，按 SOP（先分桶后终裁、处置挂终裁桶）进入处置。

## What Changes

- **辩手锚点纪律收紧**：`data` 型论点从「附至少 1 个锚点」收紧为「**逐事实断言**可锚定」——一条论点含多个事实断言时，每个断言须有对应锚点或多断言拆分；无法锚定的推断性表述必须标 `inference`。提示词补「推断冒充数据」判例（8 条终裁案例中提炼）。
- **字段解析消歧**：校验器按 field_ref 取值/重算时，遇**同义列名歧义**（如利润表同时存在「归属于母公司的净利润」与「归母净利润」且数值不同）SHALL 消歧（官方科目全名优先 + 歧义时标记 blocked 而非取错值静默通过）。
- **claim 值槽类型校验**：数值型 claim 的 stated_value 语义类型（水平值 level / 变化量 delta）SHALL 与 field_ref 所指字段一致；不一致 SHALL 判契约错误进解析桶，SHALL NOT 计入 value_mismatch（分析师幻觉口径）。
- **A4 修复记账按 claim**：`value_mismatch_repaired` 计数从「按分析师 all_passed」改为「**按单条 claim** 修好即记」；新增 per-claim 记账字段，旧口径字段保留但标注 deprecated（incident 029 处置）。

## Capabilities

### New Capabilities

（无）

### Modified Capabilities

- `agent-prompt-contracts`：辩论者锚点纪律从论点级收紧到断言级（既有 Requirement「辩论者对抗性指令」的锚点申报段）
- `citation-verification`：新增字段解析消歧要求；新增 claim 值槽类型校验要求（挂既有「计算型声明重算注册表全覆盖」同节的 claim 契约族）
- `citation-retry-policy`：修复记账口径按 claim（既有 Requirement「value_mismatch 单点修复前置分支」的记账语义补充）

## Impact

- 代码：`src/finance_agent/prompts/*.md`（辩手提示词，须走 deploy_prompts.py）、校验器字段解析、claim 校验分桶、修复记账
- 兼容性：`value_mismatch_repaired` 旧语义字段保留并标 deprecated，新增 `value_mismatch_repaired_claims`；不破坏既有 trace 契约
- 评估影响：消融 A4 主指标（修复前后真 FAIL 率差）读数口径随之修正，跨口径比较须标注切点
