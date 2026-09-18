# 验证报告：eval-driven-contract-fixes（2026-09-18）

四件契约修复对照终裁证据的验证记录。全程 TDD（先红后绿）；证据链见
`tests/validation/2026-09-16-p1-injection-pilot-validation.md` §19.5/§19.6（grounding 终裁 1.000、A4 自然腿 2/2 误报）。

## 1. 同义列名消歧（commit c2fbac2）

- **终裁证据**：601318 利润表「归属于母公司的净利润」(1347.78亿) 与「归母净利润」(235.23亿) 并存，校验器短名精确命中错列产假 FAIL（owner 终裁=误报）。
- **实现**：`_disambiguate_column`——canonical 等价多列并存取最长列名（官方全名）；无匹配返回 None 走既有不可验证→blocked。
- **验证**：`TestSynonymColumnDisambiguation` 4 用例（短名重定向/全名直取保持/单列不受影响/省行键路径同消歧）全绿；601318 案例即夹具。

## 2. claim 值槽类型校验（commit dfad1d3）

- **终裁证据**：600276 PMI claim 变化量 0.6 填水平值槽 49.8，正文算术正确（owner 终裁=误报）。
- **实现**：双信号判据（`环比/同比…个百分点`措辞 + 与真值量级差>10 倍）→ `claim_contract_error` 桶；单信号不判（变化量字段合法 claim 不误伤）。新桶不进 value_mismatch（幻觉口径）、天然绕开修复与定向重试（触发只认 value/direction_mismatch）；escape 拆报枚举扩展为五桶。
- **验证**：`TestClaimContractError` 3 用例 + `TestClaimContractErrorBucket` 全绿；600276 案例即夹具。

## 3. 修复记账按 claim（commit a5c8b5b，incident 029 处置）

- **终裁证据**：A4 实测数值改对 20/20、旧口径记账 4/22（all_passed 漏计）。
- **实现**：`value_mismatch_repaired_claims`（重校验后目标 claim PASS 即计）上线并接管 `citation_analyst_true_fail`；旧字段 deprecated 保留一轮。行为不变：残余 FAIL 照走全量定向重试。
- **验证**：`test_per_claim_accounting_when_unrelated_fail_blocks_all_passed`（修复改对目标 claim + 同分析师无关 FAIL → 新口径计 1、旧口径 0、仍进 retry_targets）全绿；口径切点已落 metrics.md §1.7。

## 4. 辩手断言级锚定（commit ed2bc75）

- **终裁证据**：grounding 扫描无源率 8/64=12.5%（owner 终裁一致率 1.000），共性=推断冒充 data。
- **实现**：三份辩手提示词（bear/bull/risk）data 论点收紧为逐事实断言锚定（多断言拆条、无法锚定标 inference）+ 反例判例（「均线死叉说明机构资金撤离」类）；`deploy_prompts.py` 已发布 production 标签。
- **验证**：`test_assertion_level_anchor_rule_present` 3 用例（每份提示词）全绿；既有锚点契约测试不回归。

## 回归与遗留

- 受影响测试区全绿：citation 族 + causal_ablation（500+）+ debate prompts（27）+ citation_node（含修复记账）；mypy/ruff 全过。
- 全套后端回归已完成（41 分钟，剔除 4 个 live 网络测试）：**2991 过 + 24 失败**；24 失败
  全部同源——任务 2 新桶未传播进 `evals/ablation` 的桶抽取与方向表（第五桶传播缺口，
  commit 73c6bfa 修复：抽取器补 `citation_fail_buckets` 源、方向表登记 True、24 处精确
  断言更新）。修复后 tests/evals 全量 **1049 过**，citation/node/prompt 区 120 过。
  4 个 live 测试为网络环境既有失败（与本次无关）。
- 提示词收紧效果验证：下一轮 P2 材料跑批后复扫 grounding（预期无源率显著下降），登记为下轮验证点。
