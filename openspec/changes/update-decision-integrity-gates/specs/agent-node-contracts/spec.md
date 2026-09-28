# Delta for agent-node-contracts

## ADDED Requirements

### Requirement: FM 审批对象完整性可见性

FM 节点构建 LLM 上下文时，SHALL 包含终稿完整性检查的标注结果（`final_price_check` / `final_inaction_check` / 终稿再评估触发条件检查的 note，如「已打回仍未申报」），使 FM 在审批时能看到审批对象的结构完整性状态，MUST NOT 在不知情的情况下对结构不完整的方案作出完备性论断。报告「基金经理决策」节 SHALL 在 FM 审批意见之外并排渲染其审批对象携带的结构不完整标注（存在时），使「方案事实」与「FM 论断」的矛盾直接可见，MUST NOT 只呈现 FM 的完备性论断。

#### Scenario: FM 上下文携带完整性标注

- **WHEN** `final_trade_decision` 为 watch 且 `reeval_triggers` 经打回后仍缺失（携带「已打回仍未申报」标注），FM 节点构建上下文
- **THEN** FM 的 LLM 上下文 SHALL 包含该标注原文
- **AND** FM MUST NOT 因上下文含标注而被禁止 approve（仲裁权保留，可见性义务优先）

#### Scenario: 报告并排渲染不完整标注与 FM 论断

- **WHEN** FM decision 为 approve，且其审批对象携带结构不完整标注（如理由检查「已打回仍未申报」）
- **THEN** 报告「基金经理决策」节 SHALL 先渲染结构不完整标注（含缺失项），再渲染 FM 审批意见
- **AND** 报告 MUST NOT 仅呈现 FM 的完备性论断而隐去标注

#### Scenario: 方案完整时不产生额外渲染

- **WHEN** `final_trade_decision` 通过全部完整性检查（无标注）
- **THEN** 报告与 FM 上下文 SHALL 维持现状形态，MUST NOT 出现空标注行

## MODIFIED Requirements

### Requirement: 非执行动作结构化理由契约

`TradeDecision` SHALL 支持 `inaction_reason`（string）与 `reeval_triggers`（string 列表）两个结构化字段，分别承载「不行动原因」与「可观察的再评估触发条件」。action 为 watch 或 hold（非执行动作）时 SHALL 申报两者；**action 为 buy/sell 时 `inaction_reason` 无约束（维持现状），但 `reeval_triggers` SHALL 同样申报**——601818 实证：sell 终稿无任何再评估触发条件，保守方与中性方两轮辩论均点名「reeval_triggers 为空是纪律性缺陷」，FM 仍以「执行安排完备」批准，退出后监控机制缺失。输入清洗 SHALL 与既有先例一致：`reeval_triggers` 为 `None`/非列表时归一为 `[]`，单字符串归一为单元素列表，非字符串条目丢弃；`inaction_reason` 为纯空白等同缺失。清洗 MUST NOT 抛解析异常中断管线。

终稿再评估触发条件必填化的重试语义 SHALL 只作用于 risk_judge 终稿（与 `final_price_check` 同款一次打回）：buy/sell 终稿缺 `reeval_triggers` 时打回一次，feedback 引用风险辩论中已提出的触发条件线索；仍缺失 SHALL 放行 + `final_reeval_check` note 如实标注「已打回仍未申报」。Trader 侧 plan 的 buy/sell 维持现状直通（缺口在终稿层收口，避免双点位校验叠加打回循环）。

(Previously: action 为 buy/sell 时 `inaction_reason` 与 `reeval_triggers` 均无约束，理由检查仅作用于 watch/hold——buy/sell 终稿可以没有任何再评估触发条件而不产生任何标注。)

#### Scenario: 非执行动作缺理由打回一次后放行

- **WHEN** trader 产出的 plan 为 watch 或 hold，且 `inaction_reason` 或 `reeval_triggers` 缺失
- **THEN** 价位 sanity 校验节点 SHALL 判定理由检查 fail 并打回 trader 一次，feedback SHALL 列出缺失项并要求结构化申报
- **AND** 打回后仍缺失时 SHALL 放行前进 + 检查结果如实标注「已打回仍未申报」，MUST NOT 死循环

#### Scenario: 终稿非执行动作缺理由打回一次后放行

- **WHEN** risk_judge 终稿决策（`final_trade_decision`）为 watch 或 hold，且 `inaction_reason` 或 `reeval_triggers` 缺失
- **THEN** 终稿完整性检查 SHALL 打回 risk_judge 一次要求补全（与 `final_price_check` 同款一次重试语义）
- **AND** 仍缺失时 SHALL 放行 + 如实标注，不虚构内容

#### Scenario: 终稿执行动作缺再评估触发条件打回一次后放行

- **WHEN** risk_judge 终稿决策为 buy 或 sell，且清洗后 `reeval_triggers` 为空
- **THEN** 终稿完整性检查 SHALL 打回 risk_judge 一次，feedback SHALL 列明缺失项并提示可从风险辩论共识（保守/中性方已提出的触发条件）中结构化申报
- **AND** 重试后仍缺失时 SHALL 放行 + `final_reeval_check` note「已打回仍未申报」，MUST NOT 虚构条目

#### Scenario: 终稿执行动作触发条件齐备直通

- **WHEN** risk_judge 终稿决策为 buy 或 sell，且 `reeval_triggers` 含至少 1 条有效条目
- **THEN** `final_reeval_check` SHALL pass，MUST NOT 产生打回或标注

#### Scenario: 噪声形态清洗不炸管线

- **WHEN** LLM 输出的 `reeval_triggers` 为 `null`、单字符串、或含非字符串条目的混合列表
- **THEN** `null`/非列表 SHALL 归一为 `[]`；单字符串 SHALL 归一为单元素列表；非字符串条目 SHALL 被丢弃
- **AND** 校验 MUST NOT 因噪声形态抛 ValidationError 中断管线

#### Scenario: 执行动作理由不受约束

- **WHEN** action 为 buy 或 sell
- **THEN** 理由检查 SHALL 不要求 `inaction_reason`，价位必填校验语义 SHALL 保持不变

### Requirement: Fund Manager 操作性结论字段

`FundManagerDecision` SHALL 新增 `action`（枚举同 `TradeDecision.action`）与 `confidence`（0-1 浮点）可选字段；`decision` 为 `"approve"` 时两者 SHALL 必填（模型校验；节点带校验错误摘要重试一次，仍缺失则中断管线），`"reject"`/`"return"` 时 SHALL 允许为 None。FM 的 action 是对 Risk Judge 裁决后最终方案的操作定性，MUST NOT 被硬拦截为管线异常（与裁决方向相悖时交由报告展示与一致性评估披露）。FM 的 LLM 上下文 SHALL 包含其审批对象 `final_trade_decision` 的完整字段，无论 state 中该值是 `TradeDecision` 对象还是 dict。

FM 的 `confidence` 语义 SHALL 定义为「对本次裁决（approve/reject/return 及其操作定性）正确性的置信度」，MUST NOT 与被审批方案的置信度混同。FM `confidence` 与 risk_judge 终稿 `confidence` 偏差超过 0.15 时，报告 SHALL 在操作定性旁渲染「置信度漂移」标注（含两个置信度值与 FM reasoning 全文，供标注人判读差异是否合理），MUST NOT 硬拦截。FM `reasoning` 复述上游指标名与数值 SHALL 保真——指标名与结构化参数 MUST 逐字引用，MUST NOT 改写指标名或混同不同量纲的数值（如把价位并入降幅阈值）。

(Previously: FM 的 `action`/`confidence` 为 approve 时必填的可选字段，confidence 无语义定义、无漂移披露义务，reasoning 复述上游内容无保真约束——「KDJ/DCF柱」类指标名改写、价格 6.12 混入降幅阈值、置信度 0.8 相对裁决 0.55 无解释漂移均无门禁。)

#### Scenario: approve 决策必含操作定性与置信度

- **WHEN** FM 输出 `decision` 为 `"approve"` 但缺少 `action` 或 `confidence`
- **THEN** 节点 SHALL 携带校验错误摘要重新调用一次 LLM
- **AND** 重试输出仍不通过校验时 SHALL 抛出 ValidationError 中断管线（重试 ≠ 降级，不静默降级）

#### Scenario: 审批对象进 FM 上下文

- **WHEN** state 中 `final_trade_decision` 为 `TradeDecision` 对象（risk_judge 原样写入，与 `trader_plan` 同惯例）或其 dict 序列化
- **THEN** FM 的 LLM 上下文 SHALL 包含「交易决策」段（action/confidence/仓位/理由等字段），FM MUST NOT 在看不到方案的情况下审批

#### Scenario: reject/return 允许无操作定性

- **WHEN** FM 输出 `decision` 为 `"reject"` 或 `"return"` 且不含 `action`/`confidence`
- **THEN** 校验 SHALL 通过（字段为 None），管线照常路由

#### Scenario: approve 的 action 与裁决方向相悖不硬拦截

- **WHEN** FM `decision` 为 `"approve"` 且 `action` 与 `final_trade_decision.action` 方向不同
- **THEN** 管线 SHALL 正常继续（保留 FM 仲裁权）
- **AND** 报告与 judge 材料中 SHALL 并排展示 FM action 与裁决 action，使矛盾可见

#### Scenario: FM 决策进 judge 变量

- **WHEN** `extract_judge_vars` 序列化 FM 决策
- **THEN** 变量文本 SHALL 包含 `action` 与 `confidence`（存在时），使 judge 与标注人无需从理由推断操作定性

#### Scenario: 置信度漂移披露

- **WHEN** FM decision 为 approve 且输出 confidence=0.8，而 risk_judge 终稿 confidence=0.55（偏差 0.25 > 0.15）
- **THEN** 报告操作定性旁 SHALL 渲染「置信度漂移」标注，内容含 FM confidence、终稿 confidence 与 FM reasoning 全文，MUST NOT 硬拦截
- **AND** 偏差 ≤ 0.15 时 SHALL 不产生该标注

#### Scenario: 复述失真可观测

- **WHEN** FM reasoning 中出现改写上游指标名的表述（如上游为「DIF-DEA 柱」而 FM 写作「DCF 柱」），或把价位数值复述进百分比阈值清单（如把近期低点 6.12 复述为「单季净利降幅阈值 6.12」）
- **THEN** 该失真 SHALL 经 judge 材料与 trace 可观测（FM 上下文中含上游原文，judge 变量含 FM reasoning 全文），供标注人按「真幻觉/复述失真」桶终裁
- **AND** 管线 MUST NOT 因复述失真中断（语义级失真不由代码硬判定，锚定靠 prompt 保真约束 + 可观测性）
