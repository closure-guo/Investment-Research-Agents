# Delta for agent-node-contracts

## ADDED Requirements

### Requirement: Research Manager 上下文论点编号渲染

research_manager 节点构建辩论上下文时，SHALL 以「R{n} 论点: ①…②…」编号行渲染辩论消息的 key_arguments（复用辩论层交锋结构化引用的同一渲染逻辑），使假设表证据可按「角色 + 轮次 + 论点序号」稳定引用。渲染 SHALL 覆盖 Bull/Bear 各轮消息；消息无 key_arguments 时该消息退化为现状 content 渲染，MUST NOT 中断。

#### Scenario: 论点编号进入 RM 上下文

- **GIVEN** debate_history 含 Bull R1 消息（content 与 3 条 key_arguments）
- **WHEN** research_manager 节点构建辩论上下文
- **THEN** 该消息的 3 条 key_arguments 以「R1 论点: ①…②…③…」编号行前置于 content
- **AND** 上下文中的论点序号与假设表证据引用（如「Bull R1 论点②」）可对位

#### Scenario: key_arguments 缺失降级

- **GIVEN** debate_history 消息无 key_arguments 字段（历史会话回放）
- **WHEN** research_manager 节点构建辩论上下文
- **THEN** 该消息退化为无编号行的基础格式（`role(R{round}): content`）渲染，MUST NOT 报错中断

### Requirement: Research Manager 假设表结构化解析与降级

research_manager 节点 SHALL 解析 LLM 输出中的 `assumption_table` 字段。表合法（恰好 4 行、code 覆盖 H1/H2/H3/H4、status ∈ {成立, 注意, 不成立}、time_tag ∈ {short, long, short+long}、assumption 与 inflection_signal 非空、bull_evidence 与 bear_evidence 至少一侧非空）时，写入 state `research_manager_assumptions`（list[dict]），并将人读表格追加拼装进 `research_manager_conclusion` 文本（评级行与 reasoning 之后），下游 trader/risk_judge/FM 上下文随之可见。表缺失或非法时 SHALL 丢弃表并记录告警日志（注明丢弃原因），保留 rating/confidence 的既有解析语义，MUST NOT 因表问题置 `parse_degraded` 或中断管线；整体 JSON 解析失败时沿用既有降级（conclusion 为原文、rating/confidence 为 None、parse_degraded=True、assumptions 为 None）。

#### Scenario: 合法假设表落 state 并拼装结论

- **GIVEN** RM JSON 输出含合法 4 行 assumption_table 与合法 rating/confidence
- **WHEN** research_manager 节点解析输出
- **THEN** state 新增 `research_manager_assumptions` 为该 4 行结构
- **AND** `research_manager_conclusion` 在评级行与 reasoning 之后追加人读假设表
- **AND** `research_manager_rating` / `research_manager_confidence` 照常落 state

#### Scenario: 表非法丢弃不中断

- **GIVEN** RM JSON 输出 rating/confidence 合法，但 assumption_table 仅 3 行（缺 H3）
- **WHEN** research_manager 节点解析输出
- **THEN** `research_manager_assumptions` 为 None，`research_manager_conclusion` 不含表内容
- **AND** rating/confidence 照常落 state，`parse_degraded` 未被置位
- **AND** 告警日志记录丢弃原因（缺 H3 行）

#### Scenario: 整体解析失败沿用既有降级

- **GIVEN** RM 输出为自由文本（JSON 解析失败）
- **WHEN** research_manager 节点解析输出
- **THEN** 沿用现状降级：conclusion 为原文、rating/confidence 为 None、parse_degraded=True
- **AND** `research_manager_assumptions` 为 None
