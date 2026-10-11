# add-fm-grounding-surface delta — agent-node-contracts

## MODIFIED Requirements

### Requirement: FM 审批对象完整性可见性

FM 节点构建 LLM 上下文时，SHALL 包含终稿完整性检查的标注结果（`final_price_check` / `final_inaction_check` / 终稿再评估触发条件检查的 note，如「已打回仍未申报」），使 FM 在审批时能看到审批对象的结构完整性状态，MUST NOT 在不知情的情况下对结构不完整的方案作出完备性论断。

FM 节点构建 LLM 上下文时，SHALL 额外包含以下三类 grounding 输入（各段非空才出现，MUST NOT 输出空段）：

1. **估值完整性标注**：`valuation_snapshot.missing_reasons` 非空时，列出全部缺失原因，使 FM 知晓估值数据缺席（PE 缺失原因等）——估值相关论断缺乏确定性数据支撑。
2. **辩论锚点告警**：`debate_anchor_checks` 的确定性汇总——`value_mismatch`（文本数字不可溯源）、`echo_only_field_refs`（field 形态锚仅回声命中）与 `data`/`event` 型 unresolved/missing 计数；违规项逐条列示（role/round/index/anchors/status，上限 5 条，超出部分计数汇总），全零 MUST NOT 出现该段。
3. **数据口径披露原文**：`_format_freshness_section(state)` 的渲染结果整段注入（管线确定性计算的快照/估值/健康度/风险指标/价位参考），供 FM 交叉核对决策数字与确定性计算的一致性——口径冲突由 FM 审批语义层判断，MUST NOT 在程序层解析叙事文本做一致性比对。

以上标注/告警/披露段 SHALL fail-open：MUST NOT 改变路由、MUST NOT 自动触发退回——FM 仲裁权保留（可见性义务优先），但 FM 提示词 SHALL 要求在 reasoning 中显式回应出现的标注/告警（为何放行或作为退回依据）。

报告「基金经理决策」节 SHALL 在 FM 审批意见之外并排渲染其审批对象携带的结构不完整标注（存在时），使「方案事实」与「FM 论断」的矛盾直接可见，MUST NOT 只呈现 FM 的完备性论断。本变更的估值完整性标注/锚点告警仅进 FM 上下文，SHALL NOT 新增报告渲染（披露节原文已在报告内呈现）。

#### Scenario: FM 上下文携带完整性标注

- **WHEN** `final_trade_decision` 为 watch 且 `reeval_triggers` 经打回后仍缺失（携带「已打回仍未申报」标注），FM 节点构建上下文
- **THEN** FM 的 LLM 上下文 SHALL 包含该标注原文
- **AND** FM MUST NOT 因上下文含标注而被禁止 approve（仲裁权保留，可见性义务优先）

#### Scenario: FM 上下文携带估值完整性标注

- **GIVEN** `valuation_snapshot.missing_reasons` = ["market_cap 缺失", "快照缺失"]
- **WHEN** FM 节点构建上下文
- **THEN** FM 的 LLM 上下文 SHALL 含「估值完整性标注」段且列出两条缺失原因
- **WHEN** `valuation_snapshot` 缺失或 `missing_reasons` 为空
- **THEN** 上下文 MUST NOT 出现该段

#### Scenario: FM 上下文携带辩论锚点告警

- **GIVEN** `debate_anchor_checks` 含一条 `status="value_mismatch"` 记录（bull R1 #2，anchors=["fundamental.中报净利润同比"]）
- **WHEN** FM 节点构建上下文
- **THEN** FM 的 LLM 上下文 SHALL 含「辩论锚点告警」段且逐条列示该记录（role/round/index/anchors/status）
- **AND** `debate_anchor_checks` 为空或全部信号为零时，上下文 MUST NOT 出现该段

#### Scenario: FM 上下文携带数据口径披露原文

- **WHEN** `_format_freshness_section(state)` 返回非 None，FM 节点构建上下文
- **THEN** FM 的 LLM 上下文 SHALL 包含该披露节原文（确定性计算数字供交叉核对）
- **AND** 返回 None 时上下文 MUST NOT 出现空段

#### Scenario: 标注与告警不改变路由与仲裁权

- **WHEN** 估值完整性标注、锚点告警、披露段任一非空
- **THEN** 图路由 SHALL 与全空时完全一致，MUST NOT 自动退回或阻断
- **AND** FM 仍可 approve（仲裁权保留）

#### Scenario: 报告并排渲染不完整标注与 FM 论断

- **WHEN** FM decision 为 approve，且其审批对象携带结构不完整标注（如理由检查「已打回仍未申报」）
- **THEN** 报告「基金经理决策」节 SHALL 先渲染结构不完整标注（含缺失项），再渲染 FM 审批意见
- **AND** 报告 MUST NOT 仅呈现 FM 的完备性论断而隐去标注

#### Scenario: 方案完整时不产生额外渲染

- **WHEN** `final_trade_decision` 通过全部完整性检查（无标注）
- **THEN** 报告与 FM 上下文 SHALL 维持现状形态，MUST NOT 出现空标注行
