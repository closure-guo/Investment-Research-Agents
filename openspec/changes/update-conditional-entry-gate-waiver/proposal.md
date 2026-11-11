# Proposal: update-conditional-entry-gate-waiver

## Why

601066（中信建投 2026-11-11 cohort）产生体系首笔 buy，四项结构性缺陷实证了决策层契约的三处漏洞与一处审计缺口：

1. **入场形态无契约**：buy 决策 `trigger_high/trigger_low` 均为 null（立即市价入场），而技术面明确逆势（MA20/MA60 下方、中期空头排列）且论点兑现期（跨 2026 年报）显著长于 T+20 考核窗——schema 现成的条件入场载体（trigger 字段）在 buy 场景零使用，「等确认再进」的工具箱里有钥匙但没人用。
2. **赔率闸被重新归类架空**：保守方三项执行参数异议（赔率 1.25:1 低于 2:1 门槛、止损窄于 VaR、Q3 未验证）被终稿定性「执行参数问题」后，既未走①联合修正也未走②显式缺口申报，而是以「不构成『可执行参数缺口』」话术直接输出 buy——现行条款只禁「既不修正也不申报缺口就输出 hold/watch」，**输出 buy 且带着未处置缺口是字面漏洞**。
3. **方位采纳不可审计**：FM 声称「采纳中性方框架」，但中性方两项直接改善期望值的建议（入场压至 21.4–21.5 等回踩、目标拆 22.5/23.0 两档）被无声丢弃——只引总结论不逐项清单，选择性采纳无法从 reasoning 复盘。

## What Changes

- **trader/risk_judge 提示词**：新增 buy 入场形态申报契约——立即市价 vs 条件入场（trigger 字段承载，语义复用 watch 触发位口径）；技术逆势（收盘低于 MA20 且 MA60）或论点兑现期显著长于考核窗时 SHOULD 默认条件入场，选立即入场 MUST 显式申报理由
- **risk_judge 提示词 + spec**：堵住否决纪律的字面漏洞——「既不修正也不申报缺口」的禁令覆盖**所有 action**（含 buy/sell）；弃权修正（以仓位/复核机制承担缺口而不改参数）MUST 归入②显式缺口申报，MUST NOT 以「不构成缺口」话术绕开
- **fund_manager 提示词 + spec**：方位采纳清单义务——引用任何方位结论时 MUST 逐项列明采纳与拒绝的具体主张，MUST NOT 仅复述总结论
- **risk_judge 节点软标注**（I-1 模式，不打回不阻断）：终稿为 buy、未申报触发位、且收盘低于 MA20 与 MA60 → 如实标注「逆势立即入场未申报条件触发位」，进 FM 审批上下文供人工复核

**BREAKING**: 无（全部为新增义务与软标注；schema 零改动——trigger 字段语义扩展复用既有字段与清洗规则）

## Capabilities

- **Modified Capabilities**: `agent-prompt-contracts`（Trader 决策语义契约 / 决策层否决纪律与参数协商 / 风控与审批评级量表-FM 审批理由段）、`agent-node-contracts`（watch 双向触发位结构化契约）

## Impact

- `src/finance_agent/prompts/trader.md`、`risk_judge.md`、`fund_manager.md`（修改后 MUST 执行 `deploy_prompts.py` 发布）
- `src/finance_agent/nodes/risk.py`（终稿收口段新增软标注键 `final_entry_form_check`）
- 评估侧：cohort judge 的 buy 样本将开始出现「入场形态」与「弃权申报」审查点（现有 rubric 的 reasoning 完整性维度天然覆盖，无需改 rubric）
