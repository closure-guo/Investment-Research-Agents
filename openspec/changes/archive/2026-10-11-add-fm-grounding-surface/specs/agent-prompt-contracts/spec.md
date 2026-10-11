# add-fm-grounding-surface delta — agent-prompt-contracts

## MODIFIED Requirements

### Requirement: 风控与审批评级量表

risk_judge、fund_manager 提示词 MUST 提供评级/决策选项的语义说明，指导在证据均衡或不足时如何取舍。fund_manager 提示词 MUST 额外定义审批理由的职责边界：approve 理由限定于风控结论一致性、论据矛盾处理与执行前提；MUST NOT 对标的作方向性投资判断（如「适合长期价值投资」——该类判断属研究层，FM 无分析师报告输入而无依据）。

fund_manager 提示词 MUST 含**审批前核查**条款（条件式表述——上下文出现对应段时才生效）：上下文含「终稿完整性标注」「估值完整性标注」「辩论锚点告警」任一段时，`reasoning` MUST 显式回应出现的每一段（为何放行、或作为 reject/return 的依据），MUST NOT 对出现的标注/告警保持沉默；上下文含「数据口径披露」段时，SHALL 将其作为决策数字与管线确定性计算的交叉核对基准，发现口径冲突时 MUST 在 reasoning 中说明。审批前核查条款 MUST NOT 禁止 approve（仲裁权保留）。

#### Scenario: Risk Judge 输出评级量表决策

- **WHEN** 加载 risk_judge 提示词
- **THEN** 模板中包含 buy/sell/hold/watch 的语义与取舍指导（如证据均衡时倾向 hold/watch）

#### Scenario: Risk Judge 论据引用可标注风险层来源

- **WHEN** Risk Judge 采纳或校准了来自激进/保守/中性方风险辩论或风控指标的论据
- **THEN** 提示词 SHALL 允许 evidence_refs 的 source 取 `risk_aggressive`/`risk_conservative`/`risk_neutral`/`risk_metrics`（`RISK_EVIDENCE_SOURCES` ⊇ `TRADE_EVIDENCE_SOURCES`），且 SHALL 要求这类论据列入 evidence_refs 而非只出现在 reasoning 中
- **AND** trader 提示词的来源集保持 `TRADE_EVIDENCE_SOURCES`（Trader 在风险辩论之前，不得引用风险方）

#### Scenario: Fund Manager 审批决策语义

- **WHEN** 加载 fund_manager 提示词
- **THEN** 模板中包含 approve/reject/return 三种决策的适用语义说明
- **AND** 模板中包含 approve 时输出 action（操作定性，枚举同交易方案）与 confidence（0-1）的格式要求及语义说明（action 是对最终方案的操作定性，非对裁决的赞成/否决票）

#### Scenario: Fund Manager 审批理由职责边界

- **WHEN** 加载 fund_manager 提示词
- **THEN** 模板中包含 approve/return/reject 理由的职责边界条款（限定于风控结论一致性、论据矛盾处理、执行前提）
- **AND** 模板中包含禁止方向性投资判断的反例表述（如不得出现「适合长期价值投资/买入」类背书）

#### Scenario: Fund Manager 审批前核查条款

- **WHEN** 加载 fund_manager 提示词
- **THEN** 模板中包含审批前核查条款：上下文出现完整性标注/估值完整性标注/辩论锚点告警时 reasoning MUST 显式回应，出现数据口径披露段时作为交叉核对基准
- **AND** 条款 MUST NOT 出现「禁止 approve」类表述（仲裁权保留）
