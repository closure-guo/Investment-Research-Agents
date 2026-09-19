# agent-node-contracts Specification

## Purpose

定义各 Agent 节点对 LLM 结构化输出的契约校验与降级行为：Fund Manager 决策枚举强校验与状态类型标注、报告决策中文标注、分析师解析失败降级可观测、prompt 枚举一致性、辩论角色与置信度约束，以及异常路径的测试覆盖要求。
## Requirements
### Requirement: Fund Manager Decision Enum Validation

Layer V Fund Manager 节点 SHALL 对 LLM 输出的 `decision` 字段做枚举强校验，合法值为 `approve`、`reject`、`return` 三者之一。校验前 SHALL 对原始值做归一化（去首尾空白 + 转小写），归一化后仍不在合法集内的 SHALL 抛出验证异常中断管线，SHALL NOT 降级为任何默认决策语义。

该行为与 Layer III Trader 的 `TradeDecision.model_validate` 保持一致——非法 LLM 输出属于契约违约，应显式失败而非静默放行。

#### Scenario: 合法决策值正常通过

- **WHEN** LLM 输出 `{"decision": "approve", "reasoning": "..."}`
- **THEN** 节点 SHALL 返回 `fund_manager_decision` 为 `"approve"`
- **AND** 管线继续执行至报告生成

#### Scenario: 大小写与首尾空白归一化

- **GIVEN** LLM 输出的决策值存在大小写或首尾空白差异
- **WHEN** 值为 `"Approve"`、`"APPROVE"`、`" approve "` 或 `"Return"`
- **THEN** 节点 SHALL 归一化为对应小写值并正常通过校验
- **AND** 写入 state 的值 SHALL 是归一化后的小写值

#### Scenario: 非法决策值中断管线

- **WHEN** LLM 输出的决策值归一化后仍不在 `approve` / `reject` / `return` 内（如 `"revise"`、`"拒绝"`、`"maybe"`）
- **THEN** 节点 SHALL 抛出验证异常
- **AND** SHALL NOT 将该非法值写入 state
- **AND** SHALL NOT 降级为 `approve` 或任何其他默认决策

#### Scenario: 缺失 decision 键中断管线

- **WHEN** LLM 输出的 JSON 中不含 `decision` 键
- **THEN** 节点 SHALL 抛出验证异常
- **AND** 异常 SHALL 携带可识别的字段缺失信息，而非裸 `KeyError`

#### Scenario: return 决策递增退回计数

- **WHEN** LLM 输出决策值为 `return`（或其归一化前的大小写变体）
- **THEN** 节点 SHALL 返回 `fund_manager_decision` 为 `"return"`
- **AND** SHALL 将 `return_count` 递增 1

### Requirement: Fund Manager Decision State Typing

`AnalysisState` 中的 `fund_manager_decision` 字段 SHALL 使用 `Literal["approve", "reject", "return"]` 类型标注，而非宽松的 `str` + 注释。该标注与同结构中 `analysis_type` 字段的既有风格保持一致，使静态类型检查能捕获非法赋值。

#### Scenario: 类型检查捕获非法字面量赋值

- **WHEN** 代码中向 `fund_manager_decision` 赋一个不在合法集内的字面量
- **THEN** `uv run mypy` SHALL 报告类型错误

### Requirement: Reject Decision Report Annotation

报告生成 SHALL 将 Fund Manager 的三种决策渲染为语义明确的中文标注，而非直接插入原始英文枚举值。`reject` 决策的报告 SHALL 包含「未通过审批」字样，与 ADR-0011 Layer V 的设计意图一致。

#### Scenario: reject 决策标注未通过审批

- **GIVEN** `fund_manager_decision` 为 `"reject"`
- **WHEN** 生成最终报告
- **THEN** 报告的基金经理决策章节 SHALL 包含「未通过审批」字样
- **AND** SHALL NOT 仅显示原始英文值 `reject`

#### Scenario: approve 决策标注审批通过

- **GIVEN** `fund_manager_decision` 为 `"approve"`
- **WHEN** 生成最终报告
- **THEN** 报告 SHALL 以明确的中文标注表示审批通过

#### Scenario: return 决策标注退回重评

- **GIVEN** `fund_manager_decision` 为 `"return"`
- **WHEN** 生成最终报告
- **THEN** 报告 SHALL 以明确的中文标注表示已退回交易员重新评估

### Requirement: Analyst Parse Degradation Observability

Layer I 分析师节点在 LLM 输出解析失败时 SHALL 记录 WARNING 级日志，并使降级产出的报告可被下游识别为「解析失败降级」而非「LLM 确实无 claims」。降级路径 SHALL NOT 静默发生。

该要求的动因：降级产出 `claims=[]` 会使引用校验的 `all_passed` 在零 claim 时返回 `True`，从而绕过 retry 分支直接生成报告——解析失败反而让校验「通过」，属隐蔽的静默失败。

#### Scenario: 解析失败记录告警日志

- **WHEN** LLM 响应无法解析为合法的分析师报告结构（坏 JSON 或 schema 不符）
- **THEN** 节点 SHALL 记录 WARNING 级日志，包含节点名与失败原因
- **AND** SHALL 返回降级报告以保证管线不中断

#### Scenario: 降级报告携带可识别标记

- **WHEN** 分析师节点走降级路径产出报告
- **THEN** 该报告 SHALL 携带可供下游区分的降级标记
- **AND** 引用校验 SHALL 能区分「降级导致的零 claim」与「LLM 正常输出的零 claim」

#### Scenario: claim 字段非法值改写记录告警

- **WHEN** LLM 输出的 claim 中 `claim_type` 或 `source_type` 不在合法集内
- **THEN** 系统 SHALL 记录 WARNING 级日志说明原值与改写后的值
- **AND** SHALL 继续执行既有的改写降级逻辑（`claim_type` 改写为 `entity`、`source_type` 改写为 `data`）

### Requirement: Prompt Enum Consistency

Prompt 模板中声明的枚举取值 SHALL 与代码中的合法值集合保持一致。任何在 prompt 中要求 LLM 输出、但不在代码合法集内的枚举值，SHALL 视为缺陷。

#### Scenario: 舆情分析师 claim_type 与代码一致

- **WHEN** 检查 `sentiment_analyst` prompt 中声明的 `claim_type` 取值
- **THEN** 所声明的每个值 SHALL 存在于分析师节点的 `claim_type` 合法集内
- **AND** SHALL NOT 出现导致输出被系统性静默改写的值

### Requirement: Debate Message Role Constraint

`DebateMessage` 模型的 `role` 字段 SHALL 使用 `Literal` 约束其合法角色取值，涵盖多空辩论与风控辩论的全部角色。LLM 输出的角色值不在合法集内时 SHALL 触发验证异常，而非静默透传。

该要求的动因：角色值错误会污染报告正文渲染与节点摘要提取（摘要依赖角色值过滤，值错误时摘要退化为兜底文案）。

#### Scenario: 合法角色值通过校验

- **WHEN** 辩论节点的 LLM 输出 `role` 为合法角色之一（多空双方、风控三辩论者、研究经理、风控裁决者）
- **THEN** 模型校验 SHALL 通过

#### Scenario: 非法角色值触发验证异常

- **WHEN** 辩论节点的 LLM 输出 `role` 为不在合法集内的值
- **THEN** 模型校验 SHALL 抛出验证异常
- **AND** SHALL NOT 将非法角色值写入辩论历史

### Requirement: Trade Decision Confidence Range

`TradeDecision` 模型的 `confidence` 字段 SHALL 约束取值范围为 0 到 1（含边界）。超出该范围的值 SHALL 触发验证异常。

该要求的动因：模型文档已声明 confidence 为 0-1 置信度，但缺少运行期约束。LLM 若按百分数返回（如 `95`），报告会渲染出「置信度 9500%」这类明显错误的展示。

#### Scenario: 合法置信度通过校验

- **WHEN** LLM 输出 `confidence` 为 0 到 1 之间的值（如 `0.75`）
- **THEN** 模型校验 SHALL 通过

#### Scenario: 越界置信度触发验证异常

- **WHEN** LLM 输出 `confidence` 为超出 0-1 范围的值（如 `95` 或 `-0.5`）
- **THEN** 模型校验 SHALL 抛出验证异常

### Requirement: LLM Output Exception Path Test Coverage

各 LLM 解析节点的枚举校验与降级逻辑 SHALL 有覆盖异常路径的单元测试，包括非法枚举值、缺失必填键、以及解析失败降级三类场景。

该要求的动因：TESTING stub 恒返回固定合法值，stub 管线测试对枚举漂移不敏感，故校验逻辑的有效性必须由直接针对节点的单元测试保证。

#### Scenario: Fund Manager 异常路径有测试覆盖

- **WHEN** 运行 Fund Manager 节点测试
- **THEN** 测试 SHALL 覆盖 `reject` 决策、非法枚举值、缺失 `decision` 键、大小写归一化四类场景
- **AND** 非法值与缺键场景 SHALL 断言抛出验证异常

#### Scenario: Analyst 降级路径有测试覆盖

- **WHEN** 运行分析师节点测试
- **THEN** 测试 SHALL 覆盖坏 JSON 触发降级、以及 `claim_type` / `source_type` 非法值改写两类场景

### Requirement: 分析师报告可读结论字段

每个分析师节点输出的 `AnalystReport` 对象 SHALL 包含 `plain_conclusion` 字段：普通人可直接看懂的一句话结论+简短解释（非纯技术黑话，如「技术面偏空：MACD 死叉、反弹动能存疑，不宜右侧追入」）。该字段 SHALL 非空；LLM 输出解析失败走降级路径时 SHALL 以可读占位回填（如「技术面数据缺失，无法给出结论」），且 `parse_degraded` 照常置位。

#### Scenario: 正常输出可读结论

- **GIVEN** 分析师节点成功解析 LLM 结构化输出
- **WHEN** 产出 `AnalystReport`
- **THEN** `plain_conclusion` SHALL 为非空字符串且面向普通人可读
- **AND** `summary`（面向 RM 的精简语言）行为保持不变

#### Scenario: 解析失败降级回填

- **GIVEN** LLM 响应无法解析为合法分析师报告结构（坏 JSON 或 schema 不符）
- **WHEN** 分析师节点走降级路径产出报告
- **THEN** `plain_conclusion` SHALL 为可读占位（非空）
- **AND** `parse_degraded` SHALL 为 True，下游可识别降级来源

#### Scenario: 空值或缺失被拒绝

- **WHEN** `AnalystReport` 校验时 `plain_conclusion` 缺失或为空串
- **THEN** 校验失败（ValidationError），不得静默产出无可读结论的分析师报告

### Requirement: Fund Manager 操作性结论字段

`FundManagerDecision` SHALL 新增 `action`（枚举同 `TradeDecision.action`）与 `confidence`（0-1 浮点）可选字段；`decision` 为 `"approve"` 时两者 SHALL 必填（模型校验；节点带校验错误摘要重试一次，仍缺失则中断管线），`"reject"`/`"return"` 时 SHALL 允许为 None。FM 的 action 是对 Risk Judge 裁决后最终方案的操作定性，MUST NOT 被硬拦截为管线异常（与裁决方向相悖时交由报告展示与一致性评估披露）。FM 的 LLM 上下文 SHALL 包含其审批对象 `final_trade_decision` 的完整字段，无论 state 中该值是 `TradeDecision` 对象还是 dict。

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

### Requirement: 报告基金经理决策章节展示操作定性

report 节点拼装「基金经理决策」章节时，SHALL 在决策标注旁展示 FM 的 `action` 与 `confidence`（存在时），与裁决 action 并排，使「批准的是什么方案」直接可见。

#### Scenario: 报告展示 approve 的操作定性

- **WHEN** FM approve 且 `action`/`confidence` 非空
- **THEN** 「基金经理决策」章节 SHALL 同时呈现 FM action/confidence 与最终裁决 action
- **AND** 审批标注（审批通过/未通过审批/退回重评）SHALL 保留（ADR-0011 语义不变）

### Requirement: Research Manager 结构化评级输出

Research Manager SHALL 输出结构化 JSON：`reasoning`（多空概括与判断依据）、`rating`（看多/看空/中性枚举）、`confidence`（0-1）。JSON 键序 SHALL 为 reasoning 在前、rating 在后（保持「先推理后结论」的生成顺序）；消费端（state 结论字符串、报告章节、judge 变量）SHALL 由代码把 rating/confidence 前置到 reasoning 之前（倒金字塔展示）。JSON 解析失败时 SHALL 降级为现行自由文本行为（原文作 conclusion，rating/confidence 置 None），MUST NOT 中断管线。

#### Scenario: RM 结构化输出与结论前置

- **WHEN** RM 正常输出结构化 JSON
- **THEN** `state["research_manager_conclusion"]` SHALL 以「评级：<rating>（置信度 <confidence>）」行开头，reasoning 全文随后
- **AND** `state` SHALL 另存 `research_manager_rating` / `research_manager_confidence` 供战绩结算与 judge 变量直取

#### Scenario: 生成顺序保持先推理后结论

- **WHEN** RM prompt 模板定义 JSON 输出
- **THEN** reasoning 键 SHALL 位于 rating 键之前（LLM 按键序生成，推理先于结论）

#### Scenario: 解析失败降级不中断

- **WHEN** RM 输出无法解析为结构化 JSON
- **THEN** 管线 SHALL 以自由文本原文作为 conclusion 继续（rating/confidence 为 None）
- **AND** 降级 SHALL 可观测（parse_degraded 类标记，对齐分析师降级语义）

#### Scenario: Trader 上下文与 judge 变量自动前置

- **WHEN** Trader context 或 judge 变量 `research_manager_decision` 引用 RM 结论
- **THEN** 所见文本 SHALL 为评级前置后的拼装（无需各自解析原文）

### Requirement: 辩论交锋结构化引用

Bull/Bear 辩论 SHALL 以既有 `key_arguments` 为编号锚点建立显式交锋引用：辩论 context 中各历史发言 SHALL 以编号列出对方论点（如「对方(R1) 论点: ①…②…」）；`DebateMessage` SHALL 新增可选字段 `rebuttal_to: list[int]`（本轮回应的对方上一轮论点编号，首轮开场为空），辩论 prompt 要求 content 中逐条标注「回应对方N」；交锋覆盖率（被回应论点并集 / 对方论点总数）SHALL 可由代码直接计算（确定性指标，不依赖 judge）。风险辩论三方为同构扩展，SHALL 在 Bull/Bear 落地验证后跟进。

#### Scenario: context 呈现对方论点编号

- **WHEN** 构建第 2 轮辩论 context
- **THEN** 对方第 1 轮发言 SHALL 以「论点: ①…②…」编号形式呈现（取自 key_arguments）
- **AND** 辩论历史仍保留发言正文（编号行前置）

#### Scenario: rebuttal_to 结构化引用

- **WHEN** 辩论方输出第 2 轮及以后发言
- **THEN** `DebateMessage` SHALL 支携带 `rebuttal_to`（回应的对方论点编号列表）；`rebuttal_to` 为空 SHALL 不视为解析失败（首轮开场/纯补充立场时合法）

#### Scenario: 交锋覆盖率代码可算

- **WHEN** 一场辩论结束
- **THEN** 「对方论点被回应的比例」SHALL 可由各轮 `rebuttal_to` 并集直接计算（零 LLM 调用）
- **AND** 该覆盖率 SHALL 进入 debate_quality 的 judge 材料与评估记录（作为 judge 打分的确定性对照锚点）

#### Scenario: 覆盖率按发言逐条计数

- **WHEN** 辩论有多轮，各轮 `rebuttal_to` 序号均在当轮对方发言内从 1 起计
- **THEN** 被回应论点 SHALL 以「哪条发言的第几条」为键去重，不同轮次的同序号论点 MUST NOT 合并（否则分子封顶在单轮论点数，r1 实证 8 条 trace 全报「4/8」）

### Requirement: 分析师解析降级保真

分析师 LLM 输出解析失败走降级路径时，SHALL 保证降级产物如实反映失败原因且不破坏既有正常结果。

#### Scenario: 字符串内未转义引号可解析

- **WHEN** LLM 输出的 JSON 字符串值内含未转义的成对引号（如 `"呈"低盈利+高扩张"格局"`）
- **THEN** 解析器 SHALL 将其识别为内嵌引号并成功解析（终止引号之后的首个非空白字符只能是结构字符或文本末尾）

#### Scenario: 降级占位如实说明解析失败

- **WHEN** 解析仍然失败进入降级
- **THEN** `plain_conclusion` 占位 SHALL 表述为「输出解析失败」，MUST NOT 表述为「数据缺失」（r1 实证：judge 与最终报告据此误判分析师无数据）
- **AND** 原始文本中已闭合的 `summary` / `plain_conclusion` 字段 SHALL 被尽力打捞回填

#### Scenario: 重跑降级不覆盖正常报告

- **WHEN** 引用重试重跑某分析师，本次结果 `parse_degraded=True`，而 state 中该分析师已有 `parse_degraded=False` 的报告
- **THEN** 节点 SHALL 保留既有正常报告（不覆盖），并记录 WARNING

#### Scenario: 降级标记可区分 agent 与轮次

- **WHEN** 多个分析师或同一分析师多轮重跑发生降级，标记落到同一父 span
- **THEN** 标记键 SHALL 含 agent 名与重试轮次（如 `degradation.fundamental.r2`），不同降级 MUST NOT 互相覆盖；legacy `degradation`/`agent` 键保留

#### Scenario: 引用曲解可检测

- **WHEN** 某轮发言的 content 标注「回应对方N」但所述与对方论点 N 原文明显不符（曲解/弱化）
- **THEN** judge 材料中 SHALL 并排呈现论点 N 原文与该轮回应，使曲解可被人工/judge 识别

### Requirement: 节点产出键 ⊆ AnalysisState 声明与图通道（系统性门禁）

任一节点写入 state 的键 SHALL 在 `AnalysisState` 声明且已建图通道——LangGraph 对未声明的 TypedDict 键在合并时**静默丢弃**（incident 027 教训：`price_check` 家族/citation 回路键曾因此整条哑火，fail 打回从未生效）。

系统 SHALL 提供系统性门禁测试（替代逐键补守卫）：对产出键集合可枚举的关键节点（至少 `compute_metrics`），以其全部可选分支的构造 state 运行节点，断言产出键 ⊆ `AnalysisState.__annotations__` ⊆ 编译图的 `channels`。新键未声明即测试红并列出键名。

#### Scenario: 未声明键被门禁拦截

- **WHEN** 某节点返回 `AnalysisState` 未声明的键
- **THEN** 门禁测试 SHALL 失败并列出未声明键（附「会被图静默丢弃」说明）

#### Scenario: 声明但未建通道同样红

- **WHEN** 键已声明但未出现在编译图的 `channels`（如声明位置不被图 schema 采用）
- **THEN** 门禁测试 SHALL 失败

### Requirement: 辩论论点结构化锚点

`DebateMessage.key_arguments` SHALL 为结构化论点列表，每项 `DebateArgument` 含 `text`（论点标头原文，非空）、`kind ∈ {data, event, inference, unspecified}` 与 `anchors: list[str]`。`kind` 对 LLM 只暴露 `data` / `event` / `inference` 三值：`data` 型的 `anchors` SHALL 为 state 英文键路径（field_ref，与分析师 claim 同一词表）；`event` 型的 `anchors` SHALL 为来源事件标题要点；`inference` 型 `anchors` 可为空。旧格式裸字符串或 `kind` 缺失/非法的论点 SHALL 解析为 `kind="unspecified"`（显式降级，SHALL NOT 推断 kind、SHALL NOT 视为解析失败）。

辩论与风控辩论节点 SHALL 在解析出 `DebateMessage` 后执行**确定性锚点校验**（零 LLM 调用）：`data` 型锚点复用引用校验器的 field_ref 解析器，解析得到非 None 值判 `resolved`，否则 `unresolved`；`event` 型锚点按既有回声源集合（`collect_text_sources`：`news_list` / `key_events` / `announcements` / `research_reports` / `share_unlock` / `block_trades`——与文本 claim 回声匹配同一实现）归一子串匹配，命中判 `resolved`；`inference` 型锚点先按 field_ref 解析、失败再按回声匹配；`data` / `event` 型零锚点判 `missing`；`inference` 型零锚点判 `none`（合法，计数）。校验结果 SHALL 写入 `AnalysisState` 中**已声明**的 channel `debate_anchor_checks`（append reducer）并落当前 span metadata。校验 SHALL fail-open：SHALL NOT 改变路由、SHALL NOT 阻断、SHALL NOT 触发重跑。

`rebuttal_to` 的 1-based 编号 SHALL 继续指向对方 `key_arguments` 的位置；对手可见的辩论历史编号行（「R{n} 论点: ①…」）SHALL 只渲染 `text`，SHALL NOT 在本变更中向对手暴露锚点。

#### Scenario: 结构化论点解析

- **WHEN** 辩手 LLM 输出 `key_arguments: [{"text": "MA5 上穿 MA20", "kind": "data", "anchors": ["technical_indicators.MA.5.-1", "technical_indicators.MA.20.-1"]}]`
- **THEN** `DebateMessage.key_arguments[0]` SHALL 为 `DebateArgument(text="MA5 上穿 MA20", kind="data", anchors=[...两条...])`
- **AND** `text` 为空 SHALL 触发验证异常（与既有非空结论字段口径一致）

#### Scenario: 旧格式显式降级

- **WHEN** `key_arguments` 为裸字符串列表 `["论点1", "论点2"]`（历史会话数据、TESTING stub、Langfuse 反解材料）或某项 `kind` 缺失 / 不在合法集
- **THEN** 该项 SHALL 解析为 `kind="unspecified"`、`anchors=[]`，SHALL NOT 抛解析异常，SHALL NOT 推断为 `inference`
- **AND** 该项 SHALL 计入锚点校验的 `unspecified` 计数

#### Scenario: data 型锚点解析校验

- **GIVEN** state 含 `technical_indicators.MA.5` 序列
- **WHEN** 论点 `kind="data"`，`anchors=["technical_indicators.MA.5.-1", "profitability_metrics.不存在的键.2024"]`
- **THEN** 第一个锚点 SHALL 判 `resolved`、第二个 SHALL 判 `unresolved`，该论点 `anchored=true`（任一锚点 resolved 即锚定）
- **AND** 解析语义（负索引 / 括号索引 / DataFrame 行键.列名 / 日期与季度形态归一）SHALL 与引用校验器完全一致，SHALL NOT 另建解析器

#### Scenario: event 型回声匹配

- **GIVEN** `news_list` 含标题「公司公告拟回购不超过 10 亿元」
- **WHEN** 论点 `kind="event"`，`anchors=["拟回购不超过 10 亿元"]`
- **THEN** 该锚点 SHALL 判 `resolved`（归一子串命中）
- **AND** 回声源集合 SHALL 与文本 claim 回声匹配复用同一集合与归一函数

#### Scenario: 申报纪律违规与推断无锚分别计数

- **WHEN** `data` 或 `event` 型论点 `anchors=[]`
- **THEN** SHALL 判 `missing` 并计入 `missing_required`
- **WHEN** `inference` 型论点 `anchors=[]`
- **THEN** SHALL 判 `none`、`anchored=false`，计入 `unanchored_inference`，SHALL NOT 视为违规

#### Scenario: fail-open 不阻断

- **WHEN** 某轮辩论全部论点 `anchored=false`（含全部 unresolved 或 missing）
- **THEN** 图路由 SHALL 与变更前完全一致（进入下一轮 / research_manager / trader），SHALL NOT 重跑辩手、SHALL NOT 置阻断标记
- **AND** 校验结果 SHALL 仍完整写入 `debate_anchor_checks` 与 span metadata

#### Scenario: channel 声明与图通道契约

- **WHEN** 构建 `build_5layer_graph()`
- **THEN** `builder.channels` SHALL 含 `debate_anchor_checks`，且该键 SHALL 出现在图通道契约测试的断言集合中（未声明键被图合并静默丢弃——incident 027）
- **AND** 一次 deep 全流程后 state 中 `debate_anchor_checks` 条目数 SHALL 等于全部辩手发言的论点总数

#### Scenario: rebuttal_to 编号语义不变

- **WHEN** 第 2 轮辩手输出 `rebuttal_to=[1, 3]`
- **THEN** SHALL 指向对方第 1 轮 `key_arguments` 的第 1、3 项（结构化后仍按位置计数）
- **AND** 对手可见历史「R1 论点: ①…②…③…」SHALL 只含各项 `text`，交锋覆盖率计算 SHALL 与变更前一致
