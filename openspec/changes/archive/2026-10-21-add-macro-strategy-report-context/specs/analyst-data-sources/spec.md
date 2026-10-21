## ADDED Requirements

### Requirement: 宏观分析师消费策略观点

宏观分析师的上下文 SHALL 包含最近 30 天内、最多 3 篇策略/宏观研报的聚合观点段；该段 MUST NOT 注入基本面分析师上下文。

- 段落 SHALL 含机构名、报告日期、样本篇数，以及行业观点、核心逻辑、催化剂、风险与分歧点。
- 段尾 SHALL 附防锚定条款：卖方观点存在立场与覆盖偏差，不得替代宏观数据推理，不得直接作为结论依据。
- 数据型 claim SHALL 仍只引用 `macro_indicators`；策略观点只作为文本参照。
- 聚合为空、全部陈旧或读取失败时 MUST NOT 注入空段；管线 SHALL 继续执行。
- 注入内容 SHALL 受上下文预算约束（硬上限截断），不得挤占三大报表与宏观指标段。

#### Scenario: 有策略观点时注入

- **GIVEN** 库中存在 2 篇 30 天内的策略报告抽取
- **WHEN** 宏观分析师构建上下文
- **THEN** 上下文 SHALL 含「卖方策略观点」段
- **AND** 段首 SHALL 显示 2 篇与对应机构、日期
- **AND** 段尾 SHALL 含防锚定条款

#### Scenario: 无策略观点不注入

- **GIVEN** 库中没有任何策略/宏观抽取
- **WHEN** 宏观分析师构建上下文
- **THEN** 上下文 SHALL 不含「卖方策略观点」段
- **AND** MUST NOT 出现「暂无数据」占位

#### Scenario: 基本面分析师不消费

- **GIVEN** 库中存在策略观点聚合
- **WHEN** 基本面分析师构建上下文
- **THEN** 上下文 MUST NOT 含「卖方策略观点」段
- **AND** 基本面仍只包含个股研报、一致预期与个股投喂合并

#### Scenario: 读取失败不阻断

- **GIVEN** 策略聚合读取抛异常
- **WHEN** 数据准备阶段完成
- **THEN** 该状态键 SHALL 为 None
- **AND** 宏观分析师 SHALL 继续基于宏观指标生成报告

### Requirement: 策略观点纳入 citation 回声匹配

citation 校验器 SHALL 把策略观点聚合中的行业论据、核心逻辑、催化剂、风险与摘要纳入文本 claim 回声源集合。

- 命中规则与既有 news/公告/研报标题一致：归一后子串命中即 PASS(echo)，未命中为 UNVERIFIABLE(text)。
- 数值型 claim MUST NOT 因回声匹配而通过；仍须 field_ref 指向可解析的 state 字段。

#### Scenario: 引用策略观点措辞命中

- **GIVEN** 宏观 claim 的 stated 内容与策略观点某条论据措辞一致
- **WHEN** citation 校验器执行文本回声匹配
- **THEN** 该 claim SHALL 判 PASS(echo)

#### Scenario: 数值 claim 不放行

- **GIVEN** 宏观 claim 引用一个策略报告里的数字但没有 field_ref
- **WHEN** citation 校验器执行
- **THEN** MUST NOT 仅凭策略观点段判 PASS
- **AND** SHALL 按既有数值 claim 规则处理
