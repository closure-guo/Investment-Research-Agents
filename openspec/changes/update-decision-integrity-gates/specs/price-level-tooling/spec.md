# Delta for price-level-tooling

## ADDED Requirements

### Requirement: 决策文本价位与已验证技术指标交叉校验

决策层产出的自由文本价位（`reeval_triggers` 条目与 `inaction_reason`/`reasoning` 中出现的数值价位）SHALL 由确定性代码与 state 中已验证的技术指标值（最新收盘价、各周期 MA、近期高低点、布林轨道）交叉核对，MUST NOT 仅因数值出现在决策文本即视为可信。以下两种形态 SHALL 各登记一条 anomaly（复用既有 anomalies 通道，附原始文本片段与最接近的已验证指标值），并在报告「再评估触发条件」/「不行动原因」旁以「价位待核实」类标注呈现：

- **偏差形态**：文本价位与语义最接近的已验证指标值偏差超过配置阈值（默认 2%），且不落在 `price_levels` 参考带内；
- **空洞形态**：上破类触发（「站上/突破/收复 X」）中 X 不高于最新收盘价，或下破类触发（「跌破/回落至 X」）中 X 不低于最新收盘价——即触发条件在当前时点已经满足，不构成有效的再评估门槛。

校验 SHALL NOT 硬中断管线：决策照常放行前进，校验结果以 anomaly + 报告标注呈现（可观测优先，与「非执行动作结构化理由契约」的放行标注语义同型）。无法与任何已验证指标建立归属对应的数值（如财报降幅阈值、赔率比值）SHALL NOT 被误报——归属匹配 SHALL 区分价格量纲与百分比/比值量纲。

#### Scenario: 触发价幻觉检测（偏差形态）

- **WHEN** watch 决策的 `reeval_triggers` 含「放量突破 MA60 并站稳 18.8 以上」，state 已验证技术指标中 MA60 = 18.066（偏差 4.1%，超过默认阈值 2%）
- **THEN** 系统 SHALL 登记一条偏差形态 anomaly，内容含触发原文、指标名（MA60）、已验证值 18.066 与偏差幅度
- **THEN** 报告该触发条目旁 SHALL 渲染价位待核实标注，标注内容含已验证值
- **AND** 决策本身 SHALL 照常放行，管线 MUST NOT 中断

#### Scenario: 空洞触发条件检测

- **WHEN** watch 决策的 `reeval_triggers` 含「价格放量站上止损参考带上沿 22.61」，最新收盘价为 23.03（高于 22.61）
- **THEN** 系统 SHALL 登记一条空洞形态 anomaly（上破触发价不高于现价，条件已满足）
- **THEN** 报告该触发条目旁 SHALL 渲染对应标注
- **AND** 决策照常放行

#### Scenario: 正常触发价直通

- **WHEN** `reeval_triggers` 中的价位与已验证指标偏差在阈值内且方向语义有效（如「跌破近期低点 22.94」且现价 23.03）
- **THEN** SHALL 不产生 anomaly，报告渲染形态与现状一致

#### Scenario: 非价格量纲数值不误报

- **WHEN** `reeval_triggers` 含「单季净利降幅收敛至 18.35% 以下」「赔率修复至 1:1」等百分比/比值表述
- **THEN** 归属匹配 SHALL 识别其为非价格量纲，SHALL NOT 按价位偏差误报 anomaly
