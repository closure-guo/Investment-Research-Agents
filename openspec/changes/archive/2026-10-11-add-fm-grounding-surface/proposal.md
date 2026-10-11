# Proposal: add-fm-grounding-surface

## Why

issue #242 断点 3（光大 601818 报告审计）：FM 上下文只有决策结构四检查 + risk_metrics，拿不到估值完整性（PE 缺失原因）、口径一致性（披露节 vs 叙事）、辩论锚点告警——估值缺席/图文矛盾/口径冲突均可无阻通过审批。

同 issue 断点 2 的 delta `add-anchor-value-grounding` 已产出确定性锚点告警信号（`debate_anchor_checks` 的 `matched_via` / `echo_only_field_refs` / `value_mismatch` 与 stats 新桶），但零消费面（Produces 接口必须有消费任务）。本 delta 是其消费面。

## What Changes

1. **FM 上下文增三段**（`_build_fund_manager_context`，数据段非空才出现）：
   - 估值完整性标注：`valuation_snapshot.missing_reasons` 非空时列出缺失原因
   - 辩论锚点告警：`debate_anchor_checks` 汇总（unresolved 计数、value_mismatch 计数、echo_only_field_refs），逐条列出违规项（上限 5 条）
   - 数据口径披露原文：`_format_freshness_section(state)` 渲染结果注入（确定性计算数字，供 FM 交叉核对决策数字）
2. **FM 提示词增「审批前核查」段**：上下文出现上述标注/告警/披露段时的回应义务——估值完整性标注出现时须在 reasoning 中说明估值论断的依据边界或考虑 return；锚点告警出现时列为论据矛盾候选；披露段用于交叉核对。**仲裁权保留**：标注/告警不禁止 approve，但 reasoning MUST 回应（为何放行）。
3. fail-open 语义延续：上下文增段不改变路由，FM 仍独立裁决。

## Impact

- Affected specs: `agent-node-contracts`（MODIFIED「FM 审批对象完整性可见性」）、`agent-prompt-contracts`（MODIFIED「风控与审批评级量表」）
- Affected code: `src/finance_agent/nodes/fund_manager.py`、`src/finance_agent/prompts/fund_manager.md`（合并后须执行 prompt deploy）
- 不改：报告渲染（披露节已在报告内；标注仅进 FM 上下文）、路由逻辑、FM 决策 schema
