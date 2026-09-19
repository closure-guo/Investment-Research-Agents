# Tasks: eval-driven-contract-fixes

## 1. 字段解析消歧（citation-verification）

- [x] 1.1 失败测试：同义列名场景（官方全名列 + 短名列并存、数值不同）——短名 field_ref 取全名列真值；无全名可消歧时判 blocked（进解析桶，不产 value_mismatch）
- [x] 1.2 实现别名映射表 + 校验器取值路径消歧（含负索引路径），四桶拆报接入 blocked 新因由（column_ambiguity）

## 2. claim 值槽类型校验（citation-verification）

- [x] 2.1 失败测试：interpretation 含环比/同比/变化词 + 数值与 field_ref 真值量级不符 → 判 `claim_contract_error` 进解析桶、不进 value_mismatch、不触发修复
- [x] 2.2 实现前置类型校验（双信号判据，单一信号不判），校验报告单列呈现

## 3. 修复记账按 claim（citation-retry-policy / incident 029）

- [x] 3.1 失败测试：claim A 修复改对但同分析师 claim B 仍 FAIL（all_passed=False）→ A 计入 `value_mismatch_repaired_claims`
- [x] 3.2 实现按 claim 记账 + 旧字段标 deprecated（保留一轮）；metrics.md §1.7 A4 切点标注

## 4. 辩手提示词断言级锚定（agent-prompt-contracts）

- [x] 4.1 失败测试：三份辩手提示词含逐断言锚定要求 + 至少一个「推断冒充 data」反例判例（grounding 终裁 8 条提炼）
- [x] 4.2 修改 `src/finance_agent/prompts/*.md` 并执行 `uv run python scripts/deploy_prompts.py` 发布（eval 门禁要求）

## 5. 验证与收口

- [x] 5.1 全套单测 + ruff + mypy 过；既有四桶/重试语义回归不红
- [x] 5.2 用 601318 / 600276 两个终裁案例做验收夹具（假 FAIL 复现 → 修复后判 blocked/contract_error）
- [x] 5.3 validation 报告：四件修复对照终裁证据的验证记录 + 人工验证报告落 `tests/validation/`

## 6. 交易指令赔率自检（derived-risk-metrics，2026-09-19 owner 批）

- [x] 6.1 失败测试：reasoning 含「赔率约1.7:1」而代码计算 1.24 → 原位修正为 1.24:1 + telemetry；容差内不改；派生值缺失跳过
- [x] 6.2 实现确定性自检函数（纯字符串替换，无 LLM）并接入 trader / fund_manager 产出路径
- [x] 6.3 用 601899 / 000333 两个终裁案例做验收夹具
