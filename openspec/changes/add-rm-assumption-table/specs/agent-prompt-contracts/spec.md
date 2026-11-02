# Delta for agent-prompt-contracts

## MODIFIED Requirements

### Requirement: Research Manager 评级表态

research_manager 提示词 MUST 要求 LLM 给出明确的看多/看空/中性倾向与依据，而非仅复述多空双方观点；证据均衡时必须显式说明取舍理由。提示词 MUST 同时要求输出结构化假设表（`assumption_table`）：固定 4 行，维度为 H1 竞争格局 / H2 成长驱动 / H3 盈利质量 / H4 估值与预期；每行 MUST 包含 `assumption`（一句话可核验命题）、`bull_evidence` / `bear_evidence`（转引辩论论点，格式含角色、轮次与论点序号，如「Bull R1 论点②: …」）、`inflection_signal`（可观察指标或事件）、`status`（成立/注意/不成立）、`time_tag`（short/long/short+long）。证据 MUST NOT 引入辩论记录中不存在的论据；辩论论据不足以支撑某维度时 MUST 如实降级该行 `status` 并说明取舍，禁止编造证据或空话填充。`inflection_signal` 无法给出具体可观察信号时，该行 `status` MUST NOT 为「成立」。

(Previously: research_manager 提示词 MUST 要求 LLM 给出明确的看多/看空/中性倾向与依据，而非仅复述多空双方观点；证据均衡时必须显式说明取舍理由。)

#### Scenario: Research Manager 输出明确倾向

- **WHEN** 加载 research_manager 提示词
- **THEN** 模板中包含"必须给出明确评级倾向（看多/看空/中性）"类指令
- **AND** 模板中包含"证据均衡时须说明取舍理由，而非回避表态"类指令
- **AND** 模板中包含"基于辩论历史中的具体论据，而非泛泛而谈"类指令

#### Scenario: 假设表输出契约在模板中

- **WHEN** 加载 research_manager 提示词
- **THEN** 模板中包含固定 4 行假设表（H1 竞争格局 / H2 成长驱动 / H3 盈利质量 / H4 估值与预期）的输出指令与 JSON 结构示例
- **AND** 模板中包含"证据必须转引辩论论点（角色+轮次+论点序号），禁止引入辩论中不存在的论据"类指令
- **AND** 模板中包含"拐点信号必须可观察；无具体信号时该行 status 不得为成立"类指令

#### Scenario: 论据不足时如实降级

- **WHEN** 模板中声明假设表纪律
- **THEN** 模板中包含"辩论论据不足以支撑某维度时如实降级 status 并说明，禁止编造证据或空话填充"类指令
