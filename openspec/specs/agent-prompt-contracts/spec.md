# Agent Prompt Contracts

## Purpose

定义各角色 LLM 提示词模板（`src/finance_agent/prompts/*.md` 及散落硬编码 system prompt）的行为契约：反幻觉硬规则、对抗性辩论指令、决策语义契约、摘要数据接地，以及提示词可测试性与股票解析单一实现。提示词是行为契约的一部分——本轮从 TradingAgents / ai-hedge-fund 对标总结出缺口后沉淀为硬性要求，防止 LLM 输出编造数据、辩论各说各话、置信度无语义。
## Requirements
### Requirement: 分析师反幻觉硬规则

每个分析师提示词（fundamental、macro、technical、sentiment）MUST 包含反幻觉硬规则段，要求 LLM 仅基于输入数据推理，以数据最新日期为"现在"，不得编造数据；各分析师方法论 MUST 体现周期感知与数据时效意识。

#### Scenario: 分析师输出基于输入数据

- **WHEN** 加载任一分析师提示词模板（fundamental_analyst / macro_analyst / technical_analyst / sentiment_analyst）
- **THEN** 模板中包含"仅使用提供的数据"约束
- **AND** 模板中包含"不得编造数字/数据"约束
- **AND** 模板中包含"以数据最新日期为现在，不使用该日期之后的知识"约束

#### Scenario: 数据不足时分析师明示降级

- **WHEN** 分析师收到的输入数据缺失或不足以支持某项分析
- **THEN** 分析师 MUST 在报告中显式标注数据不足
- **AND** MUST NOT 编造缺失数据填补分析

#### Scenario: 分析师方法论周期感知

- **WHEN** 加载 fundamental_analyst 提示词
- **THEN** 模板中 ROE/负债率等阈值表述为"同业相对 + 周期调整"语义（如"与同业中位数对比并按当前利率/通胀环境调整"），而非孤立绝对值
- **AND** 加载 technical_analyst 提示词时模板包含"强趋势中 RSI/KDJ 可能钝化、以 MA 趋势为主"类提示
- **AND** 加载 macro_analyst 提示词时模板包含"M1/M2 剪刀差判读"与"数据滞后时标注时效并降级结论"类提示

### Requirement: 辩论者对抗性指令

bull_debater、bear_debater、risk_debater 提示词 MUST 包含对抗性辩论指令，要求逐条引用对手论点并反驳，而非仅复述自身报告。

三份提示词 SHALL 同时包含**论点锚点申报纪律**：`key_arguments` 输出契约 SHALL 声明为结构化项 `{text, kind, anchors}`，`kind` 三选一（`data` / `event` / `inference`）；**`data` 型论点的每个事实断言（数字/事件/资金流/利差类可查证表述）SHALL 各自具备可解析的锚点**——一条论点含多个事实断言时，要么逐断言附锚点、要么拆分为多条论点；无法逐断言锚定的 SHALL 标 `inference`。`data` 型论点 SHALL 只能引用输入数据段标题内联标注的 state 英文键路径（与分析师 claim 同一词表，含负索引约定）；`event` 型论点 SHALL 附来源事件标题要点；`inference` 型论点 SHALL 明示为推断、可不附锚点。

提示词 SHALL 明文禁止为推断型论点伪造 field_ref，SHALL 明文要求「拿不准是否有数据支撑时标 inference，不得编造锚点」，并 SHALL 包含**推断冒充数据的反例判例**（依据 2026-09-18 grounding 扫描 owner 终裁的 8 条无源断言提炼，如「均线空头排列说明机构资金持续撤离」——价格指标推不出资金流向数据，此类表述必须标 `inference`）。提示词变更 SHALL 经 `deploy_prompts.py` 发布。
(Previously: `data` 型仅要求附至少 1 个锚点（论点级）；无逐断言锚定与拆条要求，无推断冒充数据反例判例)

#### Scenario: 辩论者反驳对手论点

- **WHEN** 加载 bull_debater / bear_debater 提示词
- **THEN** 模板中包含"针对对方上一轮论点逐条反驳"类指令
- **AND** 模板中包含"先引用对手论点（原文或要点）再给出反论"类指令

#### Scenario: 风险辩论者回应其他方位

- **WHEN** 加载 risk_debater 提示词
- **THEN** 模板中包含要求回应其它风险方位论点（激进/保守/中性）的指令

#### Scenario: 锚点申报纪律可判定生效

- **WHEN** 加载 bull_debater / bear_debater / risk_debater 提示词
- **THEN** 模板的 `key_arguments` 输出示例 SHALL 为结构化项（含 `text` / `kind` / `anchors` 三键），SHALL NOT 再是裸字符串列表
- **AND** 模板 SHALL 包含「data 型必须附 field_ref、只能引用输入数据段标注的英文键」类指令
- **AND** 模板 SHALL 包含「禁止为推断伪造 field_ref / 拿不准标 inference」类指令
- **AND** 上述三项 SHALL 由提示词契约测试断言（沿「提示词契约可测试性」要求）

#### Scenario: data 论点逐断言锚定

- **WHEN** bear 输出一条含两个事实断言的论点（如「PMI 跌破荣枯线，机构资金持续撤离」）
- **THEN** 提示词 SHALL 要求为「PMI」断言附 macro 锚点，且「机构资金」断言若无输入数据支撑 SHALL 单独成条并标 `inference`

#### Scenario: 推断冒充数据的反例判例在提示词内

- **WHEN** 加载任一辩手提示词
- **THEN** 模板中包含至少一个「从价格/摘要数字推出资金流、利差、因果类断言并自标 data」的反例，并声明该表述应标 `inference`

#### Scenario: 变更经发布流程

- **WHEN** 三份辩手提示词文件修改后
- **THEN** SHALL 执行 `deploy_prompts.py` 发布，Langfuse 快照与 git 权威源一致（否则 eval 门禁拒绝运行）

### Requirement: Trader 决策语义契约

trader 提示词 MUST 定义 action 各档位的语义与 position_size 档位含义，并为 confidence 提供分档锚点。

#### Scenario: Trader 输出带语义的决策

- **WHEN** 加载 trader 提示词
- **THEN** 模板中包含 action（buy/sell/hold/watch）各档位的行为语义描述
- **AND** 模板中包含 position_size 档位定义（如 light/moderate/heavy 对应仓位区间）
- **AND** 模板中包含 confidence 分档语义（如 0.7+ 高置信、0.4-0.7 中等、<0.4 低置信）

### Requirement: 风控与审批评级量表

risk_judge、fund_manager 提示词 MUST 提供评级/决策选项的语义说明，指导在证据均衡或不足时如何取舍。fund_manager 提示词 MUST 额外定义审批理由的职责边界：approve 理由限定于风控结论一致性、论据矛盾处理与执行前提；MUST NOT 对标的作方向性投资判断（如「适合长期价值投资」——该类判断属研究层，FM 无分析师报告输入而无依据）。

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

### Requirement: Research Manager 评级表态

research_manager 提示词 MUST 要求 LLM 给出明确的看多/看空/中性倾向与依据，而非仅复述多空双方观点；证据均衡时必须显式说明取舍理由。

#### Scenario: Research Manager 输出明确倾向

- **WHEN** 加载 research_manager 提示词
- **THEN** 模板中包含"必须给出明确评级倾向（看多/看空/中性）"类指令
- **AND** 模板中包含"证据均衡时须说明取舍理由，而非回避表态"类指令
- **AND** 模板中包含"基于辩论历史中的具体论据，而非泛泛而谈"类指令

### Requirement: 摘要仅基于输入材料

报告摘要（report.py focus summary）与 deep_mode 对话摘要 MUST 仅基于管线上游材料/工具输出组织内容，不得引入材料外数值或知识。

#### Scenario: 报告摘要基于上游材料

- **WHEN** 加载 report.py 摘要 prompt 或 deep_mode 提示词
- **THEN** 模板中包含"仅使用所提供材料中的数据"约束
- **AND** 模板中包含"不得引入材料外/工具输出外的数值"约束

#### Scenario: 摘要不得引用过期宏观数据作为最新值

- **WHEN** 管线上游宏观数据（PMI/CPI）最新日期与当前日期相差超过 3 个月
- **THEN** 摘要/分析 MUST 标注该指标数据时效滞后，不得将其表述为当前最新状态
- **AND** 相关结论 MUST 降低置信度或明示基于滞后数据

### Requirement: 股票解析收敛单一实现

股票名称/代码的 LLM 解析 MUST 收敛到 react_agent.py 的单一实现；nlp.py 冗余解析与未引用的 REACT_SYSTEM_PROMPT 死代码 MUST 被移除。

#### Scenario: 移除冗余解析后单一入口

- **WHEN** 执行股票解析收敛任务
- **THEN** 股票解析 LLM 提示词仅存在于 react_agent.py 一处（契约测试断言通过）
- **AND** nlp.py 中的冗余 LLM 解析函数已移除，且无生产代码引用（断言通过）
- **AND** react_agent.py 中未被引用的 REACT_SYSTEM_PROMPT 常量已移除（断言通过）

### Requirement: 提示词契约可测试性

提示词模板改动 MUST 附带可执行的契约测试，验证关键指令段存在，防止后续编辑漂移。

#### Scenario: 契约测试断言关键指令存在

- **WHEN** 运行提示词契约测试套件
- **THEN** 每个分析师提示词包含反幻觉硬规则段（断言通过）
- **AND** 每个辩论者提示词包含对抗性指令（断言通过）
- **AND** trader / risk_judge / fund_manager 提示词包含决策语义段（断言通过）
- **AND** research_manager 提示词包含评级表态指令（断言通过）

### Requirement: 分析师 claim 方向申报纪律

数值型与计算型 claim 的分析师 prompt（fundamental/technical/sentiment 及辩论者）SHALL 要求：登记 claim 时必填 `direction` 字段（`positive`/`negative`/`flat`），方向语义为「正文表述方向对应真值符号的修饰」——正文写「下滑 10.05%」而真值为 -10.05 时 SHALL 申报 `stated_value=10.05, direction="negative"`。prompt SHALL 提供与 field_ref ↔ metric_name 同级的 direction 申报示例。prompt 变更后 SHALL 经 `scripts/deploy_prompts.py` 发布方可进入 eval 门禁（prompt-deploy-consistency 契约不变）。

#### Scenario: prompt 含方向申报示例

- **WHEN** 审查任一分析师 prompt 的 claim 登记章节
- **THEN** SHALL 存在 direction 必填说明与「下滑 X% → stated_value=X, direction=negative」样例

#### Scenario: prompt 发布一致性

- **WHEN** prompt 修改合入后未执行 deploy_prompts.py
- **THEN** eval 门禁 SHALL 拒绝运行（沿用现有 prompt-deploy-consistency 门禁行为）

