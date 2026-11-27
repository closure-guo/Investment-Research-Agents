# Delta for factor-proposal（extend-factor-library-coverage）

## MODIFIED Requirements

### Requirement: 提案输出合同

因子提案节点的每条提案 SHALL 包含 `expression`（合法 DSL 字符串）、`hypothesis`（经济学假设，≥30 字）、`category`（枚举九选一：价量 / 反转 / 动量 / 流动性 / 波动率 / 事件状态 / 规模 / 残差 / 行业相对——后四类分别对应比较计数与上市天数构念、可选参考字段 `float_cap`、市场残差构念 `ts_resid` / `cs_mean`、行业中性化构念 `ind_neutralize`）；`expression` 无法通过 DSL 解析时 SHALL 触发修复重试，重试上限 2 次后仍失败的提案 SHALL 被丢弃并在 Langfuse trace 中标记 `proposal_rejected`。提案 prompt 模板（权威源 `prompts/factor_proposal.md`）列举的算子、字段、类别与复杂度规则 SHALL 与 `factors.dsl` 真相源一致（自由参数按去重数值计数）。

(Previously: category 枚举五选一——价量 / 反转 / 动量 / 流动性 / 波动率。)

#### Scenario: 合同违约修复流程

- **GIVEN** LLM 首轮返回的 expression 含未知算子
- **WHEN** 合同校验执行
- **THEN** 系统以解析错误消息为上下文发起修复重试
- **AND** 第 3 次返回仍不可解析时，该提案被丢弃且 trace 标记 `proposal_rejected`

#### Scenario: 新类别通过合同校验

- **GIVEN** LLM 返回一条提案 `{"expression": "log(float_cap)", "hypothesis": "流通市值小的标的流动性溢价更高……", "category": "规模"}`
- **WHEN** 合同校验执行
- **THEN** 该提案通过（`category="规模"` 在九类枚举内，表达式可解析）
- **AND** `category="基本面"` 等枚举外取值仍按合同违约进入修复重试

#### Scenario: 提示词模板与真相源一致

- **WHEN** 组装广度变异或深度改进提案 prompt
- **THEN** 模板列举的算子清单、字段清单与 `factors.dsl` 的算子表 / 字段集一致（含可选参考字段与新算子）
- **AND** 模板中的类别清单为九类、复杂度规则写明「去重后的数字字面量 ≤ 3」
