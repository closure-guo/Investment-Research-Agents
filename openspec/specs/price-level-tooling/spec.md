# price-level-tooling Specification

## Purpose
由 delta update-decision-integrity-gates 归档建立（2026-09-30 实施完毕并通过逐任务审查与全量回归）。
## Requirements
### Requirement: 决策文本价位与已验证技术指标交叉校验

决策层产出的自由文本价位（`reeval_triggers` 条目与 `inaction_reason`/`reasoning` 中出现的数值价位）SHALL 由确定性代码与 state 中已验证的技术指标值（最新收盘价、各周期 MA、近期高低点、布林轨道）交叉核对，MUST NOT 仅因数值出现在决策文本即视为可信。以下两种形态 SHALL 各登记一条 anomaly（复用既有 anomalies 通道，附原始文本片段与最接近的已验证指标值）：

- **偏差形态**：文本价位与语义最接近的已验证指标值偏差超过配置阈值（默认 2%），且不落在 `price_levels` 参考带内；
- **空洞形态**：上破类触发（「站上/突破/收复 X」）中 X 不高于最新收盘价，或下破类触发（「跌破/回落至/回撤至/回调至 X」）中 X 不低于最新收盘价——即触发条件在当前时点已经满足，不构成有效的再评估门槛。

**复合条件豁免**：同一触发条目内同一数值同时出现下破语境与上破语境（「先跌破/回撤至 X，再站稳/站上 X」的回踩确认结构）时，该数值 SHALL NOT 判空洞形态——两个子条件串联（价格须先跌破 X 才谈得上站稳 X），整体构成前瞻有效门槛，MUST NOT 因后半句句法命中上破模式而误判。

anomaly 的处置 SHALL 为门禁语义，准入按恶化判据分层（update-decision-price-gate-admission，owner 终裁已批）：

- risk_judge 校验出非空 anomaly 时，SHALL 携 anomaly 明细（原始文本片段、指标名、已验证值、偏差幅度）**定向打回重试一次**，要求修正文本价位后重新输出完整决策 JSON；
- 重试输出修正（复检无 anomaly）→ 决策放行进 Fund Manager 审批，gate 结果与「打回后已修正」复核标注落 state；
- 重试后残留 anomaly **未恶化**（残留条数不超过首次，且残留的每个 `source_text` 均在首次 anomaly 集合内）→ 决策 SHALL **放行**进 Fund Manager 审批：gate 结果记 pass，note 如实标注「打回后残留 N 条未清零（未恶化，放行待人工终裁）」，残留 anomaly 照落 `decision_price_anomalies`（state/Langfuse trace 可观测）——未知语义盲区的代价由观测通道承担，MUST NOT 因校验器无法识别的合理申辩拒绝交付；
- 重试后残留 anomaly **恶化**（条数增加，或出现首次集合外的新 `source_text`）→ 管线 SHALL 阻断：MUST NOT 进入 Fund Manager 审批、MUST NOT 产出报告，阻断原因与 anomaly 明细落 state/Langfuse trace；
- 报警信息属于内部 trace：报告 SHALL NOT 渲染「价位待核实」类标注（无论打回修正与否），anomaly 明细仅经 state/Langfuse trace 可观测。

无法与任何已验证指标建立归属对应的数值（如财报降幅阈值、赔率比值、风险度量 VaR/在险价值语境的置信水平数字）SHALL NOT 被误报——归属匹配 SHALL 区分价格量纲与百分比/比值量纲。校验器自身抛出异常时 SHALL fail-open（放行决策并告警日志），MUST NOT 因校验器缺陷阻断管线；校验器无任何已验证指标可核对时 SHALL 直通（不凭空报 anomaly）。

#### Scenario: 触发价幻觉检测（偏差形态）

- **WHEN** risk_judge 终稿的 `target_price` 相关 reasoning 含文本价位 95（如「目标价看到 95 元附近」，量纲与现价同数量级），state 已验证指标中近期低点 = 570、最新收盘 = 640（偏差远超 2% 阈值且不在 price_levels 参考带内）
- **THEN** 系统 SHALL 登记一条偏差形态 anomaly，内容含触发原文、指标名、已验证值与偏差幅度
- **AND** anomaly 明细 SHALL 仅落 state/Langfuse trace，报告 MUST NOT 渲染「价位待核实」标注

#### Scenario: 空洞触发条件检测

- **WHEN** watch 决策的 `reeval_triggers` 含「价格放量站上止损参考带上沿 22.61」，最新收盘价为 23.03（高于 22.61）
- **THEN** 系统 SHALL 登记一条空洞形态 anomaly（上破触发价不高于现价，条件已满足）
- **AND** 该 anomaly 与偏差形态同门禁处置（打回重试一次 → 修正放行 / 未恶化放行 / 恶化阻断）

#### Scenario: 正常触发价直通

- **WHEN** `reeval_triggers` 中的价位与已验证指标偏差在阈值内且方向语义有效（如「跌破近期低点 22.94」且现价 23.03）
- **THEN** SHALL 不产生 anomaly、不触发打回，决策照常进入审批（管线行为与既有直通一致）

#### Scenario: 非价格量纲数值不误报

- **WHEN** `reeval_triggers` 含「单季净利降幅收敛至 18.35% 以下」「赔率修复至 1:1」等百分比/比值表述，或 `reasoning` 含「VaR95单日6.6%」「VaR(95%置信)」「在险价值95」等风险度量语境
- **THEN** 归属匹配 SHALL 识别其为非价格量纲/风险度量语境，SHALL NOT 按价位偏差误报 anomaly

#### Scenario: 复合回踩触发不判空洞

- **WHEN** watch 决策的 `reeval_triggers` 含「价格回撤至610以下且连续5日收盘站稳610并缩量企稳」（或「需实际跌破610后连续5日收盘站稳610之上」），最新收盘价为 640（高于 610）
- **THEN** 610 因同条目内同时出现下破语境（回撤至/跌破）与上破语境（站稳）SHALL 被识别为回踩确认结构，SHALL NOT 登记空洞形态 anomaly
- **AND** 该条目其他数值的偏差/空洞校验照常进行

#### Scenario: 偏差异常打回重试后修正放行

- **GIVEN** 首次校验登记非空 anomaly（偏差形态或空洞形态）
- **WHEN** risk_judge 携 anomaly 四要素明细定向打回重试恰一次，重试输出的复检无 anomaly
- **THEN** 决策 SHALL 放行进入 fund_manager 审批，gate 结果记「打回后已修正」
- **AND** 最终报告 MUST NOT 出现「价位待核实」标注或任何报警文本

#### Scenario: 打回后残留未恶化放行待终裁

- **GIVEN** 首次校验登记 1 条 anomaly（source_text 为 S1），打回重试已执行
- **WHEN** 重试输出的复检残留 1 条 anomaly 且其 source_text 仍为 S1（LLM 坚持原文本未恶化，如校验器无法识别的复合语义申辩）
- **THEN** 决策 SHALL 放行进入 fund_manager 审批，gate 结果记 pass，note 含「未恶化」「放行待人工终裁」与残留条数
- **AND** 残留 anomaly SHALL 照落 `decision_price_anomalies`（trace 可观测），报告 MUST NOT 渲染报警文本

#### Scenario: 打回后仍异常阻断交付

- **GIVEN** 首次校验登记 1 条 anomaly（source_text 为 S1），打回重试已执行
- **WHEN** 重试输出的复检残留 2 条 anomaly，或残留 anomaly 的 source_text 为首次集合外的新文本（重试引入新错误）
- **THEN** 管线 SHALL NOT 进入 fund_manager 审批节点，SHALL NOT 产出最终报告
- **AND** 阻断原因与残留 anomaly 明细 SHALL 落 state（Langfuse trace 可查）
- **AND** 会话终态处置符合 pipeline-events「管线阻断终态可见化」

#### Scenario: 校验器自身异常 fail-open

- **WHEN** `check_decision_prices` 自身抛出异常（校验器缺陷或 state 形态噪声）
- **THEN** 管线 SHALL 放行决策（fail-open）并记录告警日志
- **AND** 决策照常进入审批，MUST NOT 因校验器异常阻断

### Requirement: 结构化触发位方向校验

`TradeDecision` 的结构化触发位（`trigger_high`/`trigger_low`）SHALL 由确定性代码与最新收盘价做方向校验，复用既有空洞形态语义：`trigger_high` ≤ 最新收盘（上破门槛在当前时点已经满足）或 `trigger_low` ≥ 最新收盘（下破门槛已经满足）时，SHALL 各登记一条 `empty_trigger` anomaly（附字段名、申报值、最新收盘价）。校验 MUST NOT 依赖对 `reeval_triggers` 文本的解析——结构化字段直接消费。

anomaly 处置 SHALL 完全复用既有门禁分层（定向打回重试一次 → 修正放行 / 未恶化放行待人工终裁 / 恶化阻断），与文本价位校验的 anomaly 共用同一通道与同一打回预算（合计一次打回，MUST NOT 因结构化与文本两类 anomaly 分别打回形成多重打回循环）。文本 `reeval_triggers` 的既有偏差/空洞校验语义 SHALL 保持不变。

校验器自身异常 SHALL fail-open（放行 + 告警日志）；无已验证最新收盘价可核对时 SHALL 直通。

#### Scenario: 上破触发位空洞检测

- **WHEN** watch 终稿申报 `trigger_high=22.61`，最新收盘价为 23.03（高于 22.61）
- **THEN** 系统 SHALL 登记一条空洞形态 anomaly（上破触发价不高于现价，条件已满足）
- **AND** 该 anomaly 与既有文本校验 anomaly 同门禁处置（共用一次打回预算）

#### Scenario: 下破触发位空洞检测

- **WHEN** watch 终稿申报 `trigger_low=24.60`，最新收盘价为 23.03（低于 24.60）
- **THEN** 系统 SHALL 登记一条空洞形态 anomaly（下破触发价不低于现价，条件已满足）

#### Scenario: 有效双向触发位直通

- **WHEN** watch 终稿申报 `trigger_high=24.6`、`trigger_low=22.91`，最新收盘价为 23.03
- **THEN** SHALL 不产生 anomaly、不触发打回，决策照常进入审批

#### Scenario: 无收盘价可核对直通

- **WHEN** 已验证指标中无可用最新收盘价（K 线缺失且无 entry_ref 回退）
- **THEN** 结构化触发位校验 SHALL 直通，MUST NOT 凭空报 anomaly

