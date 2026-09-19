# agent-prompt-contracts Delta

## MODIFIED Requirements

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
